"""Sends to the *other* party of a conversation name a template, so a recipient
whose 24h window is closed still hears about it.

Each test drives the real call site and checks the recipient, that the free-form
text is the one it always was, the template key, and that the variables are
exactly the ones the registry declares for it. Where the send was newly wrapped
in try/except, a second test makes it raise and checks that the state change
already written stands.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from bson import ObjectId

import app.services.workflow_service as workflow_service
from app.core.constants import LeadStatus, UserStates
from app.core.messages import Messages
from app.core.phone import to_local_phone
from app.providers.whatsapp.template_registry import TEMPLATES
from app.services import admin_flow, customer_flow, monitor_service, pro_flow
from app.services.dispatch_guards import (
    HANDLED,
    DispatchContext,
    GuardDeps,
    guard_new_or_existing,
)
from app.services.state_manager_service import StateManager

CHAT_ID = "972501234567@c.us"
PRO_PHONE = "972500000001"
PRO_CHAT = f"{PRO_PHONE}@c.us"
PRO_NAME = "שירותי דוגמה"
CITY = "תל אביב"
ADDRESS = f"הרצל 1, {CITY}"
OLD_TIME = "01/01/2026 09:00"
SENT = {"idMessage": "x"}


@pytest_asyncio.fixture
async def clean_db(mock_db):
    # mock_db is module-scoped, and these tests look leads up by chat id.
    await mock_db.users.delete_many({})
    await mock_db.leads.delete_many({})
    await mock_db.slots.delete_many({})
    return mock_db


def _whatsapp(side_effect=None):
    whatsapp = MagicMock()
    whatsapp.send_message = AsyncMock(return_value=SENT, side_effect=side_effect)
    return whatsapp


def _failing_for(chat_id):
    def send(to, *args, **kwargs):
        if to == chat_id:
            raise RuntimeError("window closed")
        return SENT

    return send


def _recipients(whatsapp):
    return [c.args[0] for c in whatsapp.send_message.call_args_list]


def _call_to(whatsapp, chat_id, text):
    """The one send to ``chat_id`` whose free-form text is exactly ``text``."""
    matches = [
        c
        for c in whatsapp.send_message.call_args_list
        if c.args[0] == chat_id and c.args[1] == text
    ]
    assert len(matches) == 1, whatsapp.send_message.call_args_list
    return matches[0]


def _assert_template(call, key, params=None):
    assert call.kwargs["template_key"] == key
    sent = call.kwargs.get("params") or {}
    assert set(sent) == set(TEMPLATES[key].params)
    assert sent == (params or {})


async def _pro(db, **extra):
    return (
        await db.users.insert_one(
            {
                "phone_number": PRO_PHONE,
                "role": "professional",
                "is_active": True,
                "business_name": PRO_NAME,
                **extra,
            }
        )
    ).inserted_id


async def _lead(db, **fields):
    defaults = {
        "chat_id": CHAT_ID,
        "status": LeadStatus.NEW,
        "created_at": datetime.now(timezone.utc),
        "full_address": ADDRESS,
        "city": CITY,
        "issue_type": "נזילה",
    }
    return (await db.leads.insert_one({**defaults, **fields})).inserted_id


async def _booked(db, **fields):
    """A BOOKED lead and its pro, as the documents the flows receive."""
    pro_id = await _pro(db)
    lead_id = await _lead(db, pro_id=pro_id, status=LeadStatus.BOOKED, **fields)
    return (
        await db.leads.find_one({"_id": lead_id}),
        await db.users.find_one({"_id": pro_id}),
    )


# ---------------------------------------------------------------------------
# pro_cancelled_booking -> customer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_pro_cancelling_a_booking_tells_the_customer_with_a_template(clean_db):
    lead, pro = await _booked(clean_db)
    whatsapp = _whatsapp()

    assert await pro_flow._execute_cancel(lead, pro, whatsapp) is True

    call = _call_to(whatsapp, CHAT_ID, Messages.Customer.PRO_CANCELLED_BOOKING)
    _assert_template(call, "pro_cancelled_booking")


@pytest.mark.asyncio
async def test_a_cancellation_stands_when_the_customer_notice_raises(clean_db):
    lead, pro = await _booked(clean_db)
    whatsapp = _whatsapp(side_effect=RuntimeError("window closed"))

    assert await pro_flow._execute_cancel(lead, pro, whatsapp) is True

    whatsapp.send_message.assert_awaited_once()
    stored = await clean_db.leads.find_one({"_id": lead["_id"]})
    assert stored["status"] == LeadStatus.CANCELLED


# ---------------------------------------------------------------------------
# customer_rescheduled -> pro
# ---------------------------------------------------------------------------


async def _reschedule(db, monkeypatch, whatsapp):
    pro_id = await _pro(db)
    slot_id = ObjectId()
    await db.slots.insert_one(
        {
            "_id": slot_id,
            "is_taken": False,
            "start_time": datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc),
        }
    )
    lead_id = await _lead(
        db,
        pro_id=pro_id,
        status=LeadStatus.BOOKED,
        appointment_time=OLD_TIME,
        customer_name="דנה",
    )
    state = MagicMock()
    state.clear_state = AsyncMock()
    state.get_metadata = AsyncMock(
        return_value={"reschedule_slots_context": {"1": str(slot_id)}}
    )
    monkeypatch.setattr(customer_flow, "StateManager", state)

    await customer_flow.handle_reschedule_selection(CHAT_ID, "1", whatsapp)

    return await db.leads.find_one({"_id": lead_id})


@pytest.mark.asyncio
async def test_a_rescheduling_customer_tells_the_pro_with_a_template(
    clean_db, monkeypatch
):
    whatsapp = _whatsapp()

    lead = await _reschedule(clean_db, monkeypatch, whatsapp)

    new_time = lead["appointment_time"]
    assert new_time != OLD_TIME
    call = _call_to(
        whatsapp,
        PRO_CHAT,
        Messages.Pro.CUSTOMER_RESCHEDULED_SUCCESS.format(
            customer_name="דנה", address=ADDRESS, old_time=OLD_TIME, new_time=new_time
        ),
    )
    _assert_template(
        call,
        "customer_rescheduled",
        {
            "customer_name": "דנה",
            "full_address": ADDRESS,
            "old_time": OLD_TIME,
            "new_time": new_time,
        },
    )


@pytest.mark.asyncio
async def test_a_reschedule_stands_when_the_pro_notice_raises(clean_db, monkeypatch):
    whatsapp = _whatsapp(side_effect=_failing_for(PRO_CHAT))

    lead = await _reschedule(clean_db, monkeypatch, whatsapp)

    assert lead["appointment_time"] != OLD_TIME
    assert _recipients(whatsapp) == [CHAT_ID, PRO_CHAT]
    _call_to(
        whatsapp,
        CHAT_ID,
        Messages.Customer.RESCHEDULE_SUCCESS.format(new_time=lead["appointment_time"]),
    )


# ---------------------------------------------------------------------------
# customer_existing_job_query -> pro
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_existing_job_query_reaches_the_pro_with_the_local_phone_variable(
    clean_db, monkeypatch
):
    monkeypatch.setattr(workflow_service.lead_manager, "log_message", AsyncMock())
    lead, _ = await _booked(clean_db, customer_name="דנה")
    state_manager = AsyncMock()
    state_manager.get_metadata = AsyncMock(
        return_value={"booked_lead_id": str(lead["_id"])}
    )
    whatsapp = _whatsapp()
    deps = GuardDeps(
        whatsapp=whatsapp,
        state_manager=state_manager,
        context_manager=AsyncMock(),
        users_collection=clean_db.users,
        security=MagicMock(),
        settings=MagicMock(),
    )
    ctx = DispatchContext(
        chat_id=CHAT_ID,
        user_text="2",
        media_url=None,
        normalized_text="2",
        is_emergency_detected=False,
        current_state=UserStates.AWAITING_NEW_OR_EXISTING,
    )

    assert await guard_new_or_existing(ctx, deps) is HANDLED

    text = Messages.Pro.CUSTOMER_EXISTING_JOB_QUERY.format(
        customer_name="דנה", issue="נזילה", customer_phone="972501234567"
    )
    call = _call_to(whatsapp, PRO_CHAT, text)
    assert to_local_phone(CHAT_ID) not in text
    _assert_template(
        call,
        "customer_existing_job_query",
        {
            "customer_name": "דנה",
            "issue_type": "נזילה",
            "phone": to_local_phone(CHAT_ID),
        },
    )


# ---------------------------------------------------------------------------
# rate_service -> customer
# ---------------------------------------------------------------------------


async def _finish(db, monkeypatch, whatsapp, quality_paused):
    monkeypatch.setattr(
        pro_flow, "non_essential_sends_paused", AsyncMock(return_value=quality_paused)
    )
    lead, pro = await _booked(db)
    result = await pro_flow._execute_finish(lead, pro, whatsapp, PRO_CHAT)
    return result, await db.leads.find_one({"_id": lead["_id"]})


@pytest.mark.asyncio
async def test_a_finished_job_asks_the_customer_to_rate_with_a_template(
    clean_db, monkeypatch
):
    whatsapp = _whatsapp()

    await _finish(clean_db, monkeypatch, whatsapp, quality_paused=False)

    call = _call_to(
        whatsapp, CHAT_ID, Messages.Customer.RATE_SERVICE.format(pro_name=PRO_NAME)
    )
    _assert_template(call, "rate_service", {"pro_name": PRO_NAME})


@pytest.mark.asyncio
async def test_a_finish_still_asks_the_price_when_the_rating_send_raises(
    clean_db, monkeypatch
):
    whatsapp = _whatsapp(side_effect=RuntimeError("window closed"))

    result, lead = await _finish(clean_db, monkeypatch, whatsapp, quality_paused=False)

    whatsapp.send_message.assert_awaited_once()
    assert lead["status"] == LeadStatus.COMPLETED
    assert result == Messages.Pro.FINISH_SUCCESS_ASK_PRICE


@pytest.mark.asyncio
async def test_a_degraded_number_sends_the_short_completion_line_without_a_template(
    clean_db, monkeypatch
):
    whatsapp = _whatsapp()

    await _finish(clean_db, monkeypatch, whatsapp, quality_paused=True)

    first_line = Messages.Customer.RATE_SERVICE.format(pro_name=PRO_NAME).split("\n")[0]
    call = _call_to(whatsapp, CHAT_ID, first_line)
    assert "template_key" not in call.kwargs
    assert "params" not in call.kwargs


# ---------------------------------------------------------------------------
# customer_reported_no_show -> pro
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_no_show_report_tells_the_pro_with_a_template(clean_db, monkeypatch):
    monkeypatch.setattr(monitor_service, "reassign_lead", AsyncMock(return_value=True))
    monkeypatch.setattr(customer_flow, "page_operator", MagicMock())
    await _booked(
        clean_db,
        created_at=datetime.now(timezone.utc) - timedelta(hours=8),
        completion_check_sent_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    whatsapp = _whatsapp()

    result = await customer_flow._handle_no_show_report(CHAT_ID, "3", "3", whatsapp)

    assert result == Messages.Customer.NO_SHOW_ACK
    call = _call_to(whatsapp, PRO_CHAT, Messages.Pro.CUSTOMER_REPORTED_NO_SHOW)
    _assert_template(call, "customer_reported_no_show")


# ---------------------------------------------------------------------------
# reassign_offer -> customer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_approval_sla_offers_the_customer_a_reassignment_with_a_template(
    clean_db, monkeypatch
):
    whatsapp = _whatsapp()
    monkeypatch.setattr(monitor_service, "whatsapp", whatsapp)
    monkeypatch.setattr(monitor_service, "leads_collection", clean_db.leads)
    monkeypatch.setattr(monitor_service, "users_collection", clean_db.users)
    monkeypatch.setattr(monitor_service, "within_business_hours", lambda *a, **k: True)
    monkeypatch.setattr(
        monitor_service.StateManager,
        "get_state",
        AsyncMock(return_value=UserStates.AWAITING_PRO_APPROVAL),
    )
    pro_id = await _pro(clean_db)
    await _lead(
        clean_db,
        pro_id=pro_id,
        pro_notified_at=datetime.now(timezone.utc) - timedelta(minutes=60),
        approval_nudged=False,
        reassign_offered=False,
    )

    await monitor_service.check_pro_approval_sla()

    call = _call_to(whatsapp, CHAT_ID, Messages.Customer.REASSIGN_OFFER)
    _assert_template(call, "reassign_offer")


# ---------------------------------------------------------------------------
# awaiting_approval -> customer, after a reassignment or an admin assignment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_reassignment_tells_the_customer_who_was_found_with_a_template(
    clean_db, monkeypatch
):
    whatsapp = _whatsapp()
    monkeypatch.setattr(monitor_service, "whatsapp", whatsapp)
    old_pro_id = await _pro(clean_db, phone_number="972500000009")
    lead_id = await _lead(clean_db, pro_id=old_pro_id)
    lead = await clean_db.leads.find_one({"_id": lead_id})
    new_pro = {
        "_id": ObjectId(),
        "business_name": "אבי אינסטלציה",
        "phone_number": PRO_PHONE,
    }
    monkeypatch.setattr(
        monitor_service.matching_service,
        "determine_best_pro",
        AsyncMock(return_value=new_pro),
    )

    reassigned = await monitor_service.reassign_lead(
        lead, notify_old_pro=False, announce=False
    )

    assert reassigned is True
    call = _call_to(
        whatsapp,
        CHAT_ID,
        Messages.Customer.AWAITING_APPROVAL_TRANSPARENT.format(
            pro_name="אבי אינסטלציה"
        ),
    )
    _assert_template(call, "awaiting_approval", {"pro_name": "אבי אינסטלציה"})


@pytest.mark.asyncio
async def test_an_admin_assignment_tells_the_customer_who_was_found_with_a_template(
    clean_db, monkeypatch
):
    monkeypatch.setattr(admin_flow, "leads_collection", clean_db.leads)
    lead_id = await _lead(clean_db, status=LeadStatus.PENDING_ADMIN_REVIEW)
    pro = {"_id": ObjectId(), "business_name": "דנה", "phone_number": PRO_PHONE}
    whatsapp = _whatsapp()

    offer_sent, _ = await admin_flow.assign_lead_to_pro(lead_id, pro, whatsapp)

    assert offer_sent is True
    call = _call_to(
        whatsapp,
        CHAT_ID,
        Messages.Customer.AWAITING_APPROVAL_TRANSPARENT.format(pro_name="דנה"),
    )
    _assert_template(call, "awaiting_approval", {"pro_name": "דנה"})


# ---------------------------------------------------------------------------
# customer_reported_completion -> pro
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lead_fields, shown_name",
    [
        ({"customer_name": "דנה"}, "דנה"),
        ({}, Messages.Fallbacks.CUSTOMER_NAME),
    ],
    ids=["named", "no-name"],
)
async def test_a_customer_confirming_completion_tells_the_pro_with_a_template(
    clean_db, lead_fields, shown_name
):
    await _booked(clean_db, **lead_fields)
    whatsapp = _whatsapp()

    await customer_flow.handle_customer_completion_text(CHAT_ID, "כן, הסתיים", whatsapp)

    call = _call_to(whatsapp, PRO_CHAT, Messages.Pro.CUSTOMER_REPORTED_COMPLETION)
    _assert_template(
        call, "customer_reported_completion", {"customer_name": shown_name}
    )


@pytest.mark.asyncio
async def test_a_completion_stands_and_is_acknowledged_when_the_pro_notice_raises(
    clean_db,
):
    lead, _ = await _booked(clean_db)
    whatsapp = _whatsapp(side_effect=RuntimeError("window closed"))

    result = await customer_flow.handle_customer_completion_text(
        CHAT_ID, "כן, הסתיים", whatsapp
    )

    whatsapp.send_message.assert_awaited_once()
    assert result == Messages.Customer.COMPLETION_ACK.format(pro_name=PRO_NAME)
    stored = await clean_db.leads.find_one({"_id": lead["_id"]})
    assert stored["status"] == LeadStatus.COMPLETED


# ---------------------------------------------------------------------------
# bot_paused_by_pro -> customer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_pro_pausing_the_bot_tells_the_customer_with_a_template(clean_db):
    lead, pro = await _booked(clean_db, customer_name="דנה")
    whatsapp = _whatsapp()

    await pro_flow._pause_lead(lead, pro, whatsapp)

    call = _call_to(whatsapp, CHAT_ID, Messages.Customer.BOT_PAUSED_BY_PRO)
    _assert_template(call, "bot_paused_by_pro")


@pytest.mark.asyncio
async def test_a_pause_stands_and_answers_the_pro_when_the_customer_notice_raises(
    clean_db,
):
    lead, pro = await _booked(clean_db, customer_name="דנה")
    whatsapp = _whatsapp(side_effect=RuntimeError("window closed"))

    result = await pro_flow._pause_lead(lead, pro, whatsapp)

    whatsapp.send_message.assert_awaited_once()
    assert result == Messages.Pro.PAUSE_ACK.format(customer_name="דנה")
    assert await StateManager.get_state(CHAT_ID) == UserStates.PAUSED_FOR_HUMAN
    stored = await clean_db.leads.find_one({"_id": lead["_id"]})
    assert stored["is_paused"] is True


@pytest.mark.asyncio
async def test_an_existing_job_query_sends_no_phone_once_the_lead_is_not_booked(
    clean_db, monkeypatch
):
    """PRO-59: the gate was armed minutes ago. A lead re-offered since then is NEW,
    and its pro has not earned the customer's number."""
    monkeypatch.setattr(workflow_service.lead_manager, "log_message", AsyncMock())
    lead, _ = await _booked(clean_db, customer_name="דנה")
    await clean_db.leads.update_one(
        {"_id": lead["_id"]}, {"$set": {"status": LeadStatus.NEW}}
    )
    state_manager = AsyncMock()
    state_manager.get_metadata = AsyncMock(
        return_value={"booked_lead_id": str(lead["_id"])}
    )
    whatsapp = _whatsapp()
    deps = GuardDeps(
        whatsapp=whatsapp,
        state_manager=state_manager,
        context_manager=AsyncMock(),
        users_collection=clean_db.users,
        security=MagicMock(),
        settings=MagicMock(),
    )
    ctx = DispatchContext(
        chat_id=CHAT_ID,
        user_text="2",
        media_url=None,
        normalized_text="2",
        is_emergency_detected=False,
        current_state=UserStates.AWAITING_NEW_OR_EXISTING,
    )

    assert await guard_new_or_existing(ctx, deps) is HANDLED

    recipients = [call.args[0] for call in whatsapp.send_message.await_args_list]
    assert recipients == [CHAT_ID]
