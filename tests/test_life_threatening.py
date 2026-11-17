"""PRO-222: danger to life goes to emergency services, never to a pro.

Two levels. Life-threatening (fire, smoke, gas, an explosion, electrocution):
no pro, no address, no lead suggesting a pro is coming, the operator paged with
no customer text — in every state a customer can write in, before consent too,
and without the AI. Urgent (flood, a burst pipe, a short, "דחוף") keeps the
PRO-121 fast path, and its customer copy no longer says "חירום".
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId

import app.services.workflow_service as wf
from app.core.constants import LeadStatus, UserStates
from app.core.messages import Messages
from app.core.text_matching import is_emergency_text, is_life_threatening_text

CUSTOMER = "972501113333@c.us"


# --- the detector -----------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "יש שריפה בבניין",
        "השריפה מתפשטת",
        "יש עשן מהלוח חשמל",
        "יש ריח גז במטבח",
        "מריח גז בבית",
        "דליפת גז",
        "התחשמלתי מהשקע",
        "הילד התחשמל",
        "מכת חשמל",
        "היה פיצוץ בבניין",
        "משהו התפוצץ במטבח",
        "סכנת חיים",
        "I smell gas",
    ],
)
def test_life_threatening_reports_are_detected(text):
    assert is_life_threatening_text(text)


@pytest.mark.parametrize(
    "text",
    [
        "אין ריח גז, רק נזילה",
        "פיצוץ צינור במטבח",
        "הצינור התפוצץ",
        "הדוד התפוצץ",
        "קניתי במחיר שריפה",
        "צריך אינסטלטור מחר",
        "נזילה דחופה מתחת לכיור",
        "יש לי ראש מקלחת דולף",
    ],
)
def test_negations_burst_pipes_and_everyday_words_are_not(text):
    assert not is_life_threatening_text(text)


def test_a_burst_pipe_keeps_the_urgent_fast_path():
    assert is_emergency_text("פיצוץ צינור במטבח")


def test_urgent_copy_no_longer_says_emergency():
    for copy in (
        Messages.Customer.EMERGENCY_ACK,
        Messages.Customer.EMERGENCY_NEED_CITY,
        Messages.Customer.EMERGENCY_WHILE_WAITING,
    ):
        assert "חירום" not in copy


def test_the_life_threatening_copy_names_the_emergency_numbers_in_bold():
    copy = Messages.Customer.LIFE_THREATENING
    for number in ("101", "102", "100"):
        assert f"*{number}*" in copy


def test_the_gas_line_says_to_shut_the_main_valve_safely_and_call_from_outside():
    """The owner's wording (2026-11-17): the gas line pins all of its steps."""
    gas_line = next(
        line
        for line in Messages.Customer.LIFE_THREATENING.splitlines()
        if line.startswith("בריח גז:")
    )
    assert gas_line == (
        "בריח גז: לא להדליק אש, אור או מכשיר חשמלי, "
        "לסגור את ברז הגז הראשי אם אפשר לעשות את זה בלי סיכון, "
        "לפתוח חלונות, לצאת החוצה ולהתקשר מחוץ לבית."
    )


# --- the guard, in every state a customer can write in ----------------------


@pytest.fixture
def world(monkeypatch, mock_db):
    wa = MagicMock()
    wa.send_message = AsyncMock()
    wa.send_location_link = AsyncMock()
    wa.indicate_typing = AsyncMock()
    monkeypatch.setattr(wf, "whatsapp", wa)

    state = MagicMock()
    state.get_state = AsyncMock(return_value=UserStates.IDLE)
    state.set_state = AsyncMock()
    state.clear_state = AsyncMock()
    state.get_metadata = AsyncMock(return_value={})
    state.set_metadata = AsyncMock()
    state.hold_message = AsyncMock()
    monkeypatch.setattr(wf, "StateManager", state)
    monkeypatch.setattr(wf, "ContextManager", MagicMock(clear_context=AsyncMock()))
    monkeypatch.setattr(wf, "has_consent", AsyncMock(return_value=True))

    ai = MagicMock()
    ai.analyze_conversation = AsyncMock(side_effect=RuntimeError("Gemini is down"))
    ai.detect_service_intent = AsyncMock(side_effect=RuntimeError("Gemini is down"))
    monkeypatch.setattr(wf, "ai", ai)

    lm = MagicMock()
    lm.log_message = AsyncMock()
    lm.get_chat_history = AsyncMock(return_value=[])
    lm.create_lead_from_dict = AsyncMock()
    monkeypatch.setattr(wf, "lead_manager", lm)

    pages = []
    monkeypatch.setattr(wf.notification_service, "page_operator", pages.append)
    return wa, state, ai, lm, pages, mock_db


def _sent(wa):
    return [c.args[1] for c in wa.send_message.call_args_list]


def _assert_sent_to_emergency_services(world, text):
    wa, state, ai, lm, pages, _ = world
    assert _sent(wa) == [Messages.Customer.LIFE_THREATENING]
    ai.analyze_conversation.assert_not_awaited()
    lm.create_lead_from_dict.assert_not_awaited()
    lm.log_message.assert_not_awaited()
    state.clear_state.assert_awaited_with(CUSTOMER)
    assert len(pages) == 1
    assert text not in pages[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fsm_state",
    [
        UserStates.IDLE,
        UserStates.CUSTOMER_MODE,
        UserStates.AWAITING_ADDRESS,
        UserStates.AWAITING_LOYALTY_CONFIRMATION,
        UserStates.AWAITING_NEW_OR_EXISTING,
        UserStates.PAUSED_FOR_HUMAN,
        UserStates.AWAITING_CANCEL_CONFIRMATION,
        UserStates.AWAITING_RESCHEDULE_TIME,
    ],
)
async def test_every_customer_state_sends_them_to_emergency_services(world, fsm_state):
    world[1].get_state.return_value = fsm_state
    text = "יש ריח גז חזק בבית"

    await wf.process_incoming_message(CUSTOMER, text)

    _assert_sent_to_emergency_services(world, text)


@pytest.mark.asyncio
async def test_before_consent_nothing_is_stored(world, monkeypatch):
    wa, state, *_ = world
    monkeypatch.setattr(wf, "has_consent", AsyncMock(return_value=False))
    state.get_state.return_value = UserStates.AWAITING_CONSENT
    text = "יש שריפה בבניין"

    await wf.process_incoming_message(CUSTOMER, text)

    _assert_sent_to_emergency_services(world, text)
    state.hold_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_while_awaiting_a_pros_approval_the_offer_is_withdrawn(world):
    wa, state, ai, lm, pages, db = world
    state.get_state.return_value = UserStates.AWAITING_PRO_APPROVAL
    lead_id = ObjectId()
    await db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": CUSTOMER,
            "status": LeadStatus.NEW,
            "pro_id": ObjectId(),
            "created_at": datetime.now(timezone.utc),
        }
    )

    await wf.process_incoming_message(CUSTOMER, "התחשמלתי מהלוח")

    _assert_sent_to_emergency_services(world, "התחשמלתי מהלוח")
    lead = await db.leads.find_one({"_id": lead_id})
    assert lead["status"] == LeadStatus.PENDING_ADMIN_REVIEW
    assert lead["escalation_reason"] == "life_threatening"


@pytest.mark.asyncio
async def test_a_booked_job_is_left_to_the_operator(world):
    *_, db = world
    lead_id = ObjectId()
    await db.leads.insert_one(
        {"_id": lead_id, "chat_id": CUSTOMER, "status": LeadStatus.BOOKED}
    )

    await wf.process_incoming_message(CUSTOMER, "יש עשן מהמזגן")

    _assert_sent_to_emergency_services(world, "יש עשן מהמזגן")
    assert (await db.leads.find_one({"_id": lead_id}))["status"] == LeadStatus.BOOKED


@pytest.mark.asyncio
async def test_the_operator_is_paged_once_however_often_they_write(world):
    pages = world[4]

    await wf.process_incoming_message(CUSTOMER, "שריפה!")
    await wf.process_incoming_message(CUSTOMER, "שריפה!!!")

    assert len(pages) == 1
    assert _sent(world[0]) == [Messages.Customer.LIFE_THREATENING] * 2


@pytest.mark.asyncio
async def test_a_pro_at_work_is_not_sent_to_emergency_services(world, monkeypatch):
    wa, state, *_ = world
    state.get_state.return_value = UserStates.PRO_MODE
    monkeypatch.setattr(wf, "_is_registered_pro", AsyncMock(return_value=True))
    handled = AsyncMock()
    monkeypatch.setattr(wf, "_handle_pro_cmd", handled, raising=False)
    await world[5].users.insert_one(
        {
            "_id": ObjectId(),
            "phone_number": "972501113333",
            "role": "professional",
            "is_active": True,
        }
    )

    await wf.process_incoming_message(CUSTOMER, "יש לי עבודה של נזק משריפה")

    assert Messages.Customer.LIFE_THREATENING not in _sent(wa)
    assert world[4] == []
