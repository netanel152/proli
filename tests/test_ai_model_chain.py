"""The Gemini fallback chain declared as ``Settings.AI_MODELS``.

A retired model ID at the end of the chain can never answer, so falling back to
it is a guaranteed 404 — and that 404 is also what gets reported as the last
error, hiding the earlier failures that actually sent the call down the chain.
These tests read the *declared field default* off the model rather than an
instantiated ``settings`` object, so a local ``.env`` or an ``AI_MODELS`` env
var cannot change the result — what is pinned is the default that ships.

Exact model IDs are deliberately not pinned: they change on every refresh, and
a test that restates them only has to be edited alongside the config.
"""

from app.core.config import Settings

# Model-ID prefixes of FULLY RETIRED families only. Extend this when the next
# family is shut down: https://ai.google.dev/gemini-api/docs/deprecations
# This is not the full set of IDs ever dropped from the chain: gemini-2.5-flash
# and gemini-3.5-flash were dropped because one has a shutdown date and the
# other is a redirect, not because they are retired, so they do not belong here.
RETIRED_MODEL_PREFIXES = (
    "gemini-1.5-",
    "gemini-2.0-",
)

EXPECTED_CHAIN_LENGTH = 3


def _default_chain() -> list[str]:
    return Settings.model_fields["AI_MODELS"].default


def test_ai_models_default_has_no_duplicates():
    chain = _default_chain()

    assert len(chain) == len(set(chain)), f"duplicate model in chain: {chain}"


def test_ai_models_default_contains_no_retired_model():
    retired = [
        model for model in _default_chain() if model.startswith(RETIRED_MODEL_PREFIXES)
    ]

    assert not retired, f"retired model(s) in AI_MODELS default: {retired}"


def test_ai_models_default_is_three_deep_and_starts_with_lite_model():
    chain = _default_chain()

    assert len(chain) == EXPECTED_CHAIN_LENGTH
    assert "lite" in chain[0], f"cheapest-first violated, head is {chain[0]!r}"
