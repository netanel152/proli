"""
PRO-153 — API startup survives a transient dependency blip.

Railway does not order service restarts: an environment-wide redeploy can
start the api container before redis (2026-08-22 — /health answered 502 for
8 minutes while the dashboard said SUCCESS, because the lifespan raised on
the first failed ping and nothing retried).

The fix: `_connect_with_retry` probes each hard dependency with bounded
backoff (delays 1,2,4,8,8s — a co-restart window) before falling back to the
old page_critical + raise. Fail-closed after retries is deliberate: the
API's one job is enqueueing webhooks to Redis, so serving without it would
503 every webhook while looking "up".

asyncio.sleep is patched throughout — no real waiting in tests.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.main import _connect_with_retry, app


@pytest.fixture
def no_sleep(monkeypatch):
    """Record backoff delays instead of actually sleeping."""
    delays = []

    async def fake_sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr(main_module.asyncio, "sleep", fake_sleep)
    return delays


class _FlakyProbe:
    """Fails with ConnectionError for the first `failures` calls, then succeeds."""

    def __init__(self, failures: int):
        self.failures = failures
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise ConnectionError(f"boom #{self.calls}")


# ------------------------------------------------------------- helper unit


@pytest.mark.asyncio
async def test_retry_succeeds_after_transient_failures(no_sleep):
    """Two failed pings then a good one → returns, no raise, backoff 1s, 2s."""
    probe = _FlakyProbe(failures=2)
    await _connect_with_retry("TestDep", probe)
    assert probe.calls == 3
    assert no_sleep == [1, 2]


@pytest.mark.asyncio
async def test_retry_backoff_is_bounded(no_sleep):
    """Delays follow 1,2,4,8,8 — capped at 8s, ~23s total (co-restart window)."""
    probe = _FlakyProbe(failures=5)
    await _connect_with_retry("TestDep", probe, attempts=6)
    assert no_sleep == [1, 2, 4, 8, 8]


@pytest.mark.asyncio
async def test_retry_gives_up_pages_and_raises(no_sleep, monkeypatch):
    """A genuinely dead dependency keeps the old contract: page + raise."""
    pager = MagicMock()
    monkeypatch.setattr(main_module, "page_critical", pager)
    probe = _FlakyProbe(failures=99)
    with pytest.raises(ConnectionError):
        await _connect_with_retry("TestDep", probe, attempts=3)
    assert probe.calls == 3
    pager.assert_called_once()
    assert "TestDep" in pager.call_args[0][0]
    assert "3 attempts" in pager.call_args[0][0]


# --------------------------------------------------------- lifespan-level


def test_app_boots_when_redis_is_briefly_down(no_sleep, monkeypatch):
    """AC: Redis unavailable for the first N attempts, then available → boots.

    Mongo answers immediately; Redis rejects the first two pings (the
    co-restart shape from the incident). The lifespan must retry through it
    and the app must come up serving.
    """
    mongo = MagicMock()
    mongo.admin.command = AsyncMock(return_value={"ok": 1})
    monkeypatch.setattr(main_module, "mongo_client", mongo)

    flaky = _FlakyProbe(failures=2)

    async def flaky_get_redis_client():
        await flaky()
        redis = MagicMock()
        redis.ping = AsyncMock(return_value=True)
        return redis

    monkeypatch.setattr(main_module, "get_redis_client", flaky_get_redis_client)
    monkeypatch.setattr(main_module, "create_all_indexes", AsyncMock(return_value=None))
    monkeypatch.setattr(main_module, "close_redis_client", AsyncMock())
    monkeypatch.setattr(main_module, "_close_shared_http_client", AsyncMock())

    with TestClient(app) as client:  # __enter__ runs the lifespan startup
        resp = client.get("/health")
        assert resp.status_code == 200

    assert flaky.calls == 3  # two failures + one success — it retried
    assert no_sleep[:2] == [1, 2]


def test_app_refuses_to_boot_when_redis_stays_down(no_sleep, monkeypatch):
    """Retries exhausted → the boot still fails closed (deliberate decision)."""
    mongo = MagicMock()
    mongo.admin.command = AsyncMock(return_value={"ok": 1})
    monkeypatch.setattr(main_module, "mongo_client", mongo)

    async def dead_get_redis_client():
        raise ConnectionError("redis is really gone")

    monkeypatch.setattr(main_module, "get_redis_client", dead_get_redis_client)
    pager = MagicMock()
    monkeypatch.setattr(main_module, "page_critical", pager)

    with pytest.raises(ConnectionError):
        with TestClient(app):
            pass
    pager.assert_called_once()
