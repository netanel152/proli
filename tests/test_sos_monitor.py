import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, AsyncMock, ANY
from app.core.constants import LeadStatus, WorkerConstants
from app.services.monitor_service import check_and_reassign_stale_leads

@pytest.fixture
def mock_whatsapp():
    with patch("app.services.monitor_service.whatsapp") as mock:
        mock.send_message = AsyncMock()
        mock.send_location_link = AsyncMock()
        yield mock

@pytest.fixture
def mock_matching():
    with patch("app.services.monitor_service.matching_service") as mock:
        mock.determine_best_pro = AsyncMock()
        yield mock

@pytest.mark.asyncio
async def test_detect_stale_lead(mock_db, monkeypatch, mock_whatsapp, mock_matching):
    """
    Scenario 1: Detect Stale Lead
    Lead is 'NEW' and older than timeout. Expect Reassignment attempt.
    """
    monkeypatch.setattr("app.services.monitor_service.leads_collection", mock_db.leads)
    monkeypatch.setattr("app.services.monitor_service.users_collection", mock_db.users)
    
    await mock_db.leads.delete_many({})
    
    stale_time = datetime.now(timezone.utc) - timedelta(minutes=WorkerConstants.SOS_TIMEOUT_MINUTES + 1)
    
    lead_id = await mock_db.leads.insert_one({
        "chat_id": "customer@c.us",
        "status": LeadStatus.NEW,
        "created_at": stale_time,
        "issue_type": "Plumbing",
        "full_address": "Tel Aviv"
    })
    
    # Mock finding a new pro
    mock_matching.determine_best_pro.return_value = {
        "_id": "new_pro_id",
        "phone_number": "972500000000"
    }
    
    with patch("app.services.monitor_service.logger") as mock_logger:
        await check_and_reassign_stale_leads()
        
        # Verify it found stale leads
        mock_logger.warning.assert_any_call(f"🕵️ [SOS Healer] Found 1 stale leads. Attempting reassignment...")
        
        # Verify customer notification
        mock_whatsapp.send_message.assert_any_call("customer@c.us", ANY)
        
        # Verify pro matching
        mock_matching.determine_best_pro.assert_called_once()
        
        # Verify new pro notification
        mock_whatsapp.send_message.assert_any_call("972500000000@c.us", ANY)

@pytest.mark.asyncio
async def test_ignore_fresh_lead(mock_db, monkeypatch, mock_whatsapp):
    """
    Scenario 2: Ignore Fresh Lead
    Lead is 'NEW' but created recently. Expect NO action.
    """
    monkeypatch.setattr("app.services.monitor_service.leads_collection", mock_db.leads)
    await mock_db.leads.delete_many({})
    
    fresh_time = datetime.now(timezone.utc) - timedelta(minutes=1)
    
    await mock_db.leads.insert_one({
        "status": LeadStatus.NEW,
        "created_at": fresh_time
    })
    
    with patch("app.services.monitor_service.logger") as mock_logger:
        await check_and_reassign_stale_leads()
        
        # Should not find stale leads
        mock_logger.info.assert_any_call("✅ [SOS Healer] No stale leads found.")
        mock_whatsapp.send_message.assert_not_called()

@pytest.mark.asyncio
async def test_ignore_completed_lead(mock_db, monkeypatch, mock_whatsapp):
    """
    Scenario 3: Ignore Completed Lead
    Lead is 'COMPLETED' (even if old). Expect NO action.
    """
    monkeypatch.setattr("app.services.monitor_service.leads_collection", mock_db.leads)
    await mock_db.leads.delete_many({})
    
    old_time = datetime.now(timezone.utc) - timedelta(hours=2)
    
    await mock_db.leads.insert_one({
        "status": LeadStatus.COMPLETED,
        "created_at": old_time
    })
    
    with patch("app.services.monitor_service.logger") as mock_logger:
        await check_and_reassign_stale_leads()
        
        mock_logger.info.assert_any_call("✅ [SOS Healer] No stale leads found.")
        mock_whatsapp.send_message.assert_not_called()