"""
PRO-185 — GET /health `checks.backup` freshness block.

`run_daily_backup` (app/scheduler.py) writes `backup:last_success` as a
unix-timestamp string with no TTL on every successful nightly run. This is
the *external* half of the freshness watchdog: a caller polling /health from
outside the process can see a dead worker, a misconfigured environment that
never registered the scheduler job, or a stale lock — none of which the
in-process watchdog job can report on itself, because it *is* the worker.

Mongo and the WhatsApp probe are mocked so these tests exercise only the
backup branch, same posture as tests/test_health_whatsapp_status.py.
"""

import time
from datetime import timedelta, timezone, datetime as real_datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

import app.api.routes.health as health_route
from app.core.config import settings
from app.core.constants import BACKUP_LAST_SUCCESS_KEY, WorkerConstants
from app.main import app


def _redis_with_backup(raw_value):
    """A redis stub whose .get() answers BACKUP_LAST_SUCCESS_KEY with
    `raw_value` and everything else (e.g. the worker heartbeat) with None."""
    mock_redis = AsyncMock()
    mock_redis.ping = AsyncMock(return_value=True)

    async def _get(key):
        return raw_value if key == BACKUP_LAST_SUCCESS_KEY else None

    mock_redis.get = AsyncMock(side_effect=_get)
    return mock_redis


@pytest.fixture(autouse=True)
def _mock_mongo_and_whatsapp(monkeypatch):
    """Keep mongo/whatsapp fast and deterministic — this file only cares
    about the backup branch of GET /health."""
    monkeypatch.setattr(health_route, "check_db_connection", lambda: True)

    provider = MagicMock()
    provider.name = "stub"
    provider.transmits = True
    provider.get_state = AsyncMock(return_value="authorized")
    from app.providers.whatsapp.facade import WhatsAppFacade

    monkeypatch.setattr(health_route, "get_whatsapp", lambda: WhatsAppFacade(provider))


@pytest.mark.asyncio
async def test_health_backup_absent_key_is_never(monkeypatch, mock_db):
    """No key at all, and no Mongo mirror either -> "never", not "stale" —
    the Redis key has no TTL, so absence means no success has ever landed
    since this Redis came up. `mock_db` is module-scoped, so the mirror doc
    is cleared explicitly rather than assumed absent (another test in this
    file writes one)."""
    await mock_db.settings.delete_many({"_id": "backup_state"})
    monkeypatch.setattr(
        health_route,
        "get_redis_client",
        AsyncMock(return_value=_redis_with_backup(None)),
    )

    body = TestClient(app).get("/health").json()

    assert body["checks"]["backup"]["status"] == "never"
    assert body["checks"]["backup"]["last_success"] is None
    assert body["checks"]["backup"]["age_hours"] is None
    assert (
        body["checks"]["backup"]["max_age_hours"]
        == WorkerConstants.BACKUP_MAX_AGE_HOURS
    )


def test_health_backup_recent_success_is_fresh_with_iso_timestamp(monkeypatch):
    """A backup 6h old reports "fresh" and surfaces an ISO-8601 timestamp,
    not the raw epoch value."""
    six_hours_ago = time.time() - 6 * 3600
    monkeypatch.setattr(
        health_route,
        "get_redis_client",
        AsyncMock(return_value=_redis_with_backup(str(int(six_hours_ago)))),
    )

    body = TestClient(app).get("/health").json()
    backup = body["checks"]["backup"]

    assert backup["status"] == "fresh"
    assert abs(backup["age_hours"] - 6) < 0.1
    # ISO-8601 UTC, not a bare epoch number.
    parsed = real_datetime.fromisoformat(backup["last_success"])
    assert parsed.tzinfo is not None
    assert parsed.astimezone(timezone.utc).utcoffset().total_seconds() == 0


@pytest.mark.asyncio
async def test_health_backup_old_success_is_stale_but_top_level_status_unaffected(
    monkeypatch, mock_db
):
    """A backup 50h old (past the 48h threshold), with no fresher Mongo
    mirror to rescue it, reports "stale" — but a stale backup is an
    operator page, not a liveness failure, so the top-level `status` (what
    the Docker HEALTHCHECK reads) must stay "healthy" as long as mongo/redis
    are actually up. A stale Redis key makes /health consult the mirror
    (see the fresh-mirror test below), so the mirror doc is cleared here to
    keep this test's "stale by Redis alone" premise true regardless of test
    order."""
    await mock_db.settings.delete_many({"_id": "backup_state"})
    fifty_hours_ago = time.time() - 50 * 3600
    monkeypatch.setattr(
        health_route,
        "get_redis_client",
        AsyncMock(return_value=_redis_with_backup(str(int(fifty_hours_ago)))),
    )

    resp = TestClient(app).get("/health")
    body = resp.json()

    assert body["checks"]["backup"]["status"] == "stale"
    assert body["status"] == "healthy"
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_health_backup_stale_redis_but_fresh_mirror_reports_fresh(
    monkeypatch, mock_db
):
    """Redis's copy can lag the Mongo mirror `run_daily_backup` also
    writes — a success recorded in either store counts, so a 60h-old Redis
    key must not read "stale" when the mirror is only 5h old."""
    await mock_db.settings.update_one(
        {"_id": "backup_state"},
        {
            "$set": {
                "last_success": real_datetime.now(timezone.utc) - timedelta(hours=5)
            }
        },
        upsert=True,
    )
    sixty_hours_ago = time.time() - 60 * 3600
    monkeypatch.setattr(
        health_route,
        "get_redis_client",
        AsyncMock(return_value=_redis_with_backup(str(int(sixty_hours_ago)))),
    )

    body = TestClient(app).get("/health").json()

    assert body["checks"]["backup"]["status"] == "fresh"


def test_health_backup_unauthenticated_absent_key_never_queries_mongo_mirror(
    monkeypatch,
):
    """PRO-136/PRO-185: the mirror lookup only runs for a caller who will
    actually see `checks` — an unauthenticated poller (the Docker
    HEALTHCHECK, the public staging verifier) must not pay for, or trigger,
    a Mongo round-trip it never sees the result of."""
    monkeypatch.setattr(settings, "ENVIRONMENT", "staging")
    monkeypatch.setattr(settings, "HEALTH_TOKEN", SecretStr("some-token"))
    monkeypatch.setattr(
        health_route,
        "get_redis_client",
        AsyncMock(return_value=_redis_with_backup(None)),
    )
    mock_settings_collection = MagicMock()
    mock_settings_collection.find_one = AsyncMock(return_value=None)
    import app.core.database as database_module

    monkeypatch.setattr(
        database_module, "settings_collection", mock_settings_collection
    )

    resp = TestClient(app).get("/health")  # no X-Health-Token header

    assert "checks" not in resp.json()
    mock_settings_collection.find_one.assert_not_awaited()


@pytest.mark.parametrize(
    "offset_seconds, expected_status",
    [
        (60, "fresh"),  # within BACKUP_CLOCK_SKEW_TOLERANCE_SECONDS (300s)
        (600, "unknown"),  # beyond tolerance — a manual write, not skew
    ],
)
def test_health_backup_future_timestamp_tolerance(
    monkeypatch, offset_seconds, expected_status
):
    """A `backup:last_success` slightly ahead of this container's clock
    (sub-second NTP skew right after a write from the worker container) must
    not flip to "unknown" — only a value beyond the tolerance window, which
    looks like a manual/corrupt write rather than skew, does."""
    future_ts = time.time() + offset_seconds
    monkeypatch.setattr(
        health_route,
        "get_redis_client",
        AsyncMock(return_value=_redis_with_backup(str(int(future_ts)))),
    )

    body = TestClient(app).get("/health").json()

    assert body["checks"]["backup"]["status"] == expected_status


def test_health_backup_corrupted_value_is_unknown(monkeypatch):
    """An unparseable value must not be reported as fresh or stale — the
    same "don't fake an answer" posture as the in-process watchdog."""
    monkeypatch.setattr(
        health_route,
        "get_redis_client",
        AsyncMock(return_value=_redis_with_backup("not-a-timestamp")),
    )

    body = TestClient(app).get("/health").json()

    assert body["checks"]["backup"]["status"] == "unknown"
    assert body["checks"]["backup"]["last_success"] is None
