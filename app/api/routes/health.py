from datetime import datetime, timedelta, timezone

import hmac
import os

from fastapi import APIRouter, Request, Response, status
import math

from app.core.config import settings
from app.core.constants import BACKUP_LAST_SUCCESS_KEY, LeadStatus, WorkerConstants
from app.core.database import check_db_connection, leads_collection
from app.core.logger import logger
from app.core.redis_client import get_redis_client
from app.providers.whatsapp import get_whatsapp
import time

router = APIRouter(prefix="/health", tags=["Health"])


async def _durable_backup_ts() -> float | None:
    """PRO-185: last successful backup from the Mongo mirror
    (`settings.backup_state.last_success`), as a unix timestamp; None when
    absent or unreachable. Resolved at call time so a test that patches
    `app.core.database.settings_collection` is honoured."""
    from app.core import database as _db

    try:
        doc = await _db.settings_collection.find_one(
            {"_id": "backup_state"}, {"last_success": 1}
        )
    except Exception as e:
        logger.warning(f"Health Check: backup durable record unreadable: {e}")
        return None
    value = (doc or {}).get("last_success")
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    try:
        ts = value.timestamp()
    except (OverflowError, OSError, ValueError):
        return None
    # Same rule as the watchdog's helper: pre-1970 or future beyond skew
    # tolerance is a hand-written value, not a timestamp.
    if (
        ts <= 0
        or ts > time.time() + WorkerConstants.BACKUP_CLOCK_SKEW_TOLERANCE_SECONDS
    ):
        return None
    return ts


_start_time = time.time()


def _detail_authorized(request: Request) -> bool:
    """PRO-136 — may this caller see internals (checks, latencies, KPIs)?

    * ``HEALTH_TOKEN`` set → only a matching ``X-Health-Token`` header
      (constant-time compare, same posture as the webhook auth).
    * ``HEALTH_TOKEN`` unset → fail closed in staging/production (nothing can
      authenticate, so nothing is shown), open in development so local
      ``curl``/the /health command keep working without ceremony.
    """
    token = settings.HEALTH_TOKEN
    if token is None:
        return not settings.is_prod_like
    header = request.headers.get("X-Health-Token") or ""
    return hmac.compare_digest(
        header.encode("latin-1", "replace"), token.get_secret_value().encode()
    )


@router.get("")
async def health_check(request: Request, response: Response):
    """
    Checks the health of all external dependencies with latency measurements.

    PRO-136: the unauthenticated response carries only ``status`` and
    ``uptime_seconds`` — exactly what the Docker HEALTHCHECK and the
    promotion workflow's deploy verifier read. The per-dependency ``checks``
    object (latencies, provider name, transmits flag, worker heartbeat) is
    included only for callers `_detail_authorized` accepts: it is an
    infrastructure map, not a liveness signal.
    """
    whatsapp = get_whatsapp()
    # Decided once, up front: it also gates work whose result an
    # unauthenticated caller would never see (the PRO-185 mirror lookup).
    detail_ok = _detail_authorized(request)

    # MongoDB Check
    mongo_up = False
    mongo_latency = None
    t0 = time.time()
    try:
        mongo_up = check_db_connection()
        mongo_latency = round((time.time() - t0) * 1000, 1)
    except Exception as e:
        logger.error(f"Health Check: MongoDB failed: {e}")

    # Redis Check
    redis_up = False
    redis_latency = None
    worker_status = "unknown"
    worker_heartbeat = None
    # PRO-185: backup freshness. "unknown" until Redis answers — a Redis
    # outage must not read as either fresh or stale.
    backup_status = "unknown"
    backup_last_success = None
    backup_age_hours = None
    try:
        redis = await get_redis_client()
        t0 = time.time()
        await redis.ping()
        redis_latency = round((time.time() - t0) * 1000, 1)
        redis_up = True

        # Check worker heartbeat
        hb = await redis.get("worker:heartbeat")
        if hb:
            worker_heartbeat = hb.decode() if isinstance(hb, bytes) else str(hb)
            # Worker is "up" if heartbeat is within last 120 seconds
            hb_age = time.time() - float(worker_heartbeat)
            worker_status = "up" if hb_age < 120 else "stale"
        else:
            worker_status = "no_heartbeat"

        # PRO-185: when did a backup last land? This is the half of the
        # freshness watchdog that an in-app job cannot provide — a caller
        # reading /health from outside sees a dead worker, a misconfigured
        # environment that never registered the job, or a stale lock, all
        # of which are invisible from inside the process. Redis first, then
        # the durable Mongo mirror the backup job writes alongside it — a
        # no-TTL key is still an eviction/restart casualty, so absence in
        # Redis is not evidence of "never", and a present-but-stale key can
        # lag a fresher mirror (the two writes are independent). Absent in
        # both → "never".
        #
        # The mirror lookup is a Mongo round-trip on the *public* liveness
        # path if done unconditionally — the Docker HEALTHCHECK and the
        # PRO-155 staging poller would pay for a field they never see. So it
        # runs only for a caller that will actually be shown `checks`.
        raw_backup = await redis.get(BACKUP_LAST_SUCCESS_KEY)
        last_ts = None
        mirror_consulted = False
        if raw_backup is not None:
            try:
                last_ts = float(
                    raw_backup.decode()
                    if isinstance(raw_backup, bytes)
                    else str(raw_backup)
                )
                # Same rule as scheduler._parse_unix_ts: only a finite,
                # positive value is a timestamp. Without this, "0" or "-5"
                # would report "stale" since 1970 here and ERROR there.
                if not math.isfinite(last_ts) or last_ts <= 0:
                    raise ValueError("not a usable timestamp")
            except ValueError:
                last_ts = None
                backup_status = "unknown"
        elif detail_ok:
            last_ts = await _durable_backup_ts()
            mirror_consulted = True
            if last_ts is None:
                backup_status = "never"
        else:
            backup_status = "never"
        if last_ts is not None:
            try:
                now = time.time()
                if (
                    detail_ok
                    and not mirror_consulted
                    and (now - last_ts) / 3600 > WorkerConstants.BACKUP_MAX_AGE_HOURS
                ):
                    # Redis says stale — a success recorded in either store
                    # is a success, so let a fresher mirror win.
                    durable = await _durable_backup_ts()
                    if durable is not None and durable > last_ts:
                        last_ts = durable
                age_hours = (now - last_ts) / 3600
                if last_ts > now + WorkerConstants.BACKUP_CLOCK_SKEW_TOLERANCE_SECONDS:
                    # A timestamp in the future beyond clock-skew tolerance
                    # (a manual write) would read "fresh" forever. Unknown is
                    # the honest answer.
                    raise ValueError("last_success is in the future")
                backup_last_success = datetime.fromtimestamp(
                    last_ts, tz=timezone.utc
                ).isoformat()
                backup_age_hours = round(age_hours, 1)
                backup_status = (
                    "fresh"
                    if age_hours <= WorkerConstants.BACKUP_MAX_AGE_HOURS
                    else "stale"
                )
            except (ValueError, OverflowError, OSError):
                backup_status = "unknown"
    except Exception as e:
        logger.error(f"Health Check: Redis failed: {e}")

    # WhatsApp Check — must compare to "authorized", not just truthiness:
    # "yellowCard" and "blocked" are truthy strings but mean the account is
    # filtered/blocked, so a truthiness check reports green while outbound is dead.
    whatsapp_status = "down"
    whatsapp_state = None
    try:
        # PRO-86: was a hand-rolled legacy-vendor call that reached into the client's
        # private httpx handle and rebuilt the URL from its token — a second
        # egress in everything but name. The facade owns the probe now.
        whatsapp_state = await whatsapp.get_state_instance()
        if not whatsapp.provider.transmits:
            # A non-transmitting provider always reports "authorized" — it cannot
            # be deauthorized. Reporting that as "up" would make a production
            # deployment that forgot WHATSAPP_PROVIDER a silent black hole with a
            # green dashboard, so surface it as degraded instead.
            whatsapp_status = "degraded"
        elif whatsapp_state == "authorized":
            whatsapp_status = "up"
        elif whatsapp_state == "yellowCard":
            # Instance alive but WhatsApp is silently filtering outbound — degraded.
            whatsapp_status = "degraded"
        else:
            # notAuthorized / blocked / starting / None → down.
            whatsapp_status = "down"
    except Exception as e:
        logger.warning(f"Health Check: WhatsApp check failed: {e}")

    # Aggregated Status
    is_critical_up = mongo_up and redis_up

    checks = {
        "mongodb": {
            "status": "up" if mongo_up else "down",
            "latency_ms": mongo_latency,
        },
        "redis": {
            "status": "up" if redis_up else "down",
            "latency_ms": redis_latency,
        },
        "worker": {
            "status": worker_status,
            "last_heartbeat": worker_heartbeat,
        },
        # PRO-185. `enabled` tells a synthetic monitor (PRO-17) whether
        # "never" is an alarm or just staging, where backups are not
        # scheduled at all (PRO-127). Freshness is deliberately NOT folded
        # into the top-level `status`: a stale backup is an operator page,
        # not a liveness failure, and the Docker HEALTHCHECK must not
        # restart a healthy API over it.
        "backup": {
            "status": backup_status,
            "last_success": backup_last_success,
            "age_hours": backup_age_hours,
            "max_age_hours": WorkerConstants.BACKUP_MAX_AGE_HOURS,
            "enabled": settings.is_production,
            # Railway's own environment name — the one value an operator
            # cannot mistype. If this says production while `enabled` is
            # false, the app is misconfigured (the PRO-96 shape) and "never"
            # IS the alarm. PRO-96's boot cross-check closes most of that,
            # but is exempt when the platform variable is absent.
            "platform_environment": os.environ.get("RAILWAY_ENVIRONMENT_NAME"),
        },
        "whatsapp": {
            "status": whatsapp_status,
            "state": whatsapp_state,
            # PRO-86: which transport is actually wired up. Without this, a
            # dry-run deployment and a live one are indistinguishable on /health.
            "provider": whatsapp.provider.name,
            "transmits": whatsapp.provider.transmits,
        },
    }

    uptime_seconds = round(time.time() - _start_time)

    body = {
        "status": "healthy" if is_critical_up else "unhealthy",
        "uptime_seconds": uptime_seconds,
    }
    if detail_ok:
        body["checks"] = checks
        # PRO-155: the commit this container was built from (injected by
        # Railway; None elsewhere). The verify-staging-deploy workflow compares
        # it against the pushed SHA so a silently dead deploy trigger — the
        # PRO-128/PRO-155 failure mode — turns into a red X on the merge.
        body["commit"] = os.environ.get("RAILWAY_GIT_COMMIT_SHA")
    if not is_critical_up:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return body


@router.get("/leads")
async def leads_health(request: Request, response: Response):
    """
    Business-level health signal for the lead pipeline.

    Two counters that together catch the failure modes the 2026-04-18 post-
    mortem surfaced:

      * `pending_review_count` — leads escalated to PENDING_ADMIN_REVIEW and
        waiting on a human. A small non-zero number is normal; a growing
        backlog means the admin panel isn't being worked or the Healer is
        looping (the very bug the 2026-04-18 patches fixed).

      * `stuck_contacted_count` — leads in CONTACTED older than
        UNASSIGNED_LEAD_TIMEOUT_HOURS (24h). The SOS Healer is supposed to
        reassign or escalate these on its 10-minute tick. If this number
        climbs, the Healer is silently failing.

    Intended as the source-of-truth for the Sentry alert
    `pending_review_count > 5 for > 30 min` (wire a synthetic monitor —
    Better Uptime / Cronitor / Sentry Crons — to poll this endpoint and
    alert on threshold breach).

    Always returns 200 on success; surfacing the counts alone is the
    contract. A DB failure returns 503 so monitors can distinguish "DB is
    down" from "backlog is high but DB is fine."
    """
    if not _detail_authorized(request):
        # PRO-136: business KPIs (admin backlog, stuck-lead counts) are not a
        # public liveness signal. Wire the external poller with the
        # X-Health-Token header.
        response.status_code = status.HTTP_403_FORBIDDEN
        return {"status": "forbidden"}

    try:
        now = datetime.now(timezone.utc)
        stuck_threshold = now - timedelta(
            hours=WorkerConstants.UNASSIGNED_LEAD_TIMEOUT_HOURS
        )

        pending_review_count = await leads_collection.count_documents(
            {"status": LeadStatus.PENDING_ADMIN_REVIEW}
        )
        stuck_contacted_count = await leads_collection.count_documents(
            {
                "status": LeadStatus.CONTACTED,
                "created_at": {"$lt": stuck_threshold},
            }
        )

        return {
            "status": "ok",
            "pending_review_count": pending_review_count,
            "stuck_contacted_count": stuck_contacted_count,
            "stuck_threshold_hours": WorkerConstants.UNASSIGNED_LEAD_TIMEOUT_HOURS,
            "environment": settings.ENVIRONMENT,
            "checked_at": now.isoformat(),
        }
    except Exception as e:
        # PRO-136: the raw exception (which can carry Mongo connection
        # details) goes to logs/Sentry only — the HTTP body stays fixed.
        logger.error(f"Health Check: /health/leads failed: {e}")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "error", "error": "internal error"}
