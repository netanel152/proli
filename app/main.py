from fastapi import FastAPI, BackgroundTasks, Request
from app.schemas.whatsapp import WebhookPayload
from app.services.workflow import process_incoming_message, handle_pro_response
from app.core.logger import logger
from app.scheduler import start_scheduler
from contextlib import asynccontextmanager
import uvicorn

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    start_scheduler()
    yield
    # Shutdown

app = FastAPI(title="Fixi Bot Server", lifespan=lifespan)

@app.get("/")
def health_check():
    return {"status": "Fixi is running! 🚀"}

@app.post("/webhook")
async def webhook_endpoint(payload: WebhookPayload, background_tasks: BackgroundTasks):
    """
    Main entry point for Green API Webhooks.
    """
    try:
        # 1. Basic Filters
        if payload.typeWebhook == "incomingMessageReceived":
            sender_data = payload.senderData
            msg_data = payload.messageData
            
            if not sender_data or not msg_data:
                return {"status": "ignored_no_data"}
            
            chat_id = sender_data.chatId
            
            # Group Filter
            if chat_id.endswith("@g.us"):
                return {"status": "ignored_group"}

            # Extract User Text
            user_text = ""
            media_url = None
            
            if msg_data.typeMessage == "textMessage":
                user_text = msg_data.textMessageData.textMessage
            elif msg_data.typeMessage == "extendedTextMessage":
                user_text = msg_data.extendedTextMessageData.text
            elif msg_data.typeMessage in ["imageMessage", "audioMessage"]:
                # Basic media handling if needed, existing logic had it
                if msg_data.fileMessageData:
                    media_url = msg_data.fileMessageData.downloadUrl
                    user_text = msg_data.fileMessageData.caption or ""

            # Check if it's a Button Response (Green API treats this as a specific type or subtype)
            # In some Green API versions, button response comes as incomingMessageReceived with typeMessage='buttonsResponseMessage'
            if msg_data.typeMessage == "buttonsResponseMessage":
                # Convert Pydantic model to dict for workflow handler or pass necessary data
                # We need to construct a dict-like payload for handle_pro_response or change its signature
                # Let's re-use the Pydantic model dump or pass specific args.
                # 'handle_pro_response' expects a dict in my implementation above.
                background_tasks.add_task(handle_pro_response, payload.model_dump())
                return {"status": "processing_button"}

            # Process Standard Message
            background_tasks.add_task(process_incoming_message, chat_id, user_text, media_url)
            return {"status": "processing_message"}

        elif payload.typeWebhook == "incomingBlock": 
            # Sometimes button clicks come here depending on API config
            # But usually 'incomingMessageReceived' with 'buttonsResponseMessage' type.
            # We'll leave this for safety.
            pass

        return {"status": "ignored_type"}

    except Exception as e:
        logger.error(f"Webhook Error: {e}")
        return {"status": "error"}

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
