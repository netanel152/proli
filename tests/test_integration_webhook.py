import re

import pytest
from fastapi.testclient import TestClient
from unittest.mock import ANY, AsyncMock, patch
from pydantic import SecretStr
from app.main import app

_HEX12 = re.compile(r"^[0-9a-f]{12}$")

client = TestClient(app)

# Payload Templates
VALID_PAYLOAD = {
    "typeWebhook": "incomingMessageReceived",
    "instanceData": {
        "idInstance": 7107387490,
        "wid": "1234567890@c.us",
        "typeInstance": "whatsapp",
    },
    "timestamp": 1234567890,
    "idMessage": "F1234567890",
    "senderData": {"chatId": "972501234567@c.us", "senderName": "Test User"},
    "messageData": {
        "typeMessage": "textMessage",
        "textMessageData": {"textMessage": "Hello Proli"},
    },
}


@pytest.fixture
def mock_background_tasks():
    # Patch settings to match test payload and disable token auth
    # PRO-86: the sender instance-id check went with the Green provider.
    with patch("app.core.config.settings.WEBHOOK_TOKEN", None):
        # Mock ARQ pool
        with patch("app.api.routes.webhook.get_arq_pool") as mock_get_pool:
            mock_pool = AsyncMock()
            mock_get_pool.return_value = mock_pool
            # Also mock redis client for idempotency check
            with patch("app.api.routes.webhook.get_redis_client") as mock_get_redis:
                mock_redis = AsyncMock()
                mock_get_redis.return_value = mock_redis
                # Redis set returns True (new message)
                mock_redis.set.return_value = True

                yield mock_pool


def test_webhook_valid_text_message(mock_background_tasks):
    mock_pool = mock_background_tasks

    response = client.post("/webhook", json=VALID_PAYLOAD)

    assert response.status_code == 200
    assert response.json() == {"status": "processing_message"}

    # PRO-174: the route now also forwards the trace_id it bound for this
    # turn. new_trace_id() is unkeyed random (PRO-174 blocker fix — a digest
    # of message_id+chat_id was a decryption key for mask_pii), so the exact
    # value cannot be predicted here — assert shape via ANY, then check the
    # actual forwarded value separately.
    mock_pool.enqueue_job.assert_called_once_with(
        "process_message_task",
        "972501234567@c.us",
        "Hello Proli",
        None,
        message_id="F1234567890",
        trace_id=ANY,
    )
    forwarded_trace_id = mock_pool.enqueue_job.call_args.kwargs["trace_id"]
    assert _HEX12.match(forwarded_trace_id)


def test_webhook_ignored_group_message(mock_background_tasks):
    mock_pool = mock_background_tasks

    payload = VALID_PAYLOAD.copy()
    payload["senderData"] = {"chatId": "123456789@g.us", "senderName": "Group Chat"}

    response = client.post("/webhook", json=payload)

    assert response.status_code == 200
    assert response.json() == {"status": "ignored_group"}
    mock_pool.enqueue_job.assert_not_called()


def test_webhook_invalid_json():
    response = client.post("/webhook", content="{invalid_json}")
    assert response.status_code == 422  # Validation error


def test_webhook_missing_fields(mock_background_tasks):
    # Payload missing senderData
    payload = {
        "typeWebhook": "incomingMessageReceived",
        "messageData": {"typeMessage": "textMessage"},
    }
    # Pydantic validation passes (optional fields), but logic handles it
    response = client.post("/webhook", json=payload)
    assert response.status_code == 200
    assert response.json() == {"status": "ignored_no_data"}


def test_webhook_token_query_param_required_when_configured():
    """When WEBHOOK_TOKEN is set, requests must carry ?token= parameter."""
    # PRO-86: the sender instance-id check went with the Green provider.
    # PRO-94: the field is a SecretStr and the route unwraps it — patching a
    # bare str here would pass a type the route can no longer read.
    with patch("app.core.config.settings.WEBHOOK_TOKEN", SecretStr("secret-token")):
        with patch("app.api.routes.webhook.get_arq_pool") as mock_get_pool, patch(
            "app.api.routes.webhook.get_redis_client"
        ) as mock_get_redis:
            mock_get_pool.return_value = AsyncMock()
            mock_redis = AsyncMock()
            mock_redis.set.return_value = True
            mock_get_redis.return_value = mock_redis

            # Missing token → 403
            resp_missing = client.post("/webhook", json=VALID_PAYLOAD)
            assert resp_missing.status_code == 403
            assert resp_missing.json() == {"status": "forbidden"}

            # Wrong token → 403
            resp_wrong = client.post(
                "/webhook", json=VALID_PAYLOAD, params={"token": "not-the-token"}
            )
            assert resp_wrong.status_code == 403

            # Correct token → 200
            resp_ok = client.post(
                "/webhook", json=VALID_PAYLOAD, params={"token": "secret-token"}
            )
            assert resp_ok.status_code == 200
            assert resp_ok.json() == {"status": "processing_message"}
