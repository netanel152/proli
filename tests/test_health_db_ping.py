"""PRO-251 — /health must not stall the API while Mongo is unreachable.

The route used to call a blocking pymongo ``sync_client`` ping from inside
``async def health_check``, with pymongo's 30s server-selection default. While
Mongo was down every probe (Docker HEALTHCHECK, the staging deploy verifier,
the lead monitor) froze the event loop for up to 30s, and webhook intake with
it. ``database.ping_db`` awaits the Motor ping under ``asyncio.wait_for``.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import app.api.routes.health as health_route
import app.core.database as database
from app.core.config import settings
from app.core.constants import WorkerConstants
from app.main import app
from app.providers.whatsapp.facade import WhatsAppFacade


class _HangingAdmin:
    """``client.admin`` whose ping never answers, like an unreachable cluster."""

    def __init__(self):
        self.calls = 0

    async def command(self, name):
        self.calls += 1
        await asyncio.Event().wait()


def _fake_client(admin):
    fake = MagicMock()
    fake.admin = admin
    return fake


@pytest.mark.asyncio
async def test_ping_db_reports_down_within_its_timeout_when_mongo_hangs(monkeypatch):
    admin = _HangingAdmin()
    monkeypatch.setattr(database, "client", _fake_client(admin))

    t0 = time.monotonic()
    assert await database.ping_db(timeout=0.1) is False
    assert time.monotonic() - t0 < 1.0
    assert admin.calls == 1


@pytest.mark.asyncio
async def test_ping_db_true_when_mongo_answers(monkeypatch):
    admin = MagicMock()
    admin.command = AsyncMock(return_value={"ok": 1})
    monkeypatch.setattr(database, "client", _fake_client(admin))

    assert await database.ping_db() is True
    admin.command.assert_awaited_once_with("ping")


@pytest.mark.asyncio
async def test_ping_db_false_when_the_driver_raises(monkeypatch):
    admin = MagicMock()
    admin.command = AsyncMock(side_effect=RuntimeError("no primary"))
    monkeypatch.setattr(database, "client", _fake_client(admin))

    assert await database.ping_db() is False


def test_health_ping_is_bounded_well_below_the_driver_timeout():
    assert WorkerConstants.HEALTH_DB_PING_TIMEOUT_SECONDS <= 5
    assert (
        WorkerConstants.HEALTH_DB_PING_TIMEOUT_SECONDS * 1000
        < settings.MONGO_SERVER_SELECTION_TIMEOUT_MS
    )


def test_clients_carry_an_explicit_server_selection_timeout():
    expected = settings.MONGO_SERVER_SELECTION_TIMEOUT_MS
    assert database.kwargs["serverSelectionTimeoutMS"] == expected
    assert database.sync_client.options.server_selection_timeout == expected / 1000


@pytest.mark.asyncio
async def test_health_answers_within_the_timeout_and_other_requests_keep_flowing(
    monkeypatch, mock_db
):
    """The acceptance test from the issue: with a hung Mongo, /health still
    answers (Mongo reported down) within the ping bound, and a request made
    while it is waiting is served before it, so the loop was never blocked."""
    monkeypatch.setattr(database, "client", _fake_client(_HangingAdmin()))
    monkeypatch.setattr(database.ping_db, "__defaults__", (0.5,))

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
    monkeypatch.setattr(settings, "HEALTH_TOKEN", None)  # development: full detail

    finished = []

    async def timed(client, path):
        resp = await client.get(path)
        finished.append(path)
        return resp

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        t0 = time.monotonic()
        health_task = asyncio.create_task(timed(c, "/health"))
        await asyncio.sleep(0.05)  # /health is now parked on the ping
        other = await timed(c, "/privacy")
        health = await health_task
        elapsed = time.monotonic() - t0

    assert other.status_code == 200
    assert finished == ["/privacy", "/health"]
    assert health.status_code == 503
    assert health.json()["checks"]["mongodb"]["status"] == "down"
    assert elapsed < 3.0
