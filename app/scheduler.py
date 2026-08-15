from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from app.core.database import users_collection, leads_collection, settings_collection
from app.services.workflow_service import (
    send_pro_reminder,
    send_customer_completion_check,
    whatsapp,
)
from app.services.monitor_service import (
    check_and_reassign_stale_leads,
    send_periodic_admin_report,
    auto_reject_unassigned_leads,
    check_sla_deflection,
    check_pro_approval_sla,
    remind_stale_booked_leads,
    check_whatsapp_instance_state,
)
from datetime import datetime, timedelta
import functools
import re
from pymongo.errors import OperationFailure, ServerSelectionTimeoutError
from app.core.constants import LeadStatus, WorkerConstants
from app.core.phone import to_chat_id, strip_suffix
from app.core.datetime_utils import within_business_hours
import os
import pytz
from app.services.scheduling_service import regenerate_all_templates
from app.core.logger import logger, page_critical
from app.core.redis_client import with_scheduler_lock

IL_TZ = pytz.timezone("Asia/Jerusalem")


async def send_daily_reminders():
    logger.info(
        f"⏰ [Scheduler] Starting daily reminders check at {datetime.now(IL_TZ)}"
    )

    active_pros = await users_collection.find({"is_active": True}).to_list(length=500)

    now_il = datetime.now(IL_TZ)
    today_start = now_il.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)

    start_utc = today_start.astimezone(pytz.utc)
    end_utc = today_end.astimezone(pytz.utc)

    for pro in active_pros:
        booked_jobs_cursor = leads_collection.find(
            {
                "pro_id": pro["_id"],
                "status": LeadStatus.BOOKED,
                "appointment_datetime": {"$gte": start_utc, "$lt": end_utc},
            }
        ).sort("appointment_datetime", 1)

        booked_jobs = await booked_jobs_cursor.to_list(length=50)

        if booked_jobs:
            msg = f"☀️ *בוקר טוב {pro['business_name']}!* \nהנה העבודות שלך להיום ({today_start.strftime('%d/%m')}):"
            for job in booked_jobs:
                job_time = (
                    job["appointment_datetime"]
                    .replace(tzinfo=pytz.utc)
                    .astimezone(IL_TZ)
                )
                time_str = job_time.strftime("%H:%M")
                client_phone = strip_suffix(job["chat_id"])
                details = job.get("details", "פרטים חסרים")
                msg += f"\n🛠️ *{time_str}* - {details}\n   📞 {client_phone}\n"

            msg += "\nשיהיה יום מוצלח! 💪"

            chat_id = pro.get("phone_number")
            if chat_id:
                chat_id = to_chat_id(chat_id)
                try:
                    await whatsapp.send_message(chat_id, msg)
                except Exception as e:
                    logger.error(f"❌ Failed to send to {pro['business_name']}: {e}")


# PRO-112 — Mongo auth-failure escalation. `bad auth` (Atlas 8000) on a
# scheduler job means the worker's MONGO_URI credentials are wrong — a config
# failure that kills every Mongo-touching job at once, not a data condition.
# Counted in Redis over a rolling window (shared across worker replicas); at
# the threshold logs CRITICAL — the Sentry paging level — throttled by the
# `:paged` key so a dead worker pages hourly instead of on every 2–5-min tick.
MONGO_AUTH_FAILURE_COUNT_KEY = "scheduler:mongo_auth_failures"
MONGO_AUTH_FAILURE_PAGED_KEY = "scheduler:mongo_auth_failures:paged"

# Error codes that unambiguously mean credentials/permissions:
# 13 Unauthorized, 18 AuthenticationFailed. Atlas's 8000 (AtlasError) is its
# *generic* policy code (also over-quota, disallowed commands, connection
# limits), so an 8000 counts only when the message itself says auth — as the
# real-world "bad auth : authentication failed" does. The text match is
# deliberately narrow: a bare "auth" substring would false-positive on any
# server message echoing a field name like "author".
_MONGO_AUTH_ERROR_CODES = {13, 18}
_AUTH_TEXT = re.compile(
    r"bad auth|authentication failed|not authorized|requires authentication",
    re.IGNORECASE,
)


def _is_mongo_auth_failure(exc: Exception) -> bool:
    # getattr: ServerSelectionTimeoutError carries no `code` attribute.
    if getattr(exc, "code", None) in _MONGO_AUTH_ERROR_CODES:
        return True
    return bool(_AUTH_TEXT.search(str(exc)))


async def _record_mongo_auth_failure(job_name: str, exc: Exception) -> None:
    """Count an auth failure and escalate at the threshold. Fails open on
    every Redis error — the caller re-raises the original exception
    regardless, so the existing ERROR/Sentry behavior is unchanged whenever
    the counter is unavailable."""
    from redis.exceptions import ResponseError
    from app.core.logger import mask_pii, redact_secrets
    from app.core.redis_client import get_redis_client

    try:
        redis = await get_redis_client()
        try:
            # One MULTI/EXEC round trip: INCR and its rolling-window EXPIRE
            # must not be split — a crash between them would leave the counter
            # TTL-less, and a single unrelated auth failure months later would
            # increment straight past the threshold and page immediately.
            pipe = redis.pipeline()
            pipe.incr(MONGO_AUTH_FAILURE_COUNT_KEY)
            pipe.expire(
                MONGO_AUTH_FAILURE_COUNT_KEY,
                WorkerConstants.SCHEDULER_MONGO_AUTH_WINDOW_SECONDS,
            )
            failures = int((await pipe.execute())[0])
        except ResponseError:
            # A corrupted (non-integer) value would make INCR raise on every
            # failure and silently disable escalation forever — reset it.
            await redis.set(
                MONGO_AUTH_FAILURE_COUNT_KEY,
                1,
                ex=WorkerConstants.SCHEDULER_MONGO_AUTH_WINDOW_SECONDS,
            )
            failures = 1
        if failures < WorkerConstants.SCHEDULER_MONGO_AUTH_TRIP_THRESHOLD:
            return
        paged = await redis.set(
            MONGO_AUTH_FAILURE_PAGED_KEY,
            "1",
            ex=WorkerConstants.SCHEDULER_MONGO_AUTH_REALERT_SECONDS,
            nx=True,
        )
        if paged:
            # Scrub BEFORE truncating (PRO-94/PRO-111 precedent): cutting
            # first could leave half a secret or phone number the scrubbers
            # no longer recognize. page_critical scrubs the whole message
            # again — harmless — and is the stdlib path Sentry actually sees.
            page_critical(
                f"🚨 [Scheduler] Mongo auth failure on '{job_name}' — "
                f"{failures} auth failures since the first one (no gap longer "
                f"than {WorkerConstants.SCHEDULER_MONGO_AUTH_WINDOW_SECONDS // 60} "
                f"min). MONGO_URI credentials are likely wrong (rotation "
                f"fallout?); every Mongo-touching scheduler job is failing. "
                f"Detail: {mask_pii(redact_secrets(str(exc)))[:500]}"
            )
    except Exception as e:
        logger.warning(f"[Scheduler] Mongo auth-failure counter unavailable: {e}")


def track_mongo_auth_failures(func):
    """PRO-112 — wrap a scheduler job so Mongo *auth* failures (bad
    credentials, deleted DB user) are counted and escalated to CRITICAL.
    Stacked UNDER @with_scheduler_lock so lock-skipped runs don't count.
    Always re-raises: the original exception still reaches APScheduler and
    Sentry exactly as before."""

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except (OperationFailure, ServerSelectionTimeoutError) as e:
            # AutoReconnect broadly is deliberately NOT counted (collateral of
            # the auth storm, per PRO-112); a server-selection timeout counts
            # only when its SDAM detail literally reports an auth failure —
            # a handshake auth error can surface that way on some ticks.
            if _is_mongo_auth_failure(e):
                try:
                    await _record_mongo_auth_failure(func.__name__, e)
                except Exception as track_err:  # never mask the real failure
                    logger.warning(
                        f"[Scheduler] auth-failure tracking failed: {track_err}"
                    )
            raise

    return wrapper


@with_scheduler_lock("monitor_unfinished_jobs", ttl=1500)
@track_mongo_auth_failures
async def monitor_unfinished_jobs():
    """
    Monitors 'booked' leads and takes action based on their age.
    """
    # 1. Check Config
    config = await settings_collection.find_one({"_id": "scheduler_config"})
    if config and not config.get("stale_monitor_active", True):
        return

    now_il = datetime.now(IL_TZ)
    if not (8 <= now_il.hour < 21):
        return  # Safety: Only run during business hours

    logger.info(
        f"🕵️ [Scheduler] Running Stale Job Monitor at {now_il.strftime('%H:%M')}..."
    )
    now_utc = datetime.now(pytz.utc)

    # Tier 1: 4-6 hours old -> Remind Pro
    t1_start = now_utc - timedelta(hours=6)
    t1_end = now_utc - timedelta(hours=4)
    t1_leads_cursor = leads_collection.find(
        {"status": LeadStatus.BOOKED, "created_at": {"$gte": t1_start, "$lt": t1_end}}
    )
    async for lead in t1_leads_cursor:
        logger.info(f"[Monitor] T1: Sending pro reminder for lead {lead['_id']}")
        await send_pro_reminder(str(lead["_id"]))

    # Tier 2: 6-24 hours old -> Check with Customer
    t2_start = now_utc - timedelta(hours=24)
    t2_end = now_utc - timedelta(hours=6)
    t2_leads_cursor = leads_collection.find(
        {"status": LeadStatus.BOOKED, "created_at": {"$gte": t2_start, "$lt": t2_end}}
    )
    async for lead in t2_leads_cursor:
        logger.info(f"[Monitor] T2: Sending customer check for lead {lead['_id']}")
        await send_customer_completion_check(str(lead["_id"]))

    # Tier 3: >24 hours old -> Flag for Admin
    t3_end = now_utc - timedelta(hours=24)
    update_result = await leads_collection.update_many(
        {
            "status": LeadStatus.BOOKED,
            "created_at": {"$lt": t3_end},
            "flag": {"$ne": "requires_admin"},
        },
        {"$set": {"flag": "requires_admin"}},
    )
    if update_result.modified_count > 0:
        logger.info(
            f"[Monitor] T3: Flagged {update_result.modified_count} leads for admin review."
        )


# --- Wrappers for Imported Services ---


async def _customer_cold_job_allowed(toggle_key: str) -> bool:
    """PRO-73 gate for *cold customer-facing* scheduler jobs (SOS healer, lead
    janitor, SLA deflection): run only **inside business hours** AND when the
    per-job toggle is enabled. Toggles **default OFF** — these re-engagement jobs
    that message a customer because the chat went silent stay dark until an
    operator turns them on after the WhatsApp number is warmed up (pilot safety)."""
    if not within_business_hours():
        return False
    config = await settings_collection.find_one({"_id": "scheduler_config"})
    return bool(config and config.get(toggle_key, False))


@with_scheduler_lock("run_sos_healer", ttl=500)
@track_mongo_auth_failures
async def run_sos_healer():
    """Wrapper for SOS Auto-Healer — gated (business hours + toggle, PRO-73)."""
    if not await _customer_cold_job_allowed("sos_healer_active"):
        return
    await check_and_reassign_stale_leads()


@with_scheduler_lock("run_slot_regeneration", ttl=82000)
async def run_slot_regeneration():
    """Regenerate slots from recurring templates for all active pros."""
    try:
        count = await regenerate_all_templates()
        if count > 0:
            logger.info(f"✅ [Scheduler] Slot regeneration: {count} new slots")
    except Exception as e:
        logger.error(f"❌ [Scheduler] Slot regeneration error: {e}")


# PRO-111 — consecutive backup-failure counter. Lives in Redis (shared across
# worker replicas and redeploys), cleared on the first successful run. No TTL:
# a failure streak must survive until a success actually clears it.
BACKUP_FAILURE_COUNT_KEY = "backup:consecutive_failures"


async def _report_backup_failure(detail: str) -> None:
    """PRO-111 escalation: a single failed nightly run is ERROR (transient blip);
    BACKUP_FAILURE_ESCALATION_THRESHOLD consecutive failed runs is CRITICAL —
    the Sentry paging threshold — because the previous failure already went
    unnoticed and durable backups are growing stale. Redis errors fail open:
    an uncounted streak stays ERROR (explicit `counted` flag, so this holds
    even if the threshold is ever lowered to 1) rather than crashing the job
    or paging on day one."""
    from redis.exceptions import ResponseError
    from app.core.redis_client import get_redis_client

    failures = 1
    counted = False
    try:
        redis = await get_redis_client()
        try:
            failures = int(await redis.incr(BACKUP_FAILURE_COUNT_KEY))
        except ResponseError:
            # A corrupted (non-integer) value would make INCR raise every
            # night and silently disable escalation forever — reset it.
            await redis.set(BACKUP_FAILURE_COUNT_KEY, 1)
            failures = 1
        counted = True
    except Exception as e:
        logger.warning(f"[Scheduler] Backup failure counter unavailable: {e}")

    if counted and failures >= WorkerConstants.BACKUP_FAILURE_ESCALATION_THRESHOLD:
        page_critical(
            f"🚨 [Scheduler] Nightly backup: {failures} consecutive failed runs — "
            f"no durable backup is landing in S3. {detail}"
        )
    else:
        logger.error(f"❌ [Scheduler] Backup failed: {detail}")


@with_scheduler_lock("run_daily_backup", ttl=82000)
async def run_daily_backup():
    """Run automated daily backup via subprocess (PRO-111: mongodump → S3).

    scripts/backup.py exits non-zero when the dump fails OR when --upload-s3
    can't land the archive in S3 (missing bucket / failed upload) — a backup
    that only exists on Railway's ephemeral disk is treated as a failure.
    """
    import asyncio
    import subprocess
    import sys

    script_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "scripts", "backup.py"
    )
    try:
        # to_thread: a real mongodump + S3 upload takes minutes — running it
        # synchronously would stall the worker's event loop (and every other
        # scheduler job / inbound message) for the whole dump.
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, script_path, "--upload-s3"],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode == 0:
            logger.info("✅ [Scheduler] Daily backup completed successfully.")
            try:
                from app.core.redis_client import get_redis_client

                redis = await get_redis_client()
                await redis.delete(BACKUP_FAILURE_COUNT_KEY)
            except Exception as e:
                logger.warning(f"[Scheduler] Backup failure counter reset skipped: {e}")
        else:
            # Tail only — enough to diagnose without dumping a full trace into
            # every log line. loguru sinks write to stdout (app/core/logger.py),
            # so backup.py's own failure reason is on stdout; stderr carries
            # raw tool output (e.g. a mongodump crash).
            # Belt-and-braces (PRO-94): this feeds a CRITICAL that reaches
            # Sentry — scrub every known secret BEFORE truncating, so a secret
            # straddling the 500-char cut can't slip through half-redacted.
            from app.core.logger import redact_secrets

            tail = redact_secrets(result.stderr.strip() or result.stdout.strip())
            await _report_backup_failure(
                f"exit={result.returncode}, output: {tail[-500:]}"
            )
    except Exception as e:
        from app.core.logger import redact_secrets

        await _report_backup_failure(redact_secrets(f"{e}"))


@with_scheduler_lock("run_sos_reporter", ttl=14000)
@track_mongo_auth_failures
async def run_sos_reporter():
    """Wrapper for SOS Admin Reporter with Toggle Check"""
    config = await settings_collection.find_one({"_id": "scheduler_config"})
    if config and not config.get("sos_reporter_active", True):
        return
    await send_periodic_admin_report()


@with_scheduler_lock("run_lead_janitor", ttl=20000)
@track_mongo_auth_failures
async def run_lead_janitor():
    """Auto-reject CONTACTED leads with no pro — gated (business hours + toggle, PRO-73)."""
    if not await _customer_cold_job_allowed("lead_janitor_active"):
        return
    await auto_reject_unassigned_leads()


@with_scheduler_lock("run_sla_monitor", ttl=270)
@track_mongo_auth_failures
async def run_sla_monitor():
    """Silent-handoff deflection — gated (business hours + toggle, PRO-73)."""
    if not await _customer_cold_job_allowed("sla_monitor_active"):
        return
    await check_sla_deflection()


# No @track_mongo_auth_failures here or on the stale-lead nudger: their
# monitor_service callees catch Exception internally, so a Mongo auth failure
# never propagates to the wrapper — the decorator would be dead code implying
# coverage it doesn't provide. The six decorated jobs (incl. the 2-min
# WhatsApp state monitor) trip the threshold on their own.
@with_scheduler_lock("run_pro_approval_sla", ttl=270)
async def run_pro_approval_sla():
    """PRO-56 — nudge silent pros (T+10) + offer the customer a reassignment (T+25)."""
    await check_pro_approval_sla()


# No @track_mongo_auth_failures — see the note above run_pro_approval_sla.
@with_scheduler_lock("run_stale_lead_nudger", ttl=3000)
async def run_stale_lead_nudger():
    """Wrapper for Stale Lead Nudger"""
    await remind_stale_booked_leads()


@with_scheduler_lock("run_whatsapp_state_monitor", ttl=90)
@track_mongo_auth_failures
async def run_whatsapp_state_monitor():
    """PRO-20 — Green API deauth watchdog. Toggle via `whatsapp_monitor_active`.
    Lock TTL is kept under the polling interval so a missed/crashed tick doesn't
    block the next one from running."""
    config = await settings_collection.find_one({"_id": "scheduler_config"})
    if config and not config.get("whatsapp_monitor_active", True):
        return
    await check_whatsapp_instance_state()


@with_scheduler_lock("daily_reminders_job", ttl=82000)
async def daily_reminders_job():
    """
    Daily Reminders Job (Cron).
    Uses atomic 'find_one_and_update' to check availability and lock the run in one step.
    Runs once per day at the scheduled Cron time.
    """
    try:
        # 1. Ensure Config Exists (Upsert)
        await settings_collection.update_one(
            {"_id": "scheduler_config"},
            {
                "$setOnInsert": {
                    "run_time": "08:00",
                    "is_active": True,
                    "last_run_date": None,
                    "stale_monitor_active": True,
                    # PRO-73: the three cold customer-facing jobs default OFF —
                    # enable per-toggle once the WhatsApp number is warmed up.
                    "sos_healer_active": False,
                    "lead_janitor_active": False,
                    "sla_monitor_active": False,
                    "sos_reporter_active": True,
                    "whatsapp_monitor_active": True,
                }
            },
            upsert=True,
        )

        # 2. Scheduled Run (Atomic Check-and-Lock)
        today_str = datetime.now(IL_TZ).strftime("%Y-%m-%d")

        # Only run if active and NOT run today yet
        result = await settings_collection.find_one_and_update(
            {
                "_id": "scheduler_config",
                "is_active": True,
                "last_run_date": {"$ne": today_str},
            },
            {"$set": {"last_run_date": today_str}},
        )

        if result:
            logger.info(f"⏰ [Scheduler] Executing daily reminders for {today_str}.")
            await send_daily_reminders()

    except Exception as e:
        logger.error(f"❌ [Scheduler Error] {e}")


def start_scheduler():
    scheduler = AsyncIOScheduler(timezone=IL_TZ)

    # Job 1: Daily "Good Morning" Reminders (Cron)
    scheduler.add_job(
        daily_reminders_job,
        CronTrigger(hour=8, minute=0, timezone=IL_TZ),
        id="daily_reminders_job",
        replace_existing=True,
    )

    # Job 2: Stale Job Monitor (Wrapped)
    scheduler.add_job(
        monitor_unfinished_jobs,
        IntervalTrigger(minutes=30),
        id="stale_job_monitor",
        replace_existing=True,
    )

    # Job 3: SOS Auto-Healer (Wrapped)
    scheduler.add_job(
        run_sos_healer,
        IntervalTrigger(minutes=10),
        id="sos_auto_healer",
        replace_existing=True,
    )

    # Job 4: SOS Admin Reporter (Wrapped)
    scheduler.add_job(
        run_sos_reporter,
        IntervalTrigger(hours=4),
        id="sos_admin_reporter",
        replace_existing=True,
    )

    # Job 5: Lead Janitor — auto-reject unassigned CONTACTED leads (every 6 hours)
    scheduler.add_job(
        run_lead_janitor,
        IntervalTrigger(hours=6),
        id="lead_janitor",
        replace_existing=True,
    )

    # Job 6: Weekly Slot Regeneration (Sunday 01:00 Israel time)
    scheduler.add_job(
        run_slot_regeneration,
        CronTrigger(day_of_week="sun", hour=1, minute=0, timezone=IL_TZ),
        id="slot_regeneration",
        replace_existing=True,
    )

    # Job 7: Daily Backup (02:00 Israel time)
    scheduler.add_job(
        run_daily_backup,
        CronTrigger(hour=2, minute=0, timezone=IL_TZ),
        id="daily_backup",
        replace_existing=True,
    )

    # Job 8: SLA Deflection Monitor (every 5 minutes)
    scheduler.add_job(
        run_sla_monitor,
        IntervalTrigger(minutes=5),
        id="sla_deflection_monitor",
        replace_existing=True,
    )

    # Job 8b: Pro-Approval SLA — nudge silent pros + reassignment offer (PRO-56)
    scheduler.add_job(
        run_pro_approval_sla,
        IntervalTrigger(minutes=WorkerConstants.APPROVAL_SLA_CHECK_INTERVAL_MINUTES),
        id="pro_approval_sla",
        replace_existing=True,
    )

    # Job 9: Stale Lead Nudger — remind pros to close old active jobs (every 4 hours)
    scheduler.add_job(
        run_stale_lead_nudger,
        IntervalTrigger(hours=4),
        id="stale_lead_nudger",
        replace_existing=True,
    )

    # Job 10: Green API deauth watchdog — page on-call if the WhatsApp instance
    # loses authorization (SPOF). Polls every WA_STATE_CHECK_INTERVAL_MINUTES.
    scheduler.add_job(
        run_whatsapp_state_monitor,
        IntervalTrigger(minutes=WorkerConstants.WA_STATE_CHECK_INTERVAL_MINUTES),
        id="whatsapp_state_monitor",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("🚀 APScheduler Started with all jobs!")
    return scheduler
