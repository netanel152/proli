from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from app.schemas.whatsapp import WebhookPayload
from app.core.logger import logger
from app.core.config import settings
from app.core.constants import APIStatus
from app.core.redis_client import get_redis_client, get_arq_pool
from app.services.security_service import SecurityService

router = APIRouter()

@router.post("/webhook")
async def webhook_endpoint(payload: WebhookPayload, token: str = Query(default=None)):
    """
    Main entry point for Green API Webhooks.
    """
    # Webhook Token Verification
    if settings.WEBHOOK_TOKEN:
        if token != settings.WEBHOOK_TOKEN:
            logger.warning("Security Alert: Webhook request with invalid or missing token")
            return JSONResponse(status_code=403, content={"status": "forbidden"})

    try:
        # Idempotency Check (Redis)
        if payload.idMessage:
            redis = await get_redis_client()
            cache_key = f"webhook:{payload.idMessage}"
            
            # Atomic set-if-not-exists with 24h TTL
            is_new = await redis.set(cache_key, "processed", ex=86400, nx=True)
            
            if not is_new:
                logger.info(f"Idempotency: Skipping duplicate message {payload.idMessage}")
                return {"status": APIStatus.PROCESSING, "detail": "duplicate"}
        
        # Security Verification
        # Ensure the webhook comes from OUR Green API instance
        if payload.instanceData:
            if str(payload.instanceData.idInstance) != str(settings.GREEN_API_INSTANCE_ID):
                logger.warning(f"Security Alert: Blocked webhook from unknown instance: {payload.instanceData.idInstance}")
                return {"status": APIStatus.IGNORED_WRONG_INSTANCE}
        
        # Basic Filters
        if payload.typeWebhook == "incomingMessageReceived":
            sender_data = payload.senderData
            msg_data = payload.messageData
            
            if not sender_data or not msg_data:
                return {"status": APIStatus.IGNORED_NO_DATA}
            
            chat_id = sender_data.chatId
            
            # Group Filter
            if chat_id.endswith("@g.us"):
                return {"status": APIStatus.IGNORED_GROUP}

            # Rate Limit Check
            if not await SecurityService.check_rate_limit(chat_id):
                logger.warning(f"⛔ Rate limit exceeded for {chat_id}")
                return {"status": APIStatus.IGNORED_RATE_LIMIT}

            # Extract User Text
            user_text = ""
            media_url = None

            if msg_data.typeMessage == "textMessage":
                user_text = msg_data.textMessageData.textMessage
            elif msg_data.typeMessage == "extendedTextMessage":
                user_text = msg_data.extendedTextMessageData.text or ""
            elif msg_data.typeMessage == "locationMessage":
                # Handle location pin messages
                if msg_data.locationMessageData:
                    loc = msg_data.locationMessageData
                    parts = []
                    if loc.nameLocation:
                        parts.append(loc.nameLocation)
                    if loc.address:
                        parts.append(loc.address)
                    if loc.latitude and loc.longitude:
                        parts.append(f"({loc.latitude}, {loc.longitude})")
                    user_text = " ".join(parts) if parts else f"מיקום: {loc.latitude}, {loc.longitude}"
                    logger.info(f"Location message from {chat_id}: {user_text}")
            elif msg_data.typeMessage in ["imageMessage", "audioMessage", "videoMessage"]:
                if msg_data.fileMessageData:
                    media_url = msg_data.fileMessageData.downloadUrl
                    user_text = msg_data.fileMessageData.caption or ""

            # Process Standard Message via ARQ Worker
            arq_pool = await get_arq_pool()
            await arq_pool.enqueue_job(
                'process_message_task', 
                chat_id, 
                user_text, 
                media_url
            )
            return {"status": APIStatus.PROCESSING}

        elif payload.typeWebhook == "incomingBlock": 
            pass

        return {"status": APIStatus.IGNORED_TYPE}

    except Exception as e:
        logger.error(f"Webhook Error: {e}")
        return {"status": APIStatus.ERROR}
