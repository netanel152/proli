from fastapi import APIRouter, BackgroundTasks
from app.schemas.whatsapp import WebhookPayload
from app.services.workflow import process_incoming_message
from app.core.logger import logger
from app.core.config import settings
from app.core.constants import APIStatus

router = APIRouter()

@router.post("/webhook")
async def webhook_endpoint(payload: WebhookPayload, background_tasks: BackgroundTasks):
    """
    Main entry point for Green API Webhooks.
    """
    try:
        # 0. Security Verification
        # Ensure the webhook comes from OUR Green API instance
        if payload.instanceData:
            # incoming idInstance is int, settings is usually str
            if str(payload.instanceData.idInstance) != str(settings.GREEN_API_ID):
                logger.warning(f"Security Alert: Blocked webhook from unknown instance: {payload.instanceData.idInstance}")
                return {"status": APIStatus.IGNORED_WRONG_INSTANCE}
        
        # 1. Basic Filters
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
                # Basic media handling if needed, existing logic had it
                if msg_data.fileMessageData:
                    media_url = msg_data.fileMessageData.downloadUrl
                    user_text = msg_data.fileMessageData.caption or ""

            # Process Standard Message
            background_tasks.add_task(process_incoming_message, chat_id, user_text, media_url)
            return {"status": APIStatus.PROCESSING}

        elif payload.typeWebhook == "incomingBlock": 
            # Handle blocked users if needed
            pass

        return {"status": APIStatus.IGNORED_TYPE}

    except Exception as e:
        logger.error(f"Webhook Error: {e}")
        return {"status": APIStatus.ERROR}
