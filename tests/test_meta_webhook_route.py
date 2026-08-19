"""PRO-89 — GET/POST /webhook/meta: subscription handshake, HMAC auth,
idempotency, delivery-status routing, and enqueue.

GET tests use `fastapi.testclient.TestClient` (sync, no Redis touch — the
same pattern as tests/test_integration_webhook.py). POST tests touch fakeredis
(idempotency + the service window), so they run as async tests against
`httpx.ASGITransport` instead: TestClient spins a fresh event loop per call in
a background thread, and fakeredis's internal connection binds to whichever
loop first uses it — a second `client.post()` (or a same-test Redis
assertion) on a different loop blows up with "bound to a different event
loop". Driving everything from one `pytest.mark.asyncio` coroutine keeps the
route call and the fakeredis fixture on the same loop.
"""

import hashlib
import hmac
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from unittest.mock import AsyncMock, patch

from app.core.config import settings
from app.main import app
from app.providers.whatsapp import delivery

sync_client = TestClient(app)

CHAT_ID = "972501234567@c.us"


def _text_message_payload(wamid="wamid.ROUTE1", phone="972501234567", text="hi"):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_ID",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "contacts": [{"wa_id": phone, "profile": {"name": "Dana"}}],
                            "messages": [
                                {
                                    "from": phone,
                                    "id": wamid,
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


def _sticker_message_payload(wamid="wamid.STICKER1", phone="972501234567"):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_ID",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messages": [
                                {
                                    "from": phone,
                                    "id": wamid,
                                    "type": "sticker",
                                    "sticker": {"id": "STK1"},
                                }
                            ]
                        },
                    }
                ],
            }
        ],
    }


def _status_only_payload(wamid="wamid.STATUSROUTE1", status="delivered"):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_ID",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "statuses": [
                                {
                                    "id": wamid,
                                    "status": status,
                                    "recipient_id": "972501234567",
                                }
                            ]
                        },
                    }
                ],
            }
        ],
    }


def _sign(raw_body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# GET — subscription handshake
# ---------------------------------------------------------------------------


def test_get_verify_with_correct_token_echoes_challenge():
    with patch.object(settings, "META_VERIFY_TOKEN", SecretStr("verify-me")):
        resp = sync_client.get(
            "/webhook/meta",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "verify-me",
                "hub.challenge": "challenge-123",
            },
        )
    assert resp.status_code == 200
    assert resp.text == "challenge-123"


def test_get_verify_with_wrong_token_is_forbidden():
    with patch.object(settings, "META_VERIFY_TOKEN", SecretStr("verify-me")):
        resp = sync_client.get(
            "/webhook/meta",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong-token",
                "hub.challenge": "challenge-123",
            },
        )
    assert resp.status_code == 403


def test_get_verify_with_missing_token_param_is_forbidden():
    with patch.object(settings, "META_VERIFY_TOKEN", SecretStr("verify-me")):
        resp = sync_client.get(
            "/webhook/meta", params={"hub.mode": "subscribe", "hub.challenge": "c"}
        )
    assert resp.status_code == 403


def test_get_verify_with_unset_verify_token_setting_is_forbidden():
    with patch.object(settings, "META_VERIFY_TOKEN", None):
        resp = sync_client.get(
            "/webhook/meta",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "anything",
                "hub.challenge": "c",
            },
        )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST — HMAC signature auth, idempotency, enqueue, delivery statuses
# ---------------------------------------------------------------------------


@pytest.fixture
def _capturing_pool(monkeypatch):
    pool = AsyncMock()

    async def _get_pool():
        return pool

    monkeypatch.setattr("app.api.routes.meta_webhook.get_arq_pool", _get_pool)
    return pool


async def _post(raw: bytes, headers: dict) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as async_client:
        return await async_client.post("/webhook/meta", content=raw, headers=headers)


@pytest.mark.asyncio
async def test_post_with_valid_signature_enqueues_and_opens_window(
    fake_redis, _capturing_pool
):
    payload = _text_message_payload()
    raw = json.dumps(payload).encode()
    with patch.object(settings, "META_APP_SECRET", SecretStr("shh-secret")):
        resp = await _post(
            raw,
            {
                "X-Hub-Signature-256": _sign(raw, "shh-secret"),
                "Content-Type": "application/json",
            },
        )

    assert resp.status_code == 200
    assert resp.json() == {"status": "processing_message"}
    _capturing_pool.enqueue_job.assert_awaited_once_with(
        "process_message_task", CHAT_ID, "hi", None, message_id="wamid.ROUTE1"
    )

    assert await fake_redis.exists(f"wa:window:{CHAT_ID}")
    ttl = await fake_redis.ttl(f"wa:window:{CHAT_ID}")
    assert 0 < ttl <= 86400


@pytest.mark.asyncio
async def test_post_with_invalid_signature_is_forbidden_and_nothing_enqueued(
    fake_redis, _capturing_pool
):
    payload = _text_message_payload()
    raw = json.dumps(payload).encode()
    with patch.object(settings, "META_APP_SECRET", SecretStr("shh-secret")):
        resp = await _post(
            raw,
            {
                "X-Hub-Signature-256": "sha256=deadbeef",
                "Content-Type": "application/json",
            },
        )

    assert resp.status_code == 403
    _capturing_pool.enqueue_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_with_missing_signature_header_is_forbidden(
    fake_redis, _capturing_pool
):
    payload = _text_message_payload()
    raw = json.dumps(payload).encode()
    with patch.object(settings, "META_APP_SECRET", SecretStr("shh-secret")):
        resp = await _post(raw, {"Content-Type": "application/json"})

    assert resp.status_code == 403
    _capturing_pool.enqueue_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_duplicate_wamid_second_call_does_not_re_enqueue(
    fake_redis, _capturing_pool
):
    payload = _text_message_payload(wamid="wamid.DUPTEST")
    raw = json.dumps(payload).encode()
    with patch.object(settings, "META_APP_SECRET", SecretStr("shh-secret")):
        headers = {
            "X-Hub-Signature-256": _sign(raw, "shh-secret"),
            "Content-Type": "application/json",
        }
        resp1 = await _post(raw, headers)
        resp2 = await _post(raw, headers)

    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert resp2.json() == {"status": "ignored_type"}
    _capturing_pool.enqueue_job.assert_awaited_once()


@pytest.mark.asyncio
async def test_post_statuses_only_payload_updates_delivery_nothing_enqueued(
    fake_redis, _capturing_pool, mock_db, monkeypatch
):
    monkeypatch.setattr(delivery, "wa_delivery_collection", mock_db.wa_delivery)

    payload = _status_only_payload(wamid="wamid.STATUSROUTE1", status="delivered")
    raw = json.dumps(payload).encode()
    with patch.object(settings, "META_APP_SECRET", SecretStr("shh-secret")):
        resp = await _post(
            raw,
            {
                "X-Hub-Signature-256": _sign(raw, "shh-secret"),
                "Content-Type": "application/json",
            },
        )

    assert resp.status_code == 200
    assert resp.json() == {"status": "ignored_type"}
    _capturing_pool.enqueue_job.assert_not_awaited()

    doc = await mock_db.wa_delivery.find_one({"wa_message_id": "wamid.STATUSROUTE1"})
    assert doc is not None
    assert doc["status"] == "delivered"


# ---------------------------------------------------------------------------
# Prod-like without META_APP_SECRET fail-closed path, and the dev asymmetry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_prod_like_without_app_secret_is_forbidden_nothing_enqueued(
    fake_redis, _capturing_pool, monkeypatch
):
    """The Settings validator already refuses cloud+prod-like without
    META_APP_SECRET at boot; this is the route's own belt-and-suspenders
    guard for the dryrun-provider case (route exposed, secret never
    configured). Patch the attribute directly rather than constructing a new
    Settings — the route reads the live `settings` singleton."""
    monkeypatch.setattr(settings, "ENVIRONMENT", "staging")
    monkeypatch.setattr(settings, "META_APP_SECRET", None)

    payload = _text_message_payload(wamid="wamid.PRODNOAUTH1")
    raw = json.dumps(payload).encode()
    resp = await _post(raw, {"Content-Type": "application/json"})

    assert resp.status_code == 403
    _capturing_pool.enqueue_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_development_without_app_secret_is_accepted(
    fake_redis, _capturing_pool, monkeypatch
):
    """The asymmetric case: development is not prod-like, so an unset
    META_APP_SECRET does not fail-closed there — the route only refuses to
    authenticate a payload it cannot verify when refusing actually protects
    something."""
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "META_APP_SECRET", None)

    payload = _text_message_payload(wamid="wamid.DEVNOAUTH1")
    raw = json.dumps(payload).encode()
    resp = await _post(raw, {"Content-Type": "application/json"})

    assert resp.status_code == 200
    assert resp.json() == {"status": "processing_message"}
    _capturing_pool.enqueue_job.assert_awaited_once()


# ---------------------------------------------------------------------------
# Non-ASCII signature header — must 403, never 500
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_with_non_ascii_signature_header_is_forbidden_not_500(
    fake_redis, _capturing_pool
):
    """A forged header containing a byte ≥0x80 (e.g. 'é') must fail auth like
    any other bad signature, not blow up `hmac.compare_digest` on a str/bytes
    mismatch and 500 the route."""
    payload = _text_message_payload(wamid="wamid.NONASCII1")
    raw = json.dumps(payload).encode()
    with patch.object(settings, "META_APP_SECRET", SecretStr("shh-secret")):
        resp = await _post(
            raw,
            {
                "X-Hub-Signature-256": "sha256=\xe9\xe9deadbeef".encode("latin-1"),
                "Content-Type": "application/json",
            },
        )

    assert resp.status_code == 403
    _capturing_pool.enqueue_job.assert_not_awaited()


# ---------------------------------------------------------------------------
# The service window opens even for messages the normalizer drops
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_sticker_message_opens_window_but_enqueues_nothing(
    fake_redis, _capturing_pool
):
    """Meta opens the 24h window on ANY inbound message, stickers included —
    the route must record that before normalization decides there's nothing
    to enqueue, or the window under-reports and outbound replies get blocked
    later for a recipient who did, in fact, just write in."""
    payload = _sticker_message_payload()
    raw = json.dumps(payload).encode()
    with patch.object(settings, "META_APP_SECRET", SecretStr("shh-secret")):
        resp = await _post(
            raw,
            {
                "X-Hub-Signature-256": _sign(raw, "shh-secret"),
                "Content-Type": "application/json",
            },
        )

    assert resp.status_code == 200
    assert resp.json() == {"status": "ignored_type"}
    _capturing_pool.enqueue_job.assert_not_awaited()
    assert await fake_redis.exists(f"wa:window:{CHAT_ID}")


# ---------------------------------------------------------------------------
# Enqueue failure releases the idempotency key so a Meta redelivery can land
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_enqueue_failure_returns_503_and_releases_idempotency_key(
    fake_redis, monkeypatch
):
    async def _raising_get_pool():
        pool = AsyncMock()
        pool.enqueue_job = AsyncMock(side_effect=Exception("redis pool exploded"))
        return pool

    monkeypatch.setattr("app.api.routes.meta_webhook.get_arq_pool", _raising_get_pool)

    payload = _text_message_payload(wamid="wamid.ENQFAIL1")
    raw = json.dumps(payload).encode()
    with patch.object(settings, "META_APP_SECRET", SecretStr("shh-secret")):
        resp = await _post(
            raw,
            {
                "X-Hub-Signature-256": _sign(raw, "shh-secret"),
                "Content-Type": "application/json",
            },
        )

    assert resp.status_code == 503
    assert resp.json() == {"status": "error"}
    # Released, not left dangling — a Meta redelivery of this wamid must be
    # able to claim it and actually enqueue.
    assert await fake_redis.exists("webhook:wamid.ENQFAIL1") == 0


@pytest.mark.asyncio
async def test_post_malformed_json_body_still_returns_200_ignored(
    fake_redis, _capturing_pool
):
    """Unchanged by the 503 introduction: a body we can't even parse is our
    problem to log, not an infra failure Meta should retry."""
    with patch.object(settings, "META_APP_SECRET", SecretStr("shh-secret")):
        raw = b"{not valid json"
        resp = await _post(
            raw,
            {
                "X-Hub-Signature-256": _sign(raw, "shh-secret"),
                "Content-Type": "application/json",
            },
        )

    assert resp.status_code == 200
    assert resp.json() == {"status": "ignored_type"}
    _capturing_pool.enqueue_job.assert_not_awaited()
