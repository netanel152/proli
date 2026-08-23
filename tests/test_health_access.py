"""
PRO-136 — access control on the health endpoints.

/health and /health/leads are internet-facing on Railway with no proxy in
front (nginx exists only in docker-compose), so the unauthenticated surface
must not leak internals:

  * GET /health without credentials → only {"status", "uptime_seconds"} —
    exactly what the Docker HEALTHCHECK and the promotion workflow's deploy
    verifier consume. The ``checks`` object (latencies, provider name,
    transmits flag, worker heartbeat) requires the X-Health-Token header.
  * GET /health/leads exposes business KPIs (admin backlog, stuck leads) and
    is token-only; without authorization it returns 403 and no counters.
  * A DB failure on /health/leads returns a fixed "internal error" body —
    the raw exception (which can carry Mongo connection details) stays in
    logs/Sentry.

Token semantics (``_detail_authorized``): HEALTH_TOKEN set → constant-time
header compare; unset → open in development, fail-closed in staging and
production (nothing can authenticate, so nothing is shown).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

import app.api.routes.health as health_route
from app.core.config import settings
from app.main import app
from app.providers.whatsapp.facade import WhatsAppFacade

TOKEN = "test-health-token-123"


@pytest.fixture(autouse=True)
def _mock_dependencies(monkeypatch, mock_db):
    """Deterministic mongo/redis/whatsapp so only the access logic varies."""
    monkeypatch.setattr(health_route, "check_db_connection", lambda: True)

    mock_redis = AsyncMock()
    mock_redis.ping = AsyncMock(return_value=True)
    mock_redis.get = AsyncMock(return_value=None)
    monkeypatch.setattr(
        health_route, "get_redis_client", AsyncMock(return_value=mock_redis)
    )

    provider = MagicMock()
    provider.name = "stub"
    provider.transmits = True
    provider.get_state = AsyncMock(return_value="authorized")
    monkeypatch.setattr(health_route, "get_whatsapp", lambda: WhatsAppFacade(provider))

    monkeypatch.setattr(health_route, "leads_collection", mock_db.leads)


@pytest.fixture
def client():
    return TestClient(app)


def _prod_like(monkeypatch, token: str | None):
    monkeypatch.setattr(settings, "ENVIRONMENT", "staging")
    monkeypatch.setattr(settings, "HEALTH_TOKEN", SecretStr(token) if token else None)


# ---------------------------------------------------------------- /health


def test_health_dev_no_token_keeps_full_detail(client, monkeypatch):
    """Development with no HEALTH_TOKEN → checks stay visible (local curl)."""
    monkeypatch.setattr(settings, "HEALTH_TOKEN", None)
    body = client.get("/health").json()
    assert body["status"] == "healthy"
    assert "checks" in body


def test_health_prod_like_unauthenticated_is_minimal(client, monkeypatch):
    """staging/production without the header → liveness fields only."""
    _prod_like(monkeypatch, TOKEN)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"status", "uptime_seconds"}  # what the deploy verifier reads
    assert body["status"] == "healthy"


def test_health_prod_like_no_token_configured_fails_closed(client, monkeypatch):
    """HEALTH_TOKEN unset in a prod-like env → nothing can see the checks."""
    _prod_like(monkeypatch, None)
    body = client.get("/health").json()
    assert "checks" not in body


def test_health_wrong_token_is_minimal(client, monkeypatch):
    _prod_like(monkeypatch, TOKEN)
    body = client.get("/health", headers={"X-Health-Token": "nope"}).json()
    assert "checks" not in body


def test_health_correct_token_grants_detail(client, monkeypatch):
    _prod_like(monkeypatch, TOKEN)
    body = client.get("/health", headers={"X-Health-Token": TOKEN}).json()
    assert body["checks"]["whatsapp"]["status"] == "up"
    assert body["checks"]["mongodb"]["status"] == "up"


def test_health_commit_field_only_in_authorized_detail(client, monkeypatch):
    """PRO-155: the deployed-commit SHA rides in the authenticated detail
    (read by the verify-staging-deploy workflow), never in the public body.
    Locally RAILWAY_GIT_COMMIT_SHA is unset -> the field is present but None.
    """
    _prod_like(monkeypatch, TOKEN)
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "abc123def456")
    anon = client.get("/health").json()
    assert "commit" not in anon
    body = client.get("/health", headers={"X-Health-Token": TOKEN}).json()
    assert body["commit"] == "abc123def456"


# ---------------------------------------------------------- /health/leads


@pytest.mark.asyncio
async def test_leads_prod_like_unauthenticated_403(client, monkeypatch, mock_db):
    """No counters for anonymous callers — 403 and a bare body."""
    await mock_db.leads.delete_many({})
    _prod_like(monkeypatch, TOKEN)
    resp = client.get("/health/leads")
    assert resp.status_code == 403
    assert resp.json() == {"status": "forbidden"}


@pytest.mark.asyncio
async def test_leads_correct_token_returns_counters(client, monkeypatch, mock_db):
    await mock_db.leads.delete_many({})
    _prod_like(monkeypatch, TOKEN)
    resp = client.get("/health/leads", headers={"X-Health-Token": TOKEN})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["pending_review_count"] == 0


def test_leads_error_body_is_fixed_string(client, monkeypatch):
    """DB blows up → 503 with a constant body; str(e) never reaches HTTP."""
    monkeypatch.setattr(settings, "HEALTH_TOKEN", None)  # dev: authorized

    class _Boom:
        async def count_documents(self, *_a, **_k):
            raise RuntimeError("mongodb://user:secret@host — connection refused")

    monkeypatch.setattr(health_route, "leads_collection", _Boom())
    resp = client.get("/health/leads")
    assert resp.status_code == 503
    assert resp.json() == {"status": "error", "error": "internal error"}
    assert "secret" not in resp.text
