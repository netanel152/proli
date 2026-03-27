from fastapi import APIRouter, Response, status
from app.core.database import check_db_connection
from app.core.redis_client import get_redis_client
from app.services.whatsapp_client_service import WhatsAppClient
from app.core.logger import logger
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
        wa_resp = await whatsapp._send_request("getStateInstance", {})
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
