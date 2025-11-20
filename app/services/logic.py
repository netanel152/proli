import httpx
import google.generativeai as genai
from app.core.config import settings
from rich.console import Console
from rich.theme import Theme

# --- הגדרת לוגים יפים ---
custom_theme = Theme({
    "info": "cyan",
    "warning": "yellow",
    "error": "bold red",
    "success": "bold green",
    "ai": "bold purple"
})
console = Console(theme=custom_theme)

# אתחול ג'מיני
print(f"🔑 Connecting to Gemini with key ending in: ...{settings.GEMINI_API_KEY[-4:]}")
genai.configure(api_key=settings.GEMINI_API_KEY)
SYSTEM_INSTRUCTION = """
אתה 'פיקסי' (Fixi), העוזר האישי החכם של 'יוסי אינסטלציה'.
המטרה שלך: לתת הצעת מחיר ראשונית ולסגור תור לביקור.

הנחיות התנהגות:
1. תהיה קצר, ענייני ומקצועי (אבל עם חום ישראלי).
2. אל תכתוב מגילות. תשובות של 2-3 משפטים.
3. אם חסר מידע, תשאל את הלקוח.
4. בסוף כל תשובה, תשאל אם לקבוע תור.

המחירון של יוסי (המחירים בשקלים, לא כולל מע"מ):
- ביקור רגיל: 250
- פתיחת סתימה בכיור: 350-450
- החלפת ברז ניל: 200
- החלפת סיפון: 300
- החלפת גוף חימום בדוד: 450
- התקנת ברז פרח: 350

חשוב: אם הלקוח שואל על משהו שלא במחירון, תגיד: "אני צריך לשאול את יוסי לגבי המקרה הזה, אעביר לו את הפנייה."
"""

# שימוש במודל החדש עם הוראות מערכת
model = genai.GenerativeModel(
    'gemini-2.0-flash',
    system_instruction=SYSTEM_INSTRUCTION
)

async def ask_fixi_ai(user_text: str) -> str:
    """שולח שאלה למוח של פיקסי ומקבל תשובה"""
    print(f"🤖 [AI] Sending request to Gemini: {user_text}") # לוג
    try:
        prompt = f"אתה 'פיקסי', עוזר אישי חכם לאינסטלטורים. ענה בקצרה ובענייניות בעברית להודעה: {user_text}"
        
        # בדיקה שהמפתח נטען
        if not settings.GEMINI_API_KEY:
            print("❌ [AI Error] API Key is MISSING!")
            return "תקלה: חסר מפתח AI"

        response = await model.generate_content_async(prompt)
        
        if not response.text:
            print("❌ [AI Error] Gemini returned empty response")
            return "ג'מיני לא ענה כלום."

        print(f"🤖 [AI] Response received: {response.text.strip()}") # לוג
        return response.text.strip()

    except Exception as e:
        print(f"❌ [AI CRASH] Error: {e}")
        return "סליחה, נתקע לי הפלאנג' במוח."

async def send_whatsapp(chat_id: str, text: str):
    """שולח הודעה חזרה דרך Green API"""
    url = f"https://api.green-api.com/waInstance{settings.GREEN_API_ID}/sendMessage/{settings.GREEN_API_TOKEN}"
    
    # תיקון קריטי: ChatId חייב להיות בפורמט נכון
    if not chat_id.endswith("@c.us") and not chat_id.endswith("@g.us"):
        chat_id = f"{chat_id}@c.us"

    payload = {"chatId": chat_id, "message": text}
    
    print(f"📱 [WhatsApp] Sending to {chat_id}...") # לוג
    
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(url, json=payload, timeout=10.0)
            print(f"📱 [WhatsApp] Status Code: {resp.status_code}") # לוג
            print(f"📱 [WhatsApp] Response Body: {resp.text}") # לוג
            
            if resp.status_code != 200:
                print(f"⚠️ [WhatsApp Error] API refused sending.")
        except Exception as e:
            print(f"❌ [Network Error] Failed to send to WhatsApp: {e}")