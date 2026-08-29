"""State × input matrix — how the FSM answers the ways real people actually reply.

"Cover every possible response" cannot mean infinite free text, so this is the
professional version: for **every** value of ``UserStates``, drive the **full set
of realistic input classes** and assert where the machine lands.

The table below is the authoritative record. It is **generated from the executable
``MATRIX``** and pinned by ``test_the_documented_matrix_matches_the_executable_one``,
so a cell cannot be silently empty: adding a state or an input class fails that
test until every new cell is filled in with either a real case or an explicit
``N/A`` and a reason.

Input classes
-------------
==========  ================================================================
``keyword`` The expected keyword or menu answer (the happy path).
``free``    Valid free text the model has to interpret ("סבבה מתאים לי").
``offtopic``Ambiguous or off-topic text mid-flow.
``wrong``   Wrong input type — media where text is expected.
``emoji``   An emoji-only / thumbs-up reply.
``silence`` Nobody replies: either the timeout job fires, or the state's Redis
            TTL bounds it so the user cannot be stuck forever.
``interrupt``A mid-flow interruption keyword (``התחלה``/``ביטול``/``נציג``).
``race``    A second inbound arriving before the first finished processing.
==========  ================================================================

| state | keyword | free | offtopic | wrong | emoji | silence | interrupt | race |
|---|---|---|---|---|---|---|---|---|
| `idle` | "אני המערכת החכמה של Proli" | "מאתר איש מקצוע" | "בעלי מקצוע" | "קיבלתי את התמונה" | "במה אפשר לעזור" | N/A[resting] | silent | N/A[race] |
| `awaiting_consent` | → idle, "אפשר להתחיל" | "ברוכים הבאים ל-Proli" | "ברוכים הבאים ל-Proli" | "ברוכים הבאים ל-Proli" | "ברוכים הבאים ל-Proli" | TTL ≤ 14400s | → idle, "לא נשמור מידע עליך" | N/A[race] |
| `customer_mode` | "מה הכתובת" | "מאיפה בדיוק" | "במה אפשר לעזור" | "ראיתי את התמונה" | "לעזור" | TTL ≤ 14400s | → pro_mode | N/A[race] |
| `awaiting_address` | → idle, "הכתובת עודכנה בהצלחה" | "מספר דירה" | "עוד פרטים לכתובת" | "לא הצלחתי לזהות את הכתובת" | "לא הצלחתי לזהות את הכתובת" | TTL ≤ 14400s | → idle, "הבקשה בוטלה" | N/A[race] |
| `awaiting_pro_approval` | → awaiting_pro_approval, "מאתרים עבורך איש מקצוע זמין" | "אצל איש המקצוע לאישור" | "אצל איש המקצוע לאישור" | "אצל איש המקצוע לאישור" | "אצל איש המקצוע לאישור" | TTL ≤ 3600s | → paused_for_human, "מעביר אותך לנציג אנושי" | N/A[race] |
| `paused_for_human` | silent | silent | silent | silent | silent | TTL ≤ 900s | "מעביר אותך לנציג אנושי" | N/A[race] |
| `awaiting_reschedule_time` | → idle, "המועד שונה בהצלחה" | "בחר מספר תור חוקי" | "בחר מספר תור חוקי" | "בחר מספר תור חוקי" | "בחר מספר תור חוקי" | TTL ≤ 14400s | → idle, "המועד נשאר כפי שהיה" | N/A[race] |
| `awaiting_loyalty_confirmation` | → awaiting_pro_approval, "עם הפרטים, ואעדכן אותך ברגע שיאשר" | → idle, "אחפש עבורך את איש המקצוע הפנוי" | "לא בטוח שהבנתי" | "לא בטוח שהבנתי" | "לא בטוח שהבנתי" | TTL ≤ 300s | → paused_for_human, "מעביר אותך לנציג אנושי" | N/A[race] |
| `awaiting_new_or_existing` | → idle, "הבעיה החדשה" | "אנא השב 1 (בעיה חדשה) או 2" | "אנא השב 1 (בעיה חדשה) או 2" | "אנא השב 1 (בעיה חדשה) או 2" | "אנא השב 1 (בעיה חדשה) או 2" | TTL ≤ 14400s | → paused_for_human, "מעביר אותך לנציג אנושי" | N/A[race] |
| `awaiting_cancel_confirmation` | → idle, "ביטלתי את העבודה" | → idle, "העבודה נשארת כמתוכנן" | → idle, "העבודה נשארת כמתוכנן" | → idle, "העבודה נשארת כמתוכנן" | → idle, "העבודה נשארת כמתוכנן" | TTL ≤ 300s | → paused_for_human, "מעביר אותך לנציג אנושי" | N/A[race] |
| `pro_mode` | "פקודות המערכת" | "פקודות המערכת" | "פקודות המערכת" | N/A[pro-text-only] | "פקודות המערכת" | TTL ≤ 14400s | "פקודות המערכת" | N/A[race] |
| `awaiting_intent_confirmation` | → customer_mode, "עברת למצב לקוח" | "בוא ננסה שוב" | "בוא ננסה שוב" | N/A[pro-text-only] | "בוא ננסה שוב" | TTL ≤ 300s | → idle, "ממשיכים כרגיל" | N/A[race] |
| `pro_selecting_job_to_finish` | N/A[defect-finish] | N/A[defect-finish] | N/A[defect-finish] | N/A[pro-text-only] | N/A[defect-finish] | TTL ≤ 14400s | N/A[defect-finish] | N/A[race] |
| `pro_selecting_job_to_cancel` | N/A[defect-cancel] | N/A[defect-cancel] | N/A[defect-cancel] | N/A[pro-text-only] | N/A[defect-cancel] | TTL ≤ 14400s | N/A[defect-cancel] | N/A[race] |
| `pro_awaiting_final_price` | N/A[defect-price] | N/A[defect-price] | N/A[defect-price] | N/A[pro-text-only] | N/A[defect-price] | TTL ≤ 600s | N/A[defect-price] | N/A[race] |
| `onboarding_name` | → onboarding_type, "סוג המקצוע" | → onboarding_type, "סוג המקצוע" | "בין 2 ל-100 תווים" | N/A[pro-text-only] | "בין 2 ל-100 תווים" | TTL ≤ 14400s | → idle, "ההרשמה בוטלה" | N/A[race] |
| `onboarding_type` | → onboarding_areas, "ערים/אזורים" | → onboarding_areas, "ערים/אזורים" | "שלח מספר 1-7" | N/A[pro-text-only] | "שלח מספר 1-7" | TTL ≤ 14400s | → idle, "ההרשמה בוטלה" | N/A[race] |
| `onboarding_areas` | → onboarding_prices, "המחירים" | → onboarding_prices, "המחירים" | "לא זיהיתי ערים" | N/A[pro-text-only] | → onboarding_prices, "המחירים" | TTL ≤ 14400s | → idle, "ההרשמה בוטלה" | N/A[race] |
| `onboarding_prices` | → onboarding_confirm, "סיכום הפרופיל שלך" | → onboarding_confirm, "סיכום הפרופיל שלך" | → onboarding_confirm, "סיכום הפרופיל שלך" | N/A[pro-text-only] | → onboarding_confirm, "סיכום הפרופיל שלך" | TTL ≤ 14400s | → idle, "ההרשמה בוטלה" | N/A[race] |
| `onboarding_confirm` | → idle, "הפרופיל שלך נשלח לאישור" | "השב *אשר* לשליחה" | "השב *אשר* לשליחה" | N/A[pro-text-only] | "השב *אשר* לשליחה" | TTL ≤ 14400s | → idle, "ההרשמה בוטלה" | N/A[race] |
| `admin_selecting_lead` | → admin_selecting_action, "למי להעביר" | "מספר לא חוקי" | "מספר לא חוקי" | N/A[admin-menu] | "מספר לא חוקי" | TTL ≤ 900s | → idle, "בוטל" | N/A[race] |
| `admin_selecting_action` | → admin_selecting_pro, "אנשי מקצוע פנויים" | "אפשרות לא חוקית" | "אפשרות לא חוקית" | N/A[admin-menu] | "אפשרות לא חוקית" | TTL ≤ 900s | → idle, "בוטל" | N/A[race] |
| `admin_selecting_pro` | → idle, "הליד הועבר" | "מספר לא חוקי" | "מספר לא חוקי" | N/A[admin-menu] | "מספר לא חוקי" | TTL ≤ 900s | → idle, "בוטל" | N/A[race] |

N/A legend
~~~~~~~~~~
* ``admin-menu`` — The admin wizard is a text-only numeric menu.
* ``defect-cancel`` — DEFECT: same bypass as defect-finish. test_pro_cancellation_releases_the_right_slot has to call pro_flow directly to reach this state at all.
* ``defect-finish`` — DEFECT: PRO_SELECTING_JOB_TO_FINISH is unreachable through the orchestrator — the PRO_BUSINESS_KEYWORDS bypass overwrites the state to PRO_MODE before pro_flow reads it. See test_pro_can_select_which_job_to_finish (xfail) in test_e2e_flows.py.
* ``defect-price`` — DEFECT: PRO_AWAITING_FINAL_PRICE is absent from workflow_service's dispatch, so the reply falls through to the customer dispatcher. See test_pro_final_price_is_recorded (xfail) in test_e2e_flows.py.
* ``pro-text-only`` — Pro-side commands are a text menu; a pro never sends the bot media.
* ``race`` — State-independent. The per-chat Redis lock is taken before any state is read (workflow_service.py:219), so every state behaves identically; proven once in test_a_second_message_mid_flight_is_deferred_not_dropped.
* ``resting`` — IDLE is the resting state — there is nothing to expire.

Reading a cell: ``→ state`` is the state the FSM must land in, ``"…"`` is a
fragment of the Hebrew the participant must receive, and ``silent`` means nothing
may be sent at all.
"""

from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass, field

import pytest

from app.core.constants import UserStates, WorkerConstants
from tests.e2e import reserved_numbers as R
from tests.e2e.ai_replay import reply

SAME = "<same>"
INPUT_CLASSES = (
    "keyword",
    "free",
    "offtopic",
    "wrong",
    "emoji",
    "silence",
    "interrupt",
    "race",
)


@dataclass
class Cell:
    """One (state, input class) pair. Exactly one of ``na`` or a driveable case."""

    na: str | None = None
    send: str | None = None
    media: str | None = None
    ai: list = field(default_factory=list)
    expect_state: str = SAME
    expect: tuple[str, ...] = ()
    silent: bool = False
    # `silence` cells assert the state is TTL-bounded rather than sending anything.
    max_ttl: int | None = None

    def describe(self) -> str:
        if self.na:
            return f"N/A[{self.na}]"
        parts = []
        if self.max_ttl is not None:
            parts.append(f"TTL ≤ {self.max_ttl}s")
        if self.expect_state != SAME:
            parts.append(f"→ {getattr(self.expect_state, 'value', self.expect_state)}")
        if self.expect:
            parts.append('"' + self.expect[0] + '"')
        if self.silent:
            parts.append("silent")
        return ", ".join(parts) or "→ unchanged"


# N/A reasons live here as short keys so the generated table stays readable; the
# full text is rendered as a legend underneath it. `test_every_na_key_has_a_reason`
# stops a cell being marked N/A with an undocumented excuse.
NA_REASONS = {
    "race": (
        "State-independent. The per-chat Redis lock is taken before any state is "
        "read (workflow_service.py:219), so every state behaves identically; proven "
        "once in test_a_second_message_mid_flight_is_deferred_not_dropped."
    ),
    "resting": "IDLE is the resting state — there is nothing to expire.",
    "pro-text-only": (
        "Pro-side commands are a text menu; a pro never sends the bot media."
    ),
    "admin-menu": "The admin wizard is a text-only numeric menu.",
    "defect-finish": (
        "DEFECT: PRO_SELECTING_JOB_TO_FINISH is unreachable through the "
        "orchestrator — the PRO_BUSINESS_KEYWORDS bypass overwrites the state to "
        "PRO_MODE before pro_flow reads it. See test_pro_can_select_which_job_to_"
        "finish (xfail) in test_e2e_flows.py."
    ),
    "defect-cancel": (
        "DEFECT: same bypass as defect-finish. test_pro_cancellation_releases_the_"
        "right_slot has to call pro_flow directly to reach this state at all."
    ),
    "defect-price": (
        "DEFECT: PRO_AWAITING_FINAL_PRICE is absent from workflow_service's "
        "dispatch, so the reply falls through to the customer dispatcher. See "
        "test_pro_final_price_is_recorded (xfail) in test_e2e_flows.py."
    ),
}

RACE_NA = "race"
PRO_MEDIA_NA = "pro-text-only"
ADMIN_MENU_NA = "admin-menu"


# ===========================================================================
# Arrangements — put the world into each state, the way production gets there
# ===========================================================================


async def arrange_idle(world):
    await world.standard_cast()
    return world.customer


async def arrange_awaiting_consent(world):
    await world.add_pro(R.PRO_PRIMARY, name="נתנאל אינסטלציה")
    await world.send("היי")  # first contact → consent request
    return world.customer


async def arrange_customer_mode(world):
    await world.standard_cast()
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    await world.set_state(UserStates.CUSTOMER_MODE, chat_id=pro_chat)
    return pro_chat


async def arrange_awaiting_address(world):
    await world.standard_cast()
    await world.booked_job(
        world.pros[R.PRO_PRIMARY], status="contacted", booked_slot_id=None
    )
    await world.set_state(UserStates.AWAITING_ADDRESS)
    return world.customer


async def arrange_awaiting_pro_approval(world):
    await world.standard_cast()
    # reassign_offered: the SLA has already asked "1 = find someone else, 2 = wait",
    # so there is a real keyword to answer. Without it every input is soft-held.
    await world.awaiting_approval_job(world.pros[R.PRO_PRIMARY], reassign_offered=True)
    return world.customer


async def arrange_paused_for_human(world):
    await world.standard_cast()
    await world.booked_job(world.pros[R.PRO_PRIMARY], is_paused=True)
    await world.set_state(UserStates.PAUSED_FOR_HUMAN, ttl=900)
    return world.customer


async def arrange_awaiting_reschedule_time(world):
    await world.standard_cast()
    await world.booked_job(world.pros[R.PRO_PRIMARY])
    slot = await world.db.slots.find_one(
        {"pro_id": world.pros[R.PRO_PRIMARY]["_id"], "is_taken": False}
    )
    await world.set_state(UserStates.AWAITING_RESCHEDULE_TIME)
    await world.set_metadata({"reschedule_slots_context": {"1": str(slot["_id"])}})
    return world.customer


async def arrange_awaiting_loyalty_confirmation(world):
    # PRO-119: production sets this state with a bounded TTL (the unclear-reply
    # trap this fixed), so the arrangement must match — not the 4h default —
    # or the `silence` cell would pin the pre-fix behavior right back.
    #
    # Post-review: `_accept_loyalty_offer` judges dispatchability only on the
    # five persisted address parts, never on `full_address` (an intake lead's
    # `full_address` is a bare city, and treating that as "complete" would
    # dispatch a pro to a city with no street — the regression the review
    # caught). `booked_job` only seeds `full_address`/`city` by default, so
    # the parts are seeded explicitly here to keep the `keyword` cell
    # exercising a real dispatch (AWAITING_PRO_APPROVAL) rather than the
    # NEED_DETAILS/AWAITING_ADDRESS branch.
    #
    # status="contacted", not "new": the dispatch write now guards on
    # `expected_status=LeadStatus.CONTACTED` specifically (a lead that already
    # reached NEW is assigned/notified elsewhere, and yanking it here would
    # skip the old-pro notice `reassign_lead` sends) — seeding "new" would
    # make this cell silently take the lost-race path instead of dispatching.
    await world.standard_cast()
    await world.booked_job(
        world.pros[R.PRO_PRIMARY],
        status="contacted",
        booked_slot_id=None,
        street="דיזנגוף",
        street_number="50",
        floor="3",
        apartment="12",
    )
    await world.set_state(
        UserStates.AWAITING_LOYALTY_CONFIRMATION,
        ttl=WorkerConstants.LOYALTY_CONFIRM_TTL_SECONDS,
    )
    await world.set_metadata({"past_pro_id": str(world.pros[R.PRO_PRIMARY]["_id"])})
    return world.customer


async def arrange_awaiting_new_or_existing(world):
    # PRO-116: customer has a confirmed BOOKED job and was asked whether a new
    # message is a new request or about the existing job.
    await world.standard_cast()
    booked = await world.booked_job(
        world.pros[R.PRO_PRIMARY], status="booked", booked_slot_id=None
    )
    await world.set_state(UserStates.AWAITING_NEW_OR_EXISTING)
    await world.set_metadata({"booked_lead_id": str(booked["_id"])})
    return world.customer


async def arrange_awaiting_cancel_confirmation(world):
    # PRO-118: a cancel keyword on a BOOKED job no longer cancels on the first
    # hit — it parks here awaiting an explicit '1', with the lead id stashed
    # in state metadata the way the orchestrator leaves it.
    await world.standard_cast()
    booked = await world.booked_job(world.pros[R.PRO_PRIMARY])
    await world.set_state(
        UserStates.AWAITING_CANCEL_CONFIRMATION,
        ttl=WorkerConstants.CANCEL_CONFIRM_TTL_SECONDS,
    )
    await world.set_metadata({"cancel_confirm_lead_id": str(booked["_id"])})
    return world.customer


async def arrange_pro_mode(world):
    await world.standard_cast()
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    await world.set_state(UserStates.PRO_MODE, chat_id=pro_chat)
    return pro_chat


async def arrange_awaiting_intent_confirmation(world):
    await world.standard_cast()
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    await world.set_state(
        UserStates.AWAITING_INTENT_CONFIRMATION, chat_id=pro_chat, ttl=300
    )
    return pro_chat


async def arrange_pro_selecting_finish(world):
    await world.standard_cast()
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    first = await world.booked_job(world.pros[R.PRO_PRIMARY])
    await world.booked_job(world.pros[R.PRO_PRIMARY], chat_id=R.chat(R.CUSTOMER_B))
    await world.set_state(UserStates.PRO_SELECTING_JOB_TO_FINISH, chat_id=pro_chat)
    await world.set_metadata(
        {"finishing_jobs_context": {"1": str(first["_id"])}}, chat_id=pro_chat
    )
    return pro_chat


async def arrange_pro_selecting_cancel(world):
    await world.standard_cast()
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    first = await world.booked_job(world.pros[R.PRO_PRIMARY])
    await world.booked_job(world.pros[R.PRO_PRIMARY], chat_id=R.chat(R.CUSTOMER_B))
    await world.set_state(UserStates.PRO_SELECTING_JOB_TO_CANCEL, chat_id=pro_chat)
    await world.set_metadata(
        {"cancelling_jobs_context": {"1": str(first["_id"])}}, chat_id=pro_chat
    )
    return pro_chat


async def arrange_pro_awaiting_final_price(world):
    await world.standard_cast()
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    lead = await world.booked_job(world.pros[R.PRO_PRIMARY], status="completed")
    await world.set_state(
        UserStates.PRO_AWAITING_FINAL_PRICE, chat_id=pro_chat, ttl=600
    )
    await world.set_metadata(
        {"final_price_lead_id": str(lead["_id"])}, chat_id=pro_chat
    )
    return pro_chat


def _arrange_onboarding(state: str, meta: dict):
    async def arrange(world):
        applicant = R.chat(R.PRO_THIRD)
        await world.grant_consent(applicant)
        await world.set_state(state, chat_id=applicant)
        await world.set_metadata({"onboarding": meta}, chat_id=applicant)
        return applicant

    return arrange


def _arrange_admin(state: str, meta_key: str | None = None):
    async def arrange(world):
        await world.standard_cast()
        await world.add_admin_pro()
        lead = await world.booked_job(
            world.pros[R.PRO_FAR], status="pending_admin_review"
        )
        meta = {"admin_leads_context": {"1": str(lead["_id"])}}
        if meta_key == "action":
            meta["selected_lead_id"] = str(lead["_id"])
        if meta_key == "pro":
            meta["selected_lead_id"] = str(lead["_id"])
            meta["admin_pros_context"] = {"1": str(world.pros[R.PRO_PRIMARY]["_id"])}
        await world.set_state(state, chat_id=world.admin, ttl=900)
        await world.set_metadata(meta, chat_id=world.admin)
        return world.admin

    return arrange


# ===========================================================================
# The matrix
# ===========================================================================

MATRIX: dict[str, dict] = {
    # ---------------------------------------------------------------- customer
    UserStates.IDLE: {
        "keyword": Cell(send="עזרה", expect=("אני המערכת החכמה של Proli",)),
        "free": Cell(
            send="יש לי נזילה בתל אביב",
            ai=[reply("מאתר איש מקצוע.", city="תל אביב", issue="נזילה")],
            expect=("מאתר איש מקצוע",),
        ),
        "offtopic": Cell(
            send="מה מזג האוויר מחר?",
            ai=[reply("אני עוזר למצוא בעלי מקצוע. במה אפשר לעזור?")],
            expect=("בעלי מקצוע",),
        ),
        "wrong": Cell(
            send="",
            media="image",
            ai=[reply("קיבלתי את התמונה, מה קרה?")],
            expect=("קיבלתי את התמונה",),
        ),
        "emoji": Cell(
            send="👍",
            ai=[reply("במה אפשר לעזור?")],
            expect=("במה אפשר לעזור",),
        ),
        "silence": Cell(na="resting"),
        "interrupt": Cell(send="התחלה", silent=True),
        "race": Cell(na=RACE_NA),
    },
    UserStates.AWAITING_CONSENT: {
        "keyword": Cell(
            send="כן", expect_state=UserStates.IDLE, expect=("אפשר להתחיל",)
        ),
        "free": Cell(
            send="כן בטח אין בעיה",
            expect=("ברוכים הבאים ל-Proli",),
        ),
        "offtopic": Cell(send="מה?", expect=("ברוכים הבאים ל-Proli",)),
        "wrong": Cell(send="", media="image", expect=("ברוכים הבאים ל-Proli",)),
        "emoji": Cell(send="👍", expect=("ברוכים הבאים ל-Proli",)),
        "silence": Cell(max_ttl=14400),
        "interrupt": Cell(
            send="לא", expect_state=UserStates.IDLE, expect=("לא נשמור מידע עליך",)
        ),
        "race": Cell(na=RACE_NA),
    },
    UserStates.CUSTOMER_MODE: {
        "keyword": Cell(
            send="יש לי נזילה בתל אביב",
            ai=[
                reply("מאתר איש מקצוע.", city="תל אביב", issue="נזילה"),
                reply("מה הכתובת המדויקת?"),
            ],
            expect=("מה הכתובת",),
        ),
        "free": Cell(
            send="הדוד דולף לי כבר יומיים",
            ai=[reply("מאיפה בדיוק הדליפה?")],
            expect=("מאיפה בדיוק",),
        ),
        "offtopic": Cell(
            send="מה שלומך?",
            ai=[reply("מצוין! במה אפשר לעזור?")],
            expect=("במה אפשר לעזור",),
        ),
        "wrong": Cell(
            send="",
            media="image",
            ai=[reply("ראיתי את התמונה.")],
            expect=("ראיתי את התמונה",),
        ),
        "emoji": Cell(send="👍", ai=[reply("במה אפשר לעזור?")], expect=("לעזור",)),
        "silence": Cell(max_ttl=14400),
        # A pro-only keyword always wins over sticky CUSTOMER_MODE (PRO-69).
        "interrupt": Cell(send="סיימתי", expect_state=UserStates.PRO_MODE),
        "race": Cell(na=RACE_NA),
    },
    UserStates.AWAITING_ADDRESS: {
        "keyword": Cell(
            send="דיזנגוף 50, קומה 3 דירה 12",
            ai=[
                reply(
                    "תודה!",
                    city="תל אביב",
                    street="דיזנגוף",
                    street_number="50",
                    floor="3",
                    apartment="12",
                )
            ],
            expect_state=UserStates.IDLE,
            expect=("הכתובת עודכנה בהצלחה",),
        ),
        "free": Cell(
            send="אני גר בדיזנגוף חמישים, קומה שלוש",
            ai=[
                reply(
                    "כמעט!",
                    city="תל אביב",
                    street="דיזנגוף",
                    street_number="50",
                    floor="3",
                )
            ],
            expect=("מספר דירה",),
        ),
        "offtopic": Cell(
            send="כמה זה יעלה לי בערך?",
            ai=[reply("נחזור לזה — קודם הכתובת.")],
            expect=("עוד פרטים לכתובת",),
        ),
        "wrong": Cell(
            send="",
            media="image",
            expect=("לא הצלחתי לזהות את הכתובת",),
        ),
        "emoji": Cell(send="👍", expect=("לא הצלחתי לזהות את הכתובת",)),
        "silence": Cell(max_ttl=14400),
        "interrupt": Cell(
            send="בטל",
            expect_state=UserStates.IDLE,
            expect=("הבקשה בוטלה",),
        ),
        "race": Cell(na=RACE_NA),
    },
    UserStates.AWAITING_PRO_APPROVAL: {
        # "1" accepts the SLA's reassignment offer, which re-routes to the next
        # pro via reassign_lead. PRO-117: a lead already in the approval funnel
        # (this one is NEW, not CONTACTED) re-arms the SLA — set_state back to
        # AWAITING_PRO_APPROVAL with PRO_APPROVAL_TTL_SECONDS — rather than
        # clearing state, so the nudge/reassign-offer stays live for the new pro.
        "keyword": Cell(
            send="1",
            expect_state=UserStates.AWAITING_PRO_APPROVAL,
            expect=("מאתרים עבורך איש מקצוע זמין",),
        ),
        "free": Cell(send="מתי הוא מגיע?", expect=("אצל איש המקצוע לאישור",)),
        "offtopic": Cell(send="מה מזג האוויר?", expect=("אצל איש המקצוע לאישור",)),
        "wrong": Cell(send="", media="image", expect=("אצל איש המקצוע לאישור",)),
        "emoji": Cell(send="👍", expect=("אצל איש המקצוע לאישור",)),
        "silence": Cell(max_ttl=3600),
        "interrupt": Cell(
            send="נציג",
            expect_state=UserStates.PAUSED_FOR_HUMAN,
            expect=("מעביר אותך לנציג אנושי",),
        ),
        "race": Cell(na=RACE_NA),
    },
    UserStates.PAUSED_FOR_HUMAN: {
        "keyword": Cell(send="הלו?", silent=True),
        "free": Cell(send="מישהו יכול לעזור לי בבקשה", silent=True),
        "offtopic": Cell(send="מה מזג האוויר?", silent=True),
        "wrong": Cell(send="", media="image", silent=True),
        "emoji": Cell(send="👍", silent=True),
        "silence": Cell(max_ttl=900),
        # SOS is checked above the pause gate, so asking for a human again
        # re-acknowledges and re-arms the window rather than being swallowed.
        "interrupt": Cell(send="נציג", expect=("מעביר אותך לנציג אנושי",)),
        "race": Cell(na=RACE_NA),
    },
    UserStates.AWAITING_RESCHEDULE_TIME: {
        "keyword": Cell(
            send="1", expect_state=UserStates.IDLE, expect=("המועד שונה בהצלחה",)
        ),
        "free": Cell(send="הראשון מתאים לי", expect=("בחר מספר תור חוקי",)),
        "offtopic": Cell(send="מה קורה?", expect=("בחר מספר תור חוקי",)),
        "wrong": Cell(send="", media="image", expect=("בחר מספר תור חוקי",)),
        "emoji": Cell(send="👍", expect=("בחר מספר תור חוקי",)),
        "silence": Cell(max_ttl=14400),
        "interrupt": Cell(
            send="בטל", expect_state=UserStates.IDLE, expect=("המועד נשאר כפי שהיה",)
        ),
        "race": Cell(na=RACE_NA),
    },
    UserStates.AWAITING_LOYALTY_CONFIRMATION: {
        # PRO-119: whole-token natural-language yes/no (not just literal "1"/
        # "2"), a bounded TTL, and a real dispatch on accept. Dispatchability
        # is judged only on the five persisted address parts — never on
        # `full_address` (an intake lead's is a bare city, and the fallback
        # that once treated any `full_address` as "complete" was removed as a
        # regression risk) — so the arranger seeds the parts explicitly to
        # keep this cell exercising a real dispatch (notifies the pro, parks
        # the customer in AWAITING_PRO_APPROVAL) instead of the old
        # unconditional "בודק מולו ומעדכן" that contacted nobody.
        "keyword": Cell(
            send="1",
            expect_state=UserStates.AWAITING_PRO_APPROVAL,
            expect=("עם הפרטים, ואעדכן אותך ברגע שיאשר",),
        ),
        # Natural-language decline (not the literal "2") — PRO-119's whole-token
        # NEGATIVE_KEYWORDS match, exercising the other half of the new parser.
        "free": Cell(
            send="לא תודה, תמצא לי מישהו אחר",
            expect_state=UserStates.IDLE,
            expect=("אחפש עבורך את איש המקצוע הפנוי",),
        ),
        "offtopic": Cell(send="מה השעה?", expect=("לא בטוח שהבנתי",)),
        "wrong": Cell(send="", media="image", expect=("לא בטוח שהבנתי",)),
        "emoji": Cell(send="👍", expect=("לא בטוח שהבנתי",)),
        "silence": Cell(max_ttl=WorkerConstants.LOYALTY_CONFIRM_TTL_SECONDS),
        "interrupt": Cell(
            send="נציג",
            expect_state=UserStates.PAUSED_FOR_HUMAN,
            expect=("מעביר אותך לנציג אנושי",),
        ),
        "race": Cell(na=RACE_NA),
    },
    UserStates.AWAITING_NEW_OR_EXISTING: {
        "keyword": Cell(
            send="1", expect_state=UserStates.IDLE, expect=("הבעיה החדשה",)
        ),
        "free": Cell(send="בעצם לא משנה", expect=("אנא השב 1 (בעיה חדשה) או 2",)),
        "offtopic": Cell(send="מה השעה?", expect=("אנא השב 1 (בעיה חדשה) או 2",)),
        "wrong": Cell(send="", media="image", expect=("אנא השב 1 (בעיה חדשה) או 2",)),
        "emoji": Cell(send="👍", expect=("אנא השב 1 (בעיה חדשה) או 2",)),
        "silence": Cell(max_ttl=14400),
        "interrupt": Cell(
            send="נציג",
            expect_state=UserStates.PAUSED_FOR_HUMAN,
            expect=("מעביר אותך לנציג אנושי",),
        ),
        "race": Cell(na=RACE_NA),
    },
    UserStates.AWAITING_CANCEL_CONFIRMATION: {
        # The dispatcher clears the transient state unconditionally before
        # reading the reply, so every non-race, non-silence input class lands
        # back in IDLE — either via a real cancel ('1') or an abort (anything
        # else).
        "keyword": Cell(
            send="1",
            expect_state=UserStates.IDLE,
            expect=("ביטלתי את העבודה",),
        ),
        "free": Cell(
            send="אולי בעצם עדיף שלא",
            expect_state=UserStates.IDLE,
            expect=("העבודה נשארת כמתוכנן",),
        ),
        "offtopic": Cell(
            send="מה השעה?",
            expect_state=UserStates.IDLE,
            expect=("העבודה נשארת כמתוכנן",),
        ),
        "wrong": Cell(
            send="",
            media="image",
            expect_state=UserStates.IDLE,
            expect=("העבודה נשארת כמתוכנן",),
        ),
        "emoji": Cell(
            send="👍",
            expect_state=UserStates.IDLE,
            expect=("העבודה נשארת כמתוכנן",),
        ),
        "silence": Cell(max_ttl=WorkerConstants.CANCEL_CONFIRM_TTL_SECONDS),
        # SOS is checked above this state's dispatch, same as the other
        # customer-confirmation states.
        "interrupt": Cell(
            send="נציג",
            expect_state=UserStates.PAUSED_FOR_HUMAN,
            expect=("מעביר אותך לנציג אנושי",),
        ),
        "race": Cell(na=RACE_NA),
    },
    # --------------------------------------------------------------------- pro
    UserStates.PRO_MODE: {
        "keyword": Cell(send="תפריט", expect=("פקודות המערכת",)),
        "free": Cell(
            send="מה יש לי היום ביומן",
            ai=[],
            expect=("פקודות המערכת",),
        ),
        "offtopic": Cell(send="מה שלומך", expect=("פקודות המערכת",)),
        "wrong": Cell(na=PRO_MEDIA_NA),
        "emoji": Cell(send="👍", expect=("פקודות המערכת",)),
        "silence": Cell(max_ttl=14400),
        "interrupt": Cell(
            send="התחלה",
            expect=("פקודות המערכת",),
        ),
        "race": Cell(na=RACE_NA),
    },
    UserStates.AWAITING_INTENT_CONFIRMATION: {
        "keyword": Cell(
            send="1",
            expect_state=UserStates.CUSTOMER_MODE,
            expect=("עברת למצב לקוח",),
        ),
        "free": Cell(send="בוא נגיד שכן", expect=("בוא ננסה שוב",)),
        "offtopic": Cell(send="מה קורה", expect=("בוא ננסה שוב",)),
        "wrong": Cell(na=PRO_MEDIA_NA),
        "emoji": Cell(send="👍", expect=("בוא ננסה שוב",)),
        "silence": Cell(max_ttl=300),
        "interrupt": Cell(
            send="2", expect_state=UserStates.IDLE, expect=("ממשיכים כרגיל",)
        ),
        "race": Cell(na=RACE_NA),
    },
    UserStates.PRO_SELECTING_JOB_TO_FINISH: {
        "keyword": Cell(na="defect-finish"),
        "free": Cell(na="defect-finish"),
        "offtopic": Cell(na="defect-finish"),
        "wrong": Cell(na=PRO_MEDIA_NA),
        "emoji": Cell(na="defect-finish"),
        "silence": Cell(max_ttl=14400),
        "interrupt": Cell(na="defect-finish"),
        "race": Cell(na=RACE_NA),
    },
    UserStates.PRO_SELECTING_JOB_TO_CANCEL: {
        "keyword": Cell(na="defect-cancel"),
        "free": Cell(na="defect-cancel"),
        "offtopic": Cell(na="defect-cancel"),
        "wrong": Cell(na=PRO_MEDIA_NA),
        "emoji": Cell(na="defect-cancel"),
        "silence": Cell(max_ttl=14400),
        "interrupt": Cell(na="defect-cancel"),
        "race": Cell(na=RACE_NA),
    },
    UserStates.PRO_AWAITING_FINAL_PRICE: {
        "keyword": Cell(na="defect-price"),
        "free": Cell(na="defect-price"),
        "offtopic": Cell(na="defect-price"),
        "wrong": Cell(na=PRO_MEDIA_NA),
        "emoji": Cell(na="defect-price"),
        "silence": Cell(max_ttl=600),
        "interrupt": Cell(na="defect-price"),
        "race": Cell(na=RACE_NA),
    },
    UserStates.ONBOARDING_NAME: {
        "keyword": Cell(
            send="שרברבות הצפון",
            expect_state=UserStates.ONBOARDING_TYPE,
            expect=("סוג המקצוע",),
        ),
        "free": Cell(
            send="קוראים לעסק שלי שרברבות הצפון",
            expect_state=UserStates.ONBOARDING_TYPE,
            expect=("סוג המקצוע",),
        ),
        "offtopic": Cell(send="א", expect=("בין 2 ל-100 תווים",)),
        "wrong": Cell(na=PRO_MEDIA_NA),
        # A single emoji is one codepoint, so it fails the 2-char minimum.
        "emoji": Cell(send="👍", expect=("בין 2 ל-100 תווים",)),
        "silence": Cell(max_ttl=14400),
        "interrupt": Cell(
            send="ביטול", expect_state=UserStates.IDLE, expect=("ההרשמה בוטלה",)
        ),
        "race": Cell(na=RACE_NA),
    },
    UserStates.ONBOARDING_TYPE: {
        "keyword": Cell(
            send="1",
            expect_state=UserStates.ONBOARDING_AREAS,
            expect=("ערים/אזורים",),
        ),
        "free": Cell(
            send="אינסטלטור",
            expect_state=UserStates.ONBOARDING_AREAS,
            expect=("ערים/אזורים",),
        ),
        "offtopic": Cell(send="לא יודע", expect=("שלח מספר 1-7",)),
        "wrong": Cell(na=PRO_MEDIA_NA),
        "emoji": Cell(send="👍", expect=("שלח מספר 1-7",)),
        "silence": Cell(max_ttl=14400),
        "interrupt": Cell(
            send="ביטול", expect_state=UserStates.IDLE, expect=("ההרשמה בוטלה",)
        ),
        "race": Cell(na=RACE_NA),
    },
    UserStates.ONBOARDING_AREAS: {
        "keyword": Cell(
            send="תל אביב, רמת גן",
            expect_state=UserStates.ONBOARDING_PRICES,
            expect=("המחירים",),
        ),
        "free": Cell(
            send="תל אביב",
            expect_state=UserStates.ONBOARDING_PRICES,
            expect=("המחירים",),
        ),
        "offtopic": Cell(send=",", expect=("לא זיהיתי ערים",)),
        "wrong": Cell(na=PRO_MEDIA_NA),
        "emoji": Cell(
            send="👍",
            expect_state=UserStates.ONBOARDING_PRICES,
            expect=("המחירים",),
        ),
        "silence": Cell(max_ttl=14400),
        "interrupt": Cell(
            send="ביטול", expect_state=UserStates.IDLE, expect=("ההרשמה בוטלה",)
        ),
        "race": Cell(na=RACE_NA),
    },
    UserStates.ONBOARDING_PRICES: {
        "keyword": Cell(
            send="דלג",
            expect_state=UserStates.ONBOARDING_CONFIRM,
            expect=("סיכום הפרופיל שלך",),
        ),
        "free": Cell(
            send="תיקון נזילה 250, החלפת ברז 350",
            expect_state=UserStates.ONBOARDING_CONFIRM,
            expect=("סיכום הפרופיל שלך",),
        ),
        "offtopic": Cell(
            send="לא יודע עדיין",
            expect_state=UserStates.ONBOARDING_CONFIRM,
            expect=("סיכום הפרופיל שלך",),
        ),
        "wrong": Cell(na=PRO_MEDIA_NA),
        "emoji": Cell(
            send="👍",
            expect_state=UserStates.ONBOARDING_CONFIRM,
            expect=("סיכום הפרופיל שלך",),
        ),
        "silence": Cell(max_ttl=14400),
        "interrupt": Cell(
            send="ביטול", expect_state=UserStates.IDLE, expect=("ההרשמה בוטלה",)
        ),
        "race": Cell(na=RACE_NA),
    },
    UserStates.ONBOARDING_CONFIRM: {
        "keyword": Cell(
            send="אשר",
            expect_state=UserStates.IDLE,
            expect=("הפרופיל שלך נשלח לאישור",),
        ),
        "free": Cell(send="נראה לי בסדר", expect=("השב *אשר* לשליחה",)),
        "offtopic": Cell(send="מה זה?", expect=("השב *אשר* לשליחה",)),
        "wrong": Cell(na=PRO_MEDIA_NA),
        "emoji": Cell(send="👍", expect=("השב *אשר* לשליחה",)),
        "silence": Cell(max_ttl=14400),
        "interrupt": Cell(
            send="ביטול", expect_state=UserStates.IDLE, expect=("ההרשמה בוטלה",)
        ),
        "race": Cell(na=RACE_NA),
    },
    # ------------------------------------------------------------------- admin
    UserStates.ADMIN_SELECTING_LEAD: {
        "keyword": Cell(
            send="1",
            expect_state=UserStates.ADMIN_SELECTING_ACTION,
            expect=("למי להעביר",),
        ),
        "free": Cell(send="קח את הראשון", expect=("מספר לא חוקי",)),
        "offtopic": Cell(send="מה קורה", expect=("מספר לא חוקי",)),
        "wrong": Cell(na=ADMIN_MENU_NA),
        "emoji": Cell(send="👍", expect=("מספר לא חוקי",)),
        "silence": Cell(max_ttl=900),
        "interrupt": Cell(send="בטל", expect_state=UserStates.IDLE, expect=("בוטל",)),
        "race": Cell(na=RACE_NA),
    },
    UserStates.ADMIN_SELECTING_ACTION: {
        "keyword": Cell(
            send="2",
            expect_state=UserStates.ADMIN_SELECTING_PRO,
            expect=("אנשי מקצוע פנויים",),
        ),
        "free": Cell(send="תן לי רשימה", expect=("אפשרות לא חוקית",)),
        "offtopic": Cell(send="מה קורה", expect=("אפשרות לא חוקית",)),
        "wrong": Cell(na=ADMIN_MENU_NA),
        "emoji": Cell(send="👍", expect=("אפשרות לא חוקית",)),
        "silence": Cell(max_ttl=900),
        "interrupt": Cell(send="בטל", expect_state=UserStates.IDLE, expect=("בוטל",)),
        "race": Cell(na=RACE_NA),
    },
    UserStates.ADMIN_SELECTING_PRO: {
        "keyword": Cell(send="1", expect_state=UserStates.IDLE, expect=("הליד הועבר",)),
        "free": Cell(send="תן לראשון", expect=("מספר לא חוקי",)),
        "offtopic": Cell(send="מה קורה", expect=("מספר לא חוקי",)),
        "wrong": Cell(na=ADMIN_MENU_NA),
        "emoji": Cell(send="👍", expect=("מספר לא חוקי",)),
        "silence": Cell(max_ttl=900),
        "interrupt": Cell(send="בטל", expect_state=UserStates.IDLE, expect=("בוטל",)),
        "race": Cell(na=RACE_NA),
    },
    # ------------------------------------------------- declared but unreachable
}

ARRANGERS = {
    UserStates.IDLE: arrange_idle,
    UserStates.AWAITING_CONSENT: arrange_awaiting_consent,
    UserStates.CUSTOMER_MODE: arrange_customer_mode,
    UserStates.AWAITING_ADDRESS: arrange_awaiting_address,
    UserStates.AWAITING_PRO_APPROVAL: arrange_awaiting_pro_approval,
    UserStates.PAUSED_FOR_HUMAN: arrange_paused_for_human,
    UserStates.AWAITING_RESCHEDULE_TIME: arrange_awaiting_reschedule_time,
    UserStates.AWAITING_LOYALTY_CONFIRMATION: arrange_awaiting_loyalty_confirmation,
    UserStates.AWAITING_NEW_OR_EXISTING: arrange_awaiting_new_or_existing,
    UserStates.AWAITING_CANCEL_CONFIRMATION: arrange_awaiting_cancel_confirmation,
    UserStates.PRO_MODE: arrange_pro_mode,
    UserStates.AWAITING_INTENT_CONFIRMATION: arrange_awaiting_intent_confirmation,
    UserStates.PRO_SELECTING_JOB_TO_FINISH: arrange_pro_selecting_finish,
    UserStates.PRO_SELECTING_JOB_TO_CANCEL: arrange_pro_selecting_cancel,
    UserStates.PRO_AWAITING_FINAL_PRICE: arrange_pro_awaiting_final_price,
    UserStates.ONBOARDING_NAME: _arrange_onboarding(UserStates.ONBOARDING_NAME, {}),
    UserStates.ONBOARDING_TYPE: _arrange_onboarding(
        UserStates.ONBOARDING_TYPE, {"name": "שרברבות הצפון"}
    ),
    UserStates.ONBOARDING_AREAS: _arrange_onboarding(
        UserStates.ONBOARDING_AREAS, {"name": "שרברבות הצפון", "type": "plumber"}
    ),
    UserStates.ONBOARDING_PRICES: _arrange_onboarding(
        UserStates.ONBOARDING_PRICES,
        {"name": "שרברבות הצפון", "type": "plumber", "areas": ["תל אביב"]},
    ),
    UserStates.ONBOARDING_CONFIRM: _arrange_onboarding(
        UserStates.ONBOARDING_CONFIRM,
        {
            "name": "שרברבות הצפון",
            "type": "plumber",
            "areas": ["תל אביב"],
            "prices": "",
        },
    ),
    UserStates.ADMIN_SELECTING_LEAD: _arrange_admin(UserStates.ADMIN_SELECTING_LEAD),
    UserStates.ADMIN_SELECTING_ACTION: _arrange_admin(
        UserStates.ADMIN_SELECTING_ACTION, "action"
    ),
    UserStates.ADMIN_SELECTING_PRO: _arrange_admin(
        UserStates.ADMIN_SELECTING_PRO, "pro"
    ),
}


# ===========================================================================
# The driver
# ===========================================================================


def _cases():
    for state, row in MATRIX.items():
        for input_class in INPUT_CLASSES:
            cell = row[input_class]
            marks = [pytest.mark.skip(reason=cell.na)] if cell.na else []
            yield pytest.param(
                state, input_class, id=f"{state.value}-{input_class}", marks=marks
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("state,input_class", list(_cases()))
async def test_state_input_matrix(world, state, input_class):
    cell = MATRIX[state][input_class]
    chat_id = await ARRANGERS[state](world)

    if cell.max_ttl is not None:
        ttl = await world.state_ttl(chat_id)
        assert 0 < ttl <= cell.max_ttl, (
            f"{state} must be TTL-bounded so a silent user is never stuck forever; "
            f"got ttl={ttl}"
        )
        return

    world.ai.script(*cell.ai)
    world.recorder.clear()
    await world.send(cell.send, chat_id=chat_id, media=cell.media)

    # An over-scripted cell means a branch the cell expected to be taken no longer
    # is, which would otherwise pass silently.
    assert world.ai.pending_script == 0, (
        f"{state} × {input_class} scripted {len(cell.ai)} AI turns but "
        f"{world.ai.pending_script} went unused"
    )

    if cell.expect_state != SAME:
        await world.assert_state(cell.expect_state, chat_id=chat_id)
    else:
        await world.assert_state(state, chat_id=chat_id)

    if cell.silent:
        world.recorder.assert_silent(chat_id, f"{state} × {input_class} must not reply")
    for fragment in cell.expect:
        world.recorder.assert_text_to(chat_id, fragment)


# ===========================================================================
# Completeness guards — a cell may not be silently empty
# ===========================================================================


def render_matrix_table() -> str:
    header = "| state | " + " | ".join(INPUT_CLASSES) + " |"
    sep = "|" + "---|" * (len(INPUT_CLASSES) + 1)
    lines = [header, sep]
    for state, row in MATRIX.items():
        cells = [row[cls].describe().replace("|", "/") for cls in INPUT_CLASSES]
        lines.append(f"| `{state.value}` | " + " | ".join(cells) + " |")

    used = sorted({c.na for row in MATRIX.values() for c in row.values() if c.na})
    lines.append("")
    lines.append("N/A legend")
    lines.append("~~~~~~~~~~")
    for key in used:
        lines.append(f"* ``{key}`` — {NA_REASONS[key]}")
    return "\n".join(lines)


def test_every_na_key_has_a_documented_reason():
    """An N/A cell must cite a reason from ``NA_REASONS`` — never a bare marker."""
    for state, row in MATRIX.items():
        for input_class, cell in row.items():
            if cell.na:
                assert cell.na in NA_REASONS, (
                    f"{state} × {input_class} is N/A under the undocumented key "
                    f"{cell.na!r}; add it to NA_REASONS"
                )


def test_the_matrix_covers_every_state_and_every_input_class():
    """Every ``UserStates`` value gets a row, and every row gets all eight columns."""
    assert set(MATRIX) == set(UserStates), (
        "states missing from the matrix: "
        f"{sorted(s.value for s in set(UserStates) - set(MATRIX))}"
    )
    for state, row in MATRIX.items():
        assert set(row) == set(INPUT_CLASSES), f"{state} has an incomplete row"
        for input_class, cell in row.items():
            assert cell.na or cell.send is not None or cell.max_ttl is not None, (
                f"{state} × {input_class} is empty — every cell must be a real case "
                f"or an explicit N/A with a reason"
            )


def test_every_state_has_an_arranger():
    """No unreachable rows remain, so every state in the matrix needs one."""
    assert set(MATRIX) <= set(ARRANGERS)


def test_the_documented_matrix_matches_the_executable_one():
    """The table in the module docstring is generated from ``MATRIX``.

    This is what stops a cell going quietly missing: add a state or an input class
    and this fails until the regenerated table is pasted back into the docstring.
    Run ``python -m tests.e2e.test_e2e_state_matrix`` to print the current one.
    """
    assert render_matrix_table() in __doc__, (
        "the documented matrix is stale. Regenerate it with:\n"
        "  python -m tests.e2e.test_e2e_state_matrix\n"
        "and paste the output into this module's docstring."
    )


def test_no_userstate_is_declared_without_production_setting_it():
    """The inverse of the old "unreachable states stay unreachable" guard.

    Five members — ``CUSTOMER_FLOW``, ``AWAITING_MEDIA``, ``AWAITING_TIME``,
    ``SOS``, ``ADMIN_MODE_IDLE`` — sat in the enum that no dispatcher ever set.
    They cost five permanently-N/A rows in this matrix (forty dark cells) and
    made the enum a misleading description of the FSM: a reader could not tell
    which states were real. They are gone; this keeps them gone.

    ``ONBOARDING_*`` and the ``ADMIN_*`` wizard states are set indirectly (a
    step table, a prefix match), so a bare mention of the member anywhere under
    ``app/`` counts — this asks "is it wired up at all", not "is it passed to
    set_state literally".
    """
    app_root = pathlib.Path(__file__).resolve().parents[2] / "app"
    sources = "\n".join(p.read_text(encoding="utf-8") for p in app_root.rglob("*.py"))
    # The declaration itself is not a use.
    constants_src = (app_root / "core" / "constants.py").read_text(encoding="utf-8")
    sources = sources.replace(constants_src, "")

    unused = [
        state.name
        for state in UserStates
        if not re.search(rf"UserStates\.{state.name}\b", sources)
    ]
    assert not unused, (
        f"UserStates members that no code path ever sets: {unused}. Wire the "
        f"state up or delete it — a declared-but-dead state is a lie about the "
        f"FSM, and it costs a permanently-N/A row in this matrix."
    )


if __name__ == "__main__":  # pragma: no cover - developer convenience
    print(render_matrix_table())
