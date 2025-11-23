from fastapi import FastAPI, BackgroundTasks
from app.schemas.whatsapp import WebhookPayload
from app.services.logic import ask_fixi_ai, send_whatsapp
from rich.console import Console

app = FastAPI(title="Fixi Bot Server")
console = Console()

@app.get("/")
def health_check():
    return {"status": "Fixi is running! 🚀"}

@app.post("/webhook")
async def handle_incoming_message(payload: WebhookPayload, background_tasks: BackgroundTasks):
    """Main webhook handler"""
    
    # 1. Filter: מטפלים רק בהודעות נכנסות (מלקוחות) או יוצאות (לבדיקות עצמיות)
    if payload.typeWebhook not in ["incomingMessageReceived", "outgoingMessageReceived"]:
        # נדפיס לוג אפור כדי שתדע שהשרת חי אבל מסנן רעשים
        # console.print(f"[dim]💤 Ignored event: {payload.typeWebhook}[/dim]")
        return {"status": "ignored"}
    
    # 2. חילוץ טקסט חכם (Smart Extraction)
    user_text = None
    
    # בדיקה א': האם זה טקסט רגיל?
    if payload.messageData and payload.messageData.textMessageData:
        user_text = payload.messageData.textMessageData.textMessage
        
    # בדיקה ב': האם זה טקסט מורחב? (התיקון לבעיית ה"פעמיים")
    elif payload.messageData and payload.messageData.extendedTextMessageData:
        user_text = payload.messageData.extendedTextMessageData.text

    # אם עדיין אין טקסט - נדלג
    if not user_text:
        console.print("[yellow]⚠️  Received message but no text found (Photo? Sticker?)[/yellow]")
        return {"status": "no_text"}
        
    chat_id = payload.senderData.chatId
    
    # לוג ברור של ההודעה שהתקבלה
    console.print(f"[bold blue]📨 New Message from {chat_id}:[/bold blue] {user_text}")

    # 3. שליחה לעיבוד ברקע
    background_tasks.add_task(process_message, chat_id, user_text)
    
    return {"status": "processing"}

async def process_message(chat_id: str, user_text: str):
    """Full processing flow"""
    # שים לב: הוספנו פה את chat_id לקריאה ל-AI כדי שהזיכרון יעבוד
    ai_reply = await ask_fixi_ai(user_text, chat_id)
    await send_whatsapp(chat_id, ai_reply)