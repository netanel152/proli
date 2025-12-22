from google import genai
from google.genai import types
from app.core.config import settings
from app.core.logger import logger
import traceback

class AIEngine:
    def __init__(self):
        self.client = genai.Client(api_key=settings.GEMINI_API_KEY)
        self.model_name = "gemini-2.5-flash-lite"
        self.system_prompt = """
You are Fixi, an AI scheduler for service professionals.
Your goal is to understand the user's issue and get their specific location (City + Street Address) and preferred time.

*** STRICT ADDRESS RULE ***
NEVER output [DEAL] without a full street address. If the user only gives a city, ASK for the street.
If the user provides an address and issue, output the [DEAL] tag at the end.

Format: [DEAL: <Time> | <Full Address> | <Issue Summary>]

Tone: Professional, efficient, Israeli Hebrew.
"""

    async def analyze_conversation(self, history: list, user_text: str) -> str:
        try:
            contents = []
            for msg in history:
                # msg["parts"] is expected to be a list of strings
                parts = [types.Part(text=p) for p in msg.get("parts", [])]
                contents.append(types.Content(role=msg["role"], parts=parts))
            
            if user_text:
                contents.append(types.Content(
                    role="user",
                    parts=[types.Part(text=user_text)]
                ))

            response = await self.client.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=self.system_prompt
                )
            )
            
            return response.text.strip()
        except Exception as e:
            logger.error(f"AI Engine Error: {traceback.format_exc()}")
            return "סליחה, אני חווה עומס כרגע. נסה שוב עוד רגע."
