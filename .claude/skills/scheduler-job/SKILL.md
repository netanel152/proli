---
name: scheduler-job
description: Add or change an APScheduler job in the ARQ worker (app/scheduler.py start_scheduler) — a periodic monitor, healer, watchdog or report. Use whenever a task says "every N minutes/hours", "once a day", "at boot", or touches a job's lock, gating, boot run or misfire handling.
paths:
  - "app/scheduler.py"
  - "app/services/monitor_service.py"
  - "tests/test_scheduler*.py"
---

# Adding a scheduler job

Every job in `app/scheduler.py` is shaped by an incident, and the shape is the point. Read
`start_scheduler()` and the wrappers above it first; this skill is the checklist of what a
new job must carry, with the ticket that explains each item. No value here overrides the
code — intervals, TTLs and boot-slot positions are owned by `app/scheduler.py` and
`app/core/constants.py`.

## The wrapper every job gets

```python
@with_scheduler_lock("run_<job>", ttl=<slightly under the interval, in seconds>)
@track_mongo_auth_failures
async def run_<job>():
    """<one line: what it does and what gates it>"""
    if not await _customer_cold_job_allowed("<job>_active"):   # only if it messages a customer
        return
    await <service>.<do_the_work>()
```

- **`@with_scheduler_lock(key, ttl)`** (`app/core/redis_client.py`) — one worker replica at
  a time, Redis SETNX; TTL slightly shorter than the interval; runs locally if Redis is down.
  Outermost, so a lock-skipped run is not counted by the decorator under it.
- **`@track_mongo_auth_failures`** (PRO-112) — counts Mongo auth failures across jobs and
  pages CRITICAL past `SCHEDULER_MONGO_AUTH_TRIP_THRESHOLD`; always re-raises so
  `_on_job_error` still captures to Sentry.
- **Gating.** A job that *cold-messages a customer* (a nudge, a deflection, a janitor
  rejection) goes through `_customer_cold_job_allowed(toggle)` — business hours **and** a
  per-job toggle in `settings.scheduler_config` that defaults **off** (PRO-73). A job that
  only pages the operator or writes state checks the toggle alone, like `run_sos_reporter`.
- **Environment.** Production-only jobs (backup, its freshness watchdog) are gated at
  *registration* with `if settings.is_production:` — the job never enters the scheduler
  elsewhere (PRO-127), so staging cannot page about something it is not meant to do.

## Registering it

Short-interval jobs (minutes) use a plain `IntervalTrigger` + `id` + `replace_existing`.

**Long-interval jobs (hours) must take a boot slot** — `**_long_job_kwargs(position)`
(PRO-176): without an explicit `next_run_time` an interval job first fires one full interval
after `scheduler.start()`, and the in-memory job store forgets that countdown on every
deploy, so a deploy cadence shorter than the interval starves the job silently. The kwargs
also set `coalesce=True` and the long `misfire_grace_time`, so a tick that comes due while
the loop is busy runs late instead of being dropped. Positions are distinct integers; take
the next free one and add the job to `LONG_JOB_IDS_BY_POSITION` in
`tests/test_scheduler_boot_run.py`.

Cron jobs (a time of day) use `CronTrigger(..., timezone=IL_TZ)`; give them `coalesce` and
the long misfire grace too (PRO-185's treatment of the backup cron).

## Per-lead idempotency

A job that acts on a lead — pages, nudges, reassigns — must not act twice across two ticks
or two replicas. The house pattern is a **due-filter predicate applied twice**: once in the
query and again inside an atomic `find_one_and_update` claim that stamps the timestamp the
predicate reads (`stuck_lead_report_due_filter` + `admin_reported_at` for the SOS Reporter,
`completion_check_due_filter` + `completion_check_sent_at` for the completion check). Add
a cooldown constant when the boot run could otherwise burn a per-lead budget across a few
quick deploys (`STALE_LEAD_REMINDER_COOLDOWN_HOURS`).

## Constants, docs and tests — same PR

- Interval, threshold and cooldown values live in `WorkerConstants` with a comment naming
  the incident; they are then listed in `.claude/rules/constants.md` (the docs-syncer
  subagent audits it).
- The job list in `CLAUDE.md`'s Process 2 paragraph and `docs/OPERATIONS_GUIDE.md` names
  every job and its cadence.
- Tests: `tests/test_scheduler.py` (the job does its work and respects its toggle),
  `tests/test_scheduler_gating.py` (business-hours gate), `tests/test_scheduler_boot_run.py`
  (boot slot, stagger, misfire grace — for long jobs), `tests/test_scheduler_auth_escalation.py`
  (the decorator counts your job's auth failures like the others).

```bash
pytest tests/test_scheduler.py tests/test_scheduler_boot_run.py tests/test_scheduler_gating.py tests/test_scheduler_auth_escalation.py -q
```
