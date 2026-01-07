from fastapi import APIRouter, Response, status
from app.core.database import check_db_connection
from app.core.redis_client import get_redis_client
from app.services.whatsapp_client_service import WhatsAppClient
from app.core.logger import logger
from typing import Dict

router = APIRouter(prefix="/health", tags=["Health"])

@router.get("")
async def health_check(response: Response):
    """
    Checks the health of all external dependencies.
    """
    whatsapp = WhatsAppClient()
    
    # MongoDB Check
    mongo_up = check_db_connection()
    
    # Redis Check
    redis_up = False
    try:
        redis = await get_redis_client()
        await redis.ping()
        redis_up = True
    except Exception as e:
        logger.error(f"Health Check: Redis failed: {e}")

    # WhatsApp Check
    whatsapp_status = "down"
    try:
        # We'll use the _send_request to check instance state
        wa_resp = await whatsapp._send_request("getStateInstance", {})
        # Expected: {"stateInstance": "notAuthorized" | "authorized" | ...}
        if wa_resp.get("stateInstance"):
             whatsapp_status = "up"
    except Exception as e:
        logger.warning(f"Health Check: WhatsApp check failed: {e}")

    # Aggregated Status
    # Critical components are Mongo and Redis
    is_critical_up = mongo_up and redis_up
    
    components = {
        "mongo": "up" if mongo_up else "down",
        "redis": "up" if redis_up else "down",
        "whatsapp": whatsapp_status
    }

    if not is_critical_up:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unhealthy", "components": components}

    return {"status": "ok", "components": components}
