"""Orchestrator behaviour on inputs and failures the happy path never produces.

Kept deliberately small: this file covers what nothing else does — an
unsupported media type reaching ``process_incoming_message`` (covered nowhere in
test_media_handler.py, which stops at ``detect_and_fetch_media``), and an
outbound send that raises, which must propagate so ARQ retries the job rather
than the message being silently lost.

A third test lived here until 2026-08-29: it stubbed ``analyze_conversation`` to
return "Service Unavailable" and then asserted "Service Unavailable" was sent —
a mock echoing its own stub. It could not fail, and it said nothing about how the
AI engine behaves when Gemini is actually down (that is
``ai_engine_service``'s adaptive fallback, covered in test_ai_parsing.py).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.workflow_service import process_incoming_message
from app.services.ai_engine_service import AIResponse, ExtractedData
import httpx


@pytest.fixture
def mock_dependencies():
    with patch("app.services.workflow_service.lead_manager") as mock_lm, patch(
        "app.services.workflow_service.whatsapp"
    ) as mock_wa, patch("app.services.workflow_service.ai") as mock_ai, patch(
        "app.services.workflow_service.users_collection"
    ) as mock_users, patch(
        "app.services.workflow_service.leads_collection"
    ) as mock_leads:

        # Defaults
        mock_lm.get_chat_history = AsyncMock(return_value=[])
        mock_lm.log_message = AsyncMock()
        mock_wa.send_message = AsyncMock()
        mock_wa.send_chat_state_typing = AsyncMock()
        mock_users.find.return_value.to_list = AsyncMock(
            return_value=[]
        )  # No pros default
        mock_users.find_one = AsyncMock(return_value=None)
        mock_leads.find_one = AsyncMock(return_value=None)

        # Ensure AI method is AsyncMock
        mock_ai.analyze_conversation = AsyncMock()

        yield mock_lm, mock_wa, mock_ai, mock_users, mock_leads


@pytest.mark.asyncio
async def test_process_whatsapp_down(mock_dependencies):
    mock_lm, mock_wa, mock_ai, _, _ = mock_dependencies

    # Mock AI success
    mock_ai.analyze_conversation.return_value = AIResponse(
        reply_to_user="Hi there",
        extracted_data=ExtractedData(
            city=None, issue=None, full_address=None, appointment_time=None
        ),
        transcription=None,
        is_deal=False,
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

    # We need to mock the shared http client to return a PDF content type
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b"%PDF..."
    mock_resp.headers = {"Content-Type": "application/pdf"}
    mock_client.get.return_value = mock_resp
    mock_client.head.return_value = mock_resp

    with patch(
        "app.services.media_handler.get_http_client",
        new_callable=AsyncMock,
        return_value=mock_client,
    ):

        # We want to ensure it doesn't crash and maybe passes info to AI
        mock_ai.analyze_conversation.return_value = AIResponse(
            reply_to_user="I see a PDF",
            extracted_data=ExtractedData(
                city=None, issue=None, full_address=None, appointment_time=None
            ),
            transcription=None,
            is_deal=False,
        )

        mock_lm.create_lead_from_dict = AsyncMock(return_value={"_id": "fake_id"})

        await process_incoming_message("123", "Here is a file", media_url=media_url)

        # Verify AI was called with the PDF mime type
        call_args = mock_ai.analyze_conversation.call_args
        assert call_args.kwargs["media_mime_type"] == "application/pdf"
