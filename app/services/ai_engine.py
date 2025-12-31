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
        # Define the fallback hierarchy
        self.model_hierarchy = [
            "gemini-2.5-flash-lite", # Primary: Speed & Cost
            "gemini-2.5-flash",      # Secondary: Stability
            "gemini-1.5-flash"       # Fallback: Legacy Reliable
        ]

    async def analyze_conversation(self, history: list, user_text: str, custom_system_prompt: str, media_data: bytes = None, media_mime_type: str = None, require_json: bool = True) -> AIResponse | str:
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
            elif "video" in media_mime_type:
                user_text = (user_text or "") + "\n[System: Watch the video to identify the issue and describe what you see.]"

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
                    reply_to_user="סליחה, אני חווה עומס כרגע. נסה שוב עוד רגע.",
                    transcription=None,
                    extracted_data=ExtractedData(city=None, issue=None, full_address=None, appointment_time=None)
                )
        return "סליחה, אני חווה עומס כרגע. נסה שוב עוד רגע."