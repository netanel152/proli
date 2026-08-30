"""PRO-176 — scheduler boot-run fix.

Deploy churn (a Railway redeploy on every merge to `dev`, or a crash-loop)
starved the three long-interval jobs — `sos_admin_reporter` (4h),
`stale_lead_nudger` (4h), and `lead_janitor` (6h). APScheduler's
IntervalTrigger fires its first run one full interval after `start()` by
default, and the in-memory job store forgets that countdown on every
restart, so a worker that never survives an interval's worth of uptime
never runs them at all — invisible, because the worker stays healthy and
`/health` stays green throughout.

`first_run_at`/`_long_job_kwargs` give each of the three a staggered
boot-time first run instead, without touching their steady-state interval,
and `start_scheduler` logs the resulting schedule so Railway logs make it
observable.
"""

import re
from datetime import datetime, timedelta

import pytest
import pytz

import app.scheduler as sched
from app.core.constants import WorkerConstants

LONG_JOB_IDS_BY_POSITION = [
    ("sos_admin_reporter", 0),
    ("stale_lead_nudger", 1),
    ("lead_janitor", 2),
]


@pytest.mark.parametrize("position", [0, 1, 2])
def test_first_run_at_uses_delay_and_stagger(position):
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=pytz.utc)

    result = sched.first_run_at(position, now=now)

    expected = now + timedelta(
        seconds=WorkerConstants.SCHEDULER_BOOT_RUN_DELAY_SECONDS
        + position * WorkerConstants.SCHEDULER_BOOT_RUN_STAGGER_SECONDS
    )
    assert result == expected


def test_first_run_at_default_now_is_tz_aware():
    # Must be comparable with APScheduler's next_run_time, which is always
    # tz-aware once a job is scheduled with a tz-aware trigger.
    result = sched.first_run_at(0)

    assert result.tzinfo is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("job_id,position", LONG_JOB_IDS_BY_POSITION)
async def test_long_job_first_run_within_boot_window_not_full_interval(
    job_id, position
):
    """AC1 — each long job's first run lands in its own boot-stagger slot,
    nowhere near its ~4h/~6h steady-state interval away."""
    before = datetime.now(sched.IL_TZ)
    scheduler = sched.start_scheduler()
    try:
        job = scheduler.get_job(job_id)
        delta = (job.next_run_time - before).total_seconds()

        window_start = (
            WorkerConstants.SCHEDULER_BOOT_RUN_DELAY_SECONDS
            + position * WorkerConstants.SCHEDULER_BOOT_RUN_STAGGER_SECONDS
        )
        assert window_start <= delta <= window_start + 5
    finally:
        scheduler.shutdown(wait=False)


@pytest.mark.asyncio
async def test_long_jobs_first_runs_staggered_pairwise():
    """AC2 — no two of the three long jobs fire in the same second."""
    scheduler = sched.start_scheduler()
    try:
        next_runs = {
            job_id: scheduler.get_job(job_id).next_run_time
            for job_id, _ in LONG_JOB_IDS_BY_POSITION
        }
        ids = list(next_runs)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                gap = abs((next_runs[ids[i]] - next_runs[ids[j]]).total_seconds())
                assert gap >= WorkerConstants.SCHEDULER_BOOT_RUN_STAGGER_SECONDS - 1
    finally:
        scheduler.shutdown(wait=False)


@pytest.mark.asyncio
async def test_long_jobs_coalesce_and_carry_long_misfire_grace():
    scheduler = sched.start_scheduler()
    try:
        for job_id, _ in LONG_JOB_IDS_BY_POSITION:
            job = scheduler.get_job(job_id)
            assert job.coalesce is True
            assert (
                job.misfire_grace_time
                == WorkerConstants.SCHEDULER_LONG_JOB_MISFIRE_GRACE_SECONDS
            )
    finally:
        scheduler.shutdown(wait=False)


@pytest.mark.asyncio
async def test_short_interval_job_unaffected_by_boot_run():
    """Scope guard — a job outside the three long ones keeps APScheduler's
    default behavior: first run one full interval away, default misfire
    grace (not the 600s long-job grace, not coalesced by PRO-176)."""
    before = datetime.now(sched.IL_TZ)
    scheduler = sched.start_scheduler()
    try:
        job = scheduler.get_job("stale_job_monitor")
        delta = (job.next_run_time - before).total_seconds()

        # stale_job_monitor is IntervalTrigger(minutes=30) with no boot
        # kwargs — still ~one full interval away, not pulled into the
        # boot window ([60, 150]s) the three long jobs land in.
        assert 1790 <= delta <= 1810
        assert job.misfire_grace_time == 1  # APScheduler's own default
    finally:
        scheduler.shutdown(wait=False)


@pytest.mark.asyncio
async def test_start_scheduler_logs_first_runs_for_long_jobs(monkeypatch):
    """AC3 — the boot-time log line names every long job with an ISO
    timestamp, so Railway logs make the schedule observable."""
    logged = []
    monkeypatch.setattr(sched.logger, "info", lambda msg, *a, **k: logged.append(msg))

    scheduler = sched.start_scheduler()
    try:
        boot_lines = [m for m in logged if m.startswith("[Scheduler] First runs:")]
        assert len(boot_lines) == 1
        line = boot_lines[0]
        for job_id, _ in LONG_JOB_IDS_BY_POSITION:
            assert re.search(
                rf"{job_id} @ \d{{4}}-\d{{2}}-\d{{2}}T\d{{2}}:\d{{2}}:\d{{2}}", line
            )
    finally:
        scheduler.shutdown(wait=False)
