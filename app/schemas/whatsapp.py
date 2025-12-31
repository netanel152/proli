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

class MessageData(BaseSchema):
    typeMessage: Optional[str] = None
    textMessageData: Optional[TextMessageData] = None
    extendedTextMessageData: Optional[ExtendedTextMessageData] = None
    fileMessageData: Optional[FileMessageData] = None 

class SenderData(BaseSchema):
    chatId: str
    senderName: Optional[str] = "Unknown"

class InstanceData(BaseSchema):
    idInstance: int
    wid: Optional[str] = None
    typeInstance: Optional[str] = None

class WebhookPayload(BaseSchema):
    typeWebhook: str
    instanceData: Optional[InstanceData] = None
    senderData: Optional[SenderData] = None
    messageData: Optional[MessageData] = None