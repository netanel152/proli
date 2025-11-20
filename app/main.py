from fastapi import FastAPI, BackgroundTasks
from app.schemas.whatsapp import WebhookPayload
from app.services.logic import ask_fixi_ai, send_whatsapp

app = FastAPI(title="Fixi Bot Server")

@app.get("/")
def health_check():
    return {"status": "Fixi is running! 🚀"}

@app.post("/webhook")
async def handle_incoming_message(payload: WebhookPayload, background_tasks: BackgroundTasks):
    """המוח המרכזי שמקבל את ההודעות"""
    
    # --- הדיבוג החדש: נראה בדיוק מה קיבלנו ---
    print(f"🔍 RAW DATA TYPE: {payload.typeWebhook}")
    print(f"🔍 FULL PAYLOAD: {payload.model_dump()}")
    # ----------------------------------------

    # 1. סינון: מעניין אותנו רק הודעות נכנסות
    if payload.typeWebhook not in ["incomingMessageReceived", "outgoingMessageReceived"]:
        print("⚠️ Skipping: Not an incoming message.")
        return {"status": "ignored"}
    
    # 2. חילוץ המידע בבטחה
    if not payload.messageData or not payload.messageData.textMessageData:
        print("⚠️ Skipping: No text data found in message.")
        return {"status": "no_text"}
        
    user_text = payload.messageData.textMessageData.textMessage
    chat_id = payload.senderData.chatId
    
    print(f"📨 הודעה חדשה מ-{chat_id}: {user_text}")

    # 3. שליחה לעיבוד ברקע
    background_tasks.add_task(process_message, chat_id, user_text)
    
    return {"status": "processing"}

async def process_message(chat_id: str, user_text: str):
    """תהליך העיבוד המלא"""
    ai_reply = await ask_fixi_ai(user_text)
    await send_whatsapp(chat_id, ai_reply)