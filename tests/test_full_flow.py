import pytest
import pytest_asyncio
from app.services.logic import ask_fixi_ai, handle_pro_command, analyze_pro_intent
from app.scheduler import send_daily_reminders
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime, timedelta
import pytz

# --- HELPERS ---
def create_mock_model(intent="UNKNOWN", response_text="Mock AI Response"):
    """Helper to create a fresh mock model for each test step"""
    mock_chat = MagicMock()
    mock_chat.send_message_async = AsyncMock(return_value=MagicMock(text=response_text))
    
    mock_model = MagicMock()
    mock_model.start_chat.return_value = mock_chat
    # Mock for analyze_pro_intent (JSON response)
    mock_model.generate_content_async = AsyncMock(return_value=MagicMock(text=f'{{"intent": "{intent}", "hour": 16, "day": "TOMORROW"}}'))
    return mock_model

@pytest.mark.asyncio
async def test_full_lifecycle(mock_db, monkeypatch):
    """
    Test the complete flow: 
    User Connects -> Routing -> Booking -> Pro Notified -> Pro Commands -> Completion -> Rating
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
    pro_id_str = str(pro["_id"])
    pro_chat_id = "972524828796@c.us"
    user_chat_id = "972501234567@c.us"

    # 2. USER: "I need a plumber in Tel Aviv" (Routing)
    # Mock AI for conversational response
    mock_model_1 = create_mock_model(intent="UNKNOWN", response_text="Hello! I am Yossi, how can I help?")
    monkeypatch.setattr("google.generativeai.GenerativeModel", MagicMock(return_value=mock_model_1))
    
    resp1 = await ask_fixi_ai("I need a plumber in Tel Aviv", user_chat_id)
    
    # Assert correct pro assigned in message history
    messages = await mock_db.messages.find({"chat_id": user_chat_id}).to_list(length=None)
    last_msg = messages[-1]
    assert last_msg["pro_id"] == pro["_id"]
    assert "Yossi" in resp1

    # 3. USER: "Book me for tomorrow 10:00" (Deal)
    mock_model_2 = create_mock_model(intent="UNKNOWN", response_text="Done. [DEAL: Fix leak | Tel Aviv | 10:00]")
    monkeypatch.setattr("google.generativeai.GenerativeModel", MagicMock(return_value=mock_model_2))
    
    resp2 = await ask_fixi_ai("Book me for tomorrow 10:00", user_chat_id)
    
    # Assert Lead Created
    lead = await mock_db.leads.find_one({"chat_id": user_chat_id, "status": "New"})
    assert lead is not None
    assert "Fix leak" in lead["details"]

    # 4. PRO: "Get Work" (Pull Lead)
    mock_model_3 = create_mock_model(intent="GET_WORK")
    monkeypatch.setattr("google.generativeai.GenerativeModel", MagicMock(return_value=mock_model_3))
    
    resp3 = await ask_fixi_ai("get work", pro_chat_id)
    
    assert "קיבלת עבודה חדשה" in resp3
    updated_lead = await mock_db.leads.find_one({"_id": lead["_id"]})
    assert updated_lead["status"] == "booked"
    assert updated_lead["pro_id"] == pro["_id"]

    # 5. PRO: "Vacation Tomorrow" (Blocking)
    mock_model_4 = create_mock_model(intent="VACATION")
    monkeypatch.setattr("google.generativeai.GenerativeModel", MagicMock(return_value=mock_model_4))
    
    # Insert some slots to block
    tomorrow = datetime.now(pytz.utc) + timedelta(days=1)
    await mock_db.slots.insert_one({
        "pro_id": pro["_id"],
        "start_time": tomorrow,
        "is_taken": False
    })
    
    resp4 = await ask_fixi_ai("I am on vacation tomorrow", pro_chat_id)
    assert "חסמתי לך" in resp4
    
    # Verify slots blocked
    slot = await mock_db.slots.find_one({"pro_id": pro["_id"]})
    assert slot["is_taken"] is True

    # 6. PRO: "Finish Job" (Completion)
    mock_model_5 = create_mock_model(intent="FINISH_JOB")
    monkeypatch.setattr("google.generativeai.GenerativeModel", MagicMock(return_value=mock_model_5))
    
    resp5 = await ask_fixi_ai("Job done", pro_chat_id)
    assert "העבודה הושלמה" in resp5
    
    completed_lead = await mock_db.leads.find_one({"_id": lead["_id"]})
    assert completed_lead["status"] == "completed"
    assert completed_lead["waiting_for_rating"] is True

    # 7. USER: Rate "5"
    resp6 = await ask_fixi_ai("5", user_chat_id)
    assert "תודה" in resp6
    
    # Verify Rating
    pro_updated = await mock_db.users.find_one({"_id": pro["_id"]})
    assert pro_updated["social_proof"]["review_count"] == 1

@pytest.mark.asyncio
async def test_pro_block_command(mock_db, monkeypatch):
    # Setup Pro
    result = await mock_db.users.insert_one({"phone_number": "123456", "business_name": "TestPro"})
    pro_id = result.inserted_id
    chat_id = "123456@c.us"
    
    # Create Slot at 16:00 UTC
    now = datetime.now(pytz.timezone('Asia/Jerusalem'))
    target_time_il = now.replace(hour=16, minute=0, second=0, microsecond=0)
    target_time_utc = target_time_il.astimezone(pytz.utc)
    
    await mock_db.slots.insert_one({
        "pro_id": pro_id,
        "start_time": target_time_utc,
        "is_taken": False
    })
    
    # Mock Intent: BLOCK 16
    mock_model = create_mock_model(intent="BLOCK")
    # Need to ensure the 'hour' in the mock return matches our test case
    mock_model.generate_content_async = AsyncMock(return_value=MagicMock(text='{"intent": "BLOCK", "hour": 16, "day": "TODAY"}'))
    monkeypatch.setattr("google.generativeai.GenerativeModel", MagicMock(return_value=mock_model))
    
    resp = await ask_fixi_ai("block 16:00", chat_id)
    
    assert "חסמתי לך" in resp
    slot = await mock_db.slots.find_one({"pro_id": pro_id})
    assert slot["is_taken"] is True

@pytest.mark.asyncio
async def test_pro_show_schedule(mock_db, monkeypatch):
    # Setup Pro
    result = await mock_db.users.insert_one({"phone_number": "123456", "business_name": "TestPro"})
    pro_id = result.inserted_id
    chat_id = "123456@c.us"
    
    # Mock the leads_collection.find logic to avoid mongomock datetime query issues
    # We need to mock it specifically for the module where it is used (app.services.logic)
    
    mock_leads_collection = MagicMock()
    
    # Create a dummy lead object that behaves like a dictionary
    mock_lead = {
        "pro_id": pro_id,
        "status": "booked",
        "created_at": datetime.now(pytz.utc),
        "details": "Fix sink",
        "chat_id": "999@c.us"
    }
    
    # The code calls: leads_collection.find(...).sort(...)
    # So we need to mock the chain
    mock_cursor = MagicMock()
    mock_cursor.to_list = AsyncMock(return_value=[mock_lead]) # to_list is async
    
    # find() returns the cursor, sort() returns the cursor
    mock_leads_collection.find.return_value = mock_cursor
    mock_cursor.sort.return_value = mock_cursor
    
    # Patch it in app.services.logic
    monkeypatch.setattr("app.services.logic.leads_collection", mock_leads_collection)

    mock_model = create_mock_model(intent="SHOW")
    monkeypatch.setattr("google.generativeai.GenerativeModel", MagicMock(return_value=mock_model))
    
    resp = await ask_fixi_ai("show schedule", chat_id)
    
    assert "תוכנית עבודה" in resp
    assert "Fix sink" in resp