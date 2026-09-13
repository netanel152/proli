"""PRO-191: a phone number or an address interpolated straight into a log
f-string is not scrubbed by anything.

`app/core/logger.py`'s redaction list is built from `Settings` field *names*
(PRO-94) — that catches a leaked secret, not a customer's chat id or street
address, neither of which is a `Settings` field. PRO-173 already settled the
rule for *paging*: mask the phone to its last digits, never send the street
(city only). Since PRO-184 every log line is flat, indexed JSON in Railway's
Log Explorer, so the same leak class applies to a plain `logger.info(f"...")`
call — it is now exactly as searchable as the page was.

This file is a **ratchet**, not a blanket check. An AST scan at the time this
test was written found 74 unmasked `chat_id` interpolations in `logger.*`
calls across 16 files under `app/`. PRO-191 fixed the 31 the issue scoped
(`dispatch_guards.py`, `workflow_service.py`) — those two files must NOT
appear in `KNOWN_VIOLATIONS` below, because that is exactly what would let a
new violation creep back into a file this PR just cleaned. The other 43, in
14 files, are recorded debt: the second and third assertions below make sure
that number can only ever be paid down, never inflated, and never left stale
once someone *does* pay it down (see `test_known_violation_files_are_not_
below_recorded_count`).

The detector itself (`find_violations`) is the one thing both the repo scan
and its own unit tests exercise — a scanner that silently matched nothing
would make the whole ratchet vacuous, so its rules are pinned by the small
unit tests at the bottom of the file, on in-process source snippets rather
than real files.

Detection rules, matched against the source text of every `{...}` slot
(`ast.FormattedValue`) inside a direct `logger.<method>(...)` call (a chained
call like `logger.bind(...).error(...)` is out of scope — same as the
original AST scan this ratchet's numbers come from):

  1. chat id — the expression contains `chat_id` and does not also contain
     one of the accepted masking forms: `[-8:]`, `[-4:]`, `mask_chat_id`,
     `strip_suffix`.
  2. address / name fields — the expression structurally references
     `full_address`, `street`, `street_number`, `customer_name` or
     `display_name` (as a `Name`, an `Attribute.attr`, or a dict-style
     `Subscript` key such as `lead['full_address']`). `city` is deliberately
     never flagged — PRO-173 already rules it safe enough to send
     off-platform, and it is what makes a failed address gate diagnosable.
"""

import ast
import functools
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = REPO_ROOT / "app"

_SAFE_CHAT_ID_MARKERS = ("[-8:]", "[-4:]", "mask_chat_id", "strip_suffix")
_UNSAFE_FIELD_NAMES = {
    "full_address",
    "street",
    "street_number",
    "customer_name",
    "display_name",
}

# Exact known debt, file -> violation count, as of PRO-191. `dispatch_guards.py`
# and `workflow_service.py` are the two files this PR cleaned and must never
# reappear here — that omission is what makes assertion 1 below mean anything.
KNOWN_VIOLATIONS = {
    "app/api/routes/webhook.py": 1,
    "app/core/arq_worker.py": 3,
    "app/core/redis_client.py": 2,
    "app/providers/whatsapp/cloud_api.py": 3,
    "app/providers/whatsapp/delivery.py": 2,
    "app/providers/whatsapp/facade.py": 1,
    "app/services/context_manager_service.py": 5,
    "app/services/customer_flow.py": 2,
    "app/services/data_management_service.py": 2,
    "app/services/monitor_service.py": 4,
    "app/services/notification_service.py": 4,
    "app/services/pro_flow.py": 6,
    "app/services/security_service.py": 1,
    "app/services/state_manager_service.py": 7,
}


# ---------------------------------------------------------------------------
# The detector. Shared by the repo scan below and by its own unit tests —
# otherwise the unit tests would prove nothing about what the scan does.
# ---------------------------------------------------------------------------


def _is_direct_logger_call(call: ast.Call) -> bool:
    """True only for `logger.<method>(...)` — a single attribute access on a
    bare `logger` name. A wrapped call like `logger.bind(...).error(...)` or
    `logger.opt(...).critical(...)` is deliberately out of scope, matching
    the original AST scan this ratchet's counts are drawn from."""
    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "logger"
    )


def _subscript_const_str_key(node: ast.Subscript):
    """The string key of a dict-style subscript (`lead['full_address']`),
    or None. Handles both the 3.9+ shape (slice is the expression directly)
    and the pre-3.9 `ast.Index` wrapper."""
    sl = node.slice
    if hasattr(ast, "Index") and isinstance(sl, ast.Index):  # pragma: no cover
        sl = sl.value
    if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
        return sl.value
    return None


def _references_unsafe_field(expr: ast.AST) -> bool:
    """Structural match only — a bare string literal sitting in a tuple/list
    (e.g. `('street', 'street_number')` used to report which field *names*
    are present) is not a reference to field *content* and must not trip
    this. Only a `Name`, an `Attribute.attr`, or a dict-subscript key count."""
    for node in ast.walk(expr):
        if isinstance(node, ast.Name) and node.id in _UNSAFE_FIELD_NAMES:
            return True
        if isinstance(node, ast.Attribute) and node.attr in _UNSAFE_FIELD_NAMES:
            return True
        if isinstance(node, ast.Subscript):
            if _subscript_const_str_key(node) in _UNSAFE_FIELD_NAMES:
                return True
    return False


def _is_violation(expr: ast.AST, expr_src: str) -> bool:
    if "chat_id" in expr_src and not any(
        marker in expr_src for marker in _SAFE_CHAT_ID_MARKERS
    ):
        return True
    return _references_unsafe_field(expr)


def find_violations(source: str) -> list[tuple[int, str]]:
    """Every `(lineno, expr_source)` PII violation inside f-string slots of
    direct `logger.*` calls in `source`."""
    tree = ast.parse(source)
    violations: list[tuple[int, str]] = []
    seen: set[int] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _is_direct_logger_call(node)):
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.FormattedValue):
                continue
            if id(sub) in seen:
                continue
            seen.add(id(sub))
            expr_src = ast.get_source_segment(source, sub.value) or ast.unparse(
                sub.value
            )
            if _is_violation(sub.value, expr_src):
                violations.append((sub.lineno, expr_src))
    return violations


@functools.lru_cache(maxsize=1)
def _scan_repo() -> dict:
    """`{relative/posix/path.py: [(lineno, expr), ...]}` for every file under
    `app/` that has at least one violation. Cached — three tests below all
    want the same scan and a fresh `ast.parse` of the whole tree per test
    would be pure waste."""
    results = {}
    for path in sorted(APP_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        violations = find_violations(source)
        if violations:
            results[path.relative_to(REPO_ROOT).as_posix()] = violations
    return results


# ---------------------------------------------------------------------------
# The ratchet, against the real tree.
# ---------------------------------------------------------------------------


def test_no_new_unmasked_pii_log_interpolations_outside_known_violations():
    """Assertion 1: a file not already carrying recorded debt must have zero
    violations. This is what stops a new one appearing anywhere — including
    back in `dispatch_guards.py`/`workflow_service.py`, which this PR just
    cleaned and which must therefore never gain an entry in
    `KNOWN_VIOLATIONS`."""
    scanned = _scan_repo()
    unexpected = {
        path: len(v) for path, v in scanned.items() if path not in KNOWN_VIOLATIONS
    }
    assert not unexpected, (
        "Unmasked chat_id/address/name interpolation(s) in logger calls, in "
        f"files with no recorded ratchet debt: {unexpected}. Mask the chat id "
        "(chat_id[-8:], mask_chat_id(), strip_suffix()) or drop the address/"
        "name field from the log line — city is fine, the street/name is not."
    )


def test_known_violation_files_do_not_exceed_recorded_count():
    """Assertion 2: no backsliding in a file that already has debt."""
    scanned = _scan_repo()
    regressed = {
        path: (expected, len(scanned.get(path, [])))
        for path, expected in KNOWN_VIOLATIONS.items()
        if len(scanned.get(path, [])) > expected
    }
    assert not regressed, (
        "Violation count increased in file(s) already carrying ratchet debt "
        f"(expected, actual): {regressed}. Revert the new interpolation(s) or "
        "mask them before raising the recorded count."
    )


def test_known_violation_files_are_not_below_recorded_count():
    """Assertion 3: `KNOWN_VIOLATIONS` must track reality exactly, not just
    bound it — a count that is only ever a ceiling silently rots into a
    blanket exemption for that file. When a fix lowers the real count, this
    test fails and says exactly which number to write (or that the entry can
    be deleted)."""
    scanned = _scan_repo()
    stale = {
        path: len(scanned.get(path, []))
        for path, expected in KNOWN_VIOLATIONS.items()
        if len(scanned.get(path, [])) < expected
    }
    assert not stale, (
        "KNOWN_VIOLATIONS is stale — these files now have fewer violations "
        "than recorded. Lower KNOWN_VIOLATIONS to match (or delete the entry "
        f"if it reached 0): {stale}"
    )


# ---------------------------------------------------------------------------
# Unit tests for the detector itself, on in-process source snippets.
# ---------------------------------------------------------------------------


def test_detector_allows_the_accepted_chat_id_mask():
    src = textwrap.dedent("""
        def handler():
            logger.info(f"🚦 User ...{ctx.chat_id[-8:]} is in State: {state}")
        """)
    assert find_violations(src) == []


def test_detector_catches_bare_chat_id_interpolation():
    src = textwrap.dedent("""
        def handler():
            logger.info(f"Task started: processing message for {chat_id}")
        """)
    violations = find_violations(src)
    assert len(violations) == 1


def test_detector_catches_attribute_chat_id_interpolation():
    src = textwrap.dedent("""
        def handler():
            logger.warning(f"stuck lead for pro ...{ctx.chat_id}")
        """)
    violations = find_violations(src)
    assert len(violations) == 1


def test_detector_catches_dict_style_full_address():
    src = textwrap.dedent("""
        def handler():
            logger.error(f"lead parse failed for {lead['full_address']}")
        """)
    violations = find_violations(src)
    assert len(violations) == 1


def test_detector_allows_city_interpolation():
    src = textwrap.dedent("""
        def handler():
            logger.info(f"no pro available near {city}")
        """)
    assert find_violations(src) == []


def test_detector_ignores_chat_id_in_a_non_logger_call():
    """The rule is about log lines, not every f-string in the codebase — a
    provider send call that happens to interpolate chat_id is not this
    ticket's concern."""
    src = textwrap.dedent("""
        async def handler():
            await whatsapp.send_message(chat_id, f"hello {chat_id}")
        """)
    assert find_violations(src) == []


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(pytest.main([__file__, "-q"]))
