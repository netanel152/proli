import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.workflow import process_incoming_message
from app.services.ai_engine import AIResponse, ExtractedData
import httpx

@pytest.fixture
def mock_dependencies():
    with patch("app.services.workflow.lead_manager") as mock_lm, \
         patch("app.services.workflow.whatsapp") as mock_wa, \
         patch("app.services.workflow.ai") as mock_ai, \
         patch("app.services.workflow.users_collection") as mock_users, \
         patch("app.services.workflow.leads_collection") as mock_leads:
        
        # Defaults
        mock_lm.get_chat_history = AsyncMock(return_value=[])
        mock_lm.log_message = AsyncMock()
        mock_wa.send_message = AsyncMock()
        mock_users.find.return_value.to_list = AsyncMock(return_value=[]) # No pros default
        
        # Ensure AI method is AsyncMock
        mock_ai.analyze_conversation = AsyncMock()
        
        yield mock_lm, mock_wa, mock_ai, mock_users, mock_leads

@pytest.mark.asyncio
async def test_process_gemini_failure(mock_dependencies):
    mock_lm, mock_wa, mock_ai, _, _ = mock_dependencies
    
    # Simulate Gemini failing safely (returning fallback response)
    # The AIEngine catches exceptions and returns a fallback message
    fallback_resp = AIResponse(
        reply_to_user="Service Unavailable",
        extracted_data=ExtractedData(city=None, issue=None, full_address=None, appointment_time=None),
        transcription=None
    )
    # Since mock_ai.analyze_conversation is AsyncMock, setting return_value works for await
    mock_ai.analyze_conversation.return_value = fallback_resp
    
    await process_incoming_message("123", "Hello")
    
    mock_wa.send_message.assert_called_with("123", "Service Unavailable")


@pytest.mark.asyncio
async def test_process_whatsapp_down(mock_dependencies):
    mock_lm, mock_wa, mock_ai, _, _ = mock_dependencies
    
    # Mock AI success
    mock_ai.analyze_conversation.return_value = AIResponse(
        reply_to_user="Hi there",
        extracted_data=ExtractedData(city=None, issue=None, full_address=None, appointment_time=None),
        transcription=None
    )
    
    # Mock WhatsApp failure
    mock_wa.send_message.side_effect = httpx.HTTPError("WhatsApp API Down")
    
    with pytest.raises(httpx.HTTPError):
        await process_incoming_message("123", "Hello")

@pytest.mark.asyncio
async def test_bad_input_file_type(mock_dependencies):
    mock_lm, mock_wa, mock_ai, _, _ = mock_dependencies

    # Simulate media URL with PDF content type
    media_url = "http://example.com/file.pdf"
    
    # We need to mock httpx to return a PDF content type
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"%PDF..."
        mock_resp.headers = {"Content-Type": "application/pdf"}
        mock_client.get.return_value = mock_resp
        
        # We want to ensure it doesn't crash and maybe passes info to AI
        mock_ai.analyze_conversation.return_value = AIResponse(
            reply_to_user="I see a PDF",
            extracted_data=ExtractedData(city=None, issue=None, full_address=None, appointment_time=None),
            transcription=None
        )
        
        await process_incoming_message("123", "Here is a file", media_url=media_url)
        
        # Verify AI was called with the PDF mime type
        call_args = mock_ai.analyze_conversation.call_args
        assert call_args.kwargs["media_mime_type"] == "application/pdf"
