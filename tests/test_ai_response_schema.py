"""Regression guard for the AIResponse -> Gemini response_schema conversion.

Production symptom this covers: every model in AI_MODELS failing within ~40ms of
each other with `'NoneType' object has no attribute 'upper'`, i.e. before any
HTTP call was made. The cause was schema shape, not the API:

  * requirements.txt pins pydantic==2.6.1, which resolves google-genai to 1.2.0.
  * On pydantic 2.6.x, a nested-model field that also carries sibling metadata
    (e.g. `Field(description=...)`) serialises to
    `{"allOf": [{"$ref": ...}], "description": ...}` rather than a bare `$ref`.
  * google-genai 1.2.0's `process_schema()` resolves `$ref` and `anyOf` but not
    `allOf`, so it recursed into a sub-schema with no "type" key and called
    `.upper()` on None.

Nothing in the suite could have caught it: conftest replaces `google.genai` with
a MagicMock, so the real transform never ran. These tests deliberately reach past
that mock.
"""

import importlib
import sys

import pytest

from app.services.ai_engine_service import AIResponse


def _iter_subschemas(node):
    """Yield every dict node in a JSON-schema document."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _iter_subschemas(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_subschemas(item)


def test_ai_response_schema_has_no_allof():
    """`allOf` anywhere in the schema is what google-genai 1.2.0 chokes on.

    This assertion is pydantic-version-independent: on 2.6.x it fails the moment
    a nested-model field regains sibling metadata; on newer pydantic the same
    field would inline the `$ref` and stay green, so it documents the intent
    rather than the accident.
    """
    schema = AIResponse.model_json_schema()
    offenders = [node for node in _iter_subschemas(schema) if "allOf" in node]
    assert not offenders, (
        "AIResponse.model_json_schema() contains 'allOf', which google-genai "
        f"1.2.0 cannot process: {offenders}"
    )


@pytest.fixture
def real_genai():
    """Import the *real* google.genai, bypassing the conftest-wide MagicMock.

    conftest sets `sys.modules["google.genai"] = MagicMock()` at collection time
    so the rest of the suite never needs the SDK installed. Here we want the
    genuine article, so swap it out for the duration of the test and put the mock
    back afterwards — otherwise later tests would get the real package.
    """
    stashed = {
        name: module
        for name, module in list(sys.modules.items())
        if name == "google.genai" or name.startswith("google.genai.")
    }
    for name in stashed:
        del sys.modules[name]
    try:
        yield importlib.import_module("google.genai")
    finally:
        for name in [
            n
            for n in list(sys.modules)
            if n == "google.genai" or n.startswith("google.genai.")
        ]:
            del sys.modules[name]
        sys.modules.update(stashed)


def test_ai_response_survives_genai_schema_transform(real_genai):
    """Run the SDK's own schema transform — the exact call that used to raise.

    `_transformers.t_schema` is private, but it is what
    `generate_content(config=...)` invokes on `response_schema`, and it runs
    entirely offline. Reaching for it is the only way to exercise the failure
    without a live API key.
    """
    transformers = pytest.importorskip(
        "google.genai._transformers",
        reason="google-genai internals moved; update this guard",
    )

    client = real_genai.Client(api_key="fake-key-no-network-needed")
    schema = transformers.t_schema(client._api_client, AIResponse)

    assert schema is not None
    # The nested model must survive as a real object, not get flattened away.
    extracted = schema.properties["extracted_data"]
    assert extracted.properties is not None
    assert "city" in extracted.properties
    assert "issue" in extracted.properties
