"""PRO-127 — `daily_backup` is registered by `start_scheduler` only when
`settings.ENVIRONMENT == PRODUCTION_ENV`. Staging has no R2/AWS credentials,
so the job must never enter the scheduler outside production; other jobs
must still register regardless of environment.
"""

import pytest

import app.scheduler as sched
from app.core.constants import DEVELOPMENT_ENV, PRODUCTION_ENV, STAGING_ENV


@pytest.mark.asyncio
async def test_start_scheduler_production_registers_daily_backup(monkeypatch):
    monkeypatch.setattr(sched.settings, "ENVIRONMENT", PRODUCTION_ENV)

    scheduler = sched.start_scheduler()
    try:
        assert scheduler.get_job("daily_backup") is not None
    finally:
        scheduler.shutdown(wait=False)


@pytest.mark.asyncio
async def test_start_scheduler_staging_omits_daily_backup_but_keeps_other_jobs(
    monkeypatch,
):
    monkeypatch.setattr(sched.settings, "ENVIRONMENT", STAGING_ENV)

    scheduler = sched.start_scheduler()
    try:
        assert scheduler.get_job("daily_backup") is None
        # sentinel jobs unrelated to the backup gate still register — proving
        # only the backup job is gated, not the rest of start_scheduler.
        assert scheduler.get_job("pro_approval_sla") is not None
        assert scheduler.get_job("whatsapp_state_monitor") is not None
    finally:
        scheduler.shutdown(wait=False)


@pytest.mark.asyncio
async def test_start_scheduler_development_omits_daily_backup_but_keeps_other_jobs(
    monkeypatch,
):
    monkeypatch.setattr(sched.settings, "ENVIRONMENT", DEVELOPMENT_ENV)

    scheduler = sched.start_scheduler()
    try:
        assert scheduler.get_job("daily_backup") is None
        assert scheduler.get_job("pro_approval_sla") is not None
        assert scheduler.get_job("whatsapp_state_monitor") is not None
    finally:
        scheduler.shutdown(wait=False)


@pytest.mark.asyncio
async def test_start_scheduler_staging_logs_production_only_info(monkeypatch):
    monkeypatch.setattr(sched.settings, "ENVIRONMENT", STAGING_ENV)

    logged = []
    monkeypatch.setattr(sched.logger, "info", lambda msg: logged.append(msg))

    scheduler = sched.start_scheduler()
    try:
        assert any(
            "Daily backup job not scheduled" in msg
            and "production-only" in msg
            and STAGING_ENV in msg
            for msg in logged
        )
    finally:
        scheduler.shutdown(wait=False)
