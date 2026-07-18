"""
Tests for WhatsAppClient — focuses on the new send_chat_state_typing helper.
"""

import pytest
from unittest.mock import AsyncMock, patch
from app.services.whatsapp_client_service import WhatsAppClient


@pytest.mark.asyncio
async def test_send_chat_state_typing_posts_correct_payload(monkeypatch):
    client = WhatsAppClient()
    mock_send = AsyncMock()
    monkeypatch.setattr(client, "_send_request", mock_send)
    # Isolate from the real Redis-backed PRO-71 breaker (PRO-78): a live yellowCard
    # would otherwise suppress the send and fail this payload assertion.
    monkeypatch.setattr(client, "_is_outbound_paused", AsyncMock(return_value=False))

    await client.send_chat_state_typing("972500000000@c.us")

    mock_send.assert_awaited_once_with(
        "sendChatStateTyping", {"chatId": "972500000000@c.us"}
    )


@pytest.mark.asyncio
async def test_send_chat_state_typing_swallows_errors(monkeypatch):
    client = WhatsAppClient()
    monkeypatch.setattr(
        client, "_send_request", AsyncMock(side_effect=Exception("network down"))
    )
    monkeypatch.setattr(client, "_is_outbound_paused", AsyncMock(return_value=False))

    # Must not raise
    await client.send_chat_state_typing("972500000000@c.us")
