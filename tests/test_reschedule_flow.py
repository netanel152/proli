"""
Tests for handle_reschedule_selection in customer_flow.py.

A BOOKED customer who picks a slot from the reschedule menu (state
AWAITING_RESCHEDULE_TIME) atomically claims the new slot, frees the old one,
updates the lead, and notifies the pro. Edge cases: cancel keyword, invalid
choice, slot already taken (race: a fresh list, or the picker closes when nothing
is left), a repeated reschedule phrase, and missing booked lead.

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
from app.core.constants import LeadStatus, UserStates, WorkerConstants
from app.core.messages import Messages


@pytest.fixture
def reschedule_env(monkeypatch, mock_db):
    monkeypatch.setattr(customer_flow, "slots_collection", mock_db.slots)

    mock_state = MagicMock()
    mock_state.clear_state = AsyncMock()
    mock_state.set_state = AsyncMock()
    mock_state.set_metadata = AsyncMock()
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
    # PRO-45: the datetime moves with the booking, not just the display
    # string -- otherwise a job moved from Monday to Friday would still read
    # as past-due to the no-show report's "has the appointment come due" gate.
    assert lead["appointment_datetime"] == new_slot["start_time"]
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
async def test_advertised_bitul_keyword_cancels_reschedule_booking_unchanged(
    reschedule_env,
):
    """Sibling regression to PRO-118's 'לבטל' gap: RESCHEDULE_OFFER literally
    tells the customer to reply 'ביטול' to keep the current appointment.
    Before PRO-118, substring matching caught this by accident ('בטל' ⊂
    'ביטול'); 'ביטול' is now an explicit CANCEL_KEYWORDS entry. The existing
    booking (lead + slot) must be untouched — this only aborts the
    reschedule flow, it never cancels the job itself."""
    db, mock_state, mock_wa = reschedule_env
    await db.leads.delete_many({})
    await db.slots.delete_many({})

    pro_id = ObjectId()
    await db.users.insert_one(
        {"_id": pro_id, "phone_number": "972501234567", "business_name": "יוסי"}
    )
    old_slot_id = ObjectId()
    await db.slots.insert_one({"_id": old_slot_id, "is_taken": True})
    lead_id = await _seed_booked_lead(db, pro_id=pro_id, old_slot_id=old_slot_id)
    mock_state.get_metadata.return_value = {
        "reschedule_slots_context": {"1": str(ObjectId())}
    }

    await handle_reschedule_selection("customer@c.us", "ביטול", mock_wa)

    mock_state.clear_state.assert_awaited_once_with("customer@c.us")
    assert _sent_text(mock_wa) == Messages.Customer.RESCHEDULE_CANCELLED

    # Booking (lead + its slot) is untouched — only the reschedule flow aborted
    lead = await db.leads.find_one({"_id": lead_id})
    slot = await db.slots.find_one({"_id": old_slot_id})
    assert lead["status"] == LeadStatus.BOOKED
    assert lead["booked_slot_id"] == old_slot_id
    assert slot["is_taken"] is True


@pytest.mark.asyncio
async def test_innocent_beteut_sentence_does_not_cancel_reschedule(reschedule_env):
    """PRO-118: 'בטעות' only contains 'טעות' as a substring — since the bare
    'טעות' keyword was dropped from CANCEL_KEYWORDS entirely, and
    contains_keyword only matches whole tokens, this sentence must fall
    through to the normal slot-selection path instead of cancelling."""
    db, mock_state, mock_wa = reschedule_env
    mock_state.get_metadata.return_value = {
        "reschedule_slots_context": {"1": str(ObjectId())}
    }

    await handle_reschedule_selection(
        "customer@c.us", "בטעות בחרתי את הזמן הלא נכון", mock_wa
    )

    mock_state.clear_state.assert_not_called()
    assert _sent_text(mock_wa) != Messages.Customer.RESCHEDULE_CANCELLED


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
async def test_invalid_choice_names_the_exit(reschedule_env):
    """The re-prompt says how to leave: ביטול, which this handler reads as
    "keep the current time"."""
    assert "ביטול" in Messages.Customer.RESCHEDULE_INVALID_CHOICE
    assert "ביטול" in Messages.Keywords.CANCEL_KEYWORDS


@pytest.mark.asyncio
async def test_slot_taken_meanwhile_sends_a_fresh_list(reschedule_env):
    """A valid number whose slot someone else took gets a rebuilt list, not
    the invalid-choice line, and the picker re-arms with the new mapping."""
    db, mock_state, mock_wa = reschedule_env
    await db.leads.delete_many({})
    await db.slots.delete_many({})

    pro_id = ObjectId()
    taken_slot_id = ObjectId()
    free_slot_id = ObjectId()
    await db.slots.insert_many(
        [
            {
                "_id": taken_slot_id,
                "pro_id": pro_id,
                "is_taken": True,  # claimed by someone else after the list went out
                "start_time": datetime(2099, 2, 1, 12, 0),
            },
            {
                "_id": free_slot_id,
                "pro_id": pro_id,
                "is_taken": False,
                "start_time": datetime(2099, 2, 1, 14, 0),
            },
        ]
    )
    lead_id = await _seed_booked_lead(db, pro_id=pro_id)
    mock_state.get_metadata.return_value = {
        "reschedule_slots_context": {"1": str(taken_slot_id)}
    }

    await handle_reschedule_selection("customer@c.us", "1", mock_wa)

    sent = _sent_text(mock_wa)
    assert Messages.Customer.RESCHEDULE_INVALID_CHOICE not in sent
    assert sent.startswith(Messages.Customer.RESCHEDULE_SLOT_TAKEN.split("{")[0])
    assert "1. 01/02/2099 16:00" in sent  # naive UTC 14:00 is 16:00 in Israel
    mock_state.clear_state.assert_not_called()
    mock_state.set_state.assert_awaited_once_with(
        "customer@c.us",
        UserStates.AWAITING_RESCHEDULE_TIME,
        ttl=WorkerConstants.RESCHEDULE_PICK_TTL_SECONDS,
    )
    mock_state.set_metadata.assert_awaited_once_with(
        "customer@c.us", {"reschedule_slots_context": {"1": str(free_slot_id)}}
    )
    lead = await db.leads.find_one({"_id": lead_id})
    assert lead.get("rescheduled_count") is None


@pytest.mark.asyncio
async def test_slot_taken_meanwhile_with_nothing_left_closes_the_picker(
    reschedule_env,
):
    db, mock_state, mock_wa = reschedule_env
    await db.leads.delete_many({})
    await db.slots.delete_many({})

    pro_id = ObjectId()
    taken_slot_id = ObjectId()
    await db.slots.insert_one(
        {
            "_id": taken_slot_id,
            "pro_id": pro_id,
            "is_taken": True,  # already claimed by someone else
            "start_time": datetime(2099, 2, 1, 12, 0),
        }
    )
    await _seed_booked_lead(db, pro_id=pro_id)
    mock_state.get_metadata.return_value = {
        "reschedule_slots_context": {"1": str(taken_slot_id)}
    }

    await handle_reschedule_selection("customer@c.us", "1", mock_wa)

    assert _sent_text(mock_wa) == Messages.Customer.RESCHEDULE_SLOT_TAKEN_NO_SLOTS
    mock_state.clear_state.assert_awaited_once_with("customer@c.us")
    mock_state.set_state.assert_not_called()


@pytest.mark.asyncio
async def test_slot_that_fails_the_overlap_check_is_not_offered_again(
    reschedule_env,
):
    """PRO-277's overlap check gives a clashing slot back as free, so the query
    alone would list the very slot that just failed."""
    db, mock_state, mock_wa = reschedule_env
    await db.leads.delete_many({})
    await db.slots.delete_many({})

    pro_id = ObjectId()
    clashing_id = ObjectId()
    await db.slots.insert_many(
        [
            {
                "_id": ObjectId(),
                "pro_id": pro_id,
                "is_taken": True,
                "start_time": datetime(2099, 2, 1, 12, 0),
                "end_time": datetime(2099, 2, 1, 14, 0),
            },
            {
                "_id": clashing_id,
                "pro_id": pro_id,
                "is_taken": False,
                "start_time": datetime(2099, 2, 1, 13, 0),
                "end_time": datetime(2099, 2, 1, 14, 0),
            },
        ]
    )
    await _seed_booked_lead(db, pro_id=pro_id)
    mock_state.get_metadata.return_value = {
        "reschedule_slots_context": {"1": str(clashing_id)}
    }

    await handle_reschedule_selection("customer@c.us", "1", mock_wa)

    assert _sent_text(mock_wa) == Messages.Customer.RESCHEDULE_SLOT_TAKEN_NO_SLOTS
    assert (await db.slots.find_one({"_id": clashing_id}))["is_taken"] is False


@pytest.mark.asyncio
async def test_repeated_reschedule_phrase_resends_the_list(reschedule_env):
    db, mock_state, mock_wa = reschedule_env
    await db.leads.delete_many({})
    await db.slots.delete_many({})

    pro_id = ObjectId()
    free_slot_id = ObjectId()
    await db.slots.insert_one(
        {
            "_id": free_slot_id,
            "pro_id": pro_id,
            "is_taken": False,
            "start_time": datetime(2099, 2, 1, 14, 0),
        }
    )
    await _seed_booked_lead(db, pro_id=pro_id)
    mock_state.get_metadata.return_value = {
        "reschedule_slots_context": {"1": str(ObjectId())}
    }

    await handle_reschedule_selection("customer@c.us", "אני צריך מועד אחר", mock_wa)

    sent = _sent_text(mock_wa)
    assert Messages.Customer.RESCHEDULE_INVALID_CHOICE not in sent
    assert sent.startswith(Messages.Customer.RESCHEDULE_OFFER.split("{")[0])
    mock_state.set_metadata.assert_awaited_once_with(
        "customer@c.us", {"reschedule_slots_context": {"1": str(free_slot_id)}}
    )
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
