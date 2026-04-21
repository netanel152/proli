from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Response, status

from app.core.config import settings
from app.core.constants import LeadStatus, WorkerConstants
from app.core.database import check_db_connection, leads_collection
from app.core.logger import logger
from app.core.redis_client import get_redis_client
from app.services.whatsapp_client_service import WhatsAppClient
import time

router = APIRouter(prefix="/health", tags=["Health"])

_start_time = time.time()


@router.get("")
async def health_check(response: Response):
    """
    Checks the health of all external dependencies with latency measurements.
    """
    whatsapp = WhatsAppClient()

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

    # WhatsApp Check
    whatsapp_status = "down"
    try:
        client = await whatsapp._get_client()
        url = f"{whatsapp.api_url}/getStateInstance/{whatsapp.api_token}"
        resp = await client.get(url)
        resp.raise_for_status()
        wa_resp = resp.json()
        if wa_resp.get("stateInstance"):
            whatsapp_status = "up"
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
        },
    }

    uptime_seconds = round(time.time() - _start_time)

    if not is_critical_up:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unhealthy", "checks": checks, "uptime_seconds": uptime_seconds}

    return {"status": "healthy", "checks": checks, "uptime_seconds": uptime_seconds}


@router.get("/leads")
async def leads_health(response: Response):
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
    try:
        now = datetime.now(timezone.utc)
        stuck_threshold = now - timedelta(hours=WorkerConstants.UNASSIGNED_LEAD_TIMEOUT_HOURS)

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
        logger.error(f"Health Check: /health/leads failed: {e}")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "error", "error": str(e)}
