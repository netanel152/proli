import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.workflow import process_incoming_message
from app.services.ai_engine import AIResponse, ExtractedData

@pytest.fixture
def mock_workflow_dependencies():
    with patch("app.services.workflow.ai", new_callable=MagicMock) as mock_ai, \
         patch("app.services.workflow.users_collection") as mock_users, \
         patch("app.services.workflow.whatsapp") as mock_whatsapp, \
         patch("app.services.workflow.lead_manager") as mock_lm, \
         patch("app.services.workflow.leads_collection") as mock_leads:
        
        # Async methods setup
        mock_ai.analyze_conversation = AsyncMock()
        mock_lm.log_message = AsyncMock()
        mock_lm.get_chat_history = AsyncMock(return_value=[])
        mock_lm.create_lead = AsyncMock(return_value={"_id": "123", "full_address": "Test St", "issue_type": "Leak", "appointment_time": "10:00", "chat_id": "user_id"})
        mock_lm.create_lead_from_dict = AsyncMock(return_value={"_id": "123", "full_address": "Test St", "issue_type": "Leak", "appointment_time": "10:00", "chat_id": "user_id"})
        mock_whatsapp.send_message = AsyncMock()
        mock_whatsapp.send_location_link = AsyncMock()
        mock_users.find_one = AsyncMock(return_value=None)
        
        mock_lead_data = {"_id": "123", "full_address": "Test St", "issue_type": "Leak", "appointment_time": "10:00", "chat_id": "user_id", "status": "new"}
        mock_leads.find_one = AsyncMock(side_effect=lambda query, **kwargs: mock_lead_data if "_id" in query else None)
        mock_leads.update_one = AsyncMock()
        
        # Default users find response
        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(return_value=[])
        mock_users.find.return_value = mock_cursor

        # Fix: Mock count_documents for determine_best_pro
        mock_leads.count_documents = AsyncMock(return_value=0)

        yield mock_ai, mock_users, mock_whatsapp, mock_lm, mock_leads

@pytest.mark.asyncio
async def test_dispatcher_mode_missing_info(mock_workflow_dependencies):
    """
    Scenario: User sends message, AI Dispatcher does not find City/Issue.
    Expected: System asks clarifying questions, does NOT look for Pro.
    """
    mock_ai, mock_users, mock_whatsapp, _, _ = mock_workflow_dependencies
    
    # Mock AI response: Missing City & Issue
    response = AIResponse(
        reply_to_user="Where are you located?",
        extracted_data=ExtractedData(city=None, issue=None, full_address=None, appointment_time=None),
        transcription=None
    )
    mock_ai.analyze_conversation.return_value = response
    
    await process_incoming_message("user123", "Hello")
    
    # Assertions
    mock_users.find.assert_not_called()  # Should NOT look for a pro
    mock_whatsapp.send_message.assert_called_with("user123", "Where are you located?")
    
    # Verify we didn't call AI twice (only Dispatcher phase)
    assert mock_ai.analyze_conversation.call_count == 1

@pytest.mark.asyncio
async def test_handover_to_pro_success(mock_workflow_dependencies):
    """
    Scenario: User provides City & Issue.
    Expected: System finds Pro, switches persona, and replies as Pro.
    """
    mock_ai, mock_users, mock_whatsapp, _, _ = mock_workflow_dependencies
    
    # 1. Dispatcher Response (Found Info)
    dispatcher_resp = AIResponse(
        reply_to_user="Finding pro...",
        extracted_data=ExtractedData(city="Tel Aviv", issue="Leak", full_address=None, appointment_time=None),
        transcription=None
    )
    
    # 2. Pro Response (Persona Switch)
    pro_resp = AIResponse(
        reply_to_user="Hello, I am Mario the Plumber.",
        extracted_data=ExtractedData(city="Tel Aviv", issue="Leak", full_address=None, appointment_time=None),
        transcription=None
    )
    
    # Set side_effect for sequential calls
    mock_ai.analyze_conversation.side_effect = [dispatcher_resp, pro_resp]
    
    # Mock Pro in DB
    pro_doc = {
        "_id": "pro1",
        "business_name": "Mario Plumbing",
        "service_areas": ["Tel Aviv"],
        "is_active": True,
        "phone_number": "972500000000"
    }
    # Mock find().to_list()
    mock_cursor = MagicMock()
    mock_cursor.to_list = AsyncMock(return_value=[pro_doc])
    mock_users.find.return_value = mock_cursor
    
    await process_incoming_message("user123", "I have a leak in Tel Aviv")
    
    # Assertions
    mock_users.find.assert_called() # Should search for pro
    mock_whatsapp.send_message.assert_called_with("user123", "Hello, I am Mario the Plumber.")
    assert mock_ai.analyze_conversation.call_count == 2 # Dispatcher + Pro

@pytest.mark.asyncio
async def test_audio_transcription_flow(mock_workflow_dependencies):
    """
    Scenario: User sends Audio. Dispatcher transcribes it. User books deal.
    Expected: Transcription text is passed to the Pro via WhatsApp text.
    """
    mock_ai, mock_users, mock_whatsapp, mock_lm, _ = mock_workflow_dependencies
    
    # 1. Dispatcher: Extracts info + Transcription
    dispatcher_resp = AIResponse(
        reply_to_user="...",
        extracted_data=ExtractedData(city="Tel Aviv", issue="Leak", full_address=None, appointment_time=None),
        transcription="Water is flowing everywhere"
    )
    
    # 2. Pro: Closes Deal (Mocking that Pro also sees transcription in context)
    pro_resp = AIResponse(
        reply_to_user="[DEAL: Now | Tel Aviv | Leak]",
        extracted_data=ExtractedData(city="Tel Aviv", issue="Leak", full_address="Tel Aviv", appointment_time="Now"),
        transcription="Water is flowing everywhere",
        is_deal=True
    )
    
    mock_ai.analyze_conversation.side_effect = [dispatcher_resp, pro_resp]
    
    # Mock Pro
    pro_doc = {
        "_id": "pro1",
        "business_name": "Mario Plumbing",
        "service_areas": ["Tel Aviv"],
        "is_active": True,
        "phone_number": "972500000000"
    }
    mock_cursor = MagicMock()
    mock_cursor.to_list = AsyncMock(return_value=[pro_doc])
    mock_users.find.return_value = mock_cursor
    
    await process_incoming_message("user123", "", media_url="http://audio.mp3")
    
    # Verify Lead Creation
    assert mock_lm.create_lead.called or mock_lm.create_lead_from_dict.called
    
    # Verify Message to Pro contains Transcription
    # We expect multiple calls to send_message. We look for the one to the Pro.
    # Pro phone: 972500000000 -> 972500000000@c.us
    
    pro_chat = "972500000000@c.us"
    calls_to_pro = [call for call in mock_whatsapp.send_message.call_args_list if call[0][0] == pro_chat]
    
    assert len(calls_to_pro) >= 1
    msg_to_pro = calls_to_pro[0][0][1]
    
    assert "Mario Plumbing" not in msg_to_pro # Check logic of msg construction if needed
    assert "תמליל" in msg_to_pro
    assert "Water is flowing everywhere" in msg_to_pro
    assert "אשר" in msg_to_pro # Check for text instructions
