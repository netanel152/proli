"""PRO-89 — the Meta Cloud API provider: sends, the 24h service window, the
template registry gate, webhook parsing, and delivery-status bookkeeping.

Mirrors the style of tests/test_whatsapp_facade.py (which covers provider
*selection* and the facade layer above this module) — this file is the
transport implementation itself. HTTP is never real: every send is
intercepted via an ``httpx.MockTransport`` swapped in for
``app.providers.whatsapp.cloud_api.get_http_client``.
"""

import json
import logging
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio
from pydantic import SecretStr

from app.core.config import settings
from app.core.constants import META_ERROR_WINDOW_CLOSED, WorkerConstants
from app.providers.whatsapp import delivery, template_registry
from app.providers.whatsapp.cloud_api import (
    CloudAPIProvider,
    ServiceWindowClosedError,
    TemplateNotRegisteredError,
    _split_text,
    fetch_meta_media,
    normalize_meta_message,
    parse_meta_webhook,
    parse_status_events,
)
from app.providers.whatsapp.window import _window_key

CHAT_ID = "972501234567@c.us"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _captured_logs(fn, level="WARNING") -> str:
    """Attach a real loguru sink and run ``fn`` — caplog does not see loguru
    output (it does not propagate to stdlib logging)."""
    from app.core.logger import logger as app_logger

    lines: list[str] = []
    sink_id = app_logger.add(lambda m: lines.append(str(m)), level=level)
    try:
        fn()
    finally:
        app_logger.remove(sink_id)
    return "".join(lines)


async def _captured_logs_async(coro_fn, level="WARNING") -> str:
    from app.core.logger import logger as app_logger

    lines: list[str] = []
    sink_id = app_logger.add(lambda m: lines.append(str(m)), level=level)
    try:
        await coro_fn()
    finally:
        app_logger.remove(sink_id)
    return "".join(lines)


async def _captured_records_async(coro_fn, level="WARNING") -> list[tuple[str, str]]:
    """Like ``_captured_logs_async`` but keeps each record's level alongside
    its message — needed to pin the CRITICAL-then-ERROR page-dedupe sequence,
    which a flat string of concatenated lines cannot distinguish."""
    from app.core.logger import logger as app_logger

    records: list[tuple[str, str]] = []

    def _sink(message):
        records.append((message.record["level"].name, message.record["message"]))

    sink_id = app_logger.add(_sink, level=level)
    try:
        await coro_fn()
    finally:
        app_logger.remove(sink_id)
    return records


class _Recorder:
    """A minimal request recorder standing in for the shared httpx client."""

    def __init__(self, handler):
        self.requests: list[httpx.Request] = []
        self._handler = handler
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self._wrapped))

    def _wrapped(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)

    async def get_http_client(self):
        return self.client

    async def aclose(self):
        await self.client.aclose()


def _json_response(status_code: int, payload: dict) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


def _accepted(wamid="wamid.TEST1"):
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(200, {"messages": [{"id": wamid}]})

    return handler


@pytest.fixture(autouse=True)
def _cloud_provider_env(monkeypatch):
    """Common Meta config for every test in this file."""
    monkeypatch.setattr(settings, "META_ACCESS_TOKEN", SecretStr("test-token"))
    monkeypatch.setattr(settings, "META_PHONE_NUMBER_ID", "1234567890")
    monkeypatch.setattr(settings, "META_GRAPH_API_VERSION", "v23.0")


async def _open_window(fake_redis, chat_id=CHAT_ID):
    await fake_redis.set(_window_key(chat_id), "2026-01-01T00:00:00+00:00", ex=100)


def _patch_http_client(monkeypatch, handler) -> _Recorder:
    import app.providers.whatsapp.cloud_api as cloud_api_module

    recorder = _Recorder(handler)
    monkeypatch.setattr(cloud_api_module, "get_http_client", recorder.get_http_client)
    return recorder


@pytest_asyncio.fixture
async def install_recorder(monkeypatch):
    """Factory fixture: ``install_recorder(handler)`` swaps the shared http
    client for a ``MockTransport``-backed one and returns the ``_Recorder`` so
    a test can inspect captured requests.

    Every recorder the factory builds is closed at teardown. This module
    constructs dozens of them across its tests; leaving each
    ``httpx.AsyncClient`` unclosed doesn't leak a real socket (MockTransport
    holds none) but does print an "Unclosed client" ResourceWarning per test —
    noise a review flagged as test hygiene worth fixing.
    """
    created: list[_Recorder] = []

    def _install(handler) -> _Recorder:
        recorder = _patch_http_client(monkeypatch, handler)
        created.append(recorder)
        return recorder

    yield _install

    for recorder in created:
        await recorder.aclose()


# ---------------------------------------------------------------------------
# 1-2. send_text — the 24h service window
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_text_with_window_open_posts_and_records_delivery(
    install_recorder, fake_redis, mock_db, monkeypatch
):
    await _open_window(fake_redis)
    recorder = install_recorder(_accepted("wamid.ABC"))
    monkeypatch.setattr(delivery, "wa_delivery_collection", mock_db.wa_delivery)

    provider = CloudAPIProvider()
    result = await provider.send_text(CHAT_ID, "hello there")

    assert result == {
        "idMessage": "wamid.ABC",
        "meta": {"messages": [{"id": "wamid.ABC"}]},
    }
    assert len(recorder.requests) == 1
    request = recorder.requests[0]
    assert request.url == (f"https://graph.facebook.com/v23.0/1234567890/messages")
    body = json.loads(request.content)
    assert body["messaging_product"] == "whatsapp"
    assert body["to"] == "972501234567"  # no @c.us suffix
    assert body["type"] == "text"
    assert body["text"]["body"] == "hello there"

    doc = await mock_db.wa_delivery.find_one({"wa_message_id": "wamid.ABC"})
    assert doc is not None
    assert doc["kind"] == "text"
    assert doc["status"] == "accepted"
    assert doc["chat_id"] == CHAT_ID


@pytest.mark.asyncio
async def test_send_text_with_window_closed_raises_and_pages_no_http(
    install_recorder, fake_redis
):
    recorder = install_recorder(_accepted())

    async def _send():
        provider = CloudAPIProvider()
        with pytest.raises(ServiceWindowClosedError):
            await provider.send_text(CHAT_ID, "hello")

    text = await _captured_logs_async(_send, level="CRITICAL")

    assert recorder.requests == [], "a send escaped a closed service window"
    assert "24h service window closed" in text or "service window" in text.lower()


# ---------------------------------------------------------------------------
# 3. Window fail-open on a Redis error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_window_fails_open_on_redis_error(monkeypatch):
    import app.providers.whatsapp.window as window_module

    monkeypatch.setattr(
        window_module,
        "get_redis_client",
        AsyncMock(side_effect=Exception("redis down")),
    )

    assert await window_module.is_service_window_open(CHAT_ID) is True


@pytest.mark.asyncio
async def test_open_service_window_fails_open_on_redis_error(monkeypatch):
    """The write side of the same contract: a Redis outage while recording the
    window must not raise into the Meta webhook route's request handler — the
    route already answers Meta 200 either way, but a raise here would trip the
    route's outer except and report status "error" for an inbound message that
    otherwise parsed and enqueued fine."""
    import app.providers.whatsapp.window as window_module

    monkeypatch.setattr(
        window_module,
        "get_redis_client",
        AsyncMock(side_effect=Exception("redis down")),
    )

    # Must not raise.
    await window_module.open_service_window(CHAT_ID)


# ---------------------------------------------------------------------------
# 4. send_template — the registry gate; never window-gated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_template_unknown_key_raises_no_http(install_recorder):
    recorder = install_recorder(_accepted())

    provider = CloudAPIProvider()
    with pytest.raises(TemplateNotRegisteredError):
        await provider.send_template(CHAT_ID, "not_a_real_template")

    assert recorder.requests == []


@pytest.mark.asyncio
async def test_send_template_draft_key_raises_no_http(install_recorder):
    """A registered-but-DRAFT key is indistinguishable from unknown to the
    caller — both must raise, never transmit."""
    recorder = install_recorder(_accepted())

    provider = CloudAPIProvider()
    with pytest.raises(TemplateNotRegisteredError):
        await provider.send_template(CHAT_ID, "lead_offer")  # DRAFT in registry

    assert recorder.requests == []


@pytest.mark.asyncio
async def test_send_template_approved_key_posts_positional_params_no_window_needed(
    install_recorder, fake_redis, mock_db, monkeypatch
):
    """No window key is set anywhere in this test — template sends bypass the
    24h gate entirely."""
    approved = template_registry.TemplateSpec(
        key="test_template",
        meta_name="proli_test_template",
        status=template_registry.TemplateStatus.APPROVED,
        language="he",
    )
    monkeypatch.setattr(
        template_registry,
        "TEMPLATES",
        {**template_registry.TEMPLATES, "test_template": approved},
    )
    recorder = install_recorder(_accepted("wamid.TPL"))
    monkeypatch.setattr(delivery, "wa_delivery_collection", mock_db.wa_delivery)

    provider = CloudAPIProvider()
    await provider.send_template(
        CHAT_ID, "test_template", params={"name": "Dana", "city": "Tel Aviv"}
    )

    assert len(recorder.requests) == 1
    body = json.loads(recorder.requests[0].content)
    assert body["type"] == "template"
    assert body["template"]["name"] == "proli_test_template"
    assert body["template"]["language"] == {"code": "he"}
    params = body["template"]["components"][0]["parameters"]
    assert [p["text"] for p in params] == ["Dana", "Tel Aviv"]  # insertion order


# ---------------------------------------------------------------------------
# 5. send_interactive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_interactive_three_options_sends_buttons(
    install_recorder, fake_redis
):
    await _open_window(fake_redis)
    recorder = install_recorder(_accepted())

    provider = CloudAPIProvider()
    long_title = "x" * 30
    await provider.send_interactive(CHAT_ID, "pick one", ["a", "b", long_title])

    body = json.loads(recorder.requests[0].content)
    assert body["type"] == "interactive"
    assert body["interactive"]["type"] == "button"
    buttons = body["interactive"]["action"]["buttons"]
    assert [b["reply"]["id"] for b in buttons] == ["1", "2", "3"]
    assert buttons[2]["reply"]["title"] == long_title[:20]
    assert len(buttons[2]["reply"]["title"]) == 20


@pytest.mark.asyncio
async def test_send_interactive_five_options_sends_a_list(install_recorder, fake_redis):
    await _open_window(fake_redis)
    recorder = install_recorder(_accepted())

    provider = CloudAPIProvider()
    long_title = "y" * 40
    options = ["a", "b", "c", "d", long_title]
    await provider.send_interactive(CHAT_ID, "pick one", options)

    body = json.loads(recorder.requests[0].content)
    assert body["interactive"]["type"] == "list"
    rows = body["interactive"]["action"]["sections"][0]["rows"]
    assert [r["id"] for r in rows] == ["1", "2", "3", "4", "5"]
    assert rows[4]["title"] == long_title[:24]
    assert len(rows[4]["title"]) == 24


@pytest.mark.asyncio
async def test_send_interactive_eleven_options_falls_back_to_numbered_text(
    install_recorder, fake_redis
):
    """No window key is set — if the fallback truly routed through send_text's
    window gate it would raise; falling back to a plain post proves the
    'numeric text menu' path only, not the interactive payload."""
    recorder = install_recorder(_accepted())

    provider = CloudAPIProvider()
    options = [f"opt{i}" for i in range(11)]

    with pytest.raises(ServiceWindowClosedError):
        await provider.send_interactive(CHAT_ID, "pick one", options)

    # It never even reached the interactive payload branch — no HTTP call was
    # made because send_text's own window gate raised first.
    assert recorder.requests == []


@pytest.mark.asyncio
async def test_send_interactive_eleven_options_with_window_open_sends_numbered_text(
    install_recorder, fake_redis
):
    await _open_window(fake_redis)
    recorder = install_recorder(_accepted())

    provider = CloudAPIProvider()
    options = [f"opt{i}" for i in range(11)]
    await provider.send_interactive(CHAT_ID, "pick one", options)

    assert len(recorder.requests) == 1
    body = json.loads(recorder.requests[0].content)
    assert body["type"] == "text"
    assert "1. opt0" in body["text"]["body"]
    assert "11. opt10" in body["text"]["body"]


@pytest.mark.asyncio
async def test_send_interactive_window_closed_raises_and_pages(
    install_recorder, fake_redis
):
    recorder = install_recorder(_accepted())

    async def _send():
        provider = CloudAPIProvider()
        with pytest.raises(ServiceWindowClosedError):
            await provider.send_interactive(CHAT_ID, "pick one", ["a", "b"])

    text = await _captured_logs_async(_send, level="CRITICAL")

    assert recorder.requests == []
    assert "service window" in text.lower()


@pytest.mark.asyncio
async def test_send_interactive_oversized_body_falls_back_to_numbered_text(
    install_recorder, fake_redis
):
    """A body over Meta's 1024-char interactive limit must degrade to the
    numeric text menu rather than 400 at Graph — same rule as too many rows."""
    await _open_window(fake_redis)
    recorder = install_recorder(_accepted())

    provider = CloudAPIProvider()
    oversized_body = "x" * 1025
    await provider.send_interactive(CHAT_ID, oversized_body, ["a", "b"])

    assert len(recorder.requests) == 1
    body = json.loads(recorder.requests[0].content)
    assert body["type"] == "text"
    assert oversized_body in body["text"]["body"]
    assert "1. a" in body["text"]["body"]
    assert "2. b" in body["text"]["body"]


@pytest.mark.asyncio
async def test_send_interactive_truncation_collision_falls_back_to_numbered_text(
    install_recorder, fake_redis
):
    """Two options that are only distinguishable after character 20 become
    identical button titles once Meta's truncation applies — Graph would
    reject that outright, so the provider must catch it before sending, not
    after a 400."""
    await _open_window(fake_redis)
    recorder = install_recorder(_accepted())

    provider = CloudAPIProvider()
    shared_prefix = "x" * 20
    option_a = shared_prefix + " variant A"
    option_b = shared_prefix + " variant B"
    await provider.send_interactive(CHAT_ID, "pick one", [option_a, option_b])

    assert len(recorder.requests) == 1
    body = json.loads(recorder.requests[0].content)
    assert body["type"] == "text"
    assert f"1. {option_a}" in body["text"]["body"]
    assert f"2. {option_b}" in body["text"]["body"]


# ---------------------------------------------------------------------------
# 6. send_file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_file_image_sends_image_payload_with_caption(
    install_recorder, fake_redis
):
    await _open_window(fake_redis)
    recorder = install_recorder(_accepted())

    provider = CloudAPIProvider()
    await provider.send_file(CHAT_ID, "https://cdn.example.com/x.jpg", caption="look")

    body = json.loads(recorder.requests[0].content)
    assert body["type"] == "image"
    assert body["image"]["link"] == "https://cdn.example.com/x.jpg"
    assert body["image"]["caption"] == "look"


@pytest.mark.asyncio
async def test_send_file_video_sends_video_payload(install_recorder, fake_redis):
    await _open_window(fake_redis)
    recorder = install_recorder(_accepted())

    provider = CloudAPIProvider()
    await provider.send_file(CHAT_ID, "https://cdn.example.com/x.mp4", caption="c")

    body = json.loads(recorder.requests[0].content)
    assert body["type"] == "video"
    assert body["video"]["link"] == "https://cdn.example.com/x.mp4"


@pytest.mark.asyncio
async def test_send_file_audio_sends_audio_then_caption_as_separate_text(
    install_recorder, fake_redis
):
    await _open_window(fake_redis)
    recorder = install_recorder(_accepted())

    provider = CloudAPIProvider()
    await provider.send_file(
        CHAT_ID, "https://cdn.example.com/x.ogg", caption="voice note text"
    )

    assert len(recorder.requests) == 2
    audio_body = json.loads(recorder.requests[0].content)
    assert audio_body["type"] == "audio"
    assert "caption" not in audio_body["audio"]
    assert audio_body["audio"]["link"] == "https://cdn.example.com/x.ogg"

    text_body = json.loads(recorder.requests[1].content)
    assert text_body["type"] == "text"
    assert text_body["text"]["body"] == "voice note text"


@pytest.mark.asyncio
async def test_send_file_pdf_sends_document_payload_with_filename(
    install_recorder, fake_redis
):
    await _open_window(fake_redis)
    recorder = install_recorder(_accepted())

    provider = CloudAPIProvider()
    await provider.send_file(
        CHAT_ID,
        "https://cdn.example.com/report.pdf",
        caption="the report",
        file_name="report.pdf",
    )

    body = json.loads(recorder.requests[0].content)
    assert body["type"] == "document"
    assert body["document"]["filename"] == "report.pdf"
    assert body["document"]["caption"] == "the report"


# ---------------------------------------------------------------------------
# 7. Graph 4xx never degrades to None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_graph_4xx_logs_and_raises_http_status_error(
    install_recorder, fake_redis
):
    await _open_window(fake_redis)

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            400, {"error": {"code": 100, "message": "Invalid parameter"}}
        )

    recorder = install_recorder(handler)

    async def _send():
        provider = CloudAPIProvider()
        with pytest.raises(httpx.HTTPStatusError):
            await provider.send_text(CHAT_ID, "hi")

    text = await _captured_logs_async(_send, level="ERROR")

    assert len(recorder.requests) == 1
    assert "100" in text


# ---------------------------------------------------------------------------
# 8. get_state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_state_connected_maps_to_authorized(install_recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(200, {"status": "CONNECTED", "quality_rating": "GREEN"})

    install_recorder(handler)

    assert await CloudAPIProvider().get_state() == "authorized"


@pytest.mark.asyncio
async def test_get_state_flagged_passes_through_lowercased(install_recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(200, {"status": "FLAGGED"})

    install_recorder(handler)

    assert await CloudAPIProvider().get_state() == "flagged"


@pytest.mark.asyncio
async def test_get_state_http_error_returns_none(install_recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(500, {"error": {"message": "boom"}})

    install_recorder(handler)

    assert await CloudAPIProvider().get_state() is None


@pytest.mark.asyncio
async def test_get_state_unconfigured_returns_none_without_http_call(
    install_recorder, monkeypatch
):
    monkeypatch.setattr(settings, "META_ACCESS_TOKEN", None)
    recorder = install_recorder(_accepted())

    assert await CloudAPIProvider().get_state() is None
    assert recorder.requests == []


# ---------------------------------------------------------------------------
# 9. Webhook parsing — normalize_meta_message / parse_meta_webhook
# ---------------------------------------------------------------------------


def _envelope(value: dict) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_ID",
                "changes": [{"field": "messages", "value": value}],
            }
        ],
    }


def test_parse_meta_webhook_text_message():
    value = {
        "contacts": [{"wa_id": "972501234567", "profile": {"name": "Dana"}}],
        "messages": [
            {
                "from": "972501234567",
                "id": "wamid.INBOUND1",
                "type": "text",
                "text": {"body": "hello"},
            }
        ],
    }
    msg = parse_meta_webhook(_envelope(value))

    assert msg.chat_id == "972501234567@c.us"
    assert msg.text == "hello"
    assert msg.sender_name == "Dana"
    assert msg.message_id == "wamid.INBOUND1"
    assert msg.media_url is None


def test_normalize_meta_message_interactive_button_reply():
    message = {
        "from": "972501234567",
        "id": "wamid.X",
        "type": "interactive",
        "interactive": {
            "type": "button_reply",
            "button_reply": {"id": "2", "title": "Reject"},
        },
    }
    msg = normalize_meta_message(message, {})
    assert msg.text == "2"


def test_normalize_meta_message_interactive_list_reply():
    message = {
        "from": "972501234567",
        "id": "wamid.X",
        "type": "interactive",
        "interactive": {
            "type": "list_reply",
            "list_reply": {"id": "3", "title": "Foo"},
        },
    }
    msg = normalize_meta_message(message, {})
    assert msg.text == "3"


def test_normalize_meta_message_template_quick_reply_button():
    message = {
        "from": "972501234567",
        "id": "wamid.X",
        "type": "button",
        "button": {"payload": "1", "text": "Approve"},
    }
    msg = normalize_meta_message(message, {})
    assert msg.text == "1"


def test_normalize_meta_message_image_with_caption():
    message = {
        "from": "972501234567",
        "id": "wamid.X",
        "type": "image",
        "image": {"id": "MEDIA123", "caption": "the leak"},
    }
    msg = normalize_meta_message(message, {})
    assert msg.media_url == "meta-media://MEDIA123"
    assert msg.text == "the leak"


def test_normalize_meta_message_location_with_name_and_address():
    message = {
        "from": "972501234567",
        "id": "wamid.X",
        "type": "location",
        "location": {
            "name": "My House",
            "address": "Rothschild 10",
            "latitude": 32.07,
            "longitude": 34.78,
        },
    }
    msg = normalize_meta_message(message, {})
    assert "My House" in msg.text
    assert "Rothschild 10" in msg.text
    assert "32.07" in msg.text


@pytest.mark.parametrize("msg_type", ["sticker", "reaction", "unsupported_future_type"])
def test_normalize_meta_message_unsupported_types_return_none(msg_type):
    message = {"from": "972501234567", "id": "wamid.X", "type": msg_type}
    assert normalize_meta_message(message, {}) is None


def test_parse_meta_webhook_statuses_only_envelope_returns_none():
    value = {
        "statuses": [
            {
                "id": "wamid.STATUS1",
                "status": "delivered",
                "recipient_id": "972501234567",
            }
        ]
    }
    assert parse_meta_webhook(_envelope(value)) is None


# ---------------------------------------------------------------------------
# 10. parse_status_events
# ---------------------------------------------------------------------------


def test_parse_status_events_extracts_error_code_and_chat_id():
    value = {
        "statuses": [
            {
                "id": "wamid.FAILED1",
                "status": "failed",
                "timestamp": "1234567890",
                "recipient_id": "972501234567",
                "errors": [
                    {
                        "code": META_ERROR_WINDOW_CLOSED,
                        "title": "Message failed to send",
                    }
                ],
            }
        ]
    }
    events = parse_status_events(_envelope(value))

    assert len(events) == 1
    event = events[0]
    assert event["wa_message_id"] == "wamid.FAILED1"
    assert event["chat_id"] == "972501234567@c.us"
    assert event["status"] == "failed"
    assert event["error_code"] == META_ERROR_WINDOW_CLOSED
    assert event["error_title"] == "Message failed to send"


@pytest.mark.parametrize("status", ["sent", "delivered", "read"])
def test_parse_status_events_parses_lifecycle_statuses(status):
    value = {
        "statuses": [
            {"id": "wamid.OK1", "status": status, "recipient_id": "972501234567"}
        ]
    }
    events = parse_status_events(_envelope(value))
    assert events[0]["status"] == status
    assert events[0]["error_code"] is None


# ---------------------------------------------------------------------------
# 11. delivery.apply_status_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_status_event_delivered_updates_recorded_outbound(
    mock_db, monkeypatch
):
    monkeypatch.setattr(delivery, "wa_delivery_collection", mock_db.wa_delivery)
    await delivery.record_outbound("wamid.D1", CHAT_ID, "text")

    await delivery.apply_status_event(
        {"wa_message_id": "wamid.D1", "status": "delivered", "timestamp": "111"}
    )

    doc = await mock_db.wa_delivery.find_one({"wa_message_id": "wamid.D1"})
    assert doc["status"] == "delivered"
    assert doc["kind"] == "text"


@pytest.mark.asyncio
async def test_apply_status_event_failed_window_closed_pages_no_fallback(
    mock_db, monkeypatch
):
    monkeypatch.setattr(delivery, "wa_delivery_collection", mock_db.wa_delivery)
    await delivery.record_outbound("wamid.F1", CHAT_ID, "text")

    async def _apply():
        await delivery.apply_status_event(
            {
                "wa_message_id": "wamid.F1",
                "status": "failed",
                "error_code": META_ERROR_WINDOW_CLOSED,
                "error_title": "window closed",
            }
        )

    text = await _captured_logs_async(_apply, level="CRITICAL")

    assert "131047" in text or "service window" in text.lower()
    doc = await mock_db.wa_delivery.find_one({"wa_message_id": "wamid.F1"})
    assert doc["status"] == "failed"
    assert doc["error_code"] == META_ERROR_WINDOW_CLOSED


@pytest.mark.asyncio
async def test_apply_status_event_failed_other_code_logs_error(mock_db, monkeypatch):
    monkeypatch.setattr(delivery, "wa_delivery_collection", mock_db.wa_delivery)
    await delivery.record_outbound("wamid.F2", CHAT_ID, "text")

    async def _apply():
        await delivery.apply_status_event(
            {
                "wa_message_id": "wamid.F2",
                "status": "failed",
                "error_code": 470,
                "error_title": "some other rejection",
            }
        )

    text = await _captured_logs_async(_apply, level="ERROR")
    assert "470" in text


@pytest.mark.asyncio
async def test_apply_status_event_unknown_wamid_upserts_without_crash(
    mock_db, monkeypatch
):
    monkeypatch.setattr(delivery, "wa_delivery_collection", mock_db.wa_delivery)

    await delivery.apply_status_event(
        {"wa_message_id": "wamid.NEVER_RECORDED", "status": "sent"}
    )

    doc = await mock_db.wa_delivery.find_one({"wa_message_id": "wamid.NEVER_RECORDED"})
    assert doc is not None
    assert doc["status"] == "sent"


@pytest.mark.asyncio
async def test_apply_status_event_failed_window_closed_with_approved_fallback_retries_through_facade(
    mock_db, monkeypatch
):
    """The other branch of the 131047 handler: when the registry *does* have
    an approved fallback for the failed send's kind, the retry goes back out
    through the facade (breaker/kill-switch protection intact) as a template,
    and the delivery record is annotated with which template rescued it."""
    monkeypatch.setattr(delivery, "wa_delivery_collection", mock_db.wa_delivery)
    await delivery.record_outbound("wamid.RETRY1", CHAT_ID, "text")

    fallback_spec = template_registry.TemplateSpec(
        key="reengage",
        meta_name="proli_reengage",
        status=template_registry.TemplateStatus.APPROVED,
    )
    monkeypatch.setattr(
        template_registry, "freeform_fallback", lambda kind: fallback_spec
    )

    import app.providers.whatsapp as whatsapp_pkg

    facade = MagicMock()
    facade.send_template = AsyncMock()
    monkeypatch.setattr(whatsapp_pkg, "get_whatsapp", lambda: facade)

    await delivery.apply_status_event(
        {
            "wa_message_id": "wamid.RETRY1",
            "status": "failed",
            "error_code": META_ERROR_WINDOW_CLOSED,
            "error_title": "window closed",
        }
    )

    facade.send_template.assert_awaited_once_with(CHAT_ID, "reengage")
    doc = await mock_db.wa_delivery.find_one({"wa_message_id": "wamid.RETRY1"})
    assert doc["retried_with_template"] == "reengage"


# ---------------------------------------------------------------------------
# 12. _split_text / send_text chunking (Cloud API's 4096-char body cap)
# ---------------------------------------------------------------------------


def test_split_text_under_the_limit_is_a_single_chunk():
    assert _split_text("short body") == ["short body"]


def test_split_text_prefers_a_newline_boundary_and_loses_nothing():
    text = ("A" * 4090) + "\n" + ("B" * 20)
    chunks = _split_text(text, limit=4096)

    assert len(chunks) == 2
    assert chunks[0] == "A" * 4090
    assert chunks[1] == "B" * 20
    # Nothing truncated: reassembling with the newline the cut ate reproduces
    # the original body exactly.
    assert "\n".join(chunks) == text


def test_split_text_with_no_newline_hard_cuts_at_the_limit_without_losing_chars():
    text = "Z" * 4200
    chunks = _split_text(text, limit=4096)

    assert len(chunks) == 2
    assert len(chunks[0]) == 4096
    assert "".join(chunks) == text  # no boundary to eat — nothing lost either


@pytest.mark.asyncio
async def test_send_text_over_the_limit_sends_multiple_posts_split_on_newline(
    install_recorder, fake_redis
):
    await _open_window(fake_redis)
    recorder = install_recorder(_accepted())

    provider = CloudAPIProvider()
    # The newline sits before the 4096-char cut, so the split lands exactly on
    # it (not a hard mid-word cut) — the one case where a plain "\n".join of
    # the sent chunks reconstructs the original body exactly.
    text = ("A" * 4090) + "\n" + ("line two " * 25)
    assert len(text) > 4096
    await provider.send_text(CHAT_ID, text)

    assert len(recorder.requests) == 2
    sent_bodies = [json.loads(r.content)["text"]["body"] for r in recorder.requests]
    for body in sent_bodies:
        assert len(body) <= 4096
    # Rejoined with the newline boundary the split ate, nothing is lost.
    assert "\n".join(sent_bodies) == text


@pytest.mark.asyncio
async def test_send_text_under_the_limit_sends_a_single_post(
    install_recorder, fake_redis
):
    await _open_window(fake_redis)
    recorder = install_recorder(_accepted())

    provider = CloudAPIProvider()
    await provider.send_text(CHAT_ID, "a short message")

    assert len(recorder.requests) == 1


# ---------------------------------------------------------------------------
# 13. _window_fallback page dedupe — one CRITICAL page per chat_id per day
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_window_closed_page_dedupe_downgrades_second_call_to_error(fake_redis):
    """With every registry template still DRAFT, a closed window is the
    expected steady state the moment the cloud provider goes live — so only
    the first block per chat_id per day pages CRITICAL (→ Sentry); repeats
    log at ERROR so the page does not become noise."""
    provider = CloudAPIProvider()

    async def _both():
        with pytest.raises(ServiceWindowClosedError):
            await provider.send_text(CHAT_ID, "first")
        with pytest.raises(ServiceWindowClosedError):
            await provider.send_text(CHAT_ID, "second")

    records = await _captured_records_async(_both, level="ERROR")
    page_records = [
        level for level, message in records if "service window closed" in message
    ]

    assert page_records == ["CRITICAL", "ERROR"]
    assert await fake_redis.exists(f"wa:window:page:{CHAT_ID}")


@pytest.mark.asyncio
async def test_window_closed_page_dedupe_uses_stdlib_path_for_critical_only(
    fake_redis, caplog
):
    """PRO-113: the CRITICAL branch (``page_critical``) is provably distinct
    from the downgraded ERROR branch (plain loguru ``logger.error``) — not
    just a different rendered level in the same loguru sink, but a different
    *transport*. Only ``page_critical`` **pages** (stdlib LogRecord on
    ``proli.paging`` → LoggingIntegration at ``fatal``); loguru's
    ``logger.error`` never touches stdlib ``logging`` at all, so caplog
    (which hooks stdlib logging exclusively) must see exactly one record —
    the first, paged block — and nothing for the second, downgraded one.
    (Since the ERROR-bridge landed, a loguru ERROR *can* reach Sentry as a
    non-paging `error` event via the bridge sink — but only when Sentry is
    active, which it never is in tests; the stdlib-transport distinction
    asserted here is unchanged.)"""
    provider = CloudAPIProvider()

    with caplog.at_level(logging.CRITICAL, logger="proli.paging"):
        with pytest.raises(ServiceWindowClosedError):
            await provider.send_text(CHAT_ID, "first")
        with pytest.raises(ServiceWindowClosedError):
            await provider.send_text(CHAT_ID, "second")

    paging_records = [r for r in caplog.records if r.name == "proli.paging"]
    assert len(paging_records) == 1
    assert "service window closed" in paging_records[0].message
    assert paging_records[0].levelname == "CRITICAL"


@pytest.mark.asyncio
async def test_window_closed_page_dedupe_is_per_chat_id(fake_redis):
    """A different recipient's first closed-window block must still page —
    the dedupe key is per chat_id, not a blanket "already paged today"."""
    provider = CloudAPIProvider()
    other_chat_id = "972500009999@c.us"

    async def _both():
        with pytest.raises(ServiceWindowClosedError):
            await provider.send_text(CHAT_ID, "first for chat one")
        with pytest.raises(ServiceWindowClosedError):
            await provider.send_text(other_chat_id, "first for chat two")

    records = await _captured_records_async(_both, level="ERROR")
    page_records = [
        level for level, message in records if "service window closed" in message
    ]

    assert page_records == ["CRITICAL", "CRITICAL"]


# ---------------------------------------------------------------------------
# 14. fetch_meta_media size cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_meta_media_over_size_cap_is_not_downloaded(install_recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            200,
            {
                "url": "https://lookaside.example.com/media123",
                "mime_type": "video/mp4",
                "file_size": WorkerConstants.MAX_INBOUND_MEDIA_BYTES + 1,
            },
        )

    recorder = install_recorder(handler)

    data, mime = await fetch_meta_media("MEDIA_TOO_BIG")

    assert data is None
    assert mime is None
    # Only the lookup hop happened — the CDN download must never be attempted
    # once the size cap trips.
    assert len(recorder.requests) == 1


@pytest.mark.asyncio
async def test_fetch_meta_media_under_size_cap_still_downloads(install_recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith("https://graph.facebook.com"):
            return _json_response(
                200,
                {
                    "url": "https://lookaside.example.com/media123",
                    "mime_type": "image/jpeg",
                    "file_size": 1024,
                },
            )
        return httpx.Response(200, content=b"small-file-bytes")

    recorder = install_recorder(handler)

    data, mime = await fetch_meta_media("MEDIA_OK")

    assert data == b"small-file-bytes"
    assert mime == "image/jpeg"
    assert len(recorder.requests) == 2


@pytest.mark.asyncio
async def test_fetch_meta_media_string_file_size_over_cap_is_not_downloaded(
    install_recorder,
):
    """Meta ships numeric fields as strings in these envelopes (statuses[].
    timestamp already is one) — a str file_size must be parsed and enforced,
    not skip the cap entirely because `"999999999" > int` never evaluates
    True."""

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            200,
            {
                "url": "https://lookaside.example.com/media123",
                "mime_type": "video/mp4",
                "file_size": str(WorkerConstants.MAX_INBOUND_MEDIA_BYTES + 1),
            },
        )

    recorder = install_recorder(handler)

    data, mime = await fetch_meta_media("MEDIA_STR_TOO_BIG")

    assert data is None
    assert mime is None
    assert len(recorder.requests) == 1


@pytest.mark.asyncio
async def test_fetch_meta_media_small_string_file_size_still_downloads(
    install_recorder,
):
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith("https://graph.facebook.com"):
            return _json_response(
                200,
                {
                    "url": "https://lookaside.example.com/media123",
                    "mime_type": "image/jpeg",
                    "file_size": "2338",
                },
            )
        return httpx.Response(200, content=b"small-file-bytes")

    recorder = install_recorder(handler)

    data, mime = await fetch_meta_media("MEDIA_STR_OK")

    assert data == b"small-file-bytes"
    assert mime == "image/jpeg"
    assert len(recorder.requests) == 2


@pytest.mark.asyncio
async def test_fetch_meta_media_non_https_lookup_url_is_rejected_no_download(
    install_recorder,
):
    """The Bearer token would otherwise be attached to whatever URL the
    lookup hop returned — never hand it to a non-TLS (or otherwise bizarre)
    destination."""

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            200,
            {
                "url": "http://not-tls.example.com/media123",
                "mime_type": "image/jpeg",
                "file_size": 1024,
            },
        )

    recorder = install_recorder(handler)

    data, mime = await fetch_meta_media("MEDIA_INSECURE")

    assert data is None
    assert mime is None
    assert len(recorder.requests) == 1


@pytest.mark.asyncio
async def test_fetch_meta_media_downloaded_body_over_cap_is_discarded(
    install_recorder,
):
    """file_size can be absent or lie; the cap must also be enforced on what
    actually arrived over the wire, not only on what the lookup claimed."""

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith("https://graph.facebook.com"):
            # No file_size at all — the lookup told us nothing.
            return _json_response(
                200,
                {
                    "url": "https://lookaside.example.com/media123",
                    "mime_type": "video/mp4",
                },
            )
        oversized = b"x" * (WorkerConstants.MAX_INBOUND_MEDIA_BYTES + 1)
        return httpx.Response(200, content=oversized)

    recorder = install_recorder(handler)

    data, mime = await fetch_meta_media("MEDIA_LIED_ABOUT_SIZE")

    assert data is None
    assert mime is None
    # Both hops happened — the cap only bites after the body is in hand.
    assert len(recorder.requests) == 2


# ---------------------------------------------------------------------------
# fetch_meta_media
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_meta_media_two_hop_lookup_returns_bytes_and_mime(install_recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith("https://graph.facebook.com"):
            return _json_response(
                200,
                {
                    "url": "https://lookaside.example.com/media123",
                    "mime_type": "image/jpeg",
                },
            )
        return httpx.Response(200, content=b"\xff\xd8\xff\xe0binarydata")

    install_recorder(handler)

    data, mime = await fetch_meta_media("MEDIA123")

    assert data == b"\xff\xd8\xff\xe0binarydata"
    assert mime == "image/jpeg"


@pytest.mark.asyncio
async def test_fetch_meta_media_unconfigured_returns_none_none(monkeypatch):
    monkeypatch.setattr(settings, "META_ACCESS_TOKEN", None)

    data, mime = await fetch_meta_media("MEDIA123")

    assert data is None
    assert mime is None


@pytest.mark.asyncio
async def test_fetch_meta_media_lookup_failure_returns_none_none(install_recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(404, {"error": {"message": "not found"}})

    install_recorder(handler)

    data, mime = await fetch_meta_media("MEDIA123")

    assert data is None
    assert mime is None
