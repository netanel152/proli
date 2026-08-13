"""PRO-89 — app.core.arq_worker._resolve_inbound_media.

Meta webhooks carry a `meta-media://<id>` marker (the real CDN URL is
auth-gated and expires within minutes); the worker must resolve it to a
permanent Cloudinary URL before the message reaches the dispatcher, and must
never raise — a media fetch/upload failure degrades to text-only processing.
"""

from unittest.mock import AsyncMock

import pytest

import app.core.arq_worker as arq_worker


@pytest.mark.asyncio
async def test_plain_url_passes_through_unchanged():
    result = await arq_worker._resolve_inbound_media("https://cdn.example.com/x.jpg")
    assert result == "https://cdn.example.com/x.jpg"


@pytest.mark.asyncio
async def test_none_passes_through_unchanged():
    assert await arq_worker._resolve_inbound_media(None) is None


@pytest.mark.asyncio
async def test_meta_media_marker_resolves_to_hosted_url(monkeypatch):
    monkeypatch.setattr(
        arq_worker,
        "fetch_meta_media",
        AsyncMock(return_value=(b"binarydata", "image/jpeg")),
    )
    monkeypatch.setattr(
        arq_worker,
        "upload_media_bytes",
        lambda data: "https://res.cloudinary.com/proli/x.jpg",
    )

    result = await arq_worker._resolve_inbound_media("meta-media://MEDIA123")

    assert result == "https://res.cloudinary.com/proli/x.jpg"
    arq_worker.fetch_meta_media.assert_awaited_once_with("MEDIA123")


@pytest.mark.asyncio
async def test_fetch_failure_returns_none_no_raise(monkeypatch):
    monkeypatch.setattr(
        arq_worker, "fetch_meta_media", AsyncMock(return_value=(None, None))
    )
    called = {"upload": False}

    def _upload(data):
        called["upload"] = True
        return "should-not-be-reached"

    monkeypatch.setattr(arq_worker, "upload_media_bytes", _upload)

    result = await arq_worker._resolve_inbound_media("meta-media://MEDIA123")

    assert result is None
    assert called["upload"] is False


@pytest.mark.asyncio
async def test_upload_failure_returns_none_no_raise(monkeypatch):
    monkeypatch.setattr(
        arq_worker,
        "fetch_meta_media",
        AsyncMock(return_value=(b"binarydata", "image/jpeg")),
    )
    monkeypatch.setattr(arq_worker, "upload_media_bytes", lambda data: None)

    result = await arq_worker._resolve_inbound_media("meta-media://MEDIA123")

    assert result is None


# ---------------------------------------------------------------------------
# process_message_task actually routes media_url through _resolve_inbound_media
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_message_task_resolves_meta_media_before_dispatch(monkeypatch):
    """End-to-end through the ARQ entry point: a `meta-media://` marker must
    reach `process_incoming_message` as the resolved hosted URL, not as the
    raw marker — this is the wiring `_resolve_inbound_media`'s unit tests
    above cannot see on their own."""
    monkeypatch.setattr(
        arq_worker,
        "fetch_meta_media",
        AsyncMock(return_value=(b"binarydata", "image/jpeg")),
    )
    monkeypatch.setattr(
        arq_worker,
        "upload_media_bytes",
        lambda data: "https://res.cloudinary.com/proli/routed.jpg",
    )
    captured = {}

    async def _fake_process_incoming_message(chat_id, user_text, media_url):
        captured["chat_id"] = chat_id
        captured["user_text"] = user_text
        captured["media_url"] = media_url

    monkeypatch.setattr(
        arq_worker, "process_incoming_message", _fake_process_incoming_message
    )

    await arq_worker.process_message_task(
        {}, "972500000001@c.us", "check this out", "meta-media://MEDIA_ROUTE"
    )

    assert captured["chat_id"] == "972500000001@c.us"
    assert captured["user_text"] == "check this out"
    assert captured["media_url"] == "https://res.cloudinary.com/proli/routed.jpg"


@pytest.mark.asyncio
async def test_process_message_task_passes_plain_url_through_unchanged(monkeypatch):
    called = {"fetch": False}

    async def _fetch_should_not_be_called(media_id):
        called["fetch"] = True
        return None, None

    monkeypatch.setattr(arq_worker, "fetch_meta_media", _fetch_should_not_be_called)
    captured = {}

    async def _fake_process_incoming_message(chat_id, user_text, media_url):
        captured["media_url"] = media_url

    monkeypatch.setattr(
        arq_worker, "process_incoming_message", _fake_process_incoming_message
    )

    await arq_worker.process_message_task(
        {}, "972500000001@c.us", "hi", "https://cdn.example.com/already-hosted.jpg"
    )

    assert captured["media_url"] == "https://cdn.example.com/already-hosted.jpg"
    assert called["fetch"] is False
