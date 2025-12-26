from google import genai
from google.genai import types
from app.core.config import settings
from app.core.logger import logger
from pydantic import BaseModel, Field
from typing import Optional
import traceback
import json

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
    def __init__(self):
        self.client = genai.Client(api_key=settings.GEMINI_API_KEY)
        self.model_name = "gemini-2.5-flash-lite"

    async def analyze_conversation(self, history: list, user_text: str, custom_system_prompt: str, media_data: bytes = None, media_mime_type: str = None, require_json: bool = True) -> AIResponse | str:
        try:
            contents = []
            for msg in history:
                parts = [types.Part(text=p) for p in msg.get("parts", [])]
                contents.append(types.Content(role=msg["role"], parts=parts))
            
            current_parts = []
            
            if media_data and media_mime_type:
                current_parts.append(types.Part.from_bytes(data=media_data, mime_type=media_mime_type))
                
                if "image" in media_mime_type:
                    user_text = (user_text or "") + "\n[System: Analyze the image to identify the issue.]"
                elif "audio" in media_mime_type:
                     user_text = (user_text or "") + "\n[System: Transcribe the audio verbatim and analyze the intent.]"

            if user_text:
                current_parts.append(types.Part(text=user_text))

            if current_parts:
                contents.append(types.Content(
                    role="user",
                    parts=current_parts
                ))

            if not custom_system_prompt:
                custom_system_prompt = "You are a helpful assistant."

            config_args = {
                "system_instruction": custom_system_prompt,
                "temperature": 0.3
            }

            if require_json:
                config_args["response_mime_type"] = "application/json"
                config_args["response_schema"] = AIResponse

            response = await self.client.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=types.GenerateContentConfig(**config_args)
            )
            
            if require_json:
                # The SDK automatically validates if response_schema is passed,
                # but we usually get a parsed object or need to parse `response.text`.
                # In the Google GenAI Python SDK v0.1+, parsed is in response.parsed
                # We will check if response.parsed is available, otherwise json.loads(response.text)
                try:
                    # Depending on SDK version, parsed might be available directly
                    if hasattr(response, 'parsed') and response.parsed:
                        return response.parsed # It returns the Pydantic instance if schema was Pydantic
                    else:
                         data = json.loads(response.text)
                         return AIResponse(**data)
                except Exception as parse_error:
                     logger.error(f"JSON Parse Error: {parse_error} - Text: {response.text}")
                     # Fallback object
                     return AIResponse(
                         reply_to_user="סליחה, לא הבנתי. תוכל לחזור על זה?",
                         transcription=None,
                         extracted_data=ExtractedData(city=None, issue=None, full_address=None, appointment_time=None)
                     )

            return response.text.strip()

        except Exception as e:
            logger.error(f"AI Engine Error: {traceback.format_exc()}")
            if require_json:
                return AIResponse(
                     reply_to_user="סליחה, אני חווה עומס כרגע. נסה שוב עוד רגע.",
                     transcription=None,
                     extracted_data=ExtractedData(city=None, issue=None, full_address=None, appointment_time=None)
                 )
            return "סליחה, אני חווה עומס כרגע. נסה שוב עוד רגע."