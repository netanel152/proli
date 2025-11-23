from pydantic import BaseModel
from typing import Optional, Any

class TextMessageData(BaseModel):
    textMessage: str

# --- תמיכה בהודעות מורחבות (כמו Reply או לינקים) ---
class ExtendedTextMessageData(BaseModel):
    text: Optional[str] = None
    description: Optional[str] = None
    previewType: Optional[str] = None

class MessageData(BaseModel):
    textMessageData: Optional[TextMessageData] = None
    extendedTextMessageData: Optional[ExtendedTextMessageData] = None

class SenderData(BaseModel):
    chatId: str
    senderName: Optional[str] = "Unknown"

class WebhookPayload(BaseModel):
    typeWebhook: str
    senderData: Optional[SenderData] = None
    messageData: Optional[MessageData] = None
    
    class Config:
        extra = "ignore"