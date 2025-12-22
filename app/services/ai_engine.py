from google import genai
from google.genai import types
from app.core.config import settings
from app.core.logger import logger
import traceback

class AIEngine:
    def __init__(self):
        self.client = genai.Client(api_key=settings.GEMINI_API_KEY)
        self.model_name = "gemini-2.5-flash-lite"
        # No static system_prompt here anymore. It's passed dynamically.

    async def analyze_conversation(self, history: list, user_text: str, custom_system_prompt: str, media_data: bytes = None, media_mime_type: str = None) -> str:
        try:
            contents = []
            for msg in history:
                # msg["parts"] is expected to be a list of strings
                parts = [types.Part(text=p) for p in msg.get("parts", [])]
                contents.append(types.Content(role=msg["role"], parts=parts))
            
            # Construct current user message content
            current_parts = []
            
            if media_data and media_mime_type:
                # Add media part
                current_parts.append(types.Part.from_bytes(data=media_data, mime_type=media_mime_type))
                
                # Add context instruction based on media type
                if "image" in media_mime_type:
                    user_text = (user_text or "") + "\n[System Note: User attached an image. Analyze it to understand the issue.]"
                elif "audio" in media_mime_type:
                     user_text = (user_text or "") + "\n[System Note: User sent a voice message. Transcribe and understand the need.]"

            if user_text:
                current_parts.append(types.Part(text=user_text))

            if current_parts:
                contents.append(types.Content(
                    role="user",
                    parts=current_parts
                ))

            # Fallback if no prompt provided
            if not custom_system_prompt:
                custom_system_prompt = "You are a helpful assistant."

            response = await self.client.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=custom_system_prompt
                )
            )
            
            return response.text.strip()
        except Exception as e:
            logger.error(f"AI Engine Error: {traceback.format_exc()}")
            return "סליחה, אני חווה עומס כרגע. נסה שוב עוד רגע."