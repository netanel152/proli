import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
from bson import ObjectId
from app.services.lead_manager_service import LeadManager

@pytest.fixture
def mock_lead_manager_db():
    with patch("app.services.lead_manager.leads_collection") as mock_leads, \
         patch("app.services.lead_manager.messages_collection") as mock_messages, \
         patch("app.services.lead_manager.ContextManager") as mock_context_manager:
        
        # Setup AsyncMocks for common methods
        mock_leads.insert_one = AsyncMock()
        mock_leads.find_one = AsyncMock()
        mock_leads.update_one = AsyncMock()
        
        mock_messages.insert_one = AsyncMock()
        
        # Setup cursor for find operations
        mock_cursor = MagicMock()
        mock_cursor.sort.return_value = mock_cursor
        mock_cursor.limit.return_value = mock_cursor
        mock_cursor.to_list = AsyncMock()
        mock_messages.find.return_value = mock_cursor

        # Mock ContextManager methods
        mock_context_manager.get_history = AsyncMock(return_value=None) # Default to Cache Miss
        mock_context_manager.update_history = AsyncMock()
        mock_context_manager.set_history = AsyncMock()
        
        yield mock_leads, mock_messages, mock_context_manager

@pytest.mark.asyncio
async def test_create_lead_success(mock_lead_manager_db):
    mock_leads, _, _ = mock_lead_manager_db
    lead_manager = LeadManager()
    
    # Setup mock return for insert_one
    mock_leads.insert_one.return_value.inserted_id = ObjectId("507f1f77bcf86cd799439011")
    
    deal_string = "[DEAL: 10:00 | Tel Aviv, Rotshild 10 | Leaking pipe]"
    chat_id = "123456789@c.us"
    pro_id = ObjectId("507f1f77bcf86cd799439012")
    
    result = await lead_manager.create_lead(deal_string, chat_id, pro_id)
    
    assert result is not None
    assert result["appointment_time"] == "10:00"
    assert result["full_address"] == "Tel Aviv, Rotshild 10"
    assert result["issue_type"] == "Leaking pipe"
    assert result["chat_id"] == chat_id
    assert result["pro_id"] == pro_id
    assert result["status"] == "new"
    
    mock_leads.insert_one.assert_called_once()

@pytest.mark.asyncio
async def test_create_lead_flexible_parsing(mock_lead_manager_db):
    mock_leads, _, _ = mock_lead_manager_db
    lead_manager = LeadManager()
    
    mock_leads.insert_one.return_value.inserted_id = ObjectId("507f1f77bcf86cd799439011")
    
    # Test with missing time, assuming fallback logic
    deal_string = "[DEAL: Some Address | Broken Window]"
    result = await lead_manager.create_lead(deal_string, "123")
    
    assert result["appointment_time"] == "Not specified"
    assert result["full_address"] == "Some Address"
    assert result["issue_type"] == "Broken Window"

@pytest.mark.asyncio
async def test_update_lead_status(mock_lead_manager_db):
    mock_leads, _, _ = mock_lead_manager_db
    lead_manager = LeadManager()
    
    lead_id = "507f1f77bcf86cd799439011"
    new_status = "booked"
    pro_id = ObjectId("507f1f77bcf86cd799439012")
    
    await lead_manager.update_lead_status(lead_id, new_status, pro_id)
    
    mock_leads.update_one.assert_called_once()
    call_args = mock_leads.update_one.call_args
    query, update = call_args[0]
    
    assert query["_id"] == ObjectId(lead_id)
    assert update["$set"]["status"] == new_status
    assert update["$set"]["pro_id"] == pro_id

@pytest.mark.asyncio
async def test_get_chat_history_cache_miss(mock_lead_manager_db):
    _, mock_messages, mock_context_manager = mock_lead_manager_db
    lead_manager = LeadManager()
    
    # Mock data returned from DB
    mock_history = [
        {"role": "user", "text": "Hi", "timestamp": datetime.now()},
        {"role": "model", "text": "Hello", "timestamp": datetime.now()}
    ]
    mock_messages.find.return_value.to_list.return_value = mock_history
    
    history = await lead_manager.get_chat_history("123", limit=10)
    
    # Verify Cache Miss behavior
    mock_context_manager.get_history.assert_called_once_with("123")
    mock_messages.find.assert_called_once()
    mock_context_manager.set_history.assert_called_once()

    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[0]["parts"] == ["Hi"]
    assert history[1]["role"] == "model"
    assert history[1]["parts"] == ["Hello"]

@pytest.mark.asyncio
async def test_get_chat_history_cache_hit(mock_lead_manager_db):
    _, mock_messages, mock_context_manager = mock_lead_manager_db
    lead_manager = LeadManager()
    
    # Mock data returned from Cache
    cached_history = [
        {"role": "user", "parts": ["Hi Cached"]},
        {"role": "model", "parts": ["Hello Cached"]}
    ]
    mock_context_manager.get_history.return_value = cached_history
    
    history = await lead_manager.get_chat_history("123", limit=10)
    
    # Verify Cache Hit behavior
    mock_context_manager.get_history.assert_called_once_with("123")
    mock_messages.find.assert_not_called()
    mock_context_manager.set_history.assert_not_called()

    assert len(history) == 2
    assert history[0]["parts"] == ["Hi Cached"]

@pytest.mark.asyncio
async def test_log_message(mock_lead_manager_db):
    _, mock_messages, mock_context_manager = mock_lead_manager_db
    lead_manager = LeadManager()
    
    await lead_manager.log_message("123", "user", "Test message")
    
    mock_messages.insert_one.assert_called_once()
    call_args = mock_messages.insert_one.call_args
    doc = call_args[0][0]
    
    assert doc["chat_id"] == "123"
    assert doc["role"] == "user"
    assert doc["text"] == "Test message"
    assert "timestamp" in doc
    
    # Verify Cache Update
    mock_context_manager.update_history.assert_called_once_with("123", "user", "Test message")