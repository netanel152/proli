"""
Tests for handle_reschedule_selection in customer_flow.py.

A BOOKED customer who picks a slot from the reschedule menu (state
AWAITING_RESCHEDULE_TIME) atomically claims the new slot, frees the old one,
updates the lead, and notifies the pro. Edge cases: cancel keyword, invalid
choice, slot already taken (race), and missing booked lead.

customer_flow imports slots_collection at module level but conftest only patches
its users/leads/reviews, so each test monkeypatches slots_collection too.
StateManager is Redis-backed → replaced with a mock (test_pro_flow.py pattern).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone
from bson import ObjectId

import app.services.customer_flow as customer_flow
from app.services.customer_flow import handle_reschedule_selection
from app.core.constants import LeadStatus
from app.core.messages import Messages


@pytest.fixture
def reschedule_env(monkeypatch, mock_db):
    monkeypatch.setattr(customer_flow, "slots_collection", mock_db.slots)

    mock_state = MagicMock()
    mock_state.clear_state = AsyncMock()
    mock_state.get_metadata = AsyncMock(return_value={})
    monkeypatch.setattr(customer_flow, "StateManager", mock_state)

    mock_wa = MagicMock()
    mock_wa.send_message = AsyncMock()

    return mock_db, mock_state, mock_wa


async def _seed_booked_lead(db, *, pro_id=None, old_slot_id=None):
    lead_id = ObjectId()
    doc = {
        "_id": lead_id,
        "chat_id": "customer@c.us",
        "status": LeadStatus.BOOKED,
        "appointment_time": "01/01/2026 09:00",
        "full_address": "תל אביב",
        "customer_name": "דנה",
    }
    if pro_id:
        doc["pro_id"] = pro_id
    if old_slot_id:
        doc["booked_slot_id"] = old_slot_id
    await db.leads.insert_one(doc)
    return lead_id


def _sent_text(mock_wa):
    return " ".join(str(c.args[1]) for c in mock_wa.send_message.call_args_list)


@pytest.mark.asyncio
async def test_valid_pick_reschedules_and_notifies_pro(reschedule_env):
    db, mock_state, mock_wa = reschedule_env
    await db.leads.delete_many({})
    await db.slots.delete_many({})

    pro_id = ObjectId()
    await db.users.insert_one(
        {"_id": pro_id, "phone_number": "972501234567", "business_name": "יוסי"}
    )

    old_slot_id = ObjectId()
    await db.slots.insert_one({"_id": old_slot_id, "is_taken": True})
    new_slot_id = ObjectId()
    await db.slots.insert_one(
        {
            "_id": new_slot_id,
            "is_taken": False,
            "start_time": datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc),
        }
    )

    lead_id = await _seed_booked_lead(db, pro_id=pro_id, old_slot_id=old_slot_id)
    mock_state.get_metadata.return_value = {
        "reschedule_slots_context": {"1": str(new_slot_id)}
    }

    await handle_reschedule_selection("customer@c.us", "1", mock_wa)

    new_slot = await db.slots.find_one({"_id": new_slot_id})
    old_slot = await db.slots.find_one({"_id": old_slot_id})
    lead = await db.leads.find_one({"_id": lead_id})
    assert new_slot["is_taken"] is True
    assert old_slot["is_taken"] is False
    assert lead["booked_slot_id"] == new_slot_id
    assert lead["rescheduled_count"] == 1
    mock_state.clear_state.assert_awaited_once_with("customer@c.us")
    # Customer success + pro notification both sent
    assert mock_wa.send_message.call_count == 2
    sent = _sent_text(mock_wa)
    assert "שונה בהצלחה" in sent


@pytest.mark.asyncio
async def test_cancel_keyword_keeps_appointment(reschedule_env):
    db, mock_state, mock_wa = reschedule_env

    await handle_reschedule_selection("customer@c.us", "בטל", mock_wa)

    mock_state.clear_state.assert_awaited_once_with("customer@c.us")
    assert _sent_text(mock_wa) == Messages.Customer.RESCHEDULE_CANCELLED


@pytest.mark.asyncio
async def test_invalid_choice_preserves_state(reschedule_env):
    db, mock_state, mock_wa = reschedule_env
    mock_state.get_metadata.return_value = {
        "reschedule_slots_context": {"1": str(ObjectId())}
    }

    await handle_reschedule_selection("customer@c.us", "9", mock_wa)

    assert Messages.Customer.RESCHEDULE_INVALID_CHOICE in _sent_text(mock_wa)
    mock_state.clear_state.assert_not_called()


@pytest.mark.asyncio
async def test_slot_already_taken_race_preserves_state(reschedule_env):
    db, mock_state, mock_wa = reschedule_env
    await db.leads.delete_many({})
    await db.slots.delete_many({})

    taken_slot_id = ObjectId()
    await db.slots.insert_one(
        {
            "_id": taken_slot_id,
            "is_taken": True,  # already claimed by someone else
            "start_time": datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc),
        }
    )
    await _seed_booked_lead(db)
    mock_state.get_metadata.return_value = {
        "reschedule_slots_context": {"1": str(taken_slot_id)}
    }

    await handle_reschedule_selection("customer@c.us", "1", mock_wa)

    assert Messages.Customer.RESCHEDULE_INVALID_CHOICE in _sent_text(mock_wa)
    mock_state.clear_state.assert_not_called()


@pytest.mark.asyncio
async def test_no_booked_lead_errors_and_clears(reschedule_env):
    db, mock_state, mock_wa = reschedule_env
    await db.leads.delete_many({})
    await db.slots.delete_many({})

    slot_id = ObjectId()
    await db.slots.insert_one(
        {
            "_id": slot_id,
            "is_taken": False,
            "start_time": datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc),
        }
    )
    mock_state.get_metadata.return_value = {
        "reschedule_slots_context": {"1": str(slot_id)}
    }

    await handle_reschedule_selection("customer@c.us", "1", mock_wa)

    assert Messages.Errors.GENERIC_ERROR in _sent_text(mock_wa)
    mock_state.clear_state.assert_awaited_once_with("customer@c.us")
