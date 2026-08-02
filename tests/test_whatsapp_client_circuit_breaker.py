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

import asyncio
import json

import httpx
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


# --- PRO-79 / PRO-83: WHATSAPP_DRY_RUN (local/dev never sends real WhatsApp) ---
#
# PRO-83 moved the dry-run divergence from three early returns in the send methods
# down to the httpx transport. The property under test is therefore stronger than
# "``_send_request`` was not awaited": the full production send path *does* run —
# payload, URL, breaker, retry policy — and only the socket is absent. These tests
# assert both halves: the payload is genuinely built, and no real network
# transport is ever reached.


def _spy_on_dry_run_handler(monkeypatch) -> list:
    """Record every request that reaches the dry-run transport, delegating to the
    real handler so its response contract is exercised too."""
    captured = []
    real_handler = wa_module._dry_run_handler

    def spy(request):
        captured.append(request)
        return real_handler(request)

    monkeypatch.setattr(wa_module, "_dry_run_handler", spy)
    return captured


def _forbid_real_network(monkeypatch) -> None:
    """Any attempt to open a real connection fails the test loudly."""

    async def _boom(*args, **kwargs):
        raise AssertionError("a real HTTP connection was attempted under dry-run")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _boom)


@pytest.mark.asyncio
async def test_dry_run_builds_the_real_payload_and_never_hits_the_network(monkeypatch):
    monkeypatch.setattr(wa_module.settings, "WHATSAPP_DRY_RUN", True)
    monkeypatch.setattr(wa_module, "get_redis_client", _redis_factory(False))
    captured = _spy_on_dry_run_handler(monkeypatch)
    _forbid_real_network(monkeypatch)
    client = WhatsAppClient()

    await client.send_message(CHAT_ID, "שלום 👋\nשורה שנייה")

    assert len(captured) == 1
    assert wa_module._endpoint_of(captured[0]) == "sendMessage"
    assert json.loads(captured[0].content) == {
        "chatId": CHAT_ID,
        "message": "שלום 👋\nשורה שנייה",
    }


@pytest.mark.asyncio
async def test_dry_run_send_file_builds_the_real_payload(monkeypatch):
    monkeypatch.setattr(wa_module.settings, "WHATSAPP_DRY_RUN", True)
    monkeypatch.setattr(wa_module, "get_redis_client", _redis_factory(False))
    captured = _spy_on_dry_run_handler(monkeypatch)
    _forbid_real_network(monkeypatch)
    client = WhatsAppClient()

    await client.send_file_by_url(CHAT_ID, "https://example.com/a.jpg", caption="כיתוב")

    assert len(captured) == 1
    assert wa_module._endpoint_of(captured[0]) == "sendFileByUrl"
    assert json.loads(captured[0].content) == {
        "chatId": CHAT_ID,
        "urlFile": "https://example.com/a.jpg",
        "fileName": "media.jpg",
        "caption": "כיתוב",
    }


@pytest.mark.asyncio
async def test_dry_run_still_honours_the_circuit_breaker(monkeypatch):
    """The breaker sits above the transport, so a paused instance suppresses the
    send even in dry-run. Before PRO-83 dry-run returned first and bypassed it."""
    monkeypatch.setattr(wa_module.settings, "WHATSAPP_DRY_RUN", True)
    monkeypatch.setattr(wa_module, "get_redis_client", _redis_factory(True))
    captured = _spy_on_dry_run_handler(monkeypatch)
    _forbid_real_network(monkeypatch)
    client = WhatsAppClient()

    await client.send_message(CHAT_ID, "hello")

    assert captured == []


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


@pytest.mark.asyncio
async def test_cached_client_is_rebuilt_when_the_dry_run_flag_flips(monkeypatch):
    """A client built while dry-run was on must not be reused once it is off —
    otherwise a runtime toggle would silently keep swallowing production sends."""
    monkeypatch.setattr(wa_module, "get_redis_client", _redis_factory(False))
    client = WhatsAppClient()

    monkeypatch.setattr(wa_module.settings, "WHATSAPP_DRY_RUN", True)
    dry_client = await client._get_client()
    assert isinstance(dry_client._transport, httpx.MockTransport)

    monkeypatch.setattr(wa_module.settings, "WHATSAPP_DRY_RUN", False)
    live_client = await client._get_client()
    assert live_client is not dry_client
    assert not isinstance(live_client._transport, httpx.MockTransport)

    await client.close()


# ===========================================================================
# _endpoint_of — odd URL shapes (PRO-83 review)
# ===========================================================================


def test_endpoint_of_single_segment_path_never_returns_the_token():
    """Fewer than two path segments is never expected against the real Green API,
    but the fallback must not be the path itself: on a one-segment URL that segment
    is the API token, and this value is written straight into a log line."""
    request = httpx.Request("GET", "https://api.green-api.com/a-secret-token")

    assert wa_module._endpoint_of(request) == "unknown"


def test_endpoint_of_root_path_returns_unknown():
    request = httpx.Request("GET", "https://api.green-api.com/")

    assert wa_module._endpoint_of(request) == "unknown"


# ===========================================================================
# _dry_run_handler — direct unit tests (PRO-83 review)
# ===========================================================================


def _make_request(path: str, content: bytes | None = None) -> httpx.Request:
    if content is None:
        return httpx.Request("GET", f"https://api.green-api.com{path}")
    return httpx.Request("POST", f"https://api.green-api.com{path}", content=content)


def test_dry_run_handler_send_endpoint_returns_generic_success():
    request = _make_request(
        "/waInstance123/sendMessage/tok", content=b'{"chatId": "x", "message": "hi"}'
    )

    response = wa_module._dry_run_handler(request)

    assert response.status_code == 200
    assert response.json() == {"idMessage": "dry-run"}


def test_dry_run_handler_malformed_json_body_does_not_raise():
    """The body is only parsed for a log preview — a non-JSON payload must not
    blow up the handler (logging must never break a fake send either)."""
    request = _make_request(
        "/waInstance123/sendMessage/tok", content=b"not-json-at-all"
    )

    response = wa_module._dry_run_handler(request)

    assert response.status_code == 200
    assert response.json() == {"idMessage": "dry-run"}


def test_dry_run_handler_empty_body_does_not_raise():
    """A body-less request leaves ``request.content`` falsy, so the handler must
    skip json.loads entirely rather than choke on an empty payload."""
    request = _make_request("/waInstance123/sendChatStateTyping/tok")

    response = wa_module._dry_run_handler(request)

    assert response.status_code == 200
    assert response.json() == {"idMessage": "dry-run"}


# ===========================================================================
# get_state_instance() under dry-run (PRO-83 review)
# ===========================================================================


@pytest.mark.asyncio
async def test_the_state_probe_stays_real_under_dry_run(monkeypatch):
    """Dry-run means "send nothing to anybody", not "stop looking at our own
    instance".

    ``docs/RUNBOOK_WHATSAPP_OUTAGE.md`` tells the operator to set
    WHATSAPP_DRY_RUN=true *in production* during an incident. If the probe were
    fed a synthetic "authorized" then, the PRO-20 deauth monitor would be
    permanently green and would delete the PRO-71 breaker keys on every tick —
    precisely when they matter most. So the probe gets its own always-real client.
    """
    monkeypatch.setattr(wa_module.settings, "WHATSAPP_DRY_RUN", True)
    client = WhatsAppClient()

    send_client = await client._get_client()
    probe_client = await client._get_probe_client()

    assert isinstance(send_client._transport, httpx.MockTransport), "sends are faked"
    assert not isinstance(
        probe_client._transport, httpx.MockTransport
    ), "the state probe must not be faked"

    await client.close()


@pytest.mark.asyncio
async def test_state_probe_failure_is_swallowed_and_reported_as_unknown(monkeypatch):
    """Being read-only and best-effort, an unreachable probe returns None so the
    monitor can treat "unreachable" like "not authorized" without raising."""
    monkeypatch.setattr(wa_module.settings, "WHATSAPP_DRY_RUN", True)
    _forbid_real_network(monkeypatch)
    client = WhatsAppClient()

    assert await client.get_state_instance() is None

    await client.close()


@pytest.mark.asyncio
async def test_close_shuts_down_both_the_send_and_probe_clients(monkeypatch):
    monkeypatch.setattr(wa_module.settings, "WHATSAPP_DRY_RUN", True)
    client = WhatsAppClient()
    send_client = await client._get_client()
    probe_client = await client._get_probe_client()

    await client.close()

    assert send_client.is_closed and probe_client.is_closed
    assert client._client is None and client._probe_client is None
    assert client._client_dry_run is None


# ===========================================================================
# close() resets the cached client and its mode (PRO-83 review)
# ===========================================================================


@pytest.mark.asyncio
async def test_close_resets_client_and_dry_run_mode(monkeypatch):
    """Both cached-client fields must be cleared on close() — a stale
    ``_client_dry_run`` left behind could make a later, freshly-rebuilt client
    compare equal to whatever the flag happens to be and get served as if it
    were still valid without ever passing through ``_build_client`` again."""
    monkeypatch.setattr(wa_module.settings, "WHATSAPP_DRY_RUN", True)
    client = WhatsAppClient()

    first = await client._get_client()
    assert client._client is first
    assert client._client_dry_run is True

    await client.close()

    assert client._client is None
    assert client._client_dry_run is None
    assert first.is_closed


# ===========================================================================
# _get_client() double-checked locking under concurrency (PRO-83 review)
# ===========================================================================


@pytest.mark.asyncio
async def test_get_client_concurrent_callers_build_client_once(monkeypatch):
    """The outer check in ``_get_client`` is a deliberately unsynchronized fast
    path; the re-check *inside* the lock is what actually stops two concurrent
    callers from each building (and leaking) their own httpx.AsyncClient. Force
    every caller to interleave right at the lock boundary — before any of them
    has actually acquired it — so this test would fail if that inner re-check
    were ever dropped."""
    client = WhatsAppClient()

    class _SlowLock(asyncio.Lock):
        async def acquire(self):
            # Yield here so every concurrent caller reaches the lock boundary
            # (having already passed the outer, unsynchronized check) before
            # any single one of them actually takes the lock.
            await asyncio.sleep(0)
            return await super().acquire()

    client._client_lock = _SlowLock()

    build_calls = []
    real_build = WhatsAppClient._build_client

    def _counting_build(self, dry_run):
        build_calls.append(dry_run)
        return real_build(self, dry_run)

    monkeypatch.setattr(WhatsAppClient, "_build_client", _counting_build)

    results = await asyncio.gather(*(client._get_client() for _ in range(10)))

    assert len(build_calls) == 1
    assert all(r is results[0] for r in results)

    await client.close()
