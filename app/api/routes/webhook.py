from fastapi import APIRouter
from app.schemas.whatsapp import WebhookPayload
from app.core.logger import logger
from app.core.config import settings
from app.core.constants import APIStatus
from app.core.redis_client import get_redis_client, get_arq_pool

router = APIRouter()

@router.post("/webhook")
async def webhook_endpoint(payload: WebhookPayload):
    """
    Main entry point for Green API Webhooks.
    """
    try:
        # 0. Idempotency Check (Redis)
        if payload.idMessage:
            redis = await get_redis_client()
            cache_key = f"webhook:{payload.idMessage}"
            
            # Atomic set-if-not-exists with 24h TTL
            is_new = await redis.set(cache_key, "processed", ex=86400, nx=True)
            
            if not is_new:
                logger.info(f"Idempotency: Skipping duplicate message {payload.idMessage}")
                return {"status": APIStatus.PROCESSING, "detail": "duplicate"}
        
        # 1. Security Verification
        # Ensure the webhook comes from OUR Green API instance
        if payload.instanceData:
            if str(payload.instanceData.idInstance) != str(settings.GREEN_API_ID):
                logger.warning(f"Security Alert: Blocked webhook from unknown instance: {payload.instanceData.idInstance}")
                return {"status": APIStatus.IGNORED_WRONG_INSTANCE}
        
        # 2. Basic Filters
        if payload.typeWebhook == "incomingMessageReceived":
            sender_data = payload.senderData
            msg_data = payload.messageData
            
            if not sender_data or not msg_data:
                return {"status": APIStatus.IGNORED_NO_DATA}
            
            chat_id = sender_data.chatId
            
            # Group Filter
            if chat_id.endswith("@g.us"):
                return {"status": APIStatus.IGNORED_GROUP}

            # Extract User Text
            user_text = ""
            media_url = None
            
            if msg_data.typeMessage == "textMessage":
                user_text = msg_data.textMessageData.textMessage
            elif msg_data.typeMessage == "extendedTextMessage":
                user_text = msg_data.extendedTextMessageData.text
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
