import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timedelta, timezone
from bson import ObjectId
from app.services import matching_service, whatsapp_client

# --- Task 1: Test Booking Logic ---

@pytest.mark.asyncio
async def test_book_slot_for_lead_success(mock_db, monkeypatch):
    """
    Test that a slot is correctly booked when it exists and is free.
    """
    # 1. Patch the slots_collection in matching_service (crucial as it might not be patched in conftest)
    monkeypatch.setattr(matching_service, "slots_collection", mock_db.slots)

    # 2. Setup Data
    pro_id = ObjectId()
    lead_created_at = datetime.now(timezone.utc)
    
    # Logic in matching_service:
    # estimated_time = lead_created_at + 1h (rounded to hour)
    # window = +/- 2h
    # So if now is 10:30, est is 11:30 -> 12:00? No, replace(minute=0) + 1h.
    # 10:30 -> 10:00 + 1h = 11:00.
    # Window: 09:00 to 13:00.
    
    # Let's create a slot at 11:00
    slot_time = lead_created_at.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    
    await mock_db.slots.insert_one({
        "pro_id": pro_id,
        "is_taken": False,
        "start_time": slot_time
    })

    # 3. Action
    result = await matching_service.book_slot_for_lead(str(pro_id), lead_created_at)

    # 4. Assertion
    assert result is True
    
    # Verify DB update
    updated_slot = await mock_db.slots.find_one({"pro_id": pro_id})
    assert updated_slot["is_taken"] is True

@pytest.mark.asyncio
async def test_book_slot_for_lead_no_slot(mock_db, monkeypatch):
    """
    Test that booking fails gracefully when no slot is available.
    """
    monkeypatch.setattr(matching_service, "slots_collection", mock_db.slots)
    
    pro_id = ObjectId()
    lead_created_at = datetime.now(timezone.utc)
    
    # Ensure DB is empty for this pro
    await mock_db.slots.delete_many({"pro_id": pro_id})

    result = await matching_service.book_slot_for_lead(str(pro_id), lead_created_at)
    assert result is False

@pytest.mark.asyncio
async def test_book_slot_for_lead_already_taken(mock_db, monkeypatch):
    """
    Test that booking fails if the only matching slot is already taken.
    """
    monkeypatch.setattr(matching_service, "slots_collection", mock_db.slots)
    
    pro_id = ObjectId()
    lead_created_at = datetime.now(timezone.utc)
    slot_time = lead_created_at.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

    await mock_db.slots.insert_one({
        "pro_id": pro_id,
        "is_taken": True, # Already taken
        "start_time": slot_time
    })

    result = await matching_service.book_slot_for_lead(str(pro_id), lead_created_at)
    assert result is False


# --- Task 2: Test WhatsApp Buttons Payload ---

@pytest.mark.asyncio
async def test_send_interactive_buttons_payload():
    """
    Verify that send_interactive_buttons constructs the correct JSON payload.
    """
    client = whatsapp_client.WhatsAppClient()
    
    # Mock data
    to_number = "123456789"
    text = "Please choose:"
    buttons = [
        {"id": "btn1", "title": "Option 1"},
        {"id": "btn2", "title": "Option 2"}
    ]
    
    expected_buttons_payload = [
        {"buttonId": "btn1", "buttonText": {"displayText": "Option 1"}},
        {"buttonId": "btn2", "buttonText": {"displayText": "Option 2"}}
    ]

    # Mock httpx.AsyncClient
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_instance = mock_client_cls.return_value.__aenter__.return_value
        mock_instance.post = AsyncMock(return_value=MagicMock(status_code=200, json=lambda: {"ok": True}))

        # Action
        await client.send_interactive_buttons(to_number, text, buttons)

        # Assertion
        mock_instance.post.assert_called_once()
        
        # Get arguments passed to post
        call_args = mock_instance.post.call_args
        # args[0] is url, kwargs['json'] is payload
        payload = call_args.kwargs['json']
        
        assert payload["chatId"] == "123456789@c.us"
        assert payload["message"] == text
        assert payload["buttons"] == expected_buttons_payload
