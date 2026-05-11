"""
Tests for customer_flow.py: completion checks, ratings, reviews.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from bson import ObjectId
from datetime import datetime, timezone
from app.core.constants import LeadStatus, Defaults
from app.core.messages import Messages
from app.services.customer_flow import (
    send_customer_completion_check,
    handle_customer_completion_text,
    handle_customer_rating_text,
    handle_customer_review_comment,
    handle_status_query,
)


@pytest.fixture
def flow_db(mock_db):
    """Seed DB with common test data."""
    return mock_db


@pytest.fixture
def mock_whatsapp():
    wa = MagicMock()
    wa.send_message = AsyncMock()
    return wa


# --- send_customer_completion_check ---

@pytest.mark.asyncio
async def test_completion_check_sends_text_message(flow_db, mock_whatsapp):
    pro_id = ObjectId()
    await flow_db.users.insert_one({"_id": pro_id, "business_name": "יוסי אינסטלציה"})

    lead_id = ObjectId()
    await flow_db.leads.insert_one({
        "_id": lead_id,
        "chat_id": "972501111111@c.us",
        "status": LeadStatus.BOOKED,
        "pro_id": pro_id,
    })

    await send_customer_completion_check(str(lead_id), mock_whatsapp)

    mock_whatsapp.send_message.assert_called_once()
    call_args = mock_whatsapp.send_message.call_args
    # Message should contain the numeric reply instructions
    assert "השב *1*" in str(call_args)


@pytest.mark.asyncio
async def test_completion_check_non_booked_skipped(flow_db, mock_whatsapp):
    lead_id = ObjectId()
    await flow_db.leads.insert_one({
        "_id": lead_id,
        "chat_id": "972501111111@c.us",
        "status": LeadStatus.COMPLETED,
    })

    await send_customer_completion_check(str(lead_id), mock_whatsapp)

    mock_whatsapp.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_completion_check_missing_lead(flow_db, mock_whatsapp):
    await send_customer_completion_check(str(ObjectId()), mock_whatsapp)
    mock_whatsapp.send_message.assert_not_called()


# --- handle_customer_completion_text ---

@pytest.mark.asyncio
async def test_handle_completion_confirms(flow_db, mock_whatsapp):
    pro_id = ObjectId()
    await flow_db.users.insert_one({
        "_id": pro_id, "business_name": "יוסי", "phone_number": "972500000000",
    })

    lead_id = ObjectId()
    await flow_db.leads.insert_one({
        "_id": lead_id,
        "chat_id": "972501111111@c.us",
        "status": LeadStatus.BOOKED,
        "pro_id": pro_id,
        "created_at": datetime.now(timezone.utc),
    })

    result = await handle_customer_completion_text("972501111111@c.us", "כן, הסתיים", mock_whatsapp)

    assert result is not None
    assert "יוסי" in result

    # Lead should be completed
    lead = await flow_db.leads.find_one({"_id": lead_id})
    assert lead["status"] == LeadStatus.COMPLETED
    assert lead["waiting_for_rating"] is True

    # Pro should be notified
    mock_whatsapp.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_handle_completion_numeric_yes(flow_db, mock_whatsapp):
    """Reply '1' triggers completion."""
    pro_id = ObjectId()
    await flow_db.users.insert_one({"_id": pro_id, "business_name": "Test", "phone_number": "972500000001"})

    lead_id = ObjectId()
    await flow_db.leads.insert_one({
        "_id": lead_id, "chat_id": "972501111112@c.us",
        "status": LeadStatus.BOOKED, "pro_id": pro_id,
        "created_at": datetime.now(timezone.utc),
    })

    result = await handle_customer_completion_text("972501111112@c.us", "1", mock_whatsapp)
    assert result is not None


@pytest.mark.asyncio
async def test_handle_completion_hebrew_yes(flow_db, mock_whatsapp):
    """Reply 'כן' triggers completion."""
    pro_id = ObjectId()
    await flow_db.users.insert_one({"_id": pro_id, "business_name": "Test2", "phone_number": "972500000002"})

    lead_id = ObjectId()
    await flow_db.leads.insert_one({
        "_id": lead_id, "chat_id": "972501111113@c.us",
        "status": LeadStatus.BOOKED, "pro_id": pro_id,
        "created_at": datetime.now(timezone.utc),
    })

    result = await handle_customer_completion_text("972501111113@c.us", "כן", mock_whatsapp)
    assert result is not None


@pytest.mark.asyncio
async def test_handle_completion_no_match(flow_db, mock_whatsapp):
    result = await handle_customer_completion_text("972501111111@c.us", "שלום", mock_whatsapp)
    assert result is None


@pytest.mark.asyncio
async def test_handle_completion_no_booked_lead(flow_db, mock_whatsapp):
    # Use a unique chat_id that has no booked leads
    result = await handle_customer_completion_text("972508888888@c.us", "כן, הסתיים", mock_whatsapp)
    assert result is None


# --- handle_customer_rating_text ---

@pytest.mark.asyncio
async def test_rating_valid(flow_db, monkeypatch):
    pro_id = ObjectId()
    await flow_db.users.insert_one({
        "_id": pro_id, "business_name": "Test Pro",
        "social_proof": {"rating": 5.0, "review_count": 0},
    })

    lead_id = ObjectId()
    await flow_db.leads.insert_one({
        "_id": lead_id,
        "chat_id": "972507777777@c.us",
        "waiting_for_rating": True,
        "pro_id": pro_id,
    })

    result = await handle_customer_rating_text("972507777777@c.us", "4")

    assert result is not None
    assert result == Messages.Customer.REVIEW_REQUEST

    # Lead updated
    lead = await flow_db.leads.find_one({"_id": lead_id})
    assert lead["waiting_for_rating"] is False
    assert lead["rating_given"] == 4
    assert lead["waiting_for_review_comment"] is True

    # Pro rating updated in DB
    pro = await flow_db.users.find_one({"_id": pro_id})
    assert pro["social_proof"]["review_count"] == 1
    assert pro["social_proof"]["rating"] == 4.0


@pytest.mark.asyncio
async def test_rating_invalid_text(flow_db):
    result = await handle_customer_rating_text("972501111111@c.us", "great")
    assert result is None


@pytest.mark.asyncio
async def test_rating_no_waiting_lead(flow_db):
    result = await handle_customer_rating_text("972509010101@c.us", "5")
    assert result is None


# --- handle_customer_review_comment ---

@pytest.mark.asyncio
async def test_review_saved(flow_db, monkeypatch):
    pro_id = ObjectId()
    lead_id = ObjectId()
    await flow_db.leads.insert_one({
        "_id": lead_id,
        "chat_id": "972509020202@c.us",
        "waiting_for_review_comment": True,
        "pro_id": pro_id,
        "rating_given": 5,
    })

    result = await handle_customer_review_comment("972509020202@c.us", "שירות מעולה!")

    assert result == Messages.Customer.REVIEW_SAVED

    # Review inserted
    review = await flow_db.reviews.find_one({"pro_id": pro_id, "comment": "שירות מעולה!"})
    assert review is not None
    assert review["comment"] == "שירות מעולה!"
    assert review["rating"] == 5

    # Lead updated
    lead = await flow_db.leads.find_one({"_id": lead_id})
    assert lead["waiting_for_review_comment"] is False


@pytest.mark.asyncio
async def test_review_no_waiting_lead(flow_db):
    result = await handle_customer_review_comment("972509030303@c.us", "good service")
    assert result is None


# --- handle_status_query ---

@pytest.mark.asyncio
async def test_handle_status_query_new_lead(flow_db):
    chat_id = "972511111111@c.us"
    await flow_db.leads.insert_one({
        "chat_id": chat_id,
        "status": LeadStatus.NEW,
        "issue_type": "נזילה",
        "created_at": datetime.now(timezone.utc),
    })

    result = await handle_status_query(chat_id)

    assert result is not None
    assert "נזילה" in result
    # NEW status template contains "מאתרים"
    assert "מאתרים" in result


@pytest.mark.asyncio
async def test_handle_status_query_contacted_lead(flow_db):
    chat_id = "972511111112@c.us"
    await flow_db.leads.insert_one({
        "chat_id": chat_id,
        "status": LeadStatus.CONTACTED,
        "issue_type": "תקלת חשמל",
        "created_at": datetime.now(timezone.utc),
    })

    result = await handle_status_query(chat_id)

    assert result is not None
    assert "תקלת חשמל" in result
    # CONTACTED template mentions waiting for pro
    assert "ממתינים" in result


@pytest.mark.asyncio
async def test_handle_status_query_booked_lead_includes_pro_name(flow_db):
    chat_id = "972511111113@c.us"
    pro_id = ObjectId()
    await flow_db.users.insert_one({"_id": pro_id, "business_name": "יוסי אינסטלציה"})
    await flow_db.leads.insert_one({
        "chat_id": chat_id,
        "status": LeadStatus.BOOKED,
        "issue_type": "צנרת",
        "pro_id": pro_id,
        "appointment_time": "10:00 15/05/2026",
        "created_at": datetime.now(timezone.utc),
    })

    result = await handle_status_query(chat_id)

    assert result is not None
    assert "יוסי אינסטלציה" in result
    assert "10:00 15/05/2026" in result


@pytest.mark.asyncio
async def test_handle_status_query_no_active_lead_returns_friendly_message(flow_db):
    # A chat_id with no leads at all
    result = await handle_status_query("972599999999@c.us")

    assert result == Messages.Customer.STATUS_NO_ACTIVE_LEAD


@pytest.mark.asyncio
async def test_handle_status_query_falls_back_to_recent_completed_lead(flow_db):
    chat_id = "972511111114@c.us"
    await flow_db.leads.insert_one({
        "chat_id": chat_id,
        "status": LeadStatus.COMPLETED,
        "issue_type": "תיקון דלת",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    })

    result = await handle_status_query(chat_id)

    assert result is not None
    # COMPLETED template mentions the work ended
    assert "הסתיימה" in result
