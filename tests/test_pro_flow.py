"""
Tests for pro_flow.py: all professional text commands.
Covers: approve, reject, finish, active jobs, history, stats, reviews.
"""
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock
from bson import ObjectId
from datetime import datetime, timezone
from app.core.constants import LeadStatus
from app.core.messages import Messages
from app.services.pro_flow import handle_pro_text_command

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
    await db.leads.insert_one({
        "_id": lead_id,
        "pro_id": pro_doc["_id"],
        "status": LeadStatus.NEW,
        "chat_id": "972501111111@c.us",
        "issue_type": "נזילה",
        "full_address": "תל אביב, הרצל 10",
        "appointment_time": "10:00",
        "created_at": datetime.now(timezone.utc),
    })

    # Mock book_slot_for_lead
    import app.services.pro_flow
    monkeypatch.setattr(app.services.pro_flow, "book_slot_for_lead", AsyncMock(return_value=True))

    result = await handle_pro_text_command("972500000000@c.us", "אשר", mock_wa, mock_lm)

    assert Messages.Pro.APPROVE_SUCCESS in result
    mock_lm.update_lead_status.assert_called_once()
    # Customer should receive PRO_FOUND message
    mock_wa.send_message.assert_called_once()
    customer_msg = mock_wa.send_message.call_args.args[1]
    assert "יוסי אינסטלציה" in customer_msg


@pytest.mark.asyncio
async def test_approve_no_pending(mock_db, mock_wa, mock_lm):
    """Pro with no NEW leads -> NO_PENDING_APPROVE."""
    pro_id = ObjectId()
    existing = await mock_db.users.find_one({"phone_number": "972502222222"})
    if not existing:
        await mock_db.users.insert_one({
            "_id": pro_id, "phone_number": "972502222222",
            "role": "professional", "is_active": True,
        })
    result = await handle_pro_text_command("972502222222@c.us", "אשר", mock_wa, mock_lm)
    assert result == Messages.Pro.NO_PENDING_APPROVE


@pytest.mark.asyncio
async def test_approve_with_number_command(pro_setup, mock_wa, mock_lm, monkeypatch):
    """'1' is an alias for approve."""
    pro_doc, db = pro_setup
    await db.leads.insert_one({
        "pro_id": pro_doc["_id"], "status": LeadStatus.NEW,
        "chat_id": "972501111111@c.us", "issue_type": "חשמל",
        "full_address": "חיפה", "appointment_time": "14:00",
        "created_at": datetime.now(timezone.utc),
    })
    import app.services.pro_flow
    monkeypatch.setattr(app.services.pro_flow, "book_slot_for_lead", AsyncMock(return_value=False))

    result = await handle_pro_text_command("972500000000@c.us", "1", mock_wa, mock_lm)
    assert Messages.Pro.APPROVE_SUCCESS in result


# --- Reject ---

@pytest.mark.asyncio
async def test_reject_lead(pro_setup, mock_wa, mock_lm):
    pro_doc, db = pro_setup
    await db.leads.insert_one({
        "pro_id": pro_doc["_id"], "status": LeadStatus.NEW,
        "chat_id": "972501111111@c.us", "created_at": datetime.now(timezone.utc),
    })

    result = await handle_pro_text_command("972500000000@c.us", "דחה", mock_wa, mock_lm)
    assert result == Messages.Pro.REJECT_SUCCESS
    mock_lm.update_lead_status.assert_called_once()


@pytest.mark.asyncio
async def test_reject_no_pending(mock_db, mock_wa, mock_lm):
    existing = await mock_db.users.find_one({"phone_number": "972502222222"})
    if not existing:
        await mock_db.users.insert_one({
            "phone_number": "972502222222", "role": "professional", "is_active": True,
        })
    result = await handle_pro_text_command("972502222222@c.us", "דחה", mock_wa, mock_lm)
    assert result == Messages.Pro.NO_PENDING_REJECT


# --- Finish ---

@pytest.mark.asyncio
async def test_finish_job(pro_setup, mock_wa, mock_lm):
    pro_doc, db = pro_setup
    lead_id = ObjectId()
    await db.leads.insert_one({
        "_id": lead_id, "pro_id": pro_doc["_id"],
        "status": LeadStatus.BOOKED, "chat_id": "972501111111@c.us",
        "created_at": datetime.now(timezone.utc),
    })

    result = await handle_pro_text_command("972500000000@c.us", "סיימתי", mock_wa, mock_lm)
    assert result == Messages.Pro.FINISH_SUCCESS

    # Lead should be completed
    lead = await db.leads.find_one({"_id": lead_id})
    assert lead["status"] == LeadStatus.COMPLETED
    assert lead["waiting_for_rating"] is True

    # Customer gets rating request
    mock_wa.send_message.assert_called_once()
    assert "972501111111@c.us" == mock_wa.send_message.call_args.args[0]


@pytest.mark.asyncio
async def test_finish_no_booked(mock_db, mock_wa, mock_lm):
    existing = await mock_db.users.find_one({"phone_number": "972502222222"})
    if not existing:
        await mock_db.users.insert_one({
            "phone_number": "972502222222", "role": "professional", "is_active": True,
        })
    result = await handle_pro_text_command("972502222222@c.us", "סיימתי", mock_wa, mock_lm)
    assert result == Messages.Pro.NO_ACTIVE_FINISH


# --- Active Jobs ---

@pytest.mark.asyncio
async def test_active_jobs_list(pro_setup, mock_wa, mock_lm):
    pro_doc, db = pro_setup
    await db.leads.insert_one({
        "pro_id": pro_doc["_id"], "status": LeadStatus.BOOKED,
        "issue_type": "נזילה", "full_address": "תל אביב",
        "appointment_time": "10:00", "created_at": datetime.now(timezone.utc),
    })

    result = await handle_pro_text_command("972500000000@c.us", "עבודות", mock_wa, mock_lm)
    assert "עבודות פעילות" in result
    assert "נזילה" in result


@pytest.mark.asyncio
async def test_active_jobs_empty(mock_db, mock_wa, mock_lm):
    """Pro with no active leads."""
    existing = await mock_db.users.find_one({"phone_number": "972502222222"})
    if not existing:
        await mock_db.users.insert_one({
            "phone_number": "972502222222", "role": "professional", "is_active": True,
        })
    # Use the pro that has no leads assigned to it
    result = await handle_pro_text_command("972502222222@c.us", "עבודות", mock_wa, mock_lm)
    assert result == Messages.Pro.NO_ACTIVE_JOBS


# --- History ---

@pytest.mark.asyncio
async def test_history(pro_setup, mock_wa, mock_lm):
    pro_doc, db = pro_setup
    await db.leads.insert_one({
        "pro_id": pro_doc["_id"], "status": LeadStatus.COMPLETED,
        "issue_type": "חשמל", "full_address": "חיפה",
        "completed_at": datetime(2026, 3, 15, tzinfo=timezone.utc),
        "created_at": datetime.now(timezone.utc),
    })

    result = await handle_pro_text_command("972500000000@c.us", "היסטוריה", mock_wa, mock_lm)
    assert "עבודות אחרונות" in result
    assert "חשמל" in result


@pytest.mark.asyncio
async def test_history_empty(mock_db, mock_wa, mock_lm):
    existing = await mock_db.users.find_one({"phone_number": "972502222222"})
    if not existing:
        await mock_db.users.insert_one({
            "phone_number": "972502222222", "role": "professional", "is_active": True,
        })
    result = await handle_pro_text_command("972502222222@c.us", "היסטוריה", mock_wa, mock_lm)
    assert result == Messages.Pro.NO_HISTORY


# --- Stats ---

@pytest.mark.asyncio
async def test_stats(pro_setup, mock_wa, mock_lm):
    pro_doc, db = pro_setup
    await db.leads.insert_one({
        "pro_id": pro_doc["_id"], "status": LeadStatus.COMPLETED,
        "created_at": datetime.now(timezone.utc),
    })

    result = await handle_pro_text_command("972500000000@c.us", "דוח", mock_wa, mock_lm)
    assert "סטטיסטיקות" in result
    assert "4.5" in result  # rating


# --- Reviews ---

@pytest.mark.asyncio
async def test_reviews_with_data(pro_setup, mock_wa, mock_lm):
    pro_doc, db = pro_setup
    # The _handle_reviews function matches reviews to leads via key = chat_id + rating
    chat_id = "972501234567@c.us"
    await db.leads.insert_one({
        "pro_id": pro_doc["_id"], "status": LeadStatus.COMPLETED,
        "rating_given": 5, "chat_id": chat_id,
        "completed_at": datetime.now(timezone.utc),
        "created_at": datetime.now(timezone.utc),
    })
    await db.reviews.insert_one({
        "pro_id": pro_doc["_id"],
        "customer_chat_id": chat_id,
        "rating": 5,
        "comment": "שירות מצוין",
    })

    result = await handle_pro_text_command(f"{PRO_PHONE}@c.us", "ביקורות", mock_wa, mock_lm)
    assert result is not None
    assert "דירוגים" in result or "ביקורות" in result.lower()
    assert "5" in result  # Rating value shown


@pytest.mark.asyncio
async def test_reviews_empty(pro_setup, mock_wa, mock_lm):
    # Pro has review_count=3 from fixture but let's set to 0
    pro_doc, db = pro_setup
    await db.users.update_one(
        {"_id": pro_doc["_id"]},
        {"$set": {"social_proof.review_count": 0}}
    )

    result = await handle_pro_text_command("972500000000@c.us", "ביקורות", mock_wa, mock_lm)
    assert result == Messages.Pro.NO_REVIEWS


# --- Unknown Command ---

@pytest.mark.asyncio
async def test_unknown_command_returns_none(pro_setup, mock_wa, mock_lm):
    result = await handle_pro_text_command("972500000000@c.us", "שלום", mock_wa, mock_lm)
    assert result is None


@pytest.mark.asyncio
async def test_non_pro_returns_none(mock_db, mock_wa, mock_lm):
    """Non-pro phone number -> returns None."""
    result = await handle_pro_text_command("972501111111@c.us", "אשר", mock_wa, mock_lm)
    assert result is None
