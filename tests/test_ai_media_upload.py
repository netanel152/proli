"""Static guard: every Gemini File API call matches the installed SDK's signature.

Why this is a *static* check rather than a normal unit test:

`ai_engine_service.analyze_conversation` reaches the File API only for audio
and video, and the whole media block sits inside one
``except Exception as e: logger.error(...)``. A wrong keyword therefore raises
a ``TypeError`` that is caught, logged, and then *discarded* — `current_parts`
stays empty, the prompt ("transcribe this audio") still goes to Gemini with no
audio attached, and the customer gets a confident answer about a recording the
model never heard. Nothing crashes and nothing fails CI.

That is exactly how PRO-204 shipped: the call passed ``path=tmp_path``, but
``google-genai`` takes a keyword-only ``file=``, so every voice note and every
video silently reached Gemini as text alone.

Both sides are parsed with `ast` and nothing is imported, for two reasons:
`tests/conftest.py` replaces `google.genai` in `sys.modules` with a
``MagicMock`` for the whole unit suite, and a mock accepts every keyword — a
guard that imported the SDK would pass no matter what the call site says. The
service itself needs a `GEMINI_API_KEY` at import time. Reading the SDK's own
source instead means this also catches the next keyword rename in a pinned
version bump, rather than restating ``file=`` as a literal.

Deliberate limits (a miss here is acceptable, a false positive is not):

* Positional-only SDK parameters are ignored. `AsyncFiles` has none beyond
  `self` today; one that appeared would surface through the "no positional
  args" assertion instead.
* The guard skips entirely when `google-genai` is not installed beside the
  interpreter. It is in `requirements.txt`, so CI runs it.
"""

import ast
import sys
from pathlib import Path

import pytest

SERVICE = (
    Path(__file__).resolve().parents[1] / "app" / "services" / "ai_engine_service.py"
)

# The attribute chain the service reaches the async File API through, and the
# SDK class that chain lands on.
FILES_CHAIN_SUFFIX = "aio.files."
SDK_CLASS = "AsyncFiles"


def _sdk_files_source() -> Path:
    for entry in sys.path:
        candidate = Path(entry) / "google" / "genai" / "files.py"
        if candidate.is_file():
            return candidate
    pytest.skip("google-genai is not installed beside this interpreter")


def _dotted_name(node: ast.AST) -> str | None:
    """ "self.client.aio.files.upload" for an attribute chain, else None."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _files_calls() -> list[tuple[str, ast.Call]]:
    tree = ast.parse(SERVICE.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        dotted = _dotted_name(node.func)
        if dotted and FILES_CHAIN_SUFFIX in dotted:
            found.append((dotted.rsplit(".", 1)[1], node))
    return found


def _sdk_methods() -> dict[str, ast.AST]:
    tree = ast.parse(_sdk_files_source().read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == SDK_CLASS:
            return {
                child.name: child
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    pytest.fail(f"{SDK_CLASS} not found in {_sdk_files_source()}")


def _keyword_params(func: ast.AST) -> tuple[set[str], set[str]]:
    """(accepted, required) keyword parameter names, minus `self`."""
    accepted, required = set(), set()

    named = [arg.arg for arg in func.args.args]
    first_defaulted = len(named) - len(func.args.defaults)
    for index, name in enumerate(named):
        if name == "self":
            continue
        accepted.add(name)
        if index < first_defaulted:
            required.add(name)

    for arg, default in zip(func.args.kwonlyargs, func.args.kw_defaults):
        accepted.add(arg.arg)
        if default is None:
            required.add(arg.arg)

    return accepted, required


def test_service_still_calls_the_async_file_api():
    # Guards the guard: a refactor that moves or renames the chain must not
    # leave this module silently checking nothing.
    assert _files_calls(), f"no {FILES_CHAIN_SUFFIX}* call found in {SERVICE.name}"


def test_every_file_api_call_matches_the_sdk_signature():
    methods = _sdk_methods()

    for name, call in _files_calls():
        assert name in methods, f"{SDK_CLASS} has no {name!r}"

        accepted, required = _keyword_params(methods[name])
        passed = {kw.arg for kw in call.keywords if kw.arg is not None}

        assert not call.args, (
            f"files.{name} is called with {len(call.args)} positional arg(s); "
            f"every {SDK_CLASS} parameter is keyword-only"
        )
        assert all(
            kw.arg is not None for kw in call.keywords
        ), f"files.{name} is called with ** unpacking; it cannot be checked here"
        assert passed <= accepted, (
            f"files.{name} is passed {sorted(passed - accepted)}, which the "
            f"installed SDK does not accept; it takes {sorted(accepted)}"
        )
        assert (
            required <= passed
        ), f"files.{name} is missing required {sorted(required - passed)}"
