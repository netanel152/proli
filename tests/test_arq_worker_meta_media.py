"""PRO-89 — media_handler.resolve_inbound_media.

Meta webhooks carry a `meta-media://<id>` marker (the real CDN URL is
auth-gated and expires within minutes); it must be resolved to a permanent
Cloudinary URL before the message reaches the dispatcher, but only past the
consent gate, and must never raise — a media fetch/upload failure degrades to
text-only processing. The gate-side wiring is in e2e/test_e2e_consent_hold.py.
"""

from unittest.mock import AsyncMock

import pytest

import app.core.arq_worker as arq_worker
from app.services import media_handler


@pytest.mark.asyncio
async def test_plain_url_passes_through_unchanged():
    result = await media_handler.resolve_inbound_media("https://cdn.example.com/x.jpg")
    assert result == "https://cdn.example.com/x.jpg"


@pytest.mark.asyncio
async def test_none_passes_through_unchanged():
    assert await media_handler.resolve_inbound_media(None) is None


@pytest.mark.asyncio
async def test_meta_media_marker_resolves_to_hosted_url(monkeypatch):
    monkeypatch.setattr(
        media_handler,
        "fetch_meta_media",
        AsyncMock(return_value=(b"binarydata", "image/jpeg")),
    )
    monkeypatch.setattr(
        media_handler,
        "upload_media_bytes",
        lambda data: "https://res.cloudinary.com/proli/x.jpg",
    )

    result = await media_handler.resolve_inbound_media("meta-media://MEDIA123")

    assert result == "https://res.cloudinary.com/proli/x.jpg"
    media_handler.fetch_meta_media.assert_awaited_once_with("MEDIA123")


@pytest.mark.asyncio
async def test_fetch_failure_returns_none_no_raise(monkeypatch):
    monkeypatch.setattr(
        media_handler, "fetch_meta_media", AsyncMock(return_value=(None, None))
    )
    called = {"upload": False}

    def _upload(data):
        called["upload"] = True
        return "should-not-be-reached"

    monkeypatch.setattr(media_handler, "upload_media_bytes", _upload)

    result = await media_handler.resolve_inbound_media("meta-media://MEDIA123")

    assert result is None
    assert called["upload"] is False


@pytest.mark.asyncio
async def test_upload_failure_returns_none_no_raise(monkeypatch):
    monkeypatch.setattr(
        media_handler,
        "fetch_meta_media",
        AsyncMock(return_value=(b"binarydata", "image/jpeg")),
    )
    monkeypatch.setattr(media_handler, "upload_media_bytes", lambda data: None)

    result = await media_handler.resolve_inbound_media("meta-media://MEDIA123")

    assert result is None


# ---------------------------------------------------------------------------
# process_message_task hands the marker on: the worker cannot know about consent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_message_task_does_not_resolve_media_itself(monkeypatch):
    fetch = AsyncMock(return_value=(b"binarydata", "image/jpeg"))
    monkeypatch.setattr(media_handler, "fetch_meta_media", fetch)
    captured = {}

    async def _fake_process_incoming_message(chat_id, user_text, media_url):
        captured["media_url"] = media_url

    monkeypatch.setattr(
        arq_worker, "process_incoming_message", _fake_process_incoming_message
    )

    await arq_worker.process_message_task(
        {}, "972500000001@c.us", "check this out", "meta-media://MEDIA_ROUTE"
    )

    assert captured["media_url"] == "meta-media://MEDIA_ROUTE"
    fetch.assert_not_awaited()
