from google import genai
from google.genai import types
from app.core.config import settings
from app.core.logger import logger
from app.core.messages import Messages
from pydantic import BaseModel, Field
from typing import Optional
import traceback
import json
import tempfile
import httpx
import os
import asyncio

class ExtractedData(BaseModel):
    city: Optional[str] = Field(description="The extracted city/location from the user's input.")
    issue: Optional[str] = Field(description="The extracted issue or service description.")
    full_address: Optional[str] = Field(description="The full street address if provided.")
    appointment_time: Optional[str] = Field(description="Preferred time for the appointment.")

class AIResponse(BaseModel):
    reply_to_user: str = Field(description="The response message to be sent to the user (in Hebrew).")
    transcription: Optional[str] = Field(description="Full text transcription if the user sent an audio message.")
    extracted_data: ExtractedData = Field(description="Structured data extracted from the conversation.")
    is_deal: bool = Field(default=False, description="Set to True ONLY if the user has provided specific Time AND Address and agreed to book.")

class AIEngine:
    """
    Handles interactions with Google Gemini API.
    Designed to be run within a background worker (ARQ) due to potential latency
    in media processing and LLM generation.
    """
    def __init__(self):
        self.client = genai.Client(api_key=settings.GEMINI_API_KEY)
        # Define the fallback hierarchy from settings
        self.model_hierarchy = settings.AI_MODELS

    async def analyze_conversation(self, history: list, user_text: str, custom_system_prompt: str, media_data: bytes = None, media_mime_type: str = None, require_json: bool = True, media_url: str = None) -> AIResponse | str:
        contents = []
        for msg in history:
            parts = [types.Part(text=p) for p in msg.get("parts", [])]
            contents.append(types.Content(role=msg["role"], parts=parts))
        
        current_parts = []
        
        # Handle media: URL takes precedence for large files (audio/video), bytes for small (images)
        if media_url and media_mime_type and ("audio" in media_mime_type or "video" in media_mime_type):
             # For Audio/Video, we must use the File API to avoid size limits and timeouts
             tmp_path = None
             try:
                 # Download to temp file and upload to Gemini
                 async with httpx.AsyncClient() as client:
                     async with client.stream('GET', media_url) as resp:
                         if resp.status_code == 200:
                             with tempfile.NamedTemporaryFile(delete=False, suffix=f".{media_mime_type.split('/')[-1]}") as tmp:
                                 tmp_path = tmp.name
                                 async for chunk in resp.aiter_bytes():
                                     tmp.write(chunk)

                             # Upload to Gemini
                             uploaded_file = await self.client.aio.files.upload(path=tmp_path, config=types.UploadFileConfig(mime_type=media_mime_type))

                             # Wait for processing if it's a video
                             if "video" in media_mime_type:
                                 while True:
                                     file_status = await self.client.aio.files.get(name=uploaded_file.name)
                                     if file_status.state.name == "ACTIVE":
                                         break
                                     elif file_status.state.name == "FAILED":
                                         raise Exception("Gemini File Processing Failed")
                                     logger.info(f"Waiting for video processing: {file_status.state.name}")
                                     await asyncio.sleep(2)

                             current_parts.append(types.Part.from_uri(file_uri=uploaded_file.uri, mime_type=media_mime_type))

                         else:
                             logger.error(f"Failed to download media for Gemini from {media_url}")
             except Exception as e:
                 logger.error(f"Error handling media URL for Gemini: {e}")
             finally:
                 # Ensure cleanup happens even if upload/processing fails
                 if tmp_path and os.path.exists(tmp_path):
                     os.unlink(tmp_path)

        elif media_data and media_mime_type:
            # Fallback to bytes for images or if URL failed/not provided
            current_parts.append(types.Part.from_bytes(data=media_data, mime_type=media_mime_type))
            
        if media_mime_type:
            if "image" in media_mime_type:
                user_text = (user_text or "") + f"\n{Messages.AISystemPrompts.ANALYZE_IMAGE}"
            elif "audio" in media_mime_type:
                    user_text = (user_text or "") + f"\n{Messages.AISystemPrompts.TRANSCRIBE_AUDIO}"
            elif "video" in media_mime_type:
                user_text = (user_text or "") + f"\n{Messages.AISystemPrompts.ANALYZE_VIDEO}"

        if user_text:
            current_parts.append(types.Part(text=user_text))

        if current_parts:
            contents.append(types.Content(
                role="user",
                parts=current_parts
            ))

        if not custom_system_prompt:
            custom_system_prompt = Messages.AISystemPrompts.DEFAULT_SYSTEM

        config_args = {
            "system_instruction": custom_system_prompt,
            "temperature": 0.3
        }

        if require_json:
            config_args["response_mime_type"] = "application/json"
            config_args["response_schema"] = AIResponse

        last_error = None

        # Iterate through models in hierarchy
        for model_name in self.model_hierarchy:
            try:
                # Use the Async IO (.aio) accessor
                response = await self.client.aio.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=types.GenerateContentConfig(**config_args)
                )
                
                if require_json:
                    try:
                        if hasattr(response, 'parsed') and response.parsed:
                            return response.parsed 
                        else:
                                data = json.loads(response.text)
                                return AIResponse(**data)
                    except Exception as parse_error:
                        logger.error(f"JSON Parse Error with {model_name}: {parse_error} - Text: {response.text}")
                        # If JSON fails, it might be the model's fault, so we might want to continue to next model?
                        # For now, let's treat JSON parse error as a hard failure for this model.
                        continue 

                return response.text.strip()

            except Exception as e:
                logger.warning(f"Model {model_name} failed: {e}. Trying next fallback...")
                last_error = e
                continue
        
        # If all models failed
        logger.error(f"All AI models failed. Last error: {traceback.format_exc()}")
        if require_json:
            return AIResponse(
                    reply_to_user=Messages.Errors.AI_OVERLOAD,
                    transcription=None,
                    extracted_data=ExtractedData(city=None, issue=None, full_address=None, appointment_time=None)
                )
        return Messages.Errors.AI_OVERLOAD
