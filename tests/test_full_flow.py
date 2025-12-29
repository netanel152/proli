import pytest
from app.services.workflow import process_incoming_message
from app.services.ai_engine import AIResponse, ExtractedData
from unittest.mock import MagicMock, AsyncMock

# --- HELPERS ---
def setup_mock_ai(monkeypatch, responses):
    """
    Helper to configure the mock AI in app.services.workflow.
    responses: list of AIResponse objects to return sequentially (side_effect).
    """
    mock_ai = MagicMock()
    mock_ai.analyze_conversation = AsyncMock(side_effect=responses)
    
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
        "system_prompt": "You are Yossi.",
        "social_proof": {"rating": 5.0, "review_count": 0}
    }
    await mock_db.users.insert_one(pro_data)
    pro = await mock_db.users.find_one({"business_name": "יוסי אינסטלציה"})
    pro_chat_id = "972524828796@c.us"
    user_chat_id = "972501234567@c.us"

    # 2. USER: "I need a plumber in Tel Aviv" (Routing)
    # Logic:
    # 1. Dispatcher: Extracts City=Tel Aviv, Issue=Plumber
    # 2. Workflow finds Pro "Yossi".
    # 3. Pro AI: Says "Hello, I am Yossi".
    
    resp_dispatcher = AIResponse(
        reply_to_user="...", 
        extracted_data=ExtractedData(city="Tel Aviv", issue="plumber", full_address=None, appointment_time=None),
        transcription=None
    )
    resp_pro = AIResponse(
        reply_to_user="Hello! I am Yossi, how can I help?",
        extracted_data=ExtractedData(city="Tel Aviv", issue="plumber", full_address=None, appointment_time=None),
        transcription=None
    )
    
    setup_mock_ai(monkeypatch, responses=[resp_dispatcher, resp_pro])
    
    await process_incoming_message(user_chat_id, "I need a plumber in Tel Aviv")
    
    # Assert message logged
    messages = await mock_db.messages.find({"chat_id": user_chat_id}).to_list(length=None)
    # Logged: User msg, Model msg
    assert len(messages) >= 2 
    assert messages[-1]["text"] == "Hello! I am Yossi, how can I help?"

    # 3. USER: "Book me for tomorrow 10:00 at Dizengoff 1" (Deal)
    # Logic:
    # 1. Dispatcher: Extracts ...
    # 2. Pro AI: Returns [DEAL] or structured Deal
    
    # Dispatcher sees info again (or history carries it, but we mock response)
    resp_dispatcher_2 = AIResponse(
        reply_to_user="...", 
        extracted_data=ExtractedData(city="Tel Aviv", issue="plumber", full_address="Dizengoff 1", appointment_time="tomorrow 10:00"),
        transcription=None
    )
    # Pro sees it and closes deal
    resp_pro_deal = AIResponse(
        reply_to_user="Done. [DEAL: tomorrow 10:00 | Dizengoff 1 | plumber]",
        extracted_data=ExtractedData(city="Tel Aviv", issue="plumber", full_address="Dizengoff 1", appointment_time="tomorrow 10:00"),
        transcription=None
    )
    
    setup_mock_ai(monkeypatch, responses=[resp_dispatcher_2, resp_pro_deal])
    
    await process_incoming_message(user_chat_id, "Book me for tomorrow 10:00 at Dizengoff 1")
    
    # Assert Lead Created
    lead = await mock_db.leads.find_one({"chat_id": user_chat_id, "status": "new"})
    assert lead is not None
    assert lead["issue_type"] == "plumber"
    assert lead["full_address"] == "Dizengoff 1"

    # 4. PRO: Approve via Text Command ("אשר")
    await process_incoming_message(pro_chat_id, "אשר")
    
    updated_lead = await mock_db.leads.find_one({"_id": lead["_id"]})
    assert updated_lead["status"] == "booked"
    assert updated_lead["pro_id"] == pro["_id"]

    # 5. USER: "כן, הסתיים" (Completion via Text)
    # No AI needed here
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