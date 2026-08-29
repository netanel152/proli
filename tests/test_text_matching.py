"""Unit tests for app/core/text_matching.contains_keyword (PRO-118).

Substring matching (``kw in text``) used to trip destructive flows on innocent
words: "טעות" ⊂ "בטעות" cancelled a confirmed BOOKED job, and any "מנהל"/
"admin" fragment paused the bot. contains_keyword only matches a keyword as a
complete token, or — for multi-word keywords — a complete word sequence.
"""

import pytest

from app.core.text_matching import contains_keyword, is_emergency_text
from app.core.messages import Messages

CANCEL_KEYWORDS = Messages.Keywords.CANCEL_KEYWORDS
SOS_COMMANDS = Messages.Keywords.SOS_COMMANDS
SOS_EXCLUDE_PHRASES = Messages.Keywords.SOS_EXCLUDE_PHRASES
RESCHEDULE_KEYWORDS = Messages.Keywords.RESCHEDULE_KEYWORDS


# --- The issue's exact false positives must now be False ------------------


def test_beteut_sentence_is_not_a_cancel_keyword():
    """'שלחתי בטעות את הכתובת הלא נכונה' must NOT match CANCEL_KEYWORDS —
    'טעות' is a substring of 'בטעות', not a standalone token, and the bare
    'טעות' keyword was dropped from CANCEL_KEYWORDS entirely."""
    assert contains_keyword("שלחתי בטעות את הכתובת הלא נכונה", CANCEL_KEYWORDS) is False


def test_construction_foreman_sentence_is_not_sos():
    """'אני צריך מנהל עבודה' (a construction foreman) must not trip the SOS
    keyword 'מנהל' — the exclude phrase removes it before matching."""
    assert (
        contains_keyword("אני צריך מנהל עבודה", SOS_COMMANDS, SOS_EXCLUDE_PHRASES)
        is False
    )


def test_administrator_sentence_is_not_sos():
    """'the administrator said ok' must not match 'admin' as a substring."""
    assert (
        contains_keyword("the administrator said ok", SOS_COMMANDS, SOS_EXCLUDE_PHRASES)
        is False
    )


# --- True positives must still match ---------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "בטל את העבודה",
        "לא משנה, תעזוב",
        "cancel",
        "Cancel",  # case-insensitive
    ],
)
def test_true_cancel_keywords_still_match(text):
    assert contains_keyword(text, CANCEL_KEYWORDS) is True


def test_representative_wants_a_human():
    assert (
        contains_keyword("אני רוצה לדבר עם נציג", SOS_COMMANDS, SOS_EXCLUDE_PHRASES)
        is True
    )


def test_bare_manager_word_still_triggers_sos():
    """A bare 'מנהל' (not followed by 'עבודה') is still an SOS request."""
    assert contains_keyword("מנהל", SOS_COMMANDS, SOS_EXCLUDE_PHRASES) is True


def test_multiword_reschedule_keyword_matches_as_phrase():
    assert contains_keyword("לשנות שעה בבקשה", RESCHEDULE_KEYWORDS) is True


# --- PRO-118 sibling fix: inflected forms explicitly listed -----------------
# Whole-token matching no longer catches inflections by substring accident
# ("בטל" ⊂ "לבטל"), so the common ones were added to CANCEL_KEYWORDS /
# SOS_COMMANDS explicitly. These pin that the fix actually landed.


@pytest.mark.parametrize(
    "text",
    [
        "אני רוצה לבטל את העבודה",  # infinitive, ל- prefix
        "ביטול",  # the exact reply RESCHEDULE_OFFER advertises
        "אני מבטל את התור",  # 1st person present
        "היא מבטלת את הפגישה",  # 3rd person feminine present
        "אני אבטל את התור",  # 1st person future
        "אתה תבטל בבקשה",  # 2nd person future
        "הם בטלו את הפגישה",  # 3rd person plural past
    ],
)
def test_inflected_cancel_forms_match(text):
    assert contains_keyword(text, CANCEL_KEYWORDS) is True


@pytest.mark.parametrize(
    "text",
    [
        "תעבירו אותי לנציג",
        "תעבירו אותי למנהל",
        "תעבירו אותי למנהלת",
        "הנציג לא עונה",
        "נציגה לא ענתה לי",
    ],
)
def test_handoff_phrasing_matches_sos(text):
    assert contains_keyword(text, SOS_COMMANDS, SOS_EXCLUDE_PHRASES) is True


def test_beteut_still_never_matches_despite_new_cancel_forms():
    """The new inflected CANCEL_KEYWORDS entries must not widen the net back
    onto 'בטעות' — it still contains no cancel token as a whole word."""
    assert contains_keyword("שלחתי בטעות את הכתובת הלא נכונה", CANCEL_KEYWORDS) is False


# --- Edge cases --------------------------------------------------------------


def test_empty_text_is_false():
    assert contains_keyword("", CANCEL_KEYWORDS) is False


def test_none_text_is_false():
    assert contains_keyword(None, CANCEL_KEYWORDS) is False


def test_empty_keywords_is_false():
    assert contains_keyword("בטל", []) is False


def test_punctuation_around_keyword_still_matches():
    """'!' around a keyword tokenizes away, leaving a clean whole-token match."""
    assert contains_keyword("בטל!", CANCEL_KEYWORDS) is True


def test_quotes_around_keyword_still_matches():
    assert contains_keyword('"בטל" בבקשה', CANCEL_KEYWORDS) is True


def test_exclude_phrase_at_start_of_text():
    assert (
        contains_keyword("מנהל עבודה הגיע לבדוק", SOS_COMMANDS, SOS_EXCLUDE_PHRASES)
        is False
    )


def test_exclude_phrase_at_end_of_text():
    assert (
        contains_keyword(
            "אני צריך את המספר של מנהל עבודה", SOS_COMMANDS, SOS_EXCLUDE_PHRASES
        )
        is False
    )


def test_exclude_phrase_repeated_back_to_back_is_fully_stripped():
    """Repetition must not leave a residual 'מנהל' token behind — the removal
    loops until stable rather than doing one pass."""
    assert (
        contains_keyword("מנהל עבודה מנהל עבודה", SOS_COMMANDS, SOS_EXCLUDE_PHRASES)
        is False
    )


def test_keyword_mid_word_never_matches():
    """'טעות' must never match as a mid-word fragment of a longer token."""
    assert contains_keyword("בטעות", ["טעות"]) is False
    assert contains_keyword("טעותכם", ["טעות"]) is False


def test_keyword_as_standalone_word_matches():
    assert contains_keyword("טעות", ["טעות"]) is True


# --- PRO-121: is_emergency_text (exact keywords + clitic-prefixable stems,
# minus negations) — the single detector shared by workflow_service and
# customer_flow. -------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "השריפה מתפשטת",  # clitic ה + "שריפה"
        "אני סובל מהצפה בבית",  # clitic מ + "הצפה"
        "וכשההצפה התחילה",  # stacked clitics ו+כש+ה + "הצפה"
        "יש לי נזילה דחופה בחיפה!",  # explicit prefixable stem "דחופה"
        "דחוף!",  # exact stem, punctuation stripped by tokenizing
        "יש קצר חשמלי",  # exact multi-word EMERGENCY_KEYWORDS entry
    ],
)
def test_is_emergency_text_true_positives(text):
    assert is_emergency_text(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "תסביר בקצרה",  # "קצר" only as a substring of "בקצרה" — bare "קצר"
        # was deliberately dropped from EMERGENCY_KEYWORDS
        "רק תיקון קצר",  # "קצר" as the everyday adjective, not a whole-token
        # emergency keyword
        "זה לא דחוף, אני יכול לחכות",  # EMERGENCY_EXCLUDE_PHRASES "לא דחוף"
        "תעזור לי לדחוף את הארון",  # "לדחוף" (the verb "to push") must not
        # read as a clitic-prefixed "דחוף"
        "אין סכנה",  # EMERGENCY_EXCLUDE_PHRASES entry
    ],
)
def test_is_emergency_text_true_negatives(text):
    assert is_emergency_text(text) is False
