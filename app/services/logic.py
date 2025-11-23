import httpx
import google.generativeai as genai
from app.core.config import settings
from app.core.database import users_collection, messages_collection
from rich.console import Console
from rich.theme import Theme
from datetime import datetime

# --- Rich Console Setup ---
custom_theme = Theme({
    "info": "cyan", "warning": "yellow", "error": "bold red", "success": "bold green", "ai": "bold purple"
})
console = Console(theme=custom_theme)

# --- Gemini Setup ---
genai.configure(api_key=settings.GEMINI_API_KEY)

# --- Memory Functions ---

def save_message(chat_id: str, role: str, text: str, pro_id: str = None):
    """Saves a message to MongoDB history, including the active pro_id"""
    msg_doc = {
        "chat_id": chat_id,
        "role": role,
        "text": text,
        "timestamp": datetime.utcnow()
    }
    if pro_id:
        msg_doc["pro_id"] = pro_id
        
    messages_collection.insert_one(msg_doc)

def get_chat_history(chat_id: str, limit: int = 20):
    """Fetches last N messages"""
    history = list(messages_collection.find(
        {"chat_id": chat_id}
    ).sort("timestamp", 1).limit(limit)) 
    
    formatted_history = []
    last_pro_id = None
    
    for msg in history:
        formatted_history.append({
            "role": "user" if msg["role"] == "user" else "model",
            "parts": [msg["text"]]
        })
        # נשמור את ה-ID האחרון שראינו כדי לדעת מי היה בשיחה
        if "pro_id" in msg:
            last_pro_id = msg["pro_id"]
            
    return formatted_history, last_pro_id

# --- Smart Routing Logic (With Memory!) ---

def determine_current_pro(user_text: str, last_pro_id: str = None):
    """
    מנגנון קבלת החלטות חכם:
    1. אם יש עיר חדשה בטקסט -> מחליף איש מקצוע.
    2. אם אין עיר -> נשאר עם האחרון שהיה (Sticky Session).
    3. אם אין אחרון -> ברירת מחדל (יוסי).
    """
    # מיפוי ערים (אפשר להרחיב)
    cities_map = {
        "נתניה": "דוד המהיר - שירותי אינסטלציה",
        "חדרה": "דוד המהיר - שירותי אינסטלציה",
        "קיסריה": "דוד המהיר - שירותי אינסטלציה",
        "בני ברק": "יוסי אינסטלציה ודודים",
        "רמת גן": "יוסי אינסטלציה ודודים",
        "תל אביב": "יוסי אינסטלציה ודודים",
        "פתח תקווה": "יוסי אינסטלציה ודודים"
    }
    
    # 1. בדיקה האם המשתמש מבקש לעבור עיר
    for city, pro_name in cities_map.items():
        if city in user_text:
            console.print(f"[info]📍 Detected location switch: {city} -> {pro_name}[/info]")
            return users_collection.find_one({"business_name": pro_name})
            
    # 2. אם לא צוינה עיר, נשארים עם האיש מקצוע הקודם מההיסטוריה
    if last_pro_id:
        return users_collection.find_one({"_id": last_pro_id})
        
    # 3. ברירת מחדל (התחלה)
    return users_collection.find_one({"business_name": "יוסי אינסטלציה ודודים"})


def get_dynamic_prompt(pro_data):
    """בונה את הפרומפט לפי איש המקצוע שנבחר"""
    if not pro_data:
        return "אתה מוקדן כללי."
    
    base_prompt = pro_data["system_prompt"]
    
    # הזרקת הקשר דינמי
    areas = pro_data.get("service_areas", [])
    if areas:
        areas_text = f"\n\n[System Info]: אתה עובד אך ורק באזורים: {', '.join(areas)}."
        base_prompt += areas_text
        
    return base_prompt

# --- Main AI Logic ---

async def ask_fixi_ai(user_text: str, chat_id: str) -> str:
    console.print(f"[ai]🤖 [AI] Analyzing request from {chat_id}[/ai]")
    
    try:
        # 1. טעינת היסטוריה + זיהוי מי טיפל בלקוח לאחרונה
        history, last_pro_id = get_chat_history(chat_id)
        
        # 2. קביעת איש המקצוע הנוכחי (החדש או הקיים)
        current_pro = determine_current_pro(user_text, last_pro_id)
        
        if not current_pro:
             return "תקלה בזיהוי איש מקצוע."

        # 3. שמירת הודעת המשתמש (עם ה-ID של האיש מקצוע הנבחר!)
        save_message(chat_id, "user", user_text, current_pro["_id"])
        
        # 4. בניית המוח
        system_instruction = get_dynamic_prompt(current_pro)
        model = genai.GenerativeModel(
            'gemini-2.0-flash',
            system_instruction=system_instruction
        )
        
        chat = model.start_chat(history=history)
        
        # 5. קבלת תשובה
        response = await chat.send_message_async(user_text)
        reply = response.text.strip()
        
        # 6. שמירת תשובת הבוט (גם היא משויכת לאותו איש מקצוע)
        save_message(chat_id, "model", reply, current_pro["_id"])
        
        console.print(f"[ai]🤖 [AI] Reply generated ({current_pro['business_name']}):[/ai] {reply}")
        return reply

    except Exception as e:
        console.print(f"[error]❌ [AI Error] {e}[/error]")
        return "סליחה, נתקעתי לרגע. נסה לכתוב שוב."

async def send_whatsapp(chat_id: str, text: str):
    # (החלק הזה נשאר זהה לחלוטין לקודם)
    url = f"https://api.green-api.com/waInstance{settings.GREEN_API_ID}/sendMessage/{settings.GREEN_API_TOKEN}"
    if not chat_id.endswith("@c.us") and not chat_id.endswith("@g.us"):
        chat_id = f"{chat_id}@c.us"
    payload = {"chatId": chat_id, "message": text}
    
    console.print(f"[info]📱 [WhatsApp] Sending to {chat_id}...[/info]")
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                console.print("[success]✅ Sent![/success]")
            else:
                console.print(f"[error]⚠️ WhatsApp API Error: {resp.text}[/error]")
        except Exception as e:
            console.print(f"[error]❌ Network Error: {e}[/error]")