"""
Tests for pro_flow.py: all professional text commands.
Covers: approve, reject, finish, active jobs, history, stats, reviews.
"""

import re

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from bson import ObjectId
from datetime import datetime, timedelta, timezone
from app.core.constants import LeadStatus, UserStates, WorkerConstants
from app.core.messages import Messages
from tests.copy_util import static_prefix
from app.services.pro_flow import handle_pro_text_command, _handle_search
import app.services.pro_flow

PRO_ID = ObjectId()
PRO_PHONE = "972500000000"


@pytest_asyncio.fixture
async def pro_setup(mock_db):
    """Create a pro and return (pro_doc, mock_db)."""
    pro_doc = {
        "_id": PRO_ID,
        "phone_number": PRO_PHONE,
        "role": "professional",
        "business_name": "יוסי אינסטלציה",
        "is_active": True,
        "social_proof": {"rating": 4.5, "review_count": 3},
        "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
        # PRO-123: `_handle_search` now runs leads through
        # `matching_service.is_pro_eligible_for_lead`, which needs a way to
        # place this pro geographically — give it both a location (geo path)
        # and matching service_areas (text fallback) so search tests aren't
        # coupled to which branch the predicate takes.
        "location": {"type": "Point", "coordinates": [34.7818, 32.0853]},
        "service_areas": ["תל אביב"],
    }
    # Avoid duplicate key on re-run within same module scope
    existing = await mock_db.users.find_one({"_id": PRO_ID})
    if not existing:
        await mock_db.users.insert_one(pro_doc)
    return pro_doc, mock_db


@pytest.fixture
def mock_wa():
    wa = MagicMock()
    wa.send_message = AsyncMock()
    return wa


@pytest.fixture
def mock_lm():
    lm = MagicMock()
    lm.update_lead_status = AsyncMock()
    lm.create_lead = AsyncMock()
    return lm


# --- Approve ---


@pytest.mark.asyncio
async def test_approve_with_pending_lead(pro_setup, mock_wa, mock_lm, monkeypatch):
    pro_doc, db = pro_setup

    lead_id = ObjectId()
    await db.leads.insert_one(
        {
            "_id": lead_id,
            "pro_id": pro_doc["_id"],
            "status": LeadStatus.NEW,
            "chat_id": "972501111111@c.us",
            "issue_type": "נזילה",
            "full_address": "תל אביב, הרצל 10",
            "appointment_time": "10:00",
            "created_at": datetime.now(timezone.utc),
        }
    )

    # Mock book_slot_for_lead — returns the booked slot's ObjectId on success
    import app.services.pro_flow

    monkeypatch.setattr(
        app.services.pro_flow, "book_slot_for_lead", AsyncMock(return_value=ObjectId())
    )

    result = await handle_pro_text_command("972500000000@c.us", "אשר", mock_wa, mock_lm)

    assert Messages.Pro.APPROVE_SUCCESS in result
    mock_lm.update_lead_status.assert_called_once()
    # Customer should receive PRO_FOUND message
    mock_wa.send_message.assert_called_once()
    customer_msg = mock_wa.send_message.call_args.args[1]
    assert "יוסי אינסטלציה" in customer_msg


@pytest.mark.asyncio
async def test_approve_forwards_appointment_datetime_to_book_slot_for_lead(
    pro_setup, mock_wa, mock_lm, monkeypatch
):
    """
    PRO-120: the approve path must pass the lead's appointment_datetime
    through to book_slot_for_lead so the slot search centers on the
    customer's requested time rather than always falling back to created_at.
    """
    pro_doc, db = pro_setup

    lead_id = ObjectId()
    appointment_dt = datetime(2026, 8, 25, 15, 0, tzinfo=timezone.utc)
    created_at = datetime.now(timezone.utc)
    await db.leads.insert_one(
        {
            "_id": lead_id,
            "pro_id": pro_doc["_id"],
            "status": LeadStatus.NEW,
            "chat_id": "972501111111@c.us",
            "issue_type": "נזילה",
            "full_address": "תל אביב, הרצל 10",
            "appointment_time": "10:00",
            "appointment_datetime": appointment_dt,
            "created_at": created_at,
        }
    )

    mock_book_slot = AsyncMock(return_value=ObjectId())
    monkeypatch.setattr(app.services.pro_flow, "book_slot_for_lead", mock_book_slot)

    result = await handle_pro_text_command("972500000000@c.us", "אשר", mock_wa, mock_lm)

    assert Messages.Pro.APPROVE_SUCCESS in result
    mock_book_slot.assert_awaited_once()

    # Compare against what the DB actually stored/round-tripped for this
    # lead (Mongo truncates datetime precision and drops tzinfo on
    # naive-write/read), not the original python object.
    stored_lead = await db.leads.find_one({"_id": lead_id})

    call_args, call_kwargs = mock_book_slot.call_args
    assert call_args[0] == pro_doc["_id"]
    assert call_args[1] == stored_lead["created_at"]
    assert call_kwargs["appointment_datetime"] == stored_lead["appointment_datetime"]


@pytest.mark.asyncio
async def test_approve_without_appointment_datetime_forwards_none_to_book_slot_for_lead(
    pro_setup, mock_wa, mock_lm, monkeypatch
):
    """
    Legacy/ASAP branch: a lead with no appointment_datetime field at all
    must still reach book_slot_for_lead — with the kwarg explicitly None —
    so it falls back to the created_at-based estimate rather than being
    skipped or crashing on a missing key.
    """
    pro_doc, db = pro_setup

    lead_id = ObjectId()
    await db.leads.insert_one(
        {
            "_id": lead_id,
            "pro_id": pro_doc["_id"],
            "status": LeadStatus.NEW,
            "chat_id": "972501111111@c.us",
            "issue_type": "נזילה",
            "full_address": "תל אביב, הרצל 10",
            "appointment_time": "10:00",
            # No appointment_datetime key at all — ASAP/legacy lead.
            "created_at": datetime.now(timezone.utc),
        }
    )

    mock_book_slot = AsyncMock(return_value=ObjectId())
    monkeypatch.setattr(app.services.pro_flow, "book_slot_for_lead", mock_book_slot)

    result = await handle_pro_text_command("972500000000@c.us", "אשר", mock_wa, mock_lm)

    assert Messages.Pro.APPROVE_SUCCESS in result
    mock_book_slot.assert_awaited_once()

    _, call_kwargs = mock_book_slot.call_args
    assert "appointment_datetime" in call_kwargs
    assert call_kwargs["appointment_datetime"] is None


@pytest.mark.asyncio
async def test_approve_with_quoted_price_shows_price_to_customer(
    mock_db, mock_wa, mock_lm, monkeypatch
):
    """
    PRO-55: when the lead carries an AI-quoted price (set during the estimate
    turn / deal close), approving it must surface that exact price to the
    customer in the PRO_FOUND message.
    """
    pro_id = ObjectId()
    pro_phone = "972500000010"
    await mock_db.users.insert_one(
        {
            "_id": pro_id,
            "phone_number": pro_phone,
            "role": "professional",
            "business_name": "דני חשמל",
            "is_active": True,
            "social_proof": {"rating": 0, "review_count": 0},
            "created_at": datetime.now(timezone.utc),
        }
    )

    lead_id = ObjectId()
    await mock_db.leads.insert_one(
        {
            "_id": lead_id,
            "pro_id": pro_id,
            "status": LeadStatus.NEW,
            "chat_id": "972501110010@c.us",
            "issue_type": "קצר בחשמל",
            "full_address": "תל אביב, הרצל 10",
            "appointment_time": "10:00",
            "quoted_price": "400-600",
            "created_at": datetime.now(timezone.utc),
        }
    )

    import app.services.pro_flow

    monkeypatch.setattr(
        app.services.pro_flow, "book_slot_for_lead", AsyncMock(return_value=ObjectId())
    )

    result = await handle_pro_text_command(f"{pro_phone}@c.us", "אשר", mock_wa, mock_lm)

    assert Messages.Pro.APPROVE_SUCCESS in result
    mock_wa.send_message.assert_called_once()
    customer_msg = mock_wa.send_message.call_args.args[1]
    assert "400-600" in customer_msg
    assert (
        Messages.Customer.QUOTED_PRICE_LINE.format(quoted_price="400-600")
        in customer_msg
    )


@pytest.mark.asyncio
async def test_approve_without_quoted_price_omits_price_for_customer(
    mock_db, mock_wa, mock_lm, monkeypatch
):
    """No quoted_price on the lead -> PRO_FOUND message has no price line."""
    pro_id = ObjectId()
    pro_phone = "972500000011"
    await mock_db.users.insert_one(
        {
            "_id": pro_id,
            "phone_number": pro_phone,
            "role": "professional",
            "business_name": "דני חשמל",
            "is_active": True,
            "social_proof": {"rating": 0, "review_count": 0},
            "created_at": datetime.now(timezone.utc),
        }
    )

    lead_id = ObjectId()
    await mock_db.leads.insert_one(
        {
            "_id": lead_id,
            "pro_id": pro_id,
            "status": LeadStatus.NEW,
            "chat_id": "972501110011@c.us",
            "issue_type": "קצר בחשמל",
            "full_address": "תל אביב, הרצל 10",
            "appointment_time": "10:00",
            "created_at": datetime.now(timezone.utc),
        }
    )

    import app.services.pro_flow

    monkeypatch.setattr(
        app.services.pro_flow, "book_slot_for_lead", AsyncMock(return_value=ObjectId())
    )

    result = await handle_pro_text_command(f"{pro_phone}@c.us", "אשר", mock_wa, mock_lm)

    assert Messages.Pro.APPROVE_SUCCESS in result
    mock_wa.send_message.assert_called_once()
    customer_msg = mock_wa.send_message.call_args.args[1]
    price_prefix = static_prefix(Messages.Customer.QUOTED_PRICE_LINE)
    assert price_prefix not in customer_msg
    assert "₪" not in customer_msg


@pytest.mark.asyncio
async def test_approve_no_pending(mock_db, mock_wa, mock_lm):
    """Pro with no NEW leads -> NO_PENDING_APPROVE."""
    pro_id = ObjectId()
    existing = await mock_db.users.find_one({"phone_number": "972502222222"})
    if not existing:
        await mock_db.users.insert_one(
            {
                "_id": pro_id,
                "phone_number": "972502222222",
                "role": "professional",
                "is_active": True,
            }
        )
    result = await handle_pro_text_command("972502222222@c.us", "אשר", mock_wa, mock_lm)
    assert result == Messages.Pro.NO_PENDING_APPROVALS


@pytest.mark.asyncio
async def test_approve_with_number_command(pro_setup, mock_wa, mock_lm, monkeypatch):
    """'1' is an alias for approve."""
    pro_doc, db = pro_setup
    await db.leads.insert_one(
        {
            "pro_id": pro_doc["_id"],
            "status": LeadStatus.NEW,
            "chat_id": "972501111111@c.us",
            "issue_type": "חשמל",
            "full_address": "חיפה",
            "appointment_time": "14:00",
            "created_at": datetime.now(timezone.utc),
        }
    )
    import app.services.pro_flow

    monkeypatch.setattr(
        app.services.pro_flow, "book_slot_for_lead", AsyncMock(return_value=None)
    )

    result = await handle_pro_text_command("972500000000@c.us", "1", mock_wa, mock_lm)
    assert Messages.Pro.APPROVE_SUCCESS in result


@pytest.mark.asyncio
async def test_approve_persists_correct_slot_id_with_multiple_active_jobs(
    pro_setup, mock_wa, mock_lm, monkeypatch
):
    """
    PRO-43 regression: a pro with an existing booked job (already holding
    slot A) approves a second, newer lead. book_slot_for_lead reserves a
    DIFFERENT slot (B) for the new lead. The new lead must be persisted
    with slot B's id — not slot A's, which the old "earliest taken slot"
    heuristic would have incorrectly picked. The older lead's booked_slot_id
    must be untouched.
    """
    pro_doc, db = pro_setup

    slot_a_id = ObjectId()
    slot_b_id = ObjectId()

    # Existing older lead, already booked against slot A.
    old_lead_id = ObjectId()
    await db.leads.insert_one(
        {
            "_id": old_lead_id,
            "pro_id": pro_doc["_id"],
            "status": LeadStatus.BOOKED,
            "chat_id": "972503333333@c.us",
            "issue_type": "חשמל",
            "full_address": "רמת גן",
            "appointment_time": "09:00",
            "created_at": datetime.now(timezone.utc) - timedelta(days=1),
            "booked_slot_id": slot_a_id,
        }
    )
    await db.slots.insert_one(
        {
            "_id": slot_a_id,
            "pro_id": pro_doc["_id"],
            "is_taken": True,
            "start_time": datetime.now(timezone.utc) - timedelta(hours=1),
        }
    )

    # New lead pending approval.
    new_lead_id = ObjectId()
    await db.leads.insert_one(
        {
            "_id": new_lead_id,
            "pro_id": pro_doc["_id"],
            "status": LeadStatus.NEW,
            "chat_id": "972501111111@c.us",
            "issue_type": "נזילה",
            "full_address": "תל אביב, הרצל 10",
            "appointment_time": "10:00",
            "created_at": datetime.now(timezone.utc),
        }
    )
    await db.slots.insert_one(
        {
            "_id": slot_b_id,
            "pro_id": pro_doc["_id"],
            "is_taken": False,
            "start_time": datetime.now(timezone.utc) + timedelta(hours=1),
        }
    )

    import app.services.pro_flow

    # book_slot_for_lead reserves slot B for the new lead — never slot A.
    monkeypatch.setattr(
        app.services.pro_flow, "book_slot_for_lead", AsyncMock(return_value=slot_b_id)
    )

    try:
        result = await handle_pro_text_command(
            "972500000000@c.us", "אשר", mock_wa, mock_lm
        )

        assert Messages.Pro.APPROVE_SUCCESS in result

        updated_new_lead = await db.leads.find_one({"_id": new_lead_id})
        updated_old_lead = await db.leads.find_one({"_id": old_lead_id})

        assert updated_new_lead["booked_slot_id"] == slot_b_id
        assert updated_old_lead["booked_slot_id"] == slot_a_id
    finally:
        # `mock_db` is module-scoped (shared across this file's tests) —
        # remove the BOOKED lead we planted so it doesn't inflate the
        # "active jobs" count for tests that run later in this module.
        await db.leads.delete_many({"_id": {"$in": [old_lead_id, new_lead_id]}})
        await db.slots.delete_many({"_id": {"$in": [slot_a_id, slot_b_id]}})


# --- Approve race guard (PRO-123) ---
#
# monitor_service.reassign_lead leaves a rejected/timed-out lead at NEW under
# a DIFFERENT pro. A stale "אשר" from the original pro, arriving after that
# handoff, must not be able to book the lead out from under its new owner —
# `_handle_approve` now guards the write with both expected_status=NEW and
# expected_pro_id=<this pro>, and lead_manager.update_lead_status returns the
# updated doc (or None when the guard didn't match) so the caller can tell.


@pytest.mark.asyncio
async def test_approve_guards_write_with_lead_and_pro_id(
    pro_setup, mock_wa, mock_lm, monkeypatch
):
    """The write to lead_manager must carry both guards — this is the whole
    fix; a regression here silently re-opens the race."""
    pro_doc, db = pro_setup
    lead_id = ObjectId()
    await db.leads.insert_one(
        {
            "_id": lead_id,
            "pro_id": pro_doc["_id"],
            "status": LeadStatus.NEW,
            "chat_id": "972501111111@c.us",
            "issue_type": "נזילה",
            "full_address": "תל אביב, הרצל 10",
            "appointment_time": "10:00",
            "created_at": datetime.now(timezone.utc),
        }
    )
    monkeypatch.setattr(
        app.services.pro_flow, "book_slot_for_lead", AsyncMock(return_value=None)
    )

    await handle_pro_text_command("972500000000@c.us", "אשר", mock_wa, mock_lm)

    mock_lm.update_lead_status.assert_called_once()
    _, kwargs = mock_lm.update_lead_status.call_args
    assert kwargs["expected_status"] == LeadStatus.NEW
    assert kwargs["expected_pro_id"] == pro_doc["_id"]

    await db.leads.delete_many({"_id": lead_id})


@pytest.mark.asyncio
async def test_approve_lost_race_books_nothing_and_messages_nobody(
    pro_setup, mock_wa, mock_lm, monkeypatch
):
    """The lead moved on (reassigned/answered elsewhere) between the read and
    the write — update_lead_status reports the lost guard as None. The pro
    must be told the truth, and neither a slot nor a customer message goes
    out for a lead this pro no longer owns."""
    pro_doc, db = pro_setup
    lead_id = ObjectId()
    await db.leads.insert_one(
        {
            "_id": lead_id,
            "pro_id": pro_doc["_id"],
            "status": LeadStatus.NEW,
            "chat_id": "972501111111@c.us",
            "issue_type": "נזילה",
            "full_address": "תל אביב, הרצל 10",
            "appointment_time": "10:00",
            "created_at": datetime.now(timezone.utc),
        }
    )
    mock_lm.update_lead_status = AsyncMock(return_value=None)
    mock_book_slot = AsyncMock(return_value=ObjectId())
    monkeypatch.setattr(app.services.pro_flow, "book_slot_for_lead", mock_book_slot)

    result = await handle_pro_text_command("972500000000@c.us", "אשר", mock_wa, mock_lm)

    assert result == Messages.Pro.NO_PENDING_APPROVALS
    mock_book_slot.assert_not_called()
    mock_wa.send_message.assert_not_called()
    # The lead itself is untouched — _handle_approve never wrote to it, the
    # (mocked) lead_manager is the only write path exercised here.
    lead = await db.leads.find_one({"_id": lead_id})
    assert lead["status"] == LeadStatus.NEW

    await db.leads.delete_many({"_id": lead_id})


@pytest.mark.asyncio
async def test_approve_lost_race_with_recent_response_returns_already_responded(
    pro_setup, mock_wa, mock_lm, monkeypatch
):
    """Same lost race, but this pro has a BOOKED lead from moments ago —
    read as a fat-finger double-press rather than a bare 'nothing pending'."""
    pro_doc, db = pro_setup
    lead_id = ObjectId()
    await db.leads.insert_one(
        {
            "_id": lead_id,
            "pro_id": pro_doc["_id"],
            "status": LeadStatus.NEW,
            "chat_id": "972501111111@c.us",
            "issue_type": "נזילה",
            "full_address": "תל אביב, הרצל 10",
            "appointment_time": "10:00",
            "created_at": datetime.now(timezone.utc),
        }
    )
    recent_booked_id = ObjectId()
    await db.leads.insert_one(
        {
            "_id": recent_booked_id,
            "pro_id": pro_doc["_id"],
            "status": LeadStatus.BOOKED,
            "chat_id": "972502222222@c.us",
            "created_at": datetime.now(timezone.utc),
        }
    )
    mock_lm.update_lead_status = AsyncMock(return_value=None)
    monkeypatch.setattr(
        app.services.pro_flow, "book_slot_for_lead", AsyncMock(return_value=None)
    )

    result = await handle_pro_text_command("972500000000@c.us", "אשר", mock_wa, mock_lm)

    assert result == Messages.Pro.ALREADY_RESPONDED
    mock_wa.send_message.assert_not_called()

    await db.leads.delete_many({"_id": {"$in": [lead_id, recent_booked_id]}})


# --- Reject ---
#
# PRO-117: reject no longer dead-ends the lead — it hands off to
# monitor_service.reassign_lead(lead, notify_old_pro=False). _handle_reject
# no longer takes/uses lead_manager at all (it claims the rejection itself via
# set_lead_status), so these assert against the DB doc directly rather than a
# mock lead_manager call. These two tests mock matching_service.determine_best_pro
# to land on the "replacement found" branch (REJECT_SUCCESS); see
# tests/test_pro_flow_reject_rematch.py and tests/test_reassign_escalation.py
# for the full PRO-117 branch coverage (no-replacement escalation,
# reassign_lead-raises fallback, notify_old_pro, status_history, SLA re-arm,
# and context-not-cleared-on-success).


@pytest.mark.asyncio
async def test_reject_lead(pro_setup, mock_wa, mock_lm, monkeypatch):
    pro_doc, db = pro_setup
    lead_id = ObjectId()
    await db.leads.insert_one(
        {
            "_id": lead_id,
            "pro_id": pro_doc["_id"],
            "status": LeadStatus.NEW,
            "chat_id": "972501111111@c.us",
            "full_address": "הרצל 1, תל אביב",
            "created_at": datetime.now(timezone.utc),
        }
    )
    new_pro = {
        "_id": ObjectId(),
        "business_name": "אבי אינסטלציה",
        "phone_number": "972559444143",
    }
    monkeypatch.setattr(
        "app.services.matching_service.determine_best_pro",
        AsyncMock(return_value=new_pro),
    )

    result = await handle_pro_text_command("972500000000@c.us", "דחה", mock_wa, mock_lm)
    assert result == Messages.Pro.REJECT_SUCCESS
    mock_lm.update_lead_status.assert_not_called()

    updated = await db.leads.find_one({"_id": lead_id})
    assert updated["status"] == LeadStatus.NEW
    assert updated["pro_id"] == new_pro["_id"]
    assert pro_doc["_id"] in updated["rejected_by"]


@pytest.mark.asyncio
async def test_reject_no_pending(mock_db, mock_wa, mock_lm):
    existing = await mock_db.users.find_one({"phone_number": "972502222222"})
    if not existing:
        await mock_db.users.insert_one(
            {
                "phone_number": "972502222222",
                "role": "professional",
                "is_active": True,
            }
        )
    result = await handle_pro_text_command("972502222222@c.us", "דחה", mock_wa, mock_lm)
    assert result == Messages.Pro.NO_PENDING_APPROVALS


# --- Finish ---


@pytest.mark.asyncio
async def test_finish_job_single(pro_setup, mock_wa, mock_lm, monkeypatch):
    pro_doc, db = pro_setup
    chat_id = "972500000000@c.us"
    lead_id = ObjectId()
    await db.leads.insert_one(
        {
            "_id": lead_id,
            "pro_id": pro_doc["_id"],
            "status": LeadStatus.BOOKED,
            "chat_id": "972501111111@c.us",
            "created_at": datetime.now(timezone.utc),
        }
    )

    # Mock StateManager so the new PRO_AWAITING_FINAL_PRICE state (PRO-33) isn't
    # written to real Redis where it would leak into later pro tests sharing this chat.
    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=None)
    mock_state.set_state = AsyncMock()
    mock_state.set_metadata = AsyncMock()
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    result = await handle_pro_text_command(chat_id, "סיימתי", mock_wa, mock_lm)
    # PRO-33: completion now asks for the charged price as a non-blocking follow-up.
    assert result == Messages.Pro.FINISH_SUCCESS_ASK_PRICE

    # Lead should be completed BEFORE the price is asked (never gated)
    lead = await db.leads.find_one({"_id": lead_id})
    assert lead["status"] == LeadStatus.COMPLETED

    # Pro is placed in the price-capture state with the lead id in metadata
    mock_state.set_state.assert_called_once()
    assert mock_state.set_state.call_args.args[1] == UserStates.PRO_AWAITING_FINAL_PRICE
    meta_arg = mock_state.set_metadata.call_args.args[1]
    assert meta_arg["final_price_lead_id"] == str(lead_id)


@pytest.mark.asyncio
async def test_finish_multiple_jobs_selection(pro_setup, mock_wa, mock_lm, monkeypatch):
    """If multiple BOOKED leads, pro enters selection state."""
    pro_doc, db = pro_setup
    chat_id = "972500000000@c.us"

    await db.leads.insert_many(
        [
            {
                "pro_id": pro_doc["_id"],
                "status": LeadStatus.BOOKED,
                "customer_name": "A",
                "created_at": datetime.now(timezone.utc),
            },
            {
                "pro_id": pro_doc["_id"],
                "status": LeadStatus.BOOKED,
                "customer_name": "B",
                "created_at": datetime.now(timezone.utc) - timedelta(minutes=1),
            },
        ]
    )

    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=None)
    mock_state.set_state = AsyncMock()
    mock_state.set_metadata = AsyncMock()
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    result = await handle_pro_text_command(chat_id, "סיימתי", mock_wa, mock_lm)

    assert static_prefix(Messages.Pro.SELECT_JOB_TO_FINISH) in result
    mock_state.set_state.assert_called_with(
        chat_id, UserStates.PRO_SELECTING_JOB_TO_FINISH
    )
    mock_state.set_metadata.assert_called_once()


# --- Final price capture (PRO-33) ---


def _mock_price_state(monkeypatch, lead_id):
    """StateManager mock: pro is in PRO_AWAITING_FINAL_PRICE for the given lead."""
    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=UserStates.PRO_AWAITING_FINAL_PRICE)
    mock_state.get_metadata = AsyncMock(
        return_value={"final_price_lead_id": str(lead_id)}
    )
    mock_state.clear_state = AsyncMock()
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)
    return mock_state


@pytest.mark.asyncio
async def test_final_price_valid_records_price_and_commission(
    pro_setup, mock_wa, mock_lm, monkeypatch
):
    pro_doc, db = pro_setup
    lead_id = ObjectId()
    await db.leads.insert_one(
        {
            "_id": lead_id,
            "pro_id": pro_doc["_id"],
            "status": LeadStatus.COMPLETED,
            "chat_id": "972501111111@c.us",
            "created_at": datetime.now(timezone.utc),
        }
    )
    mock_state = _mock_price_state(monkeypatch, lead_id)

    result = await handle_pro_text_command("972500000000@c.us", "450", mock_wa, mock_lm)

    assert result == Messages.Pro.FINAL_PRICE_RECORDED.format(price=450)
    lead = await db.leads.find_one({"_id": lead_id})
    assert lead["final_price"] == 450
    # commission = 450 * COMMISSION_RATE (0.10) = 45.0
    assert lead["commission_amount"] == round(450 * WorkerConstants.COMMISSION_RATE, 2)
    assert lead["status"] == LeadStatus.COMPLETED  # unchanged
    mock_state.clear_state.assert_awaited_once()


@pytest.mark.asyncio
async def test_final_price_skip_leaves_null(pro_setup, mock_wa, mock_lm, monkeypatch):
    pro_doc, db = pro_setup
    lead_id = ObjectId()
    await db.leads.insert_one(
        {
            "_id": lead_id,
            "pro_id": pro_doc["_id"],
            "status": LeadStatus.COMPLETED,
            "created_at": datetime.now(timezone.utc),
        }
    )
    mock_state = _mock_price_state(monkeypatch, lead_id)

    result = await handle_pro_text_command("972500000000@c.us", "דלג", mock_wa, mock_lm)

    assert result == Messages.Pro.FINAL_PRICE_SKIPPED
    lead = await db.leads.find_one({"_id": lead_id})
    assert "final_price" not in lead
    mock_state.clear_state.assert_awaited_once()


@pytest.mark.asyncio
async def test_final_price_non_numeric_leaves_null_no_crash(
    pro_setup, mock_wa, mock_lm, monkeypatch
):
    pro_doc, db = pro_setup
    lead_id = ObjectId()
    await db.leads.insert_one(
        {
            "_id": lead_id,
            "pro_id": pro_doc["_id"],
            "status": LeadStatus.COMPLETED,
            "created_at": datetime.now(timezone.utc),
        }
    )
    mock_state = _mock_price_state(monkeypatch, lead_id)

    result = await handle_pro_text_command(
        "972500000000@c.us", "תודה רבה", mock_wa, mock_lm
    )

    assert result == Messages.Pro.FINAL_PRICE_INVALID
    lead = await db.leads.find_one({"_id": lead_id})
    assert "final_price" not in lead  # left null, COMPLETED preserved
    assert lead["status"] == LeadStatus.COMPLETED
    mock_state.clear_state.assert_awaited_once()


def test_parse_final_price_shapes():
    from app.services.pro_flow import _parse_final_price

    assert _parse_final_price("450") == 450
    assert _parse_final_price("450₪") == 450
    assert _parse_final_price("450 שח") == 450
    assert _parse_final_price("1,200") == 1200
    assert _parse_final_price("99.5") == 99.5
    # Rejected: empty, non-numeric, ambiguous range, phone, out-of-bounds
    assert _parse_final_price("") is None
    assert _parse_final_price("דלג") is None
    assert _parse_final_price("400-600") is None  # range → ambiguous
    assert _parse_final_price("0501234567") is None  # phone-shaped, out of bounds
    assert _parse_final_price("0") is None
    assert _parse_final_price("2000000") is None  # above sanity ceiling


@pytest.mark.parametrize(
    "text, expected",
    [
        ("אשר", True),  # APPROVE_COMMANDS
        ("1", True),  # numeric alias, also an APPROVE_COMMANDS entry
        ("עזרה", True),  # HELP_COMMANDS
        ("מצא", True),  # SEARCH_COMMANDS
        ("דלג", False),  # SKIP_COMMANDS is deliberately excluded — an
        # answer to the price prompt, not a command that abandons it
        ("450", False),  # an ordinary quoted price
        ("תודה רבה", False),  # free text
    ],
)
def test_is_pro_command_matches_dispatcher_keywords_excludes_skip_and_free_text(
    text, expected
):
    """PRO-123: `_is_pro_command` is what decides whether a reply inside
    PRO_AWAITING_FINAL_PRICE abandons the price prompt. It must recognize
    every keyword list the dispatcher matches, but SKIP_COMMANDS ('דלג') is
    deliberately excluded — that's an answer to the price prompt itself."""
    assert app.services.pro_flow._is_pro_command(text) is expected


@pytest.mark.asyncio
async def test_final_price_prompt_help_command_clears_state_and_shows_help(
    pro_setup, mock_wa, mock_lm, monkeypatch
):
    """PRO-123: a recognized command arriving while PRO_AWAITING_FINAL_PRICE
    abandons the price prompt instead of being parsed as an (invalid) price —
    'עזרה' must show the help menu, not FINAL_PRICE_INVALID."""
    pro_doc, db = pro_setup
    lead_id = ObjectId()
    await db.leads.insert_one(
        {
            "_id": lead_id,
            "pro_id": pro_doc["_id"],
            "status": LeadStatus.COMPLETED,
            "created_at": datetime.now(timezone.utc),
        }
    )
    mock_state = _mock_price_state(monkeypatch, lead_id)

    result = await handle_pro_text_command(
        "972500000000@c.us", "עזרה", mock_wa, mock_lm
    )

    assert result == Messages.Pro.HELP_MENU
    mock_state.clear_state.assert_awaited_once()
    lead = await db.leads.find_one({"_id": lead_id})
    assert "final_price" not in lead  # never reached _handle_final_price_reply

    await db.leads.delete_many({"_id": lead_id})


@pytest.mark.asyncio
async def test_final_price_prompt_ambiguous_number_dispatches_command_not_price(
    pro_setup, mock_wa, mock_lm, monkeypatch
):
    """PRO-123 regression case: '1' is both an APPROVE_COMMANDS alias and a
    plausible price. While PRO_AWAITING_FINAL_PRICE is active for one
    (already-completed) lead, a separate NEW lead offer lands and the pro
    replies '1' meaning approve — it must not be recorded as final_price=1
    on the completed job."""
    pro_doc, db = pro_setup
    completed_lead_id = ObjectId()
    await db.leads.insert_one(
        {
            "_id": completed_lead_id,
            "pro_id": pro_doc["_id"],
            "status": LeadStatus.COMPLETED,
            "created_at": datetime.now(timezone.utc),
        }
    )
    new_lead_id = ObjectId()
    await db.leads.insert_one(
        {
            "_id": new_lead_id,
            "pro_id": pro_doc["_id"],
            "status": LeadStatus.NEW,
            "chat_id": "972501111111@c.us",
            "issue_type": "נזילה",
            "full_address": "תל אביב, הרצל 10",
            "appointment_time": "10:00",
            "created_at": datetime.now(timezone.utc),
        }
    )
    mock_state = _mock_price_state(monkeypatch, completed_lead_id)
    monkeypatch.setattr(
        app.services.pro_flow, "book_slot_for_lead", AsyncMock(return_value=None)
    )

    result = await handle_pro_text_command("972500000000@c.us", "1", mock_wa, mock_lm)

    assert Messages.Pro.APPROVE_SUCCESS in result
    # Two chats get cleared on this path: the pro's, because the price prompt
    # was abandoned, and the customer's, by the approve handler. Assert the one
    # this test is about by chat_id rather than by call count.
    cleared = [c.args[0] for c in mock_state.clear_state.await_args_list]
    assert "972500000000@c.us" in cleared
    completed_lead = await db.leads.find_one({"_id": completed_lead_id})
    assert "final_price" not in completed_lead

    await db.leads.delete_many({"_id": {"$in": [completed_lead_id, new_lead_id]}})


@pytest.mark.asyncio
async def test_finish_no_booked(mock_db, mock_wa, mock_lm):
    existing = await mock_db.users.find_one({"phone_number": "972502222222"})
    if not existing:
        await mock_db.users.insert_one(
            {
                "phone_number": "972502222222",
                "role": "professional",
                "is_active": True,
            }
        )
    result = await handle_pro_text_command(
        "972502222222@c.us", "סיימתי", mock_wa, mock_lm
    )
    assert result == Messages.Pro.NO_ACTIVE_JOBS


# --- Active Jobs ---


@pytest.mark.asyncio
async def test_active_jobs_list(pro_setup, mock_wa, mock_lm):
    pro_doc, db = pro_setup
    await db.leads.insert_one(
        {
            "pro_id": pro_doc["_id"],
            "status": LeadStatus.BOOKED,
            "issue_type": "נזילה",
            "full_address": "תל אביב",
            "appointment_time": "10:00",
            "created_at": datetime.now(timezone.utc),
        }
    )

    result = await handle_pro_text_command(
        "972500000000@c.us", "עבודות", mock_wa, mock_lm
    )
    assert "עבודות פעילות" in result
    assert "נזילה" in result


@pytest.mark.asyncio
async def test_active_jobs_empty(mock_db, mock_wa, mock_lm):
    """Pro with no active leads."""
    existing = await mock_db.users.find_one({"phone_number": "972502222222"})
    if not existing:
        await mock_db.users.insert_one(
            {
                "phone_number": "972502222222",
                "role": "professional",
                "is_active": True,
            }
        )
    # Use the pro that has no leads assigned to it
    result = await handle_pro_text_command(
        "972502222222@c.us", "עבודות", mock_wa, mock_lm
    )
    assert result == Messages.Pro.NO_ACTIVE_JOBS_LIST


# --- History ---


@pytest.mark.asyncio
async def test_history(pro_setup, mock_wa, mock_lm):
    pro_doc, db = pro_setup
    await db.leads.insert_one(
        {
            "pro_id": pro_doc["_id"],
            "status": LeadStatus.COMPLETED,
            "issue_type": "חשמל",
            "full_address": "חיפה",
            "completed_at": datetime(2026, 3, 15, tzinfo=timezone.utc),
            "created_at": datetime.now(timezone.utc),
        }
    )

    result = await handle_pro_text_command(
        "972500000000@c.us", "היסטוריה", mock_wa, mock_lm
    )
    assert Messages.Pro.HISTORY_HEADER in result
    assert "חשמל" in result


@pytest.mark.asyncio
async def test_history_empty(mock_db, mock_wa, mock_lm):
    existing = await mock_db.users.find_one({"phone_number": "972502222222"})
    if not existing:
        await mock_db.users.insert_one(
            {
                "phone_number": "972502222222",
                "role": "professional",
                "is_active": True,
            }
        )
    result = await handle_pro_text_command(
        "972502222222@c.us", "היסטוריה", mock_wa, mock_lm
    )
    assert result == Messages.Pro.NO_HISTORY


# --- Stats ---


@pytest.mark.asyncio
async def test_stats(pro_setup, mock_wa, mock_lm):
    pro_doc, db = pro_setup
    await db.leads.insert_one(
        {
            "pro_id": pro_doc["_id"],
            "status": LeadStatus.COMPLETED,
            "created_at": datetime.now(timezone.utc),
        }
    )

    result = await handle_pro_text_command("972500000000@c.us", "דוח", mock_wa, mock_lm)
    assert Messages.Pro.STATS_HEADER in result
    assert "4.5" in result  # rating


# --- Reviews ---
# (covered by test_reviews_returns_text_reviews / test_reviews_no_text_reviews
# below — the earlier, weaker copies that lived here were removed as duplicates)


# --- Unknown Command ---


@pytest.mark.asyncio
async def test_unknown_command_returns_dashboard(
    pro_setup, mock_wa, mock_lm, monkeypatch
):
    """Now returns dashboard instead of None."""
    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=None)
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    result = await handle_pro_text_command(
        "972500000000@c.us", "שלום", mock_wa, mock_lm
    )
    assert f"*סטטוס:* {Messages.Pro.STATUS_AVAILABLE}" in result
    assert "יוסי אינסטלציה" in result


@pytest.mark.asyncio
async def test_non_pro_returns_none(mock_db, mock_wa, mock_lm):
    """Non-pro phone number -> returns None."""
    result = await handle_pro_text_command("972501111111@c.us", "אשר", mock_wa, mock_lm)
    assert result is None


# --- Text-Based Pro Approval Handlers ---


@pytest.mark.asyncio
async def test_approve_via_text_command(pro_setup, mock_wa, mock_lm, monkeypatch):
    """Pro types 'אשר' -> lead becomes BOOKED, customer state cleared."""
    pro_doc, db = pro_setup
    monkeypatch.setattr(
        app.services.pro_flow, "book_slot_for_lead", AsyncMock(return_value=ObjectId())
    )

    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=None)
    mock_state.clear_state = AsyncMock()
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    lead_id = ObjectId()
    await db.leads.insert_one(
        {
            "_id": lead_id,
            "pro_id": pro_doc["_id"],
            "status": LeadStatus.NEW,
            "chat_id": "972501111111@c.us",
            "issue_type": "נזילה",
            "full_address": "רחוב הרצל 5",
            "appointment_time": "10:00",
            "created_at": datetime.now(timezone.utc),
        }
    )

    result = await handle_pro_text_command(f"{PRO_PHONE}@c.us", "אשר", mock_wa, mock_lm)

    assert Messages.Pro.APPROVE_SUCCESS in result
    mock_lm.update_lead_status.assert_called_once()
    # Customer state should be cleared
    mock_state.clear_state.assert_called_with("972501111111@c.us")


@pytest.mark.asyncio
async def test_pause_via_text_command(pro_setup, mock_wa, mock_lm, monkeypatch):
    """Pro types 'השהה' -> customer state set to PAUSED_FOR_HUMAN with TTL."""
    pro_doc, db = pro_setup

    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=None)
    mock_state.set_state = AsyncMock()
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    lead_id = ObjectId()
    await db.leads.insert_one(
        {
            "_id": lead_id,
            "pro_id": pro_doc["_id"],
            "status": LeadStatus.NEW,
            "chat_id": "972501111111@c.us",
            "issue_type": "נזילה",
            "full_address": "רחוב הרצל 5",
            "appointment_time": "10:00",
            "created_at": datetime.now(timezone.utc),
        }
    )

    # Note: "השהה" is now in BOT_PAUSE_COMMANDS
    result = await handle_pro_text_command(
        f"{PRO_PHONE}@c.us", "השהה", mock_wa, mock_lm
    )

    assert result == Messages.Pro.PAUSE_ACK
    # Customer state should be set with TTL
    mock_state.set_state.assert_called_with(
        "972501111111@c.us",
        UserStates.PAUSED_FOR_HUMAN,
        ttl=WorkerConstants.PAUSE_TTL_SECONDS,
    )
    # Customer notified
    mock_wa.send_message.assert_called_with(
        "972501111111@c.us", Messages.Customer.BOT_PAUSED_BY_PRO
    )


@pytest.mark.asyncio
async def test_reject_via_text_command(pro_setup, mock_wa, mock_lm, monkeypatch):
    """Pro types 'דחה' -> lead rejected and reassigned, customer SLA re-armed.

    PRO-117: a successful reassignment re-arms the PRO-56 approval SLA —
    StateManager.set_state(..., AWAITING_PRO_APPROVAL) — rather than clearing
    state, so the customer stays covered by the nudge/reassign-offer for the
    new pro. This is real (fakeredis-backed) StateManager, not a mock, so the
    assertion reads the actual FSM state back.
    """
    pro_doc, db = pro_setup

    from app.services.state_manager_service import StateManager

    new_pro = {
        "_id": ObjectId(),
        "business_name": "אבי אינסטלציה",
        "phone_number": "972559444143",
    }
    monkeypatch.setattr(
        "app.services.matching_service.determine_best_pro",
        AsyncMock(return_value=new_pro),
    )

    lead_id = ObjectId()
    chat_id = "972501111111@c.us"
    await db.leads.insert_one(
        {
            "_id": lead_id,
            "pro_id": pro_doc["_id"],
            "status": LeadStatus.NEW,
            "chat_id": chat_id,
            "issue_type": "נזילה",
            "full_address": "רחוב הרצל 5",
            "appointment_time": "10:00",
            "created_at": datetime.now(timezone.utc),
        }
    )

    result = await handle_pro_text_command(f"{PRO_PHONE}@c.us", "דחה", mock_wa, mock_lm)

    assert result == Messages.Pro.REJECT_SUCCESS
    mock_lm.update_lead_status.assert_not_called()

    updated = await db.leads.find_one({"_id": lead_id})
    assert updated["pro_id"] == new_pro["_id"]
    assert await StateManager.get_state(chat_id) == UserStates.AWAITING_PRO_APPROVAL


@pytest.mark.asyncio
async def test_resume_clears_pause(pro_setup, mock_wa, mock_lm, monkeypatch):
    """Pro sends 'המשך' -> customer pause state cleared."""
    pro_doc, db = pro_setup

    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=UserStates.PAUSED_FOR_HUMAN)
    mock_state.clear_state = AsyncMock()
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    lead_id = ObjectId()
    await db.leads.insert_one(
        {
            "_id": lead_id,
            "pro_id": pro_doc["_id"],
            "status": LeadStatus.BOOKED,
            "chat_id": "972501111111@c.us",
            "issue_type": "נזילה",
            "full_address": "רחוב הרצל 5",
            "appointment_time": "10:00",
            "created_at": datetime.now(timezone.utc),
        }
    )

    result = await handle_pro_text_command(
        f"{PRO_PHONE}@c.us", "המשך", mock_wa, mock_lm
    )

    assert Messages.Pro.BOT_RESUMED in result
    mock_state.clear_state.assert_called_with("972501111111@c.us")


# --- Zero-Touch Intent Detection ---


@pytest.mark.asyncio
async def test_intent_detected_prompts_switch(pro_setup, mock_wa, mock_lm, monkeypatch):
    """Free-text service request from Pro -> sends INTENT_DETECTED message and sets AWAITING state."""
    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=None)
    mock_state.set_state = AsyncMock()
    mock_state.get_metadata = AsyncMock(return_value={})
    mock_state.set_metadata = AsyncMock()
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    mock_ai = MagicMock()
    mock_ai.detect_service_intent = AsyncMock(return_value=True)

    result = await handle_pro_text_command(
        f"{PRO_PHONE}@c.us", "המזגן שלי דולף", mock_wa, mock_lm, ai=mock_ai
    )

    # Returns empty sentinel
    assert result == ""
    # INTENT_DETECTED sent as text message
    mock_wa.send_message.assert_called_once()
    call_text = mock_wa.send_message.call_args[0][1]
    assert call_text == Messages.Pro.INTENT_DETECTED
    # State set to AWAITING_INTENT_CONFIRMATION with 5-min TTL
    mock_state.set_state.assert_called_once_with(
        f"{PRO_PHONE}@c.us",
        UserStates.AWAITING_INTENT_CONFIRMATION,
        ttl=300,
    )


@pytest.mark.asyncio
async def test_intent_not_detected_returns_dashboard(
    pro_setup, mock_wa, mock_lm, monkeypatch
):
    """Classifier returns False -> function returns Dashboard."""
    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=None)
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    mock_ai = MagicMock()
    mock_ai.detect_service_intent = AsyncMock(return_value=False)

    result = await handle_pro_text_command(
        f"{PRO_PHONE}@c.us", "סתם הודעה", mock_wa, mock_lm, ai=mock_ai
    )

    assert "יוסי אינסטלציה" in result
    mock_ai.detect_service_intent.assert_called_once_with("סתם הודעה")


@pytest.mark.asyncio
async def test_known_command_skips_intent_detection(
    pro_setup, mock_wa, mock_lm, monkeypatch
):
    """Text 'אשר' always matches APPROVE_COMMANDS -> detect_service_intent is never called."""
    mock_ai = MagicMock()
    mock_ai.detect_service_intent = AsyncMock(return_value=True)

    # Even if the result varies (depends on DB state), classifier must NOT be called
    await handle_pro_text_command(
        f"{PRO_PHONE}@c.us", "אשר", mock_wa, mock_lm, ai=mock_ai
    )

    mock_ai.detect_service_intent.assert_not_called()


@pytest.mark.asyncio
async def test_intent_detection_no_ai_returns_dashboard(
    pro_setup, mock_wa, mock_lm, monkeypatch
):
    """When ai=None (default), unmatched text returns Dashboard."""
    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=None)
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    result = await handle_pro_text_command(
        f"{PRO_PHONE}@c.us", "שאלה כלשהי", mock_wa, mock_lm
    )
    assert "יוסי אינסטלציה" in result


# --- Proactive Search (rate-limited) ---


def _make_mock_redis():
    """Redis stub that simulates ttl / setex for _handle_search."""
    store = {}  # key -> (value, expires_at or None)

    def _now():
        return datetime.now(timezone.utc)

    async def ttl(key):
        entry = store.get(key)
        if entry is None:
            return -2
        _, expires_at = entry
        if expires_at is None:
            return -1
        remaining = int((expires_at - _now()).total_seconds())
        return remaining if remaining > 0 else -2

    async def setex(key, seconds, value):
        store[key] = (value, _now() + timedelta(seconds=seconds))

    redis = MagicMock()
    redis.ttl = AsyncMock(side_effect=ttl)
    redis.setex = AsyncMock(side_effect=setex)
    return redis, store


@pytest.mark.asyncio
async def test_search_no_stuck_leads_sets_cooldown(pro_setup, mock_wa):
    """First call with empty DB: returns NO_STUCK_LEADS and locks cool-down."""
    pro_doc, db = pro_setup
    chat_id = f"{PRO_PHONE}@c.us"
    redis, store = _make_mock_redis()
    # PRO-123: _handle_search now gates on this pro's own active load before
    # ever reaching the cool-down — clear leftovers from earlier tests in this
    # module (shared mock_db) so the load gate can't short-circuit this test.
    await db.leads.delete_many({"pro_id": pro_doc["_id"]})

    with patch(
        "app.services.pro_flow.get_redis_client",
        new_callable=AsyncMock,
        return_value=redis,
    ):
        result = await _handle_search(pro_doc, chat_id, mock_wa)

    assert result == Messages.Pro.NO_STUCK_LEADS
    assert f"rate_limit:pro_search:{chat_id}" in store
    redis.setex.assert_called_once()


@pytest.mark.asyncio
async def test_search_rate_limited_sends_wait_message(pro_setup, mock_wa):
    """Second call within cool-down returns the rate-limited sentinel and sends formatted message."""
    pro_doc, db = pro_setup
    chat_id = f"{PRO_PHONE}@c.us"
    redis, store = _make_mock_redis()
    # PRO-123: clear leaked active leads so the load gate doesn't preempt
    # the rate-limit check this test is actually about.
    await db.leads.delete_many({"pro_id": pro_doc["_id"]})
    # Pre-seed an active cool-down with ~6 minutes remaining
    store[f"rate_limit:pro_search:{chat_id}"] = (
        "1",
        datetime.now(timezone.utc) + timedelta(seconds=360),
    )

    with patch(
        "app.services.pro_flow.get_redis_client",
        new_callable=AsyncMock,
        return_value=redis,
    ):
        result = await _handle_search(pro_doc, chat_id, mock_wa)

    assert result == ""  # sentinel: handler sent message itself
    mock_wa.send_message.assert_called_once()
    sent_text = mock_wa.send_message.call_args.args[1]
    # math.ceil(360 / 60) == 6
    assert sent_text == Messages.Pro.SEARCH_RATE_LIMITED.format(minutes=6)
    # setex must NOT be refreshed when already rate-limited
    redis.setex.assert_not_called()


@pytest.mark.asyncio
async def test_search_finds_stuck_lead_and_assigns(pro_setup, mock_wa):
    """Pending-admin-review lead: assigned to pro as NEW, cool-down set."""
    pro_doc, db = pro_setup
    chat_id = f"{PRO_PHONE}@c.us"
    redis, store = _make_mock_redis()
    # PRO-123: clear leaked active leads from earlier tests so this pro isn't
    # already at MAX_PRO_LOAD when the eligibility check runs.
    await db.leads.delete_many({"pro_id": pro_doc["_id"]})

    lead_id = ObjectId()
    await db.leads.insert_one(
        {
            "_id": lead_id,
            "status": LeadStatus.PENDING_ADMIN_REVIEW,
            "issue_type": "נזילה",
            "city": "תל אביב",
            "created_at": datetime.now(timezone.utc) - timedelta(minutes=75),
        }
    )

    with patch(
        "app.services.pro_flow.get_redis_client",
        new_callable=AsyncMock,
        return_value=redis,
    ):
        result = await _handle_search(pro_doc, chat_id, mock_wa)

    assert "נזילה" in result
    assert "תל אביב" in result
    lead = await db.leads.find_one({"_id": lead_id})
    assert lead["status"] == LeadStatus.NEW
    assert lead["pro_id"] == pro_doc["_id"]
    assert f"rate_limit:pro_search:{chat_id}" in store


@pytest.mark.asyncio
async def test_search_resets_reassignment_lifecycle_after_escalation(
    pro_setup, mock_wa
):
    """PRO-63 Fix 2 — a pro claiming a stuck lead via 'מצא' must reset the
    reassignment lifecycle (count/flags/escalation_reason), or the lead
    immediately re-escalates off them on the next Healer sweep."""
    pro_doc, db = pro_setup
    chat_id = f"{PRO_PHONE}@c.us"
    redis, store = _make_mock_redis()

    await db.leads.delete_many({"status": LeadStatus.PENDING_ADMIN_REVIEW})
    # PRO-123: clear leaked active leads from earlier tests so this pro isn't
    # already at MAX_PRO_LOAD when the eligibility check runs.
    await db.leads.delete_many({"pro_id": pro_doc["_id"]})
    lead_id = ObjectId()
    await db.leads.insert_one(
        {
            "_id": lead_id,
            "status": LeadStatus.PENDING_ADMIN_REVIEW,
            "issue_type": "נזילה",
            "city": "תל אביב",
            "created_at": datetime.now(timezone.utc) - timedelta(minutes=90),
            "reassignment_count": WorkerConstants.MAX_REASSIGNMENTS,
            "escalation_reason": "max_reassignments_exhausted",
            "approval_nudged": True,
            "reassign_offered": True,
        }
    )

    with patch(
        "app.services.pro_flow.get_redis_client",
        new_callable=AsyncMock,
        return_value=redis,
    ):
        await _handle_search(pro_doc, chat_id, mock_wa)

    lead = await db.leads.find_one({"_id": lead_id})
    assert lead["status"] == LeadStatus.NEW
    assert lead["pro_id"] == pro_doc["_id"]
    assert lead["reassignment_count"] == 0
    assert "escalation_reason" not in lead
    assert lead["approval_nudged"] is False
    assert lead["reassign_offered"] is False
    assert lead["pro_notified_at"] is not None


@pytest.mark.asyncio
async def test_search_while_paused_refuses_before_setting_cooldown(pro_setup, mock_wa):
    """PRO-123: a paused pro is told to resume, and the search never consumes
    the 10-minute cool-down for a request it refused outright."""
    pro_doc, db = pro_setup
    chat_id = f"{PRO_PHONE}@c.us"
    redis, store = _make_mock_redis()
    paused_pro = {**pro_doc, "is_active": False}

    with patch(
        "app.services.pro_flow.get_redis_client",
        new_callable=AsyncMock,
        return_value=redis,
    ):
        result = await _handle_search(paused_pro, chat_id, mock_wa)

    assert result == Messages.Pro.SEARCH_WHILE_PAUSED
    redis.setex.assert_not_called()
    assert store == {}


@pytest.mark.asyncio
async def test_search_at_max_load_refuses_before_setting_cooldown(pro_setup, mock_wa):
    """PRO-123: a pro already at MAX_PRO_LOAD is told to finish a job first,
    and — same as the paused gate — this must not burn the search cool-down."""
    pro_doc, db = pro_setup
    chat_id = f"{PRO_PHONE}@c.us"
    redis, store = _make_mock_redis()
    await db.leads.delete_many({"pro_id": pro_doc["_id"]})
    for _ in range(WorkerConstants.MAX_PRO_LOAD):
        await db.leads.insert_one(
            {
                "pro_id": pro_doc["_id"],
                "status": LeadStatus.BOOKED,
                "chat_id": "customer@c.us",
                "created_at": datetime.now(timezone.utc),
            }
        )

    with patch(
        "app.services.pro_flow.get_redis_client",
        new_callable=AsyncMock,
        return_value=redis,
    ):
        result = await _handle_search(pro_doc, chat_id, mock_wa)

    assert result == Messages.Pro.SEARCH_LOAD_FULL.format(
        active=WorkerConstants.MAX_PRO_LOAD, max_jobs=WorkerConstants.MAX_PRO_LOAD
    )
    redis.setex.assert_not_called()
    assert store == {}


@pytest.mark.asyncio
async def test_search_skips_ineligible_lead_and_claims_next_eligible_one(
    pro_setup, mock_wa
):
    """PRO-123: the oldest PENDING_ADMIN_REVIEW lead is not automatically
    claimed anymore — a pro whose profession doesn't match it must be skipped
    in favor of the next, eligible candidate, rather than blocking the pro's
    search entirely."""
    pro_doc, db = pro_setup
    chat_id = f"{PRO_PHONE}@c.us"
    redis, store = _make_mock_redis()
    plumber_pro = {**pro_doc, "profession_type": "plumber"}

    await db.leads.delete_many({"status": LeadStatus.PENDING_ADMIN_REVIEW})
    await db.leads.delete_many({"pro_id": pro_doc["_id"]})

    ineligible_id = ObjectId()
    await db.leads.insert_one(
        {
            "_id": ineligible_id,
            "status": LeadStatus.PENDING_ADMIN_REVIEW,
            "issue_type": "צריך חשמלאי דחוף",  # electrician-only, pro is a plumber
            "city": "תל אביב",
            "created_at": datetime.now(timezone.utc) - timedelta(minutes=90),
        }
    )
    eligible_id = ObjectId()
    await db.leads.insert_one(
        {
            "_id": eligible_id,
            "status": LeadStatus.PENDING_ADMIN_REVIEW,
            "issue_type": "נזילה",  # no profession named -> no constraint
            "city": "תל אביב",
            "created_at": datetime.now(timezone.utc) - timedelta(minutes=80),
        }
    )

    with patch(
        "app.services.pro_flow.get_redis_client",
        new_callable=AsyncMock,
        return_value=redis,
    ):
        result = await _handle_search(plumber_pro, chat_id, mock_wa)

    assert "נזילה" in result
    ineligible = await db.leads.find_one({"_id": ineligible_id})
    assert ineligible["status"] == LeadStatus.PENDING_ADMIN_REVIEW
    assert "pro_id" not in ineligible
    eligible = await db.leads.find_one({"_id": eligible_id})
    assert eligible["status"] == LeadStatus.NEW
    assert eligible["pro_id"] == pro_doc["_id"]


# --- Help command does not clear context ---


@pytest.mark.asyncio
async def test_help_does_not_clear_context(pro_setup, mock_wa, mock_lm, monkeypatch):
    """Sending 'עזרה' returns the dashboard without touching ContextManager."""
    pro_doc, _ = pro_setup
    chat_id = f"{PRO_PHONE}@c.us"

    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=None)
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    mock_ctx = MagicMock()
    mock_ctx.clear_context = AsyncMock()
    monkeypatch.setattr(app.services.pro_flow, "ContextManager", mock_ctx)

    result = await handle_pro_text_command(chat_id, "עזרה", mock_wa, mock_lm)

    assert result == Messages.Pro.HELP_MENU  # עזרה → HELP_MENU, not dashboard
    mock_ctx.clear_context.assert_not_called()


# --- Contextual dashboard ---


@pytest.mark.asyncio
async def test_dashboard_omits_approve_when_no_pending(
    pro_setup, mock_wa, mock_lm, monkeypatch
):
    """No NEW leads → 'אשר'/'דחה' line absent from dashboard."""
    pro_doc, db = pro_setup
    chat_id = f"{PRO_PHONE}@c.us"
    await db.leads.delete_many({"pro_id": PRO_ID})

    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=None)
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    result = await handle_pro_text_command(chat_id, "תפריט", mock_wa, mock_lm)

    assert "יוסי אינסטלציה" in result
    assert "אשר" not in result


@pytest.mark.asyncio
async def test_dashboard_includes_approve_when_pending(
    pro_setup, mock_wa, mock_lm, monkeypatch
):
    """A NEW lead present → dashboard shows 'אשר'/'דחה'."""
    pro_doc, db = pro_setup
    chat_id = f"{PRO_PHONE}@c.us"

    await db.leads.insert_one(
        {
            "_id": ObjectId(),
            "pro_id": PRO_ID,
            "status": LeadStatus.NEW,
            "chat_id": "customer@c.us",
            "created_at": datetime.now(timezone.utc),
        }
    )

    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=None)
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    result = await handle_pro_text_command(chat_id, "תפריט", mock_wa, mock_lm)

    assert "אשר" in result


@pytest.mark.asyncio
async def test_dashboard_omits_finish_when_no_booked(
    pro_setup, mock_wa, mock_lm, monkeypatch
):
    """No BOOKED leads → 'סיימתי'/'פרטים'/'ביטול' absent."""
    pro_doc, db = pro_setup
    chat_id = f"{PRO_PHONE}@c.us"
    await db.leads.delete_many({"pro_id": PRO_ID})

    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=None)
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    result = await handle_pro_text_command(chat_id, "תפריט", mock_wa, mock_lm)

    assert "סיימתי" not in result
    assert "פרטים" not in result
    assert "ביטול עבודה" not in result  # dashboard cancel cmd text


@pytest.mark.asyncio
async def test_dashboard_includes_finish_when_booked(
    pro_setup, mock_wa, mock_lm, monkeypatch
):
    """At least one BOOKED lead → dashboard shows finish/details/cancel commands."""
    pro_doc, db = pro_setup
    chat_id = f"{PRO_PHONE}@c.us"

    await db.leads.insert_one(
        {
            "_id": ObjectId(),
            "pro_id": PRO_ID,
            "status": LeadStatus.BOOKED,
            "chat_id": "customer@c.us",
            "created_at": datetime.now(timezone.utc),
        }
    )

    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=None)
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    result = await handle_pro_text_command(chat_id, "תפריט", mock_wa, mock_lm)

    assert "סיימתי" in result
    assert "פרטים" in result
    assert "ביטול" in result


# --- 'חפש' synonym for search ---


@pytest.mark.asyncio
async def test_search_via_chapesh_synonym(pro_setup, mock_wa):
    """Typing 'חפש' (not 'מצא') reaches _handle_search with rate-limit behavior."""
    pro_doc, db = pro_setup
    chat_id = f"{PRO_PHONE}@c.us"
    redis, store = _make_mock_redis()
    # PRO-123: this test asserts NO_STUCK_LEADS, so it has to guarantee that
    # precondition — clear both the pro's active leads (the load gate would
    # preempt the search) and the PENDING_ADMIN_REVIEW queue itself, which an
    # earlier test in this file deliberately leaves a lead sitting in.
    await db.leads.delete_many({"pro_id": pro_doc["_id"]})
    await db.leads.delete_many({"status": LeadStatus.PENDING_ADMIN_REVIEW})

    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=None)

    with patch(
        "app.services.pro_flow.get_redis_client",
        new_callable=AsyncMock,
        return_value=redis,
    ), patch.object(app.services.pro_flow, "StateManager", mock_state):
        result = await handle_pro_text_command(chat_id, "חפש", mock_wa, MagicMock())

    assert result == Messages.Pro.NO_STUCK_LEADS
    assert f"rate_limit:pro_search:{chat_id}" in store


# --- 'פרטים' command ---


@pytest.mark.asyncio
async def test_details_command_lists_booked_only(
    pro_setup, mock_wa, mock_lm, monkeypatch
):
    """'פרטים' returns only BOOKED leads with phone/city/issue."""
    pro_doc, db = pro_setup
    chat_id = f"{PRO_PHONE}@c.us"

    await db.leads.insert_one(
        {
            "_id": ObjectId(),
            "pro_id": PRO_ID,
            "status": LeadStatus.NEW,
            "customer_phone": "972501111111",
            "city": "חיפה",
            "issue_type": "נזילה",
            "chat_id": "c1@c.us",
            "created_at": datetime.now(timezone.utc),
        }
    )
    await db.leads.insert_one(
        {
            "_id": ObjectId(),
            "pro_id": PRO_ID,
            "status": LeadStatus.BOOKED,
            "customer_phone": "972502222222",
            "city": "תל אביב",
            "issue_type": "חשמל",
            "appointment_time": "ראשון 10:00",
            "chat_id": "c2@c.us",
            "created_at": datetime.now(timezone.utc),
        }
    )
    await db.leads.insert_one(
        {
            "_id": ObjectId(),
            "pro_id": PRO_ID,
            "status": LeadStatus.BOOKED,
            "customer_phone": "972503333333",
            "city": "ירושלים",
            "issue_type": "אינסטלציה",
            "appointment_time": "שני 14:00",
            "chat_id": "c3@c.us",
            "created_at": datetime.now(timezone.utc),
        }
    )

    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=None)
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    result = await handle_pro_text_command(chat_id, "פרטים", mock_wa, mock_lm)

    assert "תל אביב" in result
    assert "ירושלים" in result
    assert "חיפה" not in result  # NEW lead excluded
    assert "חשמל" in result
    assert "אינסטלציה" in result


@pytest.mark.asyncio
async def test_details_empty(pro_setup, mock_wa, mock_lm, monkeypatch):
    """No BOOKED leads → NO_ACTIVE_JOBS_LIST returned."""
    pro_doc, db = pro_setup
    chat_id = f"{PRO_PHONE}@c.us"
    await db.leads.delete_many({"pro_id": PRO_ID, "status": LeadStatus.BOOKED})

    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=None)
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    result = await handle_pro_text_command(chat_id, "פרטים", mock_wa, mock_lm)

    assert result == Messages.Pro.NO_ACTIVE_JOBS_LIST


# --- 'ביטול' command ---


@pytest.mark.asyncio
async def test_cancel_single_booked_immediate(pro_setup, mock_wa, mock_lm, monkeypatch):
    """Single BOOKED lead: typing 'ביטול' cancels immediately without FSM."""
    pro_doc, db = pro_setup
    await db.leads.delete_many({"pro_id": PRO_ID, "status": LeadStatus.BOOKED})
    chat_id = f"{PRO_PHONE}@c.us"
    customer_chat = "customer@c.us"
    lead_id = ObjectId()

    await db.leads.insert_one(
        {
            "_id": lead_id,
            "pro_id": PRO_ID,
            "status": LeadStatus.BOOKED,
            "chat_id": customer_chat,
            "customer_name": "דני",
            "city": "תל אביב",
            "issue_type": "נזילה",
            "created_at": datetime.now(timezone.utc),
        }
    )

    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=None)
    mock_state.set_state = AsyncMock()
    mock_state.clear_state = AsyncMock()
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    mock_ctx = MagicMock()
    mock_ctx.clear_context = AsyncMock()
    monkeypatch.setattr(app.services.pro_flow, "ContextManager", mock_ctx)

    result = await handle_pro_text_command(chat_id, "ביטול", mock_wa, mock_lm)

    assert result == Messages.Pro.CANCEL_SUCCESS
    updated = await db.leads.find_one({"_id": lead_id})
    assert updated["status"] == LeadStatus.CANCELLED
    assert updated["cancel_reason"] == "pro_cancelled"
    mock_wa.send_message.assert_called_once_with(
        customer_chat, Messages.Customer.PRO_CANCELLED_BOOKING
    )


@pytest.mark.asyncio
async def test_cancel_multiple_booked_enters_selection(
    pro_setup, mock_wa, mock_lm, monkeypatch
):
    """Two BOOKED leads: typing 'ביטול' enters PRO_SELECTING_JOB_TO_CANCEL state."""
    pro_doc, db = pro_setup
    chat_id = f"{PRO_PHONE}@c.us"

    for i in range(2):
        await db.leads.insert_one(
            {
                "_id": ObjectId(),
                "pro_id": PRO_ID,
                "status": LeadStatus.BOOKED,
                "chat_id": f"customer{i}@c.us",
                "customer_name": f"לקוח {i}",
                "city": "חיפה",
                "issue_type": "חשמל",
                "created_at": datetime.now(timezone.utc),
            }
        )

    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=None)
    mock_state.set_state = AsyncMock()
    mock_state.set_metadata = AsyncMock()
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    result = await handle_pro_text_command(chat_id, "ביטול", mock_wa, mock_lm)

    mock_state.set_state.assert_called_with(
        chat_id, UserStates.PRO_SELECTING_JOB_TO_CANCEL
    )
    assert "1." in result
    assert "2." in result


@pytest.mark.asyncio
async def test_cancel_selection_executes_cancel(
    pro_setup, mock_wa, mock_lm, monkeypatch
):
    """In PRO_SELECTING_JOB_TO_CANCEL, typing '1' cancels that lead and clears state."""
    pro_doc, db = pro_setup
    chat_id = f"{PRO_PHONE}@c.us"
    customer_chat = "customer_sel@c.us"
    lead_id = ObjectId()

    await db.leads.insert_one(
        {
            "_id": lead_id,
            "pro_id": PRO_ID,
            "status": LeadStatus.BOOKED,
            "chat_id": customer_chat,
            "customer_name": "עמית",
            "city": "רמת גן",
            "issue_type": "מנעול",
            "created_at": datetime.now(timezone.utc),
        }
    )

    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(
        return_value=UserStates.PRO_SELECTING_JOB_TO_CANCEL
    )
    mock_state.get_metadata = AsyncMock(
        return_value={"cancelling_jobs_context": {"1": str(lead_id)}}
    )
    mock_state.clear_state = AsyncMock()
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    mock_ctx = MagicMock()
    mock_ctx.clear_context = AsyncMock()
    monkeypatch.setattr(app.services.pro_flow, "ContextManager", mock_ctx)

    result = await handle_pro_text_command(chat_id, "1", mock_wa, mock_lm)

    assert result == Messages.Pro.CANCEL_SUCCESS
    updated = await db.leads.find_one({"_id": lead_id})
    assert updated["status"] == LeadStatus.CANCELLED
    mock_state.clear_state.assert_any_call(chat_id)
    mock_wa.send_message.assert_called_once_with(
        customer_chat, Messages.Customer.PRO_CANCELLED_BOOKING
    )


@pytest.mark.asyncio
async def test_cancel_selection_abort(pro_setup, mock_wa, mock_lm, monkeypatch):
    """In PRO_SELECTING_JOB_TO_CANCEL, typing 'ביטול' aborts without modifying any lead."""
    pro_doc, db = pro_setup
    chat_id = f"{PRO_PHONE}@c.us"
    lead_id = ObjectId()

    await db.leads.insert_one(
        {
            "_id": lead_id,
            "pro_id": PRO_ID,
            "status": LeadStatus.BOOKED,
            "chat_id": "cust@c.us",
            "created_at": datetime.now(timezone.utc),
        }
    )

    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(
        return_value=UserStates.PRO_SELECTING_JOB_TO_CANCEL
    )
    mock_state.get_metadata = AsyncMock(
        return_value={"cancelling_jobs_context": {"1": str(lead_id)}}
    )
    mock_state.clear_state = AsyncMock()
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    result = await handle_pro_text_command(chat_id, "ביטול", mock_wa, mock_lm)

    assert result == Messages.Pro.ACTION_CANCELLED
    unchanged = await db.leads.find_one({"_id": lead_id})
    assert unchanged["status"] == LeadStatus.BOOKED


# --- HELP_MENU (עזרה) ---


@pytest.mark.asyncio
async def test_help_returns_help_menu_not_dashboard(
    pro_setup, mock_wa, mock_lm, monkeypatch
):
    """Typing 'עזרה' returns the HELP_MENU command dictionary, not the dashboard."""
    pro_doc, _ = pro_setup
    chat_id = f"{PRO_PHONE}@c.us"

    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=None)
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    result = await handle_pro_text_command(chat_id, "עזרה", mock_wa, mock_lm)

    assert result == Messages.Pro.HELP_MENU
    assert "אשר" in result
    assert "סיכום" in result
    assert "תפריט" in result  # the CMD_MENU row


@pytest.mark.asyncio
async def test_menu_returns_dashboard_not_help_menu(
    pro_setup, mock_wa, mock_lm, monkeypatch
):
    """Typing 'תפריט' returns the contextual dashboard, not the HELP_MENU."""
    pro_doc, db = pro_setup
    await db.leads.delete_many({"pro_id": PRO_ID})
    chat_id = f"{PRO_PHONE}@c.us"

    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=None)
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    result = await handle_pro_text_command(chat_id, "תפריט", mock_wa, mock_lm)

    assert result != Messages.Pro.HELP_MENU
    assert "יוסי אינסטלציה" in result
    assert Messages.Pro.DASHBOARD_TIP.strip() in result  # discovery tip


# --- Dashboard discovery tip ---


@pytest.mark.asyncio
async def test_dashboard_includes_discovery_tip(
    pro_setup, mock_wa, mock_lm, monkeypatch
):
    """Dashboard ('תפריט') appends the discovery tip at the bottom."""
    pro_doc, db = pro_setup
    await db.leads.delete_many({"pro_id": PRO_ID})
    chat_id = f"{PRO_PHONE}@c.us"

    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=None)
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    result = await handle_pro_text_command(chat_id, "תפריט", mock_wa, mock_lm)

    assert Messages.Pro.DASHBOARD_TIP.strip() in result


# --- Enhanced פרטים with links ---


@pytest.mark.asyncio
async def test_details_includes_whatsapp_and_waze_links(
    pro_setup, mock_wa, mock_lm, monkeypatch
):
    """פרטים row must contain a wa.me link and a waze link."""
    pro_doc, db = pro_setup
    await db.leads.delete_many({"pro_id": PRO_ID, "status": LeadStatus.BOOKED})
    chat_id = f"{PRO_PHONE}@c.us"

    await db.leads.insert_one(
        {
            "_id": ObjectId(),
            "pro_id": PRO_ID,
            "status": LeadStatus.BOOKED,
            "customer_phone": "972541234567",
            "city": "תל אביב",
            "street": "דיזנגוף",
            "issue_type": "נזילה",
            "appointment_time": "ראשון 10:00",
            "chat_id": "c@c.us",
            "created_at": datetime.now(timezone.utc),
        }
    )

    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=None)
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    result = await handle_pro_text_command(chat_id, "פרטים", mock_wa, mock_lm)

    assert "wa.me/972541234567" in result
    assert "waze.com/ul?q=" in result
    assert "נזילה" in result


# --- סיכום command ---


@pytest.mark.asyncio
async def test_summary_command(pro_setup, mock_wa, mock_lm, monkeypatch):
    """'סיכום' returns a motivating summary with completed/active/rating."""
    pro_doc, db = pro_setup
    await db.leads.delete_many({"pro_id": PRO_ID})
    chat_id = f"{PRO_PHONE}@c.us"

    now = datetime.now(timezone.utc)
    for _ in range(3):
        await db.leads.insert_one(
            {
                "_id": ObjectId(),
                "pro_id": PRO_ID,
                "status": LeadStatus.COMPLETED,
                "completed_at": now,
                "created_at": now,
            }
        )
    await db.leads.insert_one(
        {
            "_id": ObjectId(),
            "pro_id": PRO_ID,
            "status": LeadStatus.BOOKED,
            "created_at": now,
        }
    )

    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=None)
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    result = await handle_pro_text_command(chat_id, "סיכום", mock_wa, mock_lm)

    assert static_prefix(Messages.Pro.SUMMARY_BODY) in result
    assert "4.5" in result  # rating from pro_setup fixture


@pytest.mark.asyncio
async def test_summary_via_statistics_keyword(pro_setup, mock_wa, mock_lm, monkeypatch):
    """'סטטיסטיקה' also triggers the summary."""
    pro_doc, db = pro_setup
    await db.leads.delete_many({"pro_id": PRO_ID})
    chat_id = f"{PRO_PHONE}@c.us"

    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=None)
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    result = await handle_pro_text_command(chat_id, "סטטיסטיקה", mock_wa, mock_lm)

    assert static_prefix(Messages.Pro.SUMMARY_BODY) in result


# --- Enhanced ביקורות ---


@pytest.mark.asyncio
async def test_reviews_returns_text_reviews(pro_setup, mock_wa, mock_lm, monkeypatch):
    """'ביקורות' shows last 3 reviews with comment text from reviews_collection."""
    pro_doc, db = pro_setup
    chat_id = f"{PRO_PHONE}@c.us"
    now = datetime.now(timezone.utc)

    await db.reviews.insert_one(
        {
            "_id": ObjectId(),
            "pro_id": PRO_ID,
            "customer_chat_id": "c1@c.us",
            "rating": 5,
            "comment": "שירות מצוין!",
            "created_at": now,
        }
    )
    await db.reviews.insert_one(
        {
            "_id": ObjectId(),
            "pro_id": PRO_ID,
            "customer_chat_id": "c2@c.us",
            "rating": 4,
            "comment": "מגיע בזמן",
            "created_at": now,
        }
    )

    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=None)
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    result = await handle_pro_text_command(chat_id, "ביקורות", mock_wa, mock_lm)

    assert "שירות מצוין!" in result
    assert "מגיע בזמן" in result


@pytest.mark.asyncio
async def test_reviews_no_text_reviews(pro_setup, mock_wa, mock_lm, monkeypatch):
    """No text reviews in reviews_collection → polite no-reviews message."""
    pro_doc, db = pro_setup
    chat_id = f"{PRO_PHONE}@c.us"
    await db.reviews.delete_many({"pro_id": PRO_ID})

    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=None)
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    result = await handle_pro_text_command(chat_id, "ביקורות", mock_wa, mock_lm)

    assert result == Messages.Pro.NO_REVIEWS_WITH_TEXT


@pytest.mark.asyncio
async def test_reviews_via_feedback_keyword(pro_setup, mock_wa, mock_lm, monkeypatch):
    """'פידבק' also routes to the reviews handler."""
    pro_doc, db = pro_setup
    chat_id = f"{PRO_PHONE}@c.us"
    await db.reviews.delete_many({"pro_id": PRO_ID})

    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=None)
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    result = await handle_pro_text_command(chat_id, "פידבק", mock_wa, mock_lm)

    assert result == Messages.Pro.NO_REVIEWS_WITH_TEXT


# --- PRO-168: 'עדיין עובד' (still working) --------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("keyword", Messages.Keywords.STILL_WORKING_COMMANDS)
async def test_still_working_keyword_variants_silence_reminders_on_all_booked_leads(
    pro_setup, mock_wa, mock_lm, monkeypatch, keyword
):
    """PRO-168: `Pro.REMINDER` advertises 'עדיין עובד' as the way to stop the
    finish-nudges. Every spelling in STILL_WORKING_COMMANDS must reach
    `_handle_still_working`, which silences the reminder counters on *every*
    BOOKED lead of this pro — the reminder never names a lead, so neither can
    the reply to it."""
    pro_doc, db = pro_setup
    chat_id = f"{PRO_PHONE}@c.us"
    await db.leads.delete_many({"pro_id": PRO_ID})

    lead_a = ObjectId()
    lead_b = ObjectId()
    await db.leads.insert_many(
        [
            {
                "_id": lead_a,
                "pro_id": PRO_ID,
                "status": LeadStatus.BOOKED,
                "chat_id": "972501111111@c.us",
                "created_at": datetime.now(timezone.utc),
            },
            {
                "_id": lead_b,
                "pro_id": PRO_ID,
                "status": LeadStatus.BOOKED,
                "chat_id": "972502222222@c.us",
                "created_at": datetime.now(timezone.utc),
            },
        ]
    )

    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=None)
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    result = await handle_pro_text_command(chat_id, keyword, mock_wa, mock_lm)

    assert result == Messages.Pro.STILL_WORKING_ACK
    for lead_id in (lead_a, lead_b):
        lead = await db.leads.find_one({"_id": lead_id})
        assert lead["reminder_sent_count"] == WorkerConstants.MAX_PRO_REMINDERS
        assert lead["reminders_sent"] == WorkerConstants.MAX_PRO_REMINDERS


# --- PRO-168: HELP_MENU / dashboard keyword truthfulness -------------------


def test_every_cmd_keyword_has_a_dispatcher_list():
    """PRO-168 §7 regression guard for the original 'עדיין עובד' defect:
    `Pro.REMINDER` advertised a keyword that matched no `Messages.Keywords`
    list, so replying with it silently fell through to the dashboard. This
    walks every `*keyword*` token in every canonical `Messages.Pro.CMD_*` row
    — what both `HELP_MENU` and the dashboard are built from — and asserts
    each one is matched by some keyword list `handle_pro_text_command`
    actually dispatches on (`pro_flow._PRO_COMMAND_LISTS`)."""
    cmd_names = [name for name in dir(Messages.Pro) if name.startswith("CMD_")]
    assert cmd_names, "no CMD_* rows found on Messages.Pro"

    dispatched = set()
    for list_name in app.services.pro_flow._PRO_COMMAND_LISTS:
        dispatched.update(getattr(Messages.Keywords, list_name))

    for name in cmd_names:
        row = getattr(Messages.Pro, name)
        tokens = re.findall(r"\*(.+?)\*", row)
        assert tokens, f"Messages.Pro.{name} has no *keyword* token: {row!r}"
        keyword = tokens[0]
        assert keyword in dispatched, (
            f"Messages.Pro.{name} advertises {keyword!r} but no entry in any "
            f"pro_flow._PRO_COMMAND_LISTS list matches it — a pro replying "
            f"with it would silently fall through to the dashboard."
        )


def test_help_menu_advertises_every_cmd_row_the_dashboard_can_show():
    """HELP_MENU and the dashboard both render `Messages.Pro.CMD_*` rows —
    PRO-168 built them from the same source specifically so the two menus
    cannot drift apart. Every row the dashboard is capable of showing must
    also appear, verbatim, somewhere in HELP_MENU."""
    dashboard_rows = (
        Messages.Pro.CMD_APPROVE,
        Messages.Pro.CMD_REJECT,
        Messages.Pro.CMD_FINISH,
        Messages.Pro.CMD_DETAILS,
        Messages.Pro.CMD_CANCEL,
        Messages.Pro.CMD_SEARCH,
        Messages.Pro.CMD_PAUSE,
        Messages.Pro.CMD_RESUME,
    )
    for row in dashboard_rows:
        assert row in Messages.Pro.HELP_MENU


@pytest.mark.asyncio
async def test_dashboard_shows_pause_command_when_pro_is_active(
    pro_setup, mock_wa, mock_lm, monkeypatch
):
    """An active pro sees CMD_PAUSE (an offer to stop taking work), not CMD_RESUME."""
    pro_doc, db = pro_setup
    chat_id = f"{PRO_PHONE}@c.us"
    await db.users.update_one({"_id": PRO_ID}, {"$set": {"is_active": True}})

    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=None)
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    result = await handle_pro_text_command(chat_id, "תפריט", mock_wa, mock_lm)

    assert Messages.Pro.CMD_PAUSE in result
    assert Messages.Pro.CMD_RESUME not in result


@pytest.mark.asyncio
async def test_dashboard_shows_resume_command_when_pro_is_paused(
    pro_setup, mock_wa, mock_lm, monkeypatch
):
    """A paused pro sees CMD_RESUME (the way back in), not CMD_PAUSE."""
    pro_doc, db = pro_setup
    chat_id = f"{PRO_PHONE}@c.us"
    await db.users.update_one({"_id": PRO_ID}, {"$set": {"is_active": False}})

    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=None)
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    result = await handle_pro_text_command(chat_id, "תפריט", mock_wa, mock_lm)

    assert Messages.Pro.CMD_RESUME in result
    assert Messages.Pro.CMD_PAUSE not in result
