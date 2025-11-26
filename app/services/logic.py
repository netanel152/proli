import httpx
import google.generativeai as genai
from app.core.config import settings
from app.core.database import users_collection, messages_collection, leads_collection
from rich.console import Console
from rich.theme import Theme
from datetime import datetime
import re
import os
import tempfile

custom_theme = Theme({"info": "cyan", "warning": "yellow", "error": "bold red", "success": "bold green", "ai": "bold purple"})
console = Console(theme=custom_theme)

genai.configure(api_key=settings.GEMINI_API_KEY)

# --- Helpers ---
def save_message(chat_id: str, role: str, text: str, pro_id: str = None):
    msg_doc = {"chat_id": chat_id, "role": role, "text": text, "timestamp": datetime.now()}
    if pro_id: msg_doc["pro_id"] = pro_id
    messages_collection.insert_one(msg_doc)

def get_chat_history(chat_id: str, limit: int = 20):
    history = list(messages_collection.find({"chat_id": chat_id}).sort("timestamp", 1).limit(limit))
    formatted = []
    last_pro_id = None
    for msg in history:
        formatted.append({"role": "user" if msg["role"] == "user" else "model", "parts": [msg["text"]]})
        if "pro_id" in msg: last_pro_id = msg["pro_id"]
    return formatted, last_pro_id

def determine_current_pro(user_text: str, last_pro_id: str = None):
    """הגרסה היציבה - מסונכרנת עם seed_db.py"""
    
    # אם אין טקסט (רק תמונה), נשארים עם הקיים או ברירת מחדל
    if not user_text:
        if last_pro_id:
            found = users_collection.find_one({"_id": last_pro_id})
            if found: return found
        return users_collection.find_one({"business_name": "יוסי אינסטלציה"}) # תיקון שם!

    # מיפוי ערים לאנשי מקצוע (לפי השמות החדשים ב-seed_db)
    cities_map = {
        "נתניה": "דוד המהיר אינסטלציה", 
        "חדרה": "דוד המהיר אינסטלציה",
        "קיסריה": "דוד המהיר אינסטלציה", 
        
        "בני ברק": "יוסי אינסטלציה", # תיקון שם!
        "רמת גן": "יוסי אינסטלציה",
        "תל אביב": "יוסי אינסטלציה",
        "גבעתיים": "יוסי אינסטלציה",
        
        "פתח תקווה": "רוני חשמל דחוף" # הוספנו את רוני
    }
    
    # בדיקת מילות מפתח (למשל "חשמל" -> רוני)
    if "חשמל" in user_text or "קצר" in user_text:
         console.print("[info]⚡ Detected Electrician keywords[/info]")
         return users_collection.find_one({"business_name": "רוני חשמל דחוף"})

    for city, pro_name in cities_map.items():
        if city in user_text:
            console.print(f"[info]📍 Routing to: {pro_name}[/info]")
            return users_collection.find_one({"business_name": pro_name})
    
    # נשארים עם הקיים (עם הגנה!)
    if last_pro_id:
        found = users_collection.find_one({"_id": last_pro_id})
        if found: return found
    
    # ברירת מחדל אם הכל נכשל
    return users_collection.find_one({"business_name": "יוסי אינסטלציה"})

def get_dynamic_prompt(pro_data):
    if not pro_data: return "You are a general assistant."
    base = pro_data["system_prompt"]
    areas = pro_data.get("service_areas", [])
    if areas: base += f"\n\n[System Info]: Service Areas: {', '.join(areas)}."
    return base

async def download_media(url: str) -> str:
    console.print(f"[info]📥 Downloading media...[/info]")
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        suffix = ".tmp"
        content_type = resp.headers.get("content-type", "")
        if "image" in content_type: suffix = ".jpg"
        elif "audio" in content_type: suffix = ".ogg"
        elif "video" in content_type: suffix = ".mp4"
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(resp.content)
            return tmp.name

async def handle_new_lead(chat_id: str, details: str, pro_data: dict):
    console.print(f"[success]💰 NEW LEAD! {details}[/success]")
    
    leads_collection.insert_one({
        "chat_id": chat_id,
        "pro_id": pro_data["_id"],
        "business_name": pro_data["business_name"],
        "details": details,
        "status": "new",
        "created_at": datetime.utcnow()
    })
    
    # שליחת התראה מעוצבת למקצוען
    pro_phone = pro_data.get("phone_number")
    if pro_phone:
        # ניקוי המספר של הלקוח לפורמט בינלאומי ללינק
        clean_phone = chat_id.replace("@c.us", "")
        
        # פירוק פרטי הליד (אם ה-AI עבד לפי הפורמט החדש)
        # הפורמט המצופה: זמן | מיקום | בעיה
        parts = details.split("|")
        if len(parts) >= 3:
            time_str = parts[0].strip()
            loc_str = parts[1].strip()
            problem_str = parts[2].strip()
            
            formatted_msg = (
                f"🔔 *ליד חדש נכנס!* ({pro_data['business_name']})\n\n"
                f"📅 *מתי:* {time_str}\n"
                f"📍 *איפה:* {loc_str}\n"
                f"🔧 *תקלה:* {problem_str}\n\n"
                f"👤 *לקוח:* https://wa.me/{clean_phone}"
            )
        else:
            # Fallback למקרה שהפורמט פשוט יותר
            formatted_msg = (
                f"🔔 *ליד חדש!*\n\n"
                f"📝 פרטים: {details}\n"
                f"👤 לקוח: https://wa.me/{clean_phone}"
            )

        await send_whatsapp(pro_phone, formatted_msg)

async def ask_fixi_ai(user_text: str, chat_id: str, media_url: str = None) -> str:
    try:
        history, last_pro_id = get_chat_history(chat_id)
        
        safe_text = user_text or ""
        current_pro = determine_current_pro(safe_text, last_pro_id)
        
        if not current_pro:
            # Fallback חירום
            return "שגיאת מערכת: לא נמצא איש שירות (הרץ seed_db)."

        log_text = user_text or "[Media Message]"
        if media_url: log_text += f" (URL)"
        save_message(chat_id, "user", log_text, current_pro["_id"])
        
        model = genai.GenerativeModel('gemini-2.0-flash', system_instruction=get_dynamic_prompt(current_pro))
        chat = model.start_chat(history=history)
        
        content_parts = []
        temp_path = None
        
        if media_url:
            try:
                temp_path = await download_media(media_url)
                uploaded_file = genai.upload_file(temp_path)
                content_parts.append(uploaded_file)
                
                if temp_path.endswith(".ogg"):
                    content_parts.append("המשתמש שלח הקלטה קולית. הקשב לה וענה.")
                else:
                    content_parts.append("המשתמש שלח תמונה. נתח אותה.")
            except Exception as e:
                console.print(f"[error]Media Error: {e}[/error]")

        if user_text: content_parts.append(user_text)

        response = await chat.send_message_async(content_parts)
        reply_text = response.text.strip()
        
        if temp_path and os.path.exists(temp_path): os.remove(temp_path)

        # זיהוי חירום (URGENT)
        if "[URGENT]" in reply_text:
             # כאן אפשר להוסיף לוגיקה מיוחדת לחירום בעתיד
             reply_text = reply_text.replace("[URGENT]", "").strip()

        # זיהוי סגירה (DEAL)
        deal_match = re.search(r"\[DEAL:(.*?)\]", reply_text)
        if deal_match:
            lead_details = deal_match.group(1).strip()
            await handle_new_lead(chat_id, lead_details, current_pro)
            reply_text = reply_text.replace(deal_match.group(0), "").strip()

        save_message(chat_id, "model", reply_text, current_pro["_id"])
        
        console.print(f"[ai]🤖 Bot Reply:[/ai] {reply_text}")
        
        return reply_text
    
    except Exception as e:
        console.print(f"[error]❌ AI Error: {e}[/error]")
        return "סליחה, נתקעתי. נסה שוב."

async def send_whatsapp(chat_id: str, text: str):
    url = f"https://api.green-api.com/waInstance{settings.GREEN_API_ID}/sendMessage/{settings.GREEN_API_TOKEN}"
    if not chat_id.endswith("@c.us") and not chat_id.endswith("@g.us"): chat_id = f"{chat_id}@c.us"
    async with httpx.AsyncClient() as client:
        await client.post(url, json={"chatId": chat_id, "message": text})