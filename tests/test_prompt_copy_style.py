"""PRO-169 — the AI prompt copy follows docs/COPY_STYLE_GUIDE.md.

These pin the *mechanical* rules the guide states (gender-neutral phrasing,
no emoji, no em dash, no bold markup, the "אנחנו" voice, no Latin brand name
inside Hebrew) over the example utterances baked into the prompts, so a future
prompt edit that reintroduces a retired pattern fails here instead of shipping
to a customer. PRO-245 brought them to the owner's 2026-11-17 rules: the
examples used to open with one emoji and speak as "אני"; they now carry no
emoji and speak as "אנחנו", and the pro phase speaks for פרולי, never as the
professional.

Extraction scope: only double-quoted strings that contain a Hebrew letter are
treated as "examples" — this is deliberately narrow so it does not misfire on
prose. Two carve-outs matter:

- The first line of DISPATCHER_SYSTEM ("You are Proli (פרולי), ...") is English
  prose with a parenthetical Hebrew name, not a quoted example — it contains no
  `"..."` segment, so the quote-based extractor never sees it.
- Everything from `*** STYLE` to the end of each prompt is the guide text
  itself, which *names* retired patterns ("תשלח תמונה", "תוכל/י", "מתי נוח
  שאגיע?") as negative examples. Those are guidance about copy, not copy
  themselves, so the STYLE region is sliced off before extraction.
"""

import re
from unittest.mock import MagicMock

import pytest

from app.core.prompts import Prompts

HEBREW_RE = re.compile(r"[֐-׿]")
QUOTE_RE = re.compile(r'"([^"]+)"')
EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF☀-➿]")

# Fragments that are retired because they were *wrong*, not merely avoided:
# the AI guessing its own gender in first person ("צריך/ה"), first-person
# present tense ("אני מעביר"), assuming the pro is male ("שהוא מאשר"), and
# (PRO-245) the AI speaking as the professional who arrives ("שאגיע", "אבוא",
# "כאן {pro_name}"), the bug the first staging E2E chat showed.
RETIRED_FRAGMENTS = (
    "צריך/ה",
    "אני מעביר",
    "שהוא מאשר",
    "שאגיע",
    "אגיע",
    "אבוא",
    "כאן {pro_name}",
)
SLASHED_GENDER_RE = re.compile(r"[\u05d0-\u05ea]/[\u05d0-\u05ea]")
# §1: first person singular, in the forms the examples used before PRO-245.
FIRST_PERSON_RE = re.compile(r"(?<!\w)(אני|הבנתי|רשמתי|אטפל|אבדוק|אעדכן)(?!\w)")


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
        # (a) no emoji at all (§4)
        assert not EMOJI_RE.search(example), f"[{prompt_name}] emoji in: {example!r}"

        # (b) no em dash (§1) and no slashed gender form (§2)
        assert "—" not in example, f"[{prompt_name}] em dash in: {example!r}"
        assert not SLASHED_GENDER_RE.search(
            example
        ), f"[{prompt_name}] slashed gender form in: {example!r}"

        # (f) the voice is "אנחנו" (§1)
        assert not FIRST_PERSON_RE.search(
            example
        ), f"[{prompt_name}] first person singular in: {example!r}"

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


def test_already_welcomed_note_follows_the_same_copy_rules():
    """PRO-292: the note is appended to the dispatcher prompt after formatting, so
    it is not in DISPATCHER_SYSTEM's own scan. Its quoted examples are copy the
    model may repeat to a customer, so the same retired patterns stay out."""
    note = Prompts.DISPATCHER_ALREADY_WELCOMED
    examples = _extract_examples(note)
    assert examples, "extraction found nothing, so the checks below are vacuous"

    for text in (note, *examples):
        assert not EMOJI_RE.search(text)
        assert "—" not in text and "–" not in text
        assert not SLASHED_GENDER_RE.search(text)
        for fragment in RETIRED_FRAGMENTS:
            assert fragment not in text, f"retired fragment {fragment!r}"
    for example in examples:
        assert not FIRST_PERSON_RE.search(example), f"first person in: {example!r}"


def test_pro_phase_estimate_wording_names_whose_price_list_it_is():
    """PRO-292 acceptance: the customer text says whose estimate it is."""
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

    assert "לפי המחירון של דני" in formatted


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
    # PRO-272: the closing tag is gone too — only the JSON is_deal closes a deal.
    assert "[DEAL:" not in prompt
    assert "אבי" in prompt
    assert admin_utils.PROFESSION_CONFIG["plumber"]["safety"] in prompt
    assert keywords == admin_utils.PROFESSION_CONFIG["plumber"]["keywords"]


def test_generate_system_prompt_names_the_trades_only_when_given_them(admin_utils):
    """PRO-234: the persona of a handyman says which work they take."""
    from app.core.pro_trades import PROMPT_TRADES_MARKER, trades_line

    trades = ["handyman", "plumber"]
    with_trades, _ = admin_utils.generate_system_prompt(
        "אבי", "handyman", "תל אביב", "ביקור 200", trades
    )
    without, _ = admin_utils.generate_system_prompt(
        "אבי", "handyman", "תל אביב", "ביקור 200"
    )

    assert trades_line(trades) in with_trades.splitlines()
    assert PROMPT_TRADES_MARKER not in without


def test_generated_persona_follows_the_2026_11_17_rules(admin_utils):
    """PRO-245: the stored persona is the first thing in the pro-phase prompt,
    so the model copies its register. It carries no em dash, emoji or slashed
    form, speaks for פרולי as "אנחנו", and never as the professional."""
    prompt, _ = admin_utils.generate_system_prompt(
        "אבי", "plumber", "תל אביב", "ביקור 200"
    )
    assert "—" not in prompt
    assert not EMOJI_RE.search(prompt)
    assert not SLASHED_GENDER_RE.search(prompt)
    assert '"אנחנו"' in prompt
    for fragment in ("אגיע", "אטפל", "הבנתי", "רשמתי"):
        assert fragment not in prompt


@pytest.mark.parametrize("prompt", [Prompts.DISPATCHER_SYSTEM, Prompts.PRO_BASE_SYSTEM])
def test_prompts_ask_for_short_bubbles_without_echo_or_markdown(prompt):
    """PRO-245: each reply one WhatsApp bubble, no echo of the customer's words,
    the name written once, and no markdown the phone would show raw."""
    assert "one to three short sentences" in prompt
    assert "Do not repeat or paraphrase back" in prompt
    assert "name at most once per message" in prompt
    assert "no markdown headers" in prompt
    assert "No emoji at all" in prompt
    assert "—" not in prompt
