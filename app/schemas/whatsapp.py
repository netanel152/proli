from pydantic import BaseModel
from typing import Optional, Any

class TextMessageData(BaseModel):
    textMessage: str

class MessageData(BaseModel):
    textMessageData: Optional[TextMessageData] = None

class SenderData(BaseModel):
    chatId: str
    senderName: Optional[str] = "Unknown"

class WebhookPayload(BaseModel):
    typeWebhook: str
    senderData: Optional[SenderData] = None
    messageData: Optional[MessageData] = None
    
    class Config:
        extra = "ignore"
