"""
PRO-71 — GET /health WhatsApp status mapping.

GET /health maps Green API's getStateInstance() to a health status:
  * "authorized"  -> "up"
  * "yellowCard"  -> "degraded" (instance alive, but WhatsApp silently
    filters outbound — a plain truthiness check would misreport this as
    healthy, which is exactly the bug PRO-71 fixes)
  * anything else (notAuthorized/blocked/starting/None/unreachable) -> "down"

checks["whatsapp"] also surfaces the raw "state" field for operators.

Mongo/Redis are mocked so these tests exercise only the WhatsApp branch and
don't depend on a live DB/Redis being reachable (which would otherwise hang
or fail on serverSelectionTimeout in CI).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient

import app.api.routes.health as health_route
from app.main import app
from app.services.whatsapp_client_service import WhatsAppClient


def _mock_http_client(state):
    """Return an AsyncMock http client whose .get() yields {"stateInstance": state}."""
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"stateInstance": state}
    http = AsyncMock()
    http.get = AsyncMock(return_value=resp)
    return http


@pytest.fixture(autouse=True)
def _mock_mongo_and_redis(monkeypatch):
    """Keep mongo/redis checks fast and deterministic — this file only cares
    about the whatsapp branch of GET /health."""
    monkeypatch.setattr(health_route, "check_db_connection", lambda: True)

    mock_redis = AsyncMock()
    mock_redis.ping = AsyncMock(return_value=True)
    mock_redis.get = AsyncMock(return_value=None)
    monkeypatch.setattr(
        health_route, "get_redis_client", AsyncMock(return_value=mock_redis)
    )


def test_health_whatsapp_authorized_is_up(monkeypatch):
    monkeypatch.setattr(
        WhatsAppClient,
        "_get_client",
        AsyncMock(return_value=_mock_http_client("authorized")),
    )

    resp = TestClient(app).get("/health")

    body = resp.json()
    assert body["checks"]["whatsapp"]["status"] == "up"
    assert body["checks"]["whatsapp"]["state"] == "authorized"


def test_health_whatsapp_yellowcard_is_degraded(monkeypatch):
    monkeypatch.setattr(
        WhatsAppClient,
        "_get_client",
        AsyncMock(return_value=_mock_http_client("yellowCard")),
    )

    resp = TestClient(app).get("/health")

    body = resp.json()
    assert body["checks"]["whatsapp"]["status"] == "degraded"
    assert body["checks"]["whatsapp"]["state"] == "yellowCard"


def test_health_whatsapp_not_authorized_is_down(monkeypatch):
    monkeypatch.setattr(
        WhatsAppClient,
        "_get_client",
        AsyncMock(return_value=_mock_http_client("notAuthorized")),
    )

    resp = TestClient(app).get("/health")

    body = resp.json()
    assert body["checks"]["whatsapp"]["status"] == "down"
    assert body["checks"]["whatsapp"]["state"] == "notAuthorized"


def test_health_whatsapp_blocked_state_is_down(monkeypatch):
    """'blocked' is truthy but must not be reported as 'up' — the whole
    point of comparing against 'authorized' rather than truthiness."""
    monkeypatch.setattr(
        WhatsAppClient,
        "_get_client",
        AsyncMock(return_value=_mock_http_client("blocked")),
    )

    resp = TestClient(app).get("/health")

    body = resp.json()
    assert body["checks"]["whatsapp"]["status"] == "down"
    assert body["checks"]["whatsapp"]["state"] == "blocked"


def test_health_whatsapp_probe_error_is_down_with_null_state(monkeypatch):
    """A network/HTTP failure while probing getStateInstance must not crash
    the health endpoint — WhatsApp is reported down with no state."""
    monkeypatch.setattr(
        WhatsAppClient,
        "_get_client",
        AsyncMock(side_effect=Exception("connection reset")),
    )

    resp = TestClient(app).get("/health")

    body = resp.json()
    assert body["checks"]["whatsapp"]["status"] == "down"
    assert body["checks"]["whatsapp"]["state"] is None
