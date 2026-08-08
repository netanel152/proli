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
from app.providers.whatsapp.facade import WhatsAppFacade


def _facade(state=None, error=None):
    """A facade over a stub provider with a controlled get_state().

    PRO-86: /health used to be steered by patching the Green client's httpx
    transport. State now comes from the provider, so the stub sits there — one
    layer higher and independent of any transport.
    """
    provider = MagicMock()
    provider.name = "stub"
    # Transmitting: these tests exercise the state branches, and a
    # non-transmitting provider short-circuits to "degraded" before reaching them.
    provider.transmits = True
    provider.get_state = (
        AsyncMock(side_effect=error) if error else AsyncMock(return_value=state)
    )
    return WhatsAppFacade(provider)


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
    monkeypatch.setattr(health_route, "get_whatsapp", lambda: _facade("authorized"))

    resp = TestClient(app).get("/health")

    body = resp.json()
    assert body["checks"]["whatsapp"]["status"] == "up"
    assert body["checks"]["whatsapp"]["state"] == "authorized"


def test_health_whatsapp_yellowcard_is_degraded(monkeypatch):
    monkeypatch.setattr(health_route, "get_whatsapp", lambda: _facade("yellowCard"))

    resp = TestClient(app).get("/health")

    body = resp.json()
    assert body["checks"]["whatsapp"]["status"] == "degraded"
    assert body["checks"]["whatsapp"]["state"] == "yellowCard"


def test_health_whatsapp_not_authorized_is_down(monkeypatch):
    monkeypatch.setattr(health_route, "get_whatsapp", lambda: _facade("notAuthorized"))

    resp = TestClient(app).get("/health")

    body = resp.json()
    assert body["checks"]["whatsapp"]["status"] == "down"
    assert body["checks"]["whatsapp"]["state"] == "notAuthorized"


def test_health_whatsapp_blocked_state_is_down(monkeypatch):
    """'blocked' is truthy but must not be reported as 'up' — the whole
    point of comparing against 'authorized' rather than truthiness."""
    monkeypatch.setattr(health_route, "get_whatsapp", lambda: _facade("blocked"))

    resp = TestClient(app).get("/health")

    body = resp.json()
    assert body["checks"]["whatsapp"]["status"] == "down"
    assert body["checks"]["whatsapp"]["state"] == "blocked"


def test_health_whatsapp_probe_error_is_down_with_null_state(monkeypatch):
    """A network/HTTP failure while probing getStateInstance must not crash
    the health endpoint — WhatsApp is reported down with no state."""
    monkeypatch.setattr(
        health_route,
        "get_whatsapp",
        lambda: _facade(error=Exception("connection reset")),
    )

    resp = TestClient(app).get("/health")

    body = resp.json()
    assert body["checks"]["whatsapp"]["status"] == "down"
    assert body["checks"]["whatsapp"]["state"] is None


def test_health_reports_degraded_for_a_non_transmitting_provider(monkeypatch):
    """PRO-86: DryRunProvider always reports "authorized" — it cannot be
    deauthorized. Passing that through as "up" would make a production deploy
    that forgot WHATSAPP_PROVIDER a silent black hole with a green dashboard."""
    provider = MagicMock()
    provider.name = "dryrun"
    provider.transmits = False
    provider.get_state = AsyncMock(return_value="authorized")
    monkeypatch.setattr(health_route, "get_whatsapp", lambda: WhatsAppFacade(provider))

    body = TestClient(app).get("/health").json()

    assert body["checks"]["whatsapp"]["status"] == "degraded"
    assert body["checks"]["whatsapp"]["provider"] == "dryrun"
    assert body["checks"]["whatsapp"]["transmits"] is False


def test_health_exposes_the_provider_name(monkeypatch):
    monkeypatch.setattr(health_route, "get_whatsapp", lambda: _facade("authorized"))

    body = TestClient(app).get("/health").json()

    assert body["checks"]["whatsapp"]["provider"] == "stub"
    assert body["checks"]["whatsapp"]["transmits"] is True
