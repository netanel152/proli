from pydantic import BaseModel, ConfigDict
from typing import Optional, Any

class BaseSchema(BaseModel):
    model_config = ConfigDict(extra='ignore')

class TextMessageData(BaseSchema):
    textMessage: str

class ExtendedTextMessageData(BaseSchema):
    text: Optional[str] = None
    description: Optional[str] = None
    previewType: Optional[str] = None

class FileMessageData(BaseSchema):
    downloadUrl: str
    caption: Optional[str] = None
    mimeType: str
    fileName: Optional[str] = None

class LocationMessageData(BaseSchema):
    """Green API location message data."""
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    nameLocation: Optional[str] = None
    address: Optional[str] = None

class QuotedMessageData(BaseSchema):
    """Quoted/reply message data."""
    stanzaId: Optional[str] = None
    participant: Optional[str] = None

class MessageData(BaseSchema):
    typeMessage: Optional[str] = None
    textMessageData: Optional[TextMessageData] = None
    extendedTextMessageData: Optional[ExtendedTextMessageData] = None
    fileMessageData: Optional[FileMessageData] = None
    locationMessageData: Optional[LocationMessageData] = None
    quotedMessage: Optional[QuotedMessageData] = None

class SenderData(BaseSchema):
    chatId: str
    senderName: Optional[str] = "Unknown"

class InstanceData(BaseSchema):
    idInstance: int
    wid: Optional[str] = None
    typeInstance: Optional[str] = None

class WebhookPayload(BaseSchema):
    typeWebhook: str
    idMessage: Optional[str] = None
    instanceData: Optional[InstanceData] = None
    senderData: Optional[SenderData] = None
    messageData: Optional[MessageData] = None