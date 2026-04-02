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
)


@pytest.fixture
def flow_db(mock_db):
    """Seed DB with common test data."""
    return mock_db


@pytest.fixture
def mock_whatsapp():
    wa = MagicMock()
    wa.send_message = AsyncMock()
    wa.send_interactive_buttons = AsyncMock()
    return wa


# --- send_customer_completion_check ---

@pytest.mark.asyncio
async def test_completion_check_sends_buttons(flow_db, mock_whatsapp):
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

    mock_whatsapp.send_interactive_buttons.assert_called_once()
    call_kwargs = mock_whatsapp.send_interactive_buttons.call_args
    assert "972501111111" in str(call_kwargs)


@pytest.mark.asyncio
async def test_completion_check_non_booked_skipped(flow_db, mock_whatsapp):
    lead_id = ObjectId()
    await flow_db.leads.insert_one({
        "_id": lead_id,
        "chat_id": "972501111111@c.us",
        "status": LeadStatus.COMPLETED,
    })

    await send_customer_completion_check(str(lead_id), mock_whatsapp)

    mock_whatsapp.send_interactive_buttons.assert_not_called()


@pytest.mark.asyncio
async def test_completion_check_missing_lead(flow_db, mock_whatsapp):
    await send_customer_completion_check(str(ObjectId()), mock_whatsapp)
    mock_whatsapp.send_interactive_buttons.assert_not_called()


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
async def test_handle_completion_no_match(flow_db, mock_whatsapp):
    result = await handle_customer_completion_text("972501111111@c.us", "שלום", mock_whatsapp)
    assert result is None


@pytest.mark.asyncio
async def test_handle_completion_no_booked_lead(flow_db, mock_whatsapp):
    # Use a unique chat_id that has no booked leads
    result = await handle_customer_completion_text("972508888888@c.us", "כן, הסתיים", mock_whatsapp)
    assert result is None


@pytest.mark.asyncio
async def test_handle_completion_button_id(flow_db, mock_whatsapp):
    """Button ID 'confirm_finish' also triggers completion."""
    pro_id = ObjectId()
    await flow_db.users.insert_one({"_id": pro_id, "business_name": "Test", "phone_number": "972500000000"})

    lead_id = ObjectId()
    await flow_db.leads.insert_one({
        "_id": lead_id, "chat_id": "972501111111@c.us",
        "status": LeadStatus.BOOKED, "pro_id": pro_id,
        "created_at": datetime.now(timezone.utc),
    })

    result = await handle_customer_completion_text("972501111111@c.us", "confirm_finish", mock_whatsapp)
    assert result is not None


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
