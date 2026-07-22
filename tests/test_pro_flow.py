"""
Tests for pro_flow.py: all professional text commands.
Covers: approve, reject, finish, active jobs, history, stats, reviews.
"""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from bson import ObjectId
from datetime import datetime, timedelta, timezone
from app.core.constants import LeadStatus, UserStates, WorkerConstants
from app.core.messages import Messages
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
    assert "הערכת המחיר" in customer_msg


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
    assert "הערכת המחיר" not in customer_msg
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


# --- Reject ---


@pytest.mark.asyncio
async def test_reject_lead(pro_setup, mock_wa, mock_lm):
    pro_doc, db = pro_setup
    await db.leads.insert_one(
        {
            "pro_id": pro_doc["_id"],
            "status": LeadStatus.NEW,
            "chat_id": "972501111111@c.us",
            "created_at": datetime.now(timezone.utc),
        }
    )

    result = await handle_pro_text_command("972500000000@c.us", "דחה", mock_wa, mock_lm)
    assert result == Messages.Pro.REJECT_SUCCESS
    mock_lm.update_lead_status.assert_called_once()


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

    assert "איזו עבודה סיימת?" in result
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
    assert "עבודות אחרונות" in result
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
    assert "סטטיסטיקות" in result
    assert "4.5" in result  # rating


# --- Reviews ---


@pytest.mark.asyncio
async def test_reviews_with_data(pro_setup, mock_wa, mock_lm):
    pro_doc, db = pro_setup
    # The _handle_reviews function matches reviews to leads via key = chat_id + rating
    chat_id = "972501234567@c.us"
    await db.leads.insert_one(
        {
            "pro_id": pro_doc["_id"],
            "status": LeadStatus.COMPLETED,
            "rating_given": 5,
            "chat_id": chat_id,
            "completed_at": datetime.now(timezone.utc),
            "created_at": datetime.now(timezone.utc),
        }
    )
    await db.reviews.insert_one(
        {
            "pro_id": pro_doc["_id"],
            "customer_chat_id": chat_id,
            "rating": 5,
            "comment": "שירות מצוין",
        }
    )

    result = await handle_pro_text_command(
        f"{PRO_PHONE}@c.us", "ביקורות", mock_wa, mock_lm
    )
    assert result is not None
    assert "דירוגים" in result or "ביקורות" in result.lower()
    assert "5" in result  # Rating value shown


@pytest.mark.asyncio
async def test_reviews_empty(pro_setup, mock_wa, mock_lm):
    pro_doc, db = pro_setup
    # Remove any text reviews so the handler returns the no-reviews message
    await db.reviews.delete_many({"pro_id": PRO_ID})

    result = await handle_pro_text_command(
        "972500000000@c.us", "ביקורות", mock_wa, mock_lm
    )
    assert result == Messages.Pro.NO_REVIEWS_WITH_TEXT


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
    assert "סטטוס: זמין" in result
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
    """Pro types 'דחה' -> lead rejected, customer state cleared."""
    pro_doc, db = pro_setup

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

    result = await handle_pro_text_command(f"{PRO_PHONE}@c.us", "דחה", mock_wa, mock_lm)

    assert result == Messages.Pro.REJECT_SUCCESS
    mock_lm.update_lead_status.assert_called_once()
    mock_state.clear_state.assert_called_with("972501111111@c.us")


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

    assert "חזר לפעולה" in result
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
    assert "השב *1*" in call_text
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
    pro_doc, _ = pro_setup
    chat_id = f"{PRO_PHONE}@c.us"
    redis, store = _make_mock_redis()

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
    pro_doc, _ = pro_setup
    chat_id = f"{PRO_PHONE}@c.us"
    redis, store = _make_mock_redis()
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
    assert "6" in sent_text  # math.ceil(360 / 60) == 6
    assert "דקות" in sent_text
    # setex must NOT be refreshed when already rate-limited
    redis.setex.assert_not_called()


@pytest.mark.asyncio
async def test_search_finds_stuck_lead_and_assigns(pro_setup, mock_wa):
    """Pending-admin-review lead: assigned to pro as NEW, cool-down set."""
    pro_doc, db = pro_setup
    chat_id = f"{PRO_PHONE}@c.us"
    redis, store = _make_mock_redis()

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
    pro_doc, _ = pro_setup
    chat_id = f"{PRO_PHONE}@c.us"
    redis, store = _make_mock_redis()

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

    assert result == "הפעולה בוטלה."
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
    assert "מדריך" in result
    assert "אשר" in result
    assert "סיכום" in result
    assert "תפריט" in result  # tip at the bottom


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
    assert "💡 טיפ" in result  # discovery tip


@pytest.mark.asyncio
async def test_help_does_not_clear_state_or_context(
    pro_setup, mock_wa, mock_lm, monkeypatch
):
    """'עזרה' must not touch StateManager or ContextManager."""
    pro_doc, _ = pro_setup
    chat_id = f"{PRO_PHONE}@c.us"

    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=None)
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    mock_ctx = MagicMock()
    mock_ctx.clear_context = AsyncMock()
    monkeypatch.setattr(app.services.pro_flow, "ContextManager", mock_ctx)

    await handle_pro_text_command(chat_id, "עזרה", mock_wa, mock_lm)

    mock_ctx.clear_context.assert_not_called()
    mock_state.clear_state = getattr(mock_state, "clear_state", None)
    # clear_state must not have been called
    if mock_state.clear_state:
        mock_state.clear_state.assert_not_called()


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

    assert "סיכום ביצועים" in result
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

    assert "סיכום ביצועים" in result


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
