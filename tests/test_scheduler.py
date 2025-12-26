import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from datetime import datetime, timedelta
import pytz
from bson import ObjectId
from app.scheduler import monitor_unfinished_jobs, send_daily_reminders

IL_TZ = pytz.timezone('Asia/Jerusalem')

@pytest.fixture
def mock_collections():
    with patch("app.scheduler.leads_collection") as mock_leads, \
         patch("app.scheduler.users_collection") as mock_users, \
         patch("app.scheduler.settings_collection") as mock_settings:
        
        # Setup Async cursors
        mock_leads.find = MagicMock()
        mock_users.find = MagicMock()
        mock_leads.update_many = AsyncMock()
        mock_leads.update_many.return_value.modified_count = 0
        
        yield mock_leads, mock_users, mock_settings

@pytest.fixture
def mock_actions():
    with patch("app.scheduler.send_pro_reminder", new_callable=AsyncMock) as mock_remind, \
         patch("app.scheduler.send_customer_completion_check", new_callable=AsyncMock) as mock_check, \
         patch("app.scheduler.whatsapp") as mock_whatsapp:
        
        mock_whatsapp.send_message = AsyncMock()
        mock_whatsapp.send_buttons = AsyncMock()
        
        yield mock_remind, mock_check, mock_whatsapp

@pytest.mark.asyncio
async def test_monitor_unfinished_jobs_tier1(mock_collections, mock_actions):
    mock_leads, _, _ = mock_collections
    mock_remind, mock_check, _ = mock_actions
    
    # Mock datetime to be 12:00 PM IL time (Business hours)
    fixed_now = datetime(2023, 1, 1, 12, 0, 0, tzinfo=IL_TZ)
    
    with patch("app.scheduler.datetime") as mock_datetime:
        mock_datetime.now.return_value = fixed_now
        mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)

        # Setup mock leads for Tier 1 (4-6 hours old)
        lead_id = ObjectId()
        mock_cursor = AsyncMock()
        mock_cursor.__aiter__.return_value = [{"_id": lead_id, "status": "booked"}]
        mock_leads.find.return_value = mock_cursor

        # Run Scheduler
        await monitor_unfinished_jobs()
        
        mock_remind.assert_called() 

@pytest.mark.asyncio
async def test_monitor_unfinished_jobs_tier3(mock_collections, mock_actions):
    mock_leads, _, _ = mock_collections
    
    # Mock datetime 12:00 PM
    fixed_now = datetime(2023, 1, 1, 12, 0, 0, tzinfo=IL_TZ)
    
    with patch("app.scheduler.datetime") as mock_datetime:
        mock_datetime.now.return_value = fixed_now
        
        mock_leads.update_many.return_value.modified_count = 5
        
        # Empty cursors for find
        empty_cursor = AsyncMock()
        empty_cursor.__aiter__.return_value = []
        mock_leads.find.return_value = empty_cursor
        
        await monitor_unfinished_jobs()
        
        mock_leads.update_many.assert_called()
        args = mock_leads.update_many.call_args
        assert args[0][0]["flag"] == {"$ne": "requires_admin"} 
        assert args[0][1]["$set"]["flag"] == "requires_admin"

@pytest.mark.asyncio
async def test_send_daily_reminders(mock_collections, mock_actions):
    mock_leads, mock_users, _ = mock_collections
    _, _, mock_whatsapp = mock_actions
    
    # Mock Users: explicit cursor setup
    pro = {"_id": ObjectId(), "business_name": "Mario Plumbing", "phone_number": "972500000000", "is_active": True}
    users_cursor = MagicMock()
    users_cursor.to_list = AsyncMock(return_value=[pro])
    mock_users.find.return_value = users_cursor
    
    # Mock Leads (Booked Today)
    job = {
        "created_at": datetime.now(pytz.utc), 
        "chat_id": "12345@c.us",
        "details": "Leaky Faucet"
    }
    
    # Setup chained cursor mock for Leads
    # The code does: leads_collection.find(...).sort(...).to_list(...)
    leads_cursor = MagicMock()
    sort_cursor = MagicMock()
    sort_cursor.to_list = AsyncMock(return_value=[job])
    leads_cursor.sort.return_value = sort_cursor
    mock_leads.find.return_value = leads_cursor
    
    with patch("app.scheduler.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2023, 1, 1, 8, 0, 0, tzinfo=IL_TZ)
        
        await send_daily_reminders()
        
        # Verify intermediate calls
        mock_users.find.assert_called()
        mock_leads.find.assert_called()
        leads_cursor.sort.assert_called()
        sort_cursor.to_list.assert_called()
        
        mock_whatsapp.send_message.assert_called_once()
        msg_sent = mock_whatsapp.send_message.call_args[0][1]
        assert "Mario Plumbing" in msg_sent
        assert "Leaky Faucet" in msg_sent
