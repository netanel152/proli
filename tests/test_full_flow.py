import pytest
import pytest_asyncio
from app.services.logic import ask_fixi_ai, handle_pro_command
from app.scheduler import send_daily_reminders
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime, timedelta
import pytz

@pytest.mark.asyncio
async def test_happy_path(mock_db, monkeypatch):
    """
    Simulates a full real-world lifecycle of a lead.
    """
    
    # --- SETUP: Seed Pro (Using MOCK DB) ---
    pro_data = {
        "business_name": "יוסי אינסטלציה",
        "phone_number": "972524828796",
        "service_areas": ["Tel Aviv"],
        "is_active": True,
        "keywords": ["plumber", "water"],
        "system_prompt": "You are Yossi."
    }
    mock_db.users.insert_one(pro_data)
    
    # Verify Seed
    pro_doc = mock_db.users.find_one({"business_name": "יוסי אינסטלציה"})
    assert pro_doc is not None
    
    user_id = "972524828796@c.us"
    pro_id = "972524828796@c.us"

    # --- STEP 1: Routing ---
    # User sends "Plumber in Tel Aviv"
    # We expect the logic to find Yossi
    
    # Mock AI to return just a greeting, logic should detect location switch
    mock_chat = MagicMock()
    mock_chat.send_message_async = AsyncMock(return_value=MagicMock(text="Hello from Yossi"))
    mock_model = MagicMock()
    mock_model.start_chat.return_value = mock_chat
    # Mock default generation too
    mock_model.generate_content_async = AsyncMock(return_value=MagicMock(text='{"intent": "UNKNOWN"}'))
    
    monkeypatch.setattr("google.generativeai.GenerativeModel", MagicMock(return_value=mock_model))
    
    response = await ask_fixi_ai("Plumber in Tel Aviv", user_id)
    # Debug print if fails
    if "Hello from Yossi" not in response:
        print(f"DEBUG: Response was: {response}")
    
    assert "Hello from Yossi" in response
    
    # Verify Routing
    history = list(mock_db.messages.find({"chat_id": user_id}))
    assert len(history) > 0
    assert history[-1]["pro_id"] == pro_doc["_id"]

    # --- STEP 2: Lead Creation ---
    # User says "I need help tomorrow at 10:00"
    # AI should output [DEAL: ...]
    
    mock_chat.send_message_async = AsyncMock(return_value=MagicMock(
        text="Great, I booked you. [DEAL: Fix leak tomorrow 10:00]"
    ))
    
    response = await ask_fixi_ai("I need help tomorrow at 10:00", user_id)
    assert "Great, I booked you" in response
    
    # Assert Lead Created
    lead = mock_db.leads.find_one({"chat_id": user_id})
    assert lead is not None
    assert lead["details"] == "Fix leak tomorrow 10:00"
    assert lead["status"] == "New"

    # --- STEP 3: Pro Confirms (GET_WORK) ---
    # Pro sends "GET_WORK" command
    
    mock_model.generate_content_async = AsyncMock(return_value=MagicMock(
        text='{"intent": "GET_WORK"}'
    ))
    
    response = await ask_fixi_ai("get work", pro_id) # Should trigger handle_pro_command
    
    assert "קיבלת עבודה חדשה" in response
    
    # Assert Lead Updated
    updated_lead = mock_db.leads.find_one({"_id": lead["_id"]})
    assert updated_lead["status"] == "booked"
    assert updated_lead["pro_id"] == pro_doc["_id"]

    # --- STEP 3.5: Finish Job ---
    # Pro says "Done"
    mock_model.generate_content_async = AsyncMock(return_value=MagicMock(
        text='{"intent": "FINISH_JOB"}'
    ))
    
    response = await ask_fixi_ai("Job done", pro_id)
    assert "סומנה כהושלמה" in response
    
    updated_lead_2 = mock_db.leads.find_one({"_id": lead["_id"]})
    assert updated_lead_2["status"] == "completed"
    assert updated_lead_2["waiting_for_rating"] is True

    # --- STEP 4: Rating ---
    # User sends "5"
    response = await ask_fixi_ai("5", user_id)
    assert "תודה רבה" in response
    
    # Assert Pro Rating Updated
    updated_pro = mock_db.users.find_one({"_id": pro_doc["_id"]})
    assert updated_pro["social_proof"]["review_count"] == 1
    assert updated_pro["social_proof"]["rating"] == 5.0

    # --- STEP 5: Scheduler Test ---
    # We need to simulate a booked job for TODAY to trigger reminder
    
    # Create a dummy booked lead for today
    today = datetime.now(pytz.timezone('Asia/Jerusalem'))
    today_utc = today.astimezone(pytz.utc)
    
    mock_db.leads.insert_one({
        "chat_id": "972524828796@c.us",
        "pro_id": pro_doc["_id"],
        "status": "booked",
        "created_at": today_utc, 
        "details": "Urgent fix"
    })
    
    # Run Scheduler Logic
    import app.scheduler
    # Trigger manually
    await send_daily_reminders()
    
    # Assert Message Sent
    mock_whatsapp = app.scheduler.send_whatsapp_message
    assert mock_whatsapp.called
    args, _ = mock_whatsapp.call_args
    # Check args
    assert args[0] == pro_id
    assert "Urgent fix" in args[1]