from fastapi import FastAPI, BackgroundTasks
from app.schemas.whatsapp import WebhookPayload
from app.services.logic import ask_fixi_ai, send_whatsapp
from app.scheduler import start_scheduler
from contextlib import asynccontextmanager
from rich.console import Console
from app.core.logger import logger
import traceback

console = Console()

@asynccontextmanager
async def lifespan(app: FastAPI):
    start_scheduler()
    yield

app = FastAPI(title="Fixi Bot Server", lifespan=lifespan)

@app.get("/")
def health_check():
    return {"status": "Fixi is running! 🚀"}

@app.post("/webhook")
async def handle_incoming_message(payload: WebhookPayload, background_tasks: BackgroundTasks):
    # 1. סינון ראשוני: רק הודעות נכנסות
    if payload.typeWebhook != "incomingMessageReceived":
        return {"status": "ignored"}
    
    if not payload.senderData:
        return {"status": "ignored_no_sender"}
    
    # שליפת Chat ID
    chat_id = payload.senderData.chatId
    
    # 2. סינון קבוצות (הכי חשוב!)
    # קבוצות בוואטסאפ מסתיימות ב- @g.us
    if chat_id.endswith("@g.us"):
        return {"status": "ignored_group"}

    user_text = None
    media_url = None
    msg = payload.messageData

    if not msg: return {"status": "no_data"}

    # 3. זיהוי סוג ההודעה
    msg_type = msg.typeMessage
    
    if msg_type == "textMessage" and msg.textMessageData:
        user_text = msg.textMessageData.textMessage
        
    elif msg_type == "extendedTextMessage" and msg.extendedTextMessageData:
        user_text = msg.extendedTextMessageData.text
        
    elif msg_type == "imageMessage" and msg.fileMessageData:
        media_url = msg.fileMessageData.downloadUrl
        user_text = msg.fileMessageData.caption 
        console.print("[magenta]📷 Image Received![/magenta]")
        
    elif msg_type == "audioMessage" and msg.fileMessageData:
        media_url = msg.fileMessageData.downloadUrl
        console.print("[magenta]🎤 Voice Note Received![/magenta]")

    if not user_text and not media_url:
        console.print(f"[yellow]⚠️ Received unknown message type: {msg_type}[/yellow]")
        return {"status": "unknown_type"}
        
    log_text = user_text or "[Media File]"
    console.print(f"[bold blue]📨 New Message from {chat_id}:[/bold blue] {log_text}")

    # 4. שליחה ללוגיקה
    background_tasks.add_task(process_message, chat_id, user_text, media_url)
    return {"status": "processing"}

async def process_message(chat_id: str, user_text: str, media_url: str = None):
    # כאן הבוט עונה
    try:
        logger.info(f"Processing message for {chat_id}")
        ai_reply = await ask_fixi_ai(user_text, chat_id, media_url)
        await send_whatsapp(chat_id, ai_reply)
        logger.info(f"Message sent to {chat_id}")
    except Exception as e:
        logger.error(f"Error in process_message: {e}")
        logger.error(traceback.format_exc())