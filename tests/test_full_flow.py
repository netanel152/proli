import pytest
from app.services.workflow import process_incoming_message, handle_pro_response
from app.scheduler import send_daily_reminders
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime, timedelta

# --- HELPERS ---
def setup_mock_ai(monkeypatch, response_text="Mock AI Response"):
    """Helper to configure the mock AI in app.services.workflow"""
    mock_ai = MagicMock()
    mock_ai.analyze_conversation = AsyncMock(return_value=response_text)
    
    import app.services.workflow
    monkeypatch.setattr(app.services.workflow, "ai", mock_ai)
    return mock_ai

@pytest.mark.asyncio
async def test_full_lifecycle(mock_db, monkeypatch):
    """
    Test the complete flow using Workflow: 
    User Connects -> Routing -> Booking -> Pro Notified -> Completion -> Rating
    """
    
    # 1. SETUP: Create Pro
    pro_data = {
        "business_name": "יוסי אינסטלציה",
        "phone_number": "972524828796",
        "service_areas": ["Tel Aviv"],
        "is_active": True,
        "keywords": ["plumber", "water"],
        "system_prompt": "You are Yossi."
    }
    await mock_db.users.insert_one(pro_data)
    pro = await mock_db.users.find_one({"business_name": "יוסי אינסטלציה"})
    pro_chat_id = "972524828796@c.us"
    user_chat_id = "972501234567@c.us"

    # 2. USER: "I need a plumber in Tel Aviv" (Routing)
    # Note: process_incoming_message doesn't return the text, it sends it via WhatsAppClient.
    # We check the mock_db messages or mock_whatsapp calls.
    
    setup_mock_ai(monkeypatch, response_text="Hello! I am Yossi, how can I help?")
    
    await process_incoming_message(user_chat_id, "I need a plumber in Tel Aviv")
    
    # Assert message logged
    messages = await mock_db.messages.find({"chat_id": user_chat_id}).to_list(length=None)
    assert len(messages) >= 2 # User msg + Model msg
    assert messages[-1]["text"] == "Hello! I am Yossi, how can I help?"

    # 3. USER: "Book me for tomorrow 10:00" (Deal)
    # The AIEngine should return [DEAL:...]
    setup_mock_ai(monkeypatch, response_text="Done. [DEAL: 10:00 | Tel Aviv | Fix leak]")
    
    await process_incoming_message(user_chat_id, "Book me for tomorrow 10:00")
    
    # Assert Lead Created
    lead = await mock_db.leads.find_one({"chat_id": user_chat_id, "status": "new"})
    assert lead is not None
    assert "Fix leak" in lead["issue"]

    # 4. PRO: Approve via Button
    # Payload structure for handle_pro_response
    payload = {
        "senderData": {"chatId": pro_chat_id},
        "messageData": {
            "buttonsResponseMessage": {"selectedButtonId": f"approve_{lead['_id']}"}
        }
    }
    
    await handle_pro_response(payload)
    
    updated_lead = await mock_db.leads.find_one({"_id": lead["_id"]})
    assert updated_lead["status"] == "booked"
    assert updated_lead["pro_id"] == pro["_id"]

    # 5. USER: "כן, הסתיים" (Completion via Text)
    # No AI needed here, workflow checks specific text first
    
    await process_incoming_message(user_chat_id, "כן, הסתיים")
    
    completed_lead = await mock_db.leads.find_one({"_id": lead["_id"]})
    assert completed_lead["status"] == "completed"
    assert completed_lead["waiting_for_rating"] is True

    # 6. USER: Rate "5"
    await process_incoming_message(user_chat_id, "5")
    
    # Verify Rating
    pro_updated = await mock_db.users.find_one({"_id": pro["_id"]})
    assert pro_updated["social_proof"]["review_count"] == 1
    assert pro_updated["social_proof"]["rating"] == 5.0
