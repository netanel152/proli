import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, AsyncMock, ANY
from app.core.constants import LeadStatus, WorkerConstants
from app.services.monitor_service import remind_stale_booked_leads

@pytest.fixture
def mock_whatsapp():
    with patch("app.services.monitor_service.whatsapp") as mock:
        mock.send_message = AsyncMock()
        yield mock

@pytest.mark.asyncio
async def test_remind_stale_booked_lead(mock_db, monkeypatch, mock_whatsapp):
    """
    Test that stale booked leads get a reminder.
    """
    monkeypatch.setattr("app.services.monitor_service.leads_collection", mock_db.leads)
    monkeypatch.setattr("app.services.monitor_service.users_collection", mock_db.users)
    
    await mock_db.leads.delete_many({})
    await mock_db.users.delete_many({})
    
    stale_time = datetime.now(timezone.utc) - timedelta(hours=WorkerConstants.STALE_BOOKED_LEAD_HOURS + 1)
    
    pro_id = "pro_123"
    await mock_db.users.insert_one({
        "_id": pro_id,
        "phone_number": "972500000000",
        "business_name": "Test Pro"
    })
    
    lead_id = await mock_db.leads.insert_one({
        "chat_id": "customer@c.us",
        "status": LeadStatus.BOOKED,
        "appointment_time": stale_time,
        "pro_id": pro_id,
        "customer_name": "John Doe"
    })
    
    await remind_stale_booked_leads()
    
    # Verify pro notification
    mock_whatsapp.send_message.assert_called_once_with("972500000000@c.us", ANY)
    
    # Verify lead updated
    updated_lead = await mock_db.leads.find_one({"_id": lead_id.inserted_id})
    assert updated_lead["reminders_sent"] == 1
    assert "last_reminder_at" in updated_lead

@pytest.mark.asyncio
async def test_nudger_respects_max_reminders(mock_db, monkeypatch, mock_whatsapp):
    """
    Test that nudger stops after MAX_PRO_REMINDERS.
    """
    monkeypatch.setattr("app.services.monitor_service.leads_collection", mock_db.leads)
    monkeypatch.setattr("app.services.monitor_service.users_collection", mock_db.users)
    
    await mock_db.leads.delete_many({})
    
    stale_time = datetime.now(timezone.utc) - timedelta(hours=WorkerConstants.STALE_BOOKED_LEAD_HOURS + 1)
    
    await mock_db.leads.insert_one({
        "status": LeadStatus.BOOKED,
        "appointment_time": stale_time,
        "reminders_sent": WorkerConstants.MAX_PRO_REMINDERS,
        "pro_id": "pro_123"
    })
    
    await remind_stale_booked_leads()
    
    mock_whatsapp.send_message.assert_not_called()

@pytest.mark.asyncio
async def test_nudger_ignores_fresh_leads(mock_db, monkeypatch, mock_whatsapp):
    """
    Test that fresh leads (within 24h) are ignored.
    """
    monkeypatch.setattr("app.services.monitor_service.leads_collection", mock_db.leads)
    monkeypatch.setattr("app.services.monitor_service.users_collection", mock_db.users)
    
    await mock_db.leads.delete_many({})
    
    fresh_time = datetime.now(timezone.utc) - timedelta(hours=WorkerConstants.STALE_BOOKED_LEAD_HOURS - 1)
    
    await mock_db.leads.insert_one({
        "status": LeadStatus.BOOKED,
        "appointment_time": fresh_time,
        "pro_id": "pro_123"
    })
    
    await remind_stale_booked_leads()
    
    mock_whatsapp.send_message.assert_not_called()
