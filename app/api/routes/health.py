from datetime import datetime, timedelta, timezone

import hmac

from fastapi import APIRouter, Request, Response, status

from app.core.config import settings
from app.core.constants import LeadStatus, WorkerConstants
from app.core.database import check_db_connection, leads_collection
from app.core.logger import logger
from app.core.redis_client import get_redis_client
from app.providers.whatsapp import get_whatsapp
import time

router = APIRouter(prefix="/health", tags=["Health"])

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
    if _detail_authorized(request):
        body["checks"] = checks
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
