"""Inbound webhook, end to end through the FastAPI app: ``POST /webhook/meta``.

These used to drive the legacy ``POST /webhook`` envelope. That route is being
retired, so every ingress check here now goes through the Meta Cloud API route
instead. What the legacy route alone did (the ``?token=`` WEBHOOK_TOKEN check,
the ``@g.us`` group filter) has no Meta counterpart: Cloud API authenticates
with the ``X-Hub-Signature-256`` HMAC, which the signature tests below cover,
and does not deliver group traffic to business numbers.

The POSTs run on one event loop through ``httpx.ASGITransport`` rather than
``TestClient``: the route touches fakeredis (idempotency and the service
window), and fakeredis binds to the first loop that uses it. The same reason
is spelled out in tests/test_meta_webhook_route.py.
"""

import hashlib
import hmac
import json
import re
from unittest.mock import ANY, AsyncMock

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import settings
from app.main import app

_HEX12 = re.compile(r"^[0-9a-f]{12}$")

PHONE = "972501234567"
CHAT_ID = f"{PHONE}@c.us"
APP_SECRET = "integration-app-secret"


def _payload(message: dict | None = None, *, contacts: bool = True) -> dict:
    """One Meta ``messages`` change carrying ``message`` (a text by default)."""
    if message is None:
        message = {
            "from": PHONE,
            "id": "wamid.F1234567890",
            "timestamp": "1234567890",
            "type": "text",
            "text": {"body": "Hello Proli"},
        }
    value = {"messaging_product": "whatsapp", "messages": [message]}
    if contacts:
        value["contacts"] = [{"wa_id": PHONE, "profile": {"name": "Test User"}}]
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {"id": "WABA_ID", "changes": [{"field": "messages", "value": value}]}
        ],
    }


def _sign(raw: bytes, secret: str = APP_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


async def _post(raw: bytes, headers: dict | None = None) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        return await client.post(
            "/webhook/meta",
            content=raw,
            headers={"Content-Type": "application/json", **(headers or {})},
        )


@pytest.fixture
def mock_pool(monkeypatch):
    """The ARQ pool the route enqueues to, captured instead of a real Redis."""
    pool = AsyncMock()

    async def _get_pool():
        return pool

    monkeypatch.setattr("app.api.routes.meta_webhook.get_arq_pool", _get_pool)
    return pool


@pytest.fixture
def dev_unsigned(monkeypatch):
    """Development with no META_APP_SECRET: the route accepts unsigned bodies."""
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "META_APP_SECRET", None)


@pytest.fixture
def staging_signed(monkeypatch):
    """A prod-like environment with META_APP_SECRET configured."""
    monkeypatch.setattr(settings, "ENVIRONMENT", "staging")
    monkeypatch.setattr(settings, "META_APP_SECRET", SecretStr(APP_SECRET))


# ---------------------------------------------------------------------------
# Payload handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_valid_text_message(fake_redis, mock_pool, dev_unsigned):
    response = await _post(json.dumps(_payload()).encode())

    assert response.status_code == 200
    assert response.json() == {"status": "processing_message"}

    # PRO-174: the route also forwards the trace_id it bound for this turn.
    # new_trace_id() is unkeyed random, so the exact value cannot be
    # predicted here: assert shape via ANY, then check the forwarded value.
    mock_pool.enqueue_job.assert_awaited_once_with(
        "process_message_task",
        CHAT_ID,
        "Hello Proli",
        None,
        message_id="wamid.F1234567890",
        trace_id=ANY,
    )
    forwarded_trace_id = mock_pool.enqueue_job.await_args.kwargs["trace_id"]
    assert _HEX12.match(forwarded_trace_id)
    # The wamid is claimed for idempotency, and the sender's window is open.
    assert await fake_redis.exists("webhook:wamid.F1234567890")
    assert await fake_redis.exists(f"wa:window:{CHAT_ID}")


@pytest.mark.asyncio
async def test_webhook_unsupported_message_type_is_ignored(
    fake_redis, mock_pool, dev_unsigned
):
    reaction = {
        "from": PHONE,
        "id": "wamid.REACTION1",
        "type": "reaction",
        "reaction": {"message_id": "wamid.F1234567890", "emoji": "👍"},
    }

    response = await _post(json.dumps(_payload(reaction)).encode())

    assert response.status_code == 200
    assert response.json() == {"status": "ignored_type"}
    mock_pool.enqueue_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_webhook_invalid_json(fake_redis, mock_pool, dev_unsigned):
    # Unlike the legacy route's 422, the Meta route answers 200: a body it
    # cannot parse is ours to log, not Meta's to retry.
    response = await _post(b"{invalid_json}")

    assert response.status_code == 200
    assert response.json() == {"status": "ignored_type"}
    mock_pool.enqueue_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_webhook_missing_fields(fake_redis, mock_pool, dev_unsigned):
    # A message with no sender, and a change with no messages at all: both
    # parse as JSON, neither carries anything to process.
    no_sender = _payload(
        {"id": "wamid.NOFROM1", "type": "text", "text": {"body": "hi"}},
        contacts=False,
    )
    response = await _post(json.dumps(no_sender).encode())
    assert response.status_code == 200
    assert response.json() == {"status": "ignored_type"}

    empty_change = {
        "object": "whatsapp_business_account",
        "entry": [{"id": "WABA_ID", "changes": [{"field": "messages", "value": {}}]}],
    }
    response = await _post(json.dumps(empty_change).encode())
    assert response.status_code == 200
    assert response.json() == {"status": "ignored_type"}

    mock_pool.enqueue_job.assert_not_awaited()


# ---------------------------------------------------------------------------
# Authentication in a prod-like environment: X-Hub-Signature-256
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_signature_missing_is_forbidden_in_staging(
    fake_redis, mock_pool, staging_signed
):
    response = await _post(json.dumps(_payload()).encode())

    assert response.status_code == 403
    assert response.json() == {"status": "forbidden"}
    mock_pool.enqueue_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_webhook_signature_wrong_secret_is_forbidden_in_staging(
    fake_redis, mock_pool, staging_signed
):
    raw = json.dumps(_payload()).encode()

    response = await _post(raw, {"X-Hub-Signature-256": _sign(raw, "not-the-secret")})

    assert response.status_code == 403
    assert response.json() == {"status": "forbidden"}
    mock_pool.enqueue_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_webhook_signature_over_a_different_body_is_forbidden_in_staging(
    fake_redis, mock_pool, staging_signed
):
    # A genuine signature authenticates only the bytes it was computed over:
    # replaying it on an altered body must not pass.
    signed_raw = json.dumps(_payload()).encode()
    tampered = _payload()
    tampered["entry"][0]["changes"][0]["value"]["messages"][0]["text"][
        "body"
    ] = "Tampered"
    tampered_raw = json.dumps(tampered).encode()

    response = await _post(tampered_raw, {"X-Hub-Signature-256": _sign(signed_raw)})

    assert response.status_code == 403
    mock_pool.enqueue_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_webhook_signature_correct_is_processed_in_staging(
    fake_redis, mock_pool, staging_signed
):
    raw = json.dumps(_payload()).encode()

    response = await _post(raw, {"X-Hub-Signature-256": _sign(raw)})

    assert response.status_code == 200
    assert response.json() == {"status": "processing_message"}
    mock_pool.enqueue_job.assert_awaited_once()
