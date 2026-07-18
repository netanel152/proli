"""
PRO-71 — Outbound circuit breaker (``wa:instance:paused``).

``WhatsAppClient._is_outbound_paused()`` checks a Redis key before every
outbound send. Set by the deauth monitor (``check_whatsapp_instance_state``)
the moment the instance goes non-authorized, or by hand as a manual kill
switch. Tests here cover:

  * paused  → ``send_message`` / ``send_file_by_url`` short-circuit before
    ``_send_request`` (no HTTP call), logging a WARNING that names the
    chat_id and never claims a successful send.
  * not paused → normal send path, ``_send_request`` is called.
  * Redis unreachable while checking the flag → fail-open: the send proceeds
    as if not paused (a monitoring dependency must never take down sends).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

import app.services.whatsapp_client_service as wa_module
from app.services.whatsapp_client_service import WhatsAppClient


class _FakeRedis:
    """Minimal async Redis stub exposing only ``exists``, the sole call
    ``_is_outbound_paused`` makes."""

    def __init__(self, paused: bool):
        self._paused = paused

    async def exists(self, *keys: str) -> int:
        # Mirrors redis EXISTS(k1, k2, ...) → count of existing keys. The breaker
        # only cares about bool(count), so any positive count when paused suffices.
        return 1 if self._paused else 0


def _redis_factory(paused: bool) -> AsyncMock:
    """Return an AsyncMock that, when awaited, yields a _FakeRedis(paused)."""
    return AsyncMock(return_value=_FakeRedis(paused))


CHAT_ID = "972500000000@c.us"


# ===========================================================================
# Paused → short-circuit before any HTTP call
# ===========================================================================


@pytest.mark.asyncio
async def test_send_message_paused_does_not_call_send_request(monkeypatch):
    client = WhatsAppClient()
    mock_send_request = AsyncMock()
    monkeypatch.setattr(client, "_send_request", mock_send_request)
    monkeypatch.setattr(wa_module, "get_redis_client", _redis_factory(True))
    mock_logger = MagicMock()
    monkeypatch.setattr(wa_module, "logger", mock_logger)

    await client.send_message(CHAT_ID, "hello")

    mock_send_request.assert_not_awaited()
    mock_logger.warning.assert_called_once()
    warning_text = mock_logger.warning.call_args[0][0]
    # chat_id is masked in logs (PII) — only the trailing fragment appears.
    assert CHAT_ID[-8:] in warning_text
    assert "972500000000" not in warning_text
    assert "Message sent" not in warning_text


@pytest.mark.asyncio
async def test_send_file_by_url_paused_does_not_call_send_request(monkeypatch):
    client = WhatsAppClient()
    mock_send_request = AsyncMock()
    monkeypatch.setattr(client, "_send_request", mock_send_request)
    monkeypatch.setattr(wa_module, "get_redis_client", _redis_factory(True))
    mock_logger = MagicMock()
    monkeypatch.setattr(wa_module, "logger", mock_logger)

    await client.send_file_by_url(CHAT_ID, "https://example.com/a.jpg")

    mock_send_request.assert_not_awaited()
    mock_logger.warning.assert_called_once()
    warning_text = mock_logger.warning.call_args[0][0]
    # chat_id is masked in logs (PII) — only the trailing fragment appears.
    assert CHAT_ID[-8:] in warning_text
    assert "972500000000" not in warning_text
    assert "File sent" not in warning_text


# ===========================================================================
# Not paused → normal send path
# ===========================================================================


@pytest.mark.asyncio
async def test_send_message_not_paused_sends_normally(monkeypatch):
    client = WhatsAppClient()
    mock_send_request = AsyncMock(return_value={"idMessage": "abc"})
    monkeypatch.setattr(client, "_send_request", mock_send_request)
    monkeypatch.setattr(wa_module, "get_redis_client", _redis_factory(False))

    await client.send_message(CHAT_ID, "hello")

    mock_send_request.assert_awaited_once_with(
        "sendMessage", {"chatId": CHAT_ID, "message": "hello"}
    )


@pytest.mark.asyncio
async def test_send_file_by_url_not_paused_sends_normally(monkeypatch):
    client = WhatsAppClient()
    mock_send_request = AsyncMock(return_value={"idMessage": "abc"})
    monkeypatch.setattr(client, "_send_request", mock_send_request)
    monkeypatch.setattr(wa_module, "get_redis_client", _redis_factory(False))

    await client.send_file_by_url(
        CHAT_ID, "https://example.com/a.jpg", caption="c", file_name="a.jpg"
    )

    mock_send_request.assert_awaited_once_with(
        "sendFileByUrl",
        {
            "chatId": CHAT_ID,
            "urlFile": "https://example.com/a.jpg",
            "fileName": "a.jpg",
            "caption": "c",
        },
    )


# ===========================================================================
# Fail-open: Redis unreachable while checking the pause flag
# ===========================================================================


@pytest.mark.asyncio
async def test_send_message_redis_down_fails_open_and_sends(monkeypatch):
    """Redis unreachable while checking the pause flag must not block sends
    — ``_is_outbound_paused`` fails open per its own docstring."""
    client = WhatsAppClient()
    mock_send_request = AsyncMock(return_value={"idMessage": "abc"})
    monkeypatch.setattr(client, "_send_request", mock_send_request)
    monkeypatch.setattr(
        wa_module, "get_redis_client", AsyncMock(side_effect=Exception("redis down"))
    )

    await client.send_message(CHAT_ID, "hello")

    mock_send_request.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_file_by_url_redis_down_fails_open_and_sends(monkeypatch):
    client = WhatsAppClient()
    mock_send_request = AsyncMock(return_value={"idMessage": "abc"})
    monkeypatch.setattr(client, "_send_request", mock_send_request)
    monkeypatch.setattr(
        wa_module, "get_redis_client", AsyncMock(side_effect=Exception("redis down"))
    )

    await client.send_file_by_url(CHAT_ID, "https://example.com/a.jpg")

    mock_send_request.assert_awaited_once()


# ===========================================================================
# _is_outbound_paused() directly
# ===========================================================================


@pytest.mark.asyncio
async def test_is_outbound_paused_true_when_key_exists(monkeypatch):
    monkeypatch.setattr(wa_module, "get_redis_client", _redis_factory(True))
    client = WhatsAppClient()

    assert await client._is_outbound_paused() is True


@pytest.mark.asyncio
async def test_is_outbound_paused_false_when_key_absent(monkeypatch):
    monkeypatch.setattr(wa_module, "get_redis_client", _redis_factory(False))
    client = WhatsAppClient()

    assert await client._is_outbound_paused() is False


# --- PRO-79: WHATSAPP_DRY_RUN (local/dev never sends real WhatsApp) ---


@pytest.mark.asyncio
async def test_send_message_dry_run_does_not_call_send_request(monkeypatch):
    client = WhatsAppClient()
    mock_send_request = AsyncMock()
    monkeypatch.setattr(client, "_send_request", mock_send_request)
    monkeypatch.setattr(wa_module.settings, "WHATSAPP_DRY_RUN", True)

    await client.send_message(CHAT_ID, "hello")

    mock_send_request.assert_not_awaited()  # logged, never transmitted


@pytest.mark.asyncio
async def test_send_file_dry_run_does_not_call_send_request(monkeypatch):
    client = WhatsAppClient()
    mock_send_request = AsyncMock()
    monkeypatch.setattr(client, "_send_request", mock_send_request)
    monkeypatch.setattr(wa_module.settings, "WHATSAPP_DRY_RUN", True)

    await client.send_file_by_url(CHAT_ID, "https://example.com/a.jpg")

    mock_send_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_dry_run_off_sends_normally(monkeypatch):
    """Sanity: with dry-run off the send path runs (regression guard on the gate)."""
    client = WhatsAppClient()
    mock_send_request = AsyncMock(return_value={"idMessage": "abc"})
    monkeypatch.setattr(client, "_send_request", mock_send_request)
    monkeypatch.setattr(wa_module.settings, "WHATSAPP_DRY_RUN", False)
    monkeypatch.setattr(wa_module, "get_redis_client", _redis_factory(False))

    await client.send_message(CHAT_ID, "hello")

    mock_send_request.assert_awaited_once()
