"""PRO-169 — the AI prompt copy follows docs/COPY_STYLE_GUIDE.md.

These pin the *mechanical* rules the guide states (gender-neutral phrasing,
emoji placement, no bold markup, no Latin brand name inside Hebrew) over the
example utterances baked into the prompts, so a future prompt edit that
reintroduces a retired pattern fails here instead of shipping to a customer.

Extraction scope: only double-quoted strings that contain a Hebrew letter are
treated as "examples" — this is deliberately narrow so it does not misfire on
prose. Two carve-outs matter:

- The first line of DISPATCHER_SYSTEM ("You are Proli (פרולי), ...") is English
  prose with a parenthetical Hebrew name, not a quoted example — it contains no
  `"..."` segment, so the quote-based extractor never sees it.
- Everything from `*** STYLE` to the end of each prompt is the guide text
  itself, which *names* retired patterns ("תשלח תמונה", "מעביר/מעבירה") as
  negative examples and the accepted stand-ins ("תוכל/י", "את/ה") for an
  unavoidable second-person verb. Those are guidance about copy, not copy
  themselves, so the STYLE region is sliced off before extraction.
"""

import re
from unittest.mock import MagicMock

import pytest

from app.core.prompts import Prompts

HEBREW_RE = re.compile(r"[֐-׿]")
QUOTE_RE = re.compile(r'"([^"]+)"')
EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF☀-➿]")

# Fragments that are retired because they were *wrong*, not merely avoided.
# "תוכל/י", "את/ה", "נמצא/ת" and "מתאר/ת" are the guide's own sanctioned
# inclusive fallback for a second-person verb that can't be phrased around
# (§2 rule 2, and the STYLE block quotes them as accepted) — they must NOT be
# in this denylist. What's actually retired: the AI guessing its own gender in
# first person ("צריך/ה"), first-person present tense ("אני מעביר"), and
# assuming the pro is male ("שהוא מאשר").
RETIRED_FRAGMENTS = ("צריך/ה", "אני מעביר", "שהוא מאשר")


def _extract_examples(prompt_text: str) -> list[str]:
    """Quoted, Hebrew-containing segments from the flow-steps region only."""
    body = prompt_text.split("*** STYLE", 1)[0]
    return [m.group(1) for m in QUOTE_RE.finditer(body) if HEBREW_RE.search(m.group(1))]


DISPATCHER_EXAMPLES = _extract_examples(Prompts.DISPATCHER_SYSTEM)
PRO_EXAMPLES = _extract_examples(Prompts.PRO_BASE_SYSTEM)


def test_extraction_actually_found_examples():
    # A guard against the extractor silently matching nothing (e.g. after a
    # quoting-style rewrite) and every check below turning vacuous.
    assert len(DISPATCHER_EXAMPLES) >= 5
    assert len(PRO_EXAMPLES) >= 5


def test_dispatcher_first_line_is_not_treated_as_an_example():
    first_line = Prompts.DISPATCHER_SYSTEM.strip().split("\n", 1)[0]
    assert "Proli" in first_line  # sanity: this is the line the docstring warns about
    assert first_line not in DISPATCHER_EXAMPLES


@pytest.mark.parametrize(
    "prompt_name,examples",
    [("DISPATCHER_SYSTEM", DISPATCHER_EXAMPLES), ("PRO_BASE_SYSTEM", PRO_EXAMPLES)],
)
def test_example_utterances_follow_style_rules(prompt_name, examples):
    for example in examples:
        emojis = EMOJI_RE.findall(example)

        # (a) at most one emoji
        assert len(emojis) <= 1, f"[{prompt_name}] multiple emoji in: {example!r}"

        # (b) if present, the emoji is the first character of the first line
        if emojis:
            first_line = example.split("\n", 1)[0]
            assert first_line and EMOJI_RE.match(
                first_line[0]
            ), f"[{prompt_name}] emoji not leading in: {example!r}"

        # (c) no *bold* markup
        assert not re.search(
            r"\*[^*]+\*", example
        ), f"[{prompt_name}] bold markup in: {example!r}"

        # (d) no Latin "Proli" inside Hebrew example text (the Hebrew name is "פרולי")
        assert (
            "proli" not in example.lower()
        ), f"[{prompt_name}] Latin brand name in: {example!r}"

        # (e) none of the retired gendered fragments
        for fragment in RETIRED_FRAGMENTS:
            assert (
                fragment not in example
            ), f"[{prompt_name}] retired fragment {fragment!r} in: {example!r}"


def test_dispatcher_system_formats_with_known_facts_placeholders_and_no_stray_braces():
    formatted = Prompts.DISPATCHER_SYSTEM.format(
        known_customer_name="none",
        known_city="none",
        known_issue="none",
        known_street="none",
        known_street_number="none",
        known_floor="none",
        known_apartment="none",
    )
    # {{name}} is an escaped literal meant to survive .format() untouched, so
    # exactly that one brace pair is expected to remain.
    remaining = re.findall(r"\{[^{}]*\}", formatted)
    assert remaining == ["{name}"]


def test_pro_base_system_formats_with_full_placeholder_set_and_no_stray_braces():
    formatted = Prompts.PRO_BASE_SYSTEM.format(
        base_system_prompt="BASE",
        pro_name="דני",
        price_list="PL",
        social_proof_text="SP",
        extracted_city="תל אביב",
        extracted_issue="נזילה",
        transcription="",
        current_datetime="2026-01-01T10:00:00",
    )
    assert re.findall(r"\{[^{}]*\}", formatted) == []


def test_ai_replay_marker_invariant_dispatcher_first_line_not_in_pro_prompt():
    # tests/e2e/test_e2e_ai_replay.py relies on the two system prompts being
    # distinguishable by their opening line; a copy edit that duplicated the
    # dispatcher's greeting text into the pro prompt would silently break that
    # replay fixture's routing.
    first_line = Prompts.DISPATCHER_SYSTEM.strip().split("\n", 1)[0]
    assert first_line not in Prompts.PRO_BASE_SYSTEM


# ---------------------------------------------------------------------------
# admin_panel/core/utils.py: PROFESSION_CONFIG safety lines + generate_system_prompt
#
# The safety lines moved from bare masculine imperatives ("סגור", "הורד"...) to
# neutral infinitive phrasing ("לסגור", "להוריד"...). tests/test_seed_coverage_matrix.py
# already asserts the seven-key set against the seeded matrix; this only pins the
# safety-line wording rule.
#
# admin_panel.core.utils constructs a real pymongo MongoClient at module scope
# (tests/test_environment_config.py:241 documents the same convention). We
# import it lazily via this fixture instead of at module scope, and only
# neutralise MongoClient if nothing has imported the module yet — another test
# file importing admin_panel.* first (e.g. test_admin_kanban.py) may already
# have it loaded, and reloading here would be wasteful and could disturb that
# module's already-initialized `db`/collection globals.
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_utils(monkeypatch):
    import sys

    if "admin_panel.core.utils" not in sys.modules:
        monkeypatch.setattr("pymongo.MongoClient", lambda *a, **k: MagicMock())
    import admin_panel.core.utils as m

    return m


RETIRED_IMPERATIVE_PREFIXES = (
    "סגור",
    "הורד",
    "ודא",
    "אל תנסה",
    "אוורר",
    "אל תערבב",
    "אל תיגע",
)


def test_profession_config_safety_lines_are_not_bare_masculine_imperatives(
    admin_utils,
):
    profession_config = admin_utils.PROFESSION_CONFIG
    assert set(profession_config.keys()) == {
        "plumber",
        "electrician",
        "handyman",
        "locksmith",
        "painter",
        "cleaner",
        "general",
    }
    for profession, config in profession_config.items():
        safety = config["safety"]
        for prefix in RETIRED_IMPERATIVE_PREFIXES:
            assert not safety.startswith(prefix), (
                f"{profession} safety line still opens with a bare masculine "
                f"imperative {prefix!r}: {safety!r}"
            )


def test_generate_system_prompt_drops_urgent_tag_and_interpolates_persona(
    admin_utils,
):
    prompt, keywords = admin_utils.generate_system_prompt(
        "אבי", "plumber", "תל אביב", "ביקור 200"
    )

    # The [URGENT] tag instruction was removed: nothing in app/ parsed it, so
    # the model was emitting a token the customer saw verbatim.
    assert "[URGENT]" not in prompt
    assert "[DEAL:" in prompt
    assert "אבי" in prompt
    assert admin_utils.PROFESSION_CONFIG["plumber"]["safety"] in prompt
    assert keywords == admin_utils.PROFESSION_CONFIG["plumber"]["keywords"]
