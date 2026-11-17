"""PRO-17: the lead-pipeline watch runs in the worker, with a Sentry Crons heartbeat.

The rules are those of the GitHub-scheduled monitor (scripts/check_health_leads.py);
what changed is who runs them, so GitHub's scheduler is no longer what stands
between a stuck pipeline and a page.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.core import sentry
from app.core.constants import LeadStatus
from app.services import pipeline_watch as pw

T0 = datetime(2026, 11, 17, 10, 0, tzinfo=timezone.utc)


def _run(_unused, readings):
    """Feed (minute offset, counts) readings through evaluate; collect pages."""
    state, pages = {}, []
    for minutes, counts in readings:
        state, new = pw.evaluate(state, counts, T0 + timedelta(minutes=minutes))
        pages += new
    return state, pages


def test_a_backlog_pages_after_thirty_minutes_and_hourly_after_that():
    _, pages = _run(
        {},
        [(m, (6, 0)) for m in range(0, 100, 10)],
    )
    _, more = _run({}, [(m, (6, 0)) for m in range(0, 110, 10)])
    assert len(more) == 2  # ... and 100 is the hourly re-page
    # Over the threshold from minute 0: first page at 40 (past 30), the next an
    # hour later — 90 is only 50 minutes on, so readings to 90 give one page.
    assert len(pages) == 1
    assert all("pending_review_count=6" in p for p in pages)


def test_a_backlog_that_clears_resets_the_clock():
    _, pages = _run({}, [(0, (6, 0)), (20, (2, 0)), (40, (6, 0)), (60, (6, 0))])
    assert pages == []


def test_a_climbing_stuck_count_pages_once_per_climb():
    _, pages = _run(
        {},
        [(0, (0, 1)), (10, (0, 3)), (30, (0, 3)), (50, (0, 3)), (60, (0, 3))],
    )
    assert len(pages) == 1
    assert "rose from 1 to 3" in pages[0]


def test_a_stuck_count_that_comes_back_down_never_pages():
    _, pages = _run({}, [(0, (0, 1)), (10, (0, 3)), (20, (0, 1)), (60, (0, 1))])
    assert pages == []


@pytest.fixture
def checkins(monkeypatch):
    calls = []

    def fake(status, check_in_id=None, duration=None):
        calls.append((status, check_in_id))
        return "cid-1" if status == "in_progress" else None

    monkeypatch.setattr(pw, "cron_checkin", fake)
    return calls


@pytest.mark.asyncio
async def test_a_tick_checks_in_and_pages_from_the_real_counts(
    checkins, monkeypatch, mock_db, fake_redis
):
    monkeypatch.setattr(pw, "leads_collection", mock_db.leads)
    pages = []
    monkeypatch.setattr(pw, "page_operator", pages.append)
    await mock_db.leads.insert_many(
        [{"status": LeadStatus.PENDING_ADMIN_REVIEW} for _ in range(7)]
    )
    # The backlog has been over the threshold for 40 minutes already.
    await fake_redis.set(
        pw.STATE_KEY,
        '{"pending_since": "%s"}'
        % (datetime.now(timezone.utc) - timedelta(minutes=40)).isoformat(),
    )

    await pw.run_pipeline_watch()

    assert checkins == [("in_progress", None), ("ok", "cid-1")]
    assert len(pages) == 1 and "pending_review_count=7" in pages[0]


@pytest.mark.asyncio
async def test_an_unreadable_pipeline_checks_in_as_an_error(checkins, monkeypatch):
    monkeypatch.setattr(
        pw, "lead_pipeline_counts", AsyncMock(side_effect=RuntimeError("mongo down"))
    )

    with pytest.raises(RuntimeError):
        await pw.run_pipeline_watch()

    assert checkins == [("in_progress", None), ("error", "cid-1")]


def test_the_check_in_is_a_no_op_without_sentry(monkeypatch):
    monkeypatch.setattr(sentry, "sentry_active", lambda: False)
    assert sentry.cron_checkin("ok") is None


def test_the_check_in_upserts_a_ten_minute_monitor(monkeypatch):
    seen = {}
    monkeypatch.setattr(sentry, "sentry_active", lambda: True)

    def capture(**kwargs):
        seen.update(kwargs)
        return "cid"

    monkeypatch.setattr("sentry_sdk.crons.capture_checkin", capture)

    assert sentry.cron_checkin("in_progress") == "cid"
    assert seen["monitor_slug"] == "worker-pipeline-watch"
    assert seen["monitor_config"]["schedule"] == {
        "type": "interval",
        "value": 10,
        "unit": "minute",
    }


def test_a_failing_check_in_never_fails_the_job(monkeypatch):
    monkeypatch.setattr(sentry, "sentry_active", lambda: True)

    def boom(**kwargs):
        raise RuntimeError("sentry unreachable")

    monkeypatch.setattr("sentry_sdk.crons.capture_checkin", boom)
    assert sentry.cron_checkin("ok") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("env, expected", [("production", True), ("staging", False)])
async def test_the_watch_is_registered_every_ten_minutes_in_production_only(
    monkeypatch, env, expected
):
    import app.scheduler as sched

    monkeypatch.setattr(sched.settings, "ENVIRONMENT", env)
    scheduler = sched.start_scheduler()
    try:
        job = scheduler.get_job("pipeline_watch")
        assert (job is not None) is expected
        if job:
            assert job.trigger.interval == timedelta(minutes=10)
    finally:
        scheduler.shutdown(wait=False)
