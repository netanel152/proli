"""PRO-292: the last pass over a model reply before the customer reads it.

`clean_customer_reply` fixes what the first staging chat showed the model still
writes after PRO-245's prompts (a doubled name, emoji, a dash, פרולי speaking as
the professional who arrives) and must leave a correct reply byte-for-byte alone.
`consent_accepted_within` is what lets the dispatcher prompt know the consent
request already said hello.

The pipeline's use of both is covered end to end in
`tests/e2e/test_e2e_reply_copy.py`.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.core.constants import WorkerConstants
from app.services.data_management_service import consent_accepted_within
from app.services.reply_copy import clean_customer_reply, copy_violations

NAME = "נתקס"


@pytest.mark.parametrize(
    "text, names, expected",
    [
        pytest.param(
            f"נעים להכיר, {NAME}{NAME}",
            (NAME,),
            f"נעים להכיר, {NAME}",
            id="name-written-twice-glued",
        ),
        pytest.param(
            "דנה, דנה, מה קרה?",
            ("דנה",),
            "דנה, מה קרה?",
            id="name-written-twice-with-comma",
        ),
        pytest.param(
            f"נעים להכיר, {NAME}{NAME}",
            (None, 7, NAME),
            f"נעים להכיר, {NAME}",
            id="non-string-names-are-ignored",
        ),
        pytest.param(
            "👋 שלום, אני פרולי.",
            (),
            "שלום, אני פרולי.",
            id="emoji-removed-and-leftover-space-tidied",
        ),
        pytest.param(
            "👋 שלום\nמה קרה? 🙏",
            (),
            "שלום\nמה קרה?",
            id="emoji-removed-keeps-line-breaks",
        ),
        pytest.param(
            "היום ב-18:00 — תיקון נזילה",
            (),
            "היום ב-18:00, תיקון נזילה",
            id="dash-between-words-becomes-comma",
        ),
        pytest.param(
            "בין 300–450 ₪",
            (),
            "בין 300-450 ₪",
            id="dash-between-digits-becomes-hyphen",
        ),
        pytest.param(
            "מה קרה? — דחוף",
            (),
            "מה קרה? דחוף",
            id="dash-after-punctuation-is-dropped",
        ),
        pytest.param(
            "— תיקון נזילה —",
            (),
            "תיקון נזילה",
            id="dash-at-line-edges-is-dropped",
        ),
        pytest.param(
            "ומתי נוח שאגיע?",
            (),
            "ומתי נוח שאיש המקצוע יגיע?",
            id="arrival-with-prefix-goes-third-person",
        ),
        pytest.param(
            "אני אגיע מחר",
            (),
            "איש המקצוע יגיע מחר",
            id="arrival-with-pronoun-goes-third-person",
        ),
        pytest.param(
            "נבדוק ואבוא מחר",
            (),
            "נבדוק ואיש המקצוע יבוא מחר",
            id="come-with-vav-goes-third-person",
        ),
        pytest.param(
            f"נעים להכיר, {NAME}",
            (NAME,),
            f"נעים להכיר, {NAME}",
            id="name-written-once-is-untouched",
        ),
        pytest.param(
            "שלום, איך קוראים לך?\nומה קרה?\n\nתודה",
            ("דנה",),
            "שלום, איך קוראים לך?\nומה קרה?\n\nתודה",
            id="clean-reply-is-unchanged-newlines-included",
        ),
    ],
)
def test_clean_customer_reply_fixes_only_what_the_rules_name(text, names, expected):
    assert clean_customer_reply(text, *names) == expected


def test_copy_violations_names_what_is_left_and_the_cleaner_removes_all_of_it():
    dirty = f"👋 שלום {NAME}{NAME} — ומתי נוח שאגיע?"

    assert copy_violations(dirty) == ["emoji", "dash", "first_person_arrival"]
    assert copy_violations("שלום, איך קוראים לך?") == []

    cleaned = clean_customer_reply(dirty, NAME)
    assert copy_violations(cleaned) == []
    assert cleaned.count(NAME) == 1
    assert clean_customer_reply(cleaned, NAME) == cleaned, "cleaning is stable"


# ---------------------------------------------------------------------------
# data_management_service.consent_accepted_within
# ---------------------------------------------------------------------------

WINDOW = WorkerConstants.CONSENT_HOLD_TTL_SECONDS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case, accepted, age_seconds, naive, expected",
    [
        ("recent-accept", True, 60, False, True),
        ("naive-utc-recent-accept", True, 60, True, True),
        ("accept-older-than-the-window", True, WINDOW + 60, False, False),
        ("recent-decline", False, 60, False, False),
        ("no-record", None, None, False, False),
    ],
)
async def test_consent_accepted_within_is_true_only_for_a_recent_accept(
    mock_db, case, accepted, age_seconds, naive, expected
):
    # mock_db is module-scoped, so every case gets its own chat id.
    chat_id = f"{case}@c.us"
    if accepted is not None:
        stamp = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
        if naive:
            stamp = stamp.replace(tzinfo=None)  # what Mongo hands back
        await mock_db.consent.insert_one(
            {"chat_id": chat_id, "accepted": accepted, "timestamp": stamp}
        )

    assert await consent_accepted_within(chat_id, WINDOW) is expected
