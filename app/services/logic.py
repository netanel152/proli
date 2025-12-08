import httpx
import google.generativeai as genai
from app.core.config import settings
from app.core.database import users_collection, messages_collection, leads_collection, slots_collection
from app.core.logger import logger
from datetime import datetime, timedelta
from tenacity import retry, stop_after_attempt, wait_fixed, wait_exponential, retry_if_exception_type
import tenacity
from google.api_core.exceptions import ResourceExhausted
import cloudinary
import cloudinary.uploader
import re
import os
import tempfile
import json
import pytz
from datetime import datetime, timedelta, timezone
import traceback

genai.configure(api_key=settings.GEMINI_API_KEY)

# Configure Cloudinary
cloudinary.config(
    cloud_name=settings.CLOUDINARY_CLOUD_NAME,
    api_key=settings.CLOUDINARY_API_KEY,
    api_secret=settings.CLOUDINARY_API_SECRET
)

IL_TZ = pytz.timezone('Asia/Jerusalem')

# --- Helpers ---
@retry(
    retry=retry_if_exception_type(ResourceExhausted),
    wait=wait_exponential(multiplier=2, min=5, max=120),
    stop=stop_after_attempt(10)
)
async def _generate_with_retry(chat_session, content_parts):
    """
    Wraps the Gemini generation call with a retry mechanism specifically for
    ResourceExhausted (429) errors.
    """
    return await chat_session.send_message_async(content_parts)

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
    """לוגיקת ניתוב מתוקנת ויציבה"""
    
    # אם אין טקסט, נשארים עם הקיים
    if not user_text:
        if last_pro_id:
            return users_collection.find_one({"_id": last_pro_id})
        return users_collection.find_one({"business_name": "יוסי אינסטלציה"})

    # שינוי מיקום (גובר על הכל)
    # מחפש התאמה מדויקת של עיר בטקסט
    active_pros = list(users_collection.find({"is_active": True}))
    for pro in active_pros:
        for area in pro.get("service_areas", []):
            if area.strip() in user_text:
                logger.info(f"📍 Location Switch: {area} -> {pro['business_name']}")
                return pro

    # אם כבר יש איש מקצוע, נשארים איתו!
    # זה מונע את הבאג שבו "סתימה" מחזירה את יוסי באמצע שיחה עם דוד
    if last_pro_id:
        found = users_collection.find_one({"_id": last_pro_id})
        if found: return found

    # רק אם זו שיחה חדשה לגמרי: חיפוש לפי מילות מפתח
    for pro in active_pros:
        for keyword in pro.get("keywords", []):
            if keyword.strip() in user_text:
                logger.info(f"🔧 Keyword Match: {keyword} -> {pro['business_name']}")
                return pro

    # ברירת מחדל
    return users_collection.find_one({"business_name": "יוסי אינסטלציה"})

def get_dynamic_prompt(pro_data):
    if not pro_data: return "You are a general assistant."
    base = pro_data["system_prompt"]
    
    # הזרקת יומן זמינות
    slots_str = get_available_slots(pro_data["_id"])
    base += f"\n\n*** יומן זמינות בזמן אמת ***\nהצע ללקוח אך ורק את השעות האלו:\n{slots_str}"
    
    return base

# --- Availability & Pro Command Logic ---
def get_il_time():
    """מחזיר זמן ישראל נוכחי"""
    return datetime.now(pytz.timezone('Asia/Jerusalem'))

def get_available_slots(pro_id):
    now_utc = datetime.now(pytz.utc)
    # שליפת סלוטים שהזמן שלהם (ב-UTC) גדול מעכשיו
    slots = list(slots_collection.find({
        "pro_id": pro_id, "is_taken": False, "start_time": {"$gt": now_utc}
    }).sort("start_time", 1).limit(5))
    
    if not slots: return "אין תורים פנויים כרגע."
    
    lines = []
    for s in slots:
        # המרה לתצוגה בשעון ישראל
        local_start = s['start_time'].replace(tzinfo=pytz.utc).astimezone(IL_TZ)
        
        # Check if end_time exists (for backward compatibility)
        if 'end_time' in s and s['end_time']:
            local_end = s['end_time'].replace(tzinfo=pytz.utc).astimezone(IL_TZ)
            fmt_time = f"{local_start.strftime('%d/%m')} בשעות {local_start.strftime('%H:%M')}-{local_end.strftime('%H:%M')}"
        else:
            # Fallback for old slots (assume 60 mins or just show start)
            fmt_time = local_start.strftime("%d/%m בשעה %H:%M")
            
        lines.append(f"- {fmt_time}")
    return "\n".join(lines)

# --- Pro Command Analysis ---
async def analyze_pro_intent(text: str):
    now_il = datetime.now(IL_TZ)
    current_date = now_il.strftime("%d/%m")
    current_hour = now_il.hour
    
    prompt = f"""
    Analyze the technician's message and output JSON.
    Current IL Time: {current_date} {current_hour}:00.
    Message: "{text}"
    
    Intents:
    1. BLOCK: "busy at 4", "block 16:00", "hasom 10", "taken", "ain li makom".
    2. FREE: "done", "finished", "available", "hitpaneti", "shahrrer", "sayamti".
    3. SHOW: "my schedule", "yoman", "matay panuy", "torim", "luz".
    4. FINISH_JOB: "finished", "sayamti", "job done", "close ticket", "done with client".
    5. GET_WORK: "get work", "avoda", "lead", "give me job", "avoda hadasha".
    6. UNKNOWN: regular chat.

    **Logic:**
    - If hour requested (e.g. 10) is smaller than current hour ({current_hour}) -> Assume TOMORROW.
    - "4" usually means 16:00 (4 PM) if said in the afternoon, but prefer 24h format. 
    
    Output JSON: {{ "intent": "BLOCK"|"FREE"|"SHOW"|"FINISH_JOB"|"GET_WORK"|"UNKNOWN", "hour": int, "day": "TODAY"|"TOMORROW" }}
    """
    try:
        model = genai.GenerativeModel('gemini-2.0-flash', generation_config={"response_mime_type": "application/json"})
        response = await model.generate_content_async(prompt)
        return json.loads(response.text)
    except: return {"intent": "UNKNOWN"}

async def handle_pro_command(chat_id: str, text: str):
    # ניקוי אגרסיבי של המספר כדי למנוע אי-התאמות
    clean_phone = chat_id.replace("@c.us", "").replace("+", "").replace("-", "")
    
    # חיפוש גמיש ב-DB (גם אם המספר שמור עם/בלי 972)
    # נחפש מספר שמסתיים ב-9 הספרות האחרונות
    short_phone = clean_phone[-9:] 
    
    pro = users_collection.find_one({
        "phone_number": {"$regex": short_phone} 
    })
    
    if not pro: return None

    logger.info(f"👨‍🔧 Pro Command Check: {text} (User: {pro['business_name']})")
    
    parsed = await analyze_pro_intent(text)
    intent = parsed.get("intent")
    logger.info(f"🧠 AI Decoded: {parsed}")

    if intent == "BLOCK":
        hour = parsed.get("hour")
        if hour is None: return "לא הבנתי איזו שעה לחסום."
        
        # חישוב זמנים מדויק עם אזורי זמן
        now_il = datetime.now(IL_TZ)
        target_date = now_il
        
        if parsed.get("day") == "TOMORROW":
            target_date += timedelta(days=1)
            
        # יצירת זמן ישראל המבוקש (שעת התחלה של הבלוק)
        target_time_il = target_date.replace(hour=hour, minute=0, second=0, microsecond=0)
        
        # המרה ל-UTC לחיפוש ב-DB
        search_utc = target_time_il.astimezone(pytz.utc)
        
        # לוגיקה משופרת: חיפוש סלוט שמתחיל בדיוק בשעה הזו, או שהשעה הזו נופלת בתוכו
        # נחפש סלוט שהתחיל לפני (או ב) השעה המבוקשת, ונגמר אחרי השעה המבוקשת
        result = slots_collection.update_one(
            {
                "pro_id": pro["_id"], 
                "is_taken": False,
                "$or": [
                    # אופציה 1: התאמה מדויקת לשעת התחלה (טווח של דקה)
                    {
                        "start_time": {
                            "$gte": search_utc - timedelta(minutes=1),
                            "$lte": search_utc + timedelta(minutes=1)
                        }
                    },
                    # אופציה 2: השעה המבוקשת נמצאת בתוך סלוט קיים
                    {
                        "start_time": {"$lte": search_utc},
                        "end_time": {"$gt": search_utc}
                    }
                ]
            },
            {"$set": {"is_taken": True}}
        )
        
        display_date = target_time_il.strftime("%d/%m")
        if result.modified_count > 0:
            return f"✅ חסמתי לך את {hour}:00 ({display_date})."
        else:
            # בדיקה אם זה כבר תפוס
            return f"❌ לא מצאתי תור פנוי ב-{hour}:00 ({display_date}). אולי הוא כבר תפוס או עבר?"

    elif intent == "FREE":
        # כאן אפשר להוסיף לוגיקה שתשחרר את הסלוט הנוכחי אם הוא היה תפוס
        users_collection.update_one({"_id": pro["_id"]}, {"$set": {"is_available": True}})
        return "👍 מעולה. סימנתי שסיימת ואתה פנוי."

    elif intent == "SHOW":
        # שליפת עבודות מוזמנות (booked) להיום ומחר
        now = datetime.now(IL_TZ)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow_end = (today_start + timedelta(days=2)).replace(microsecond=0)
        
        # המרה ל-UTC עבור השאילתה
        start_utc = today_start.astimezone(pytz.utc)
        end_utc = tomorrow_end.astimezone(pytz.utc)
        
        booked_leads = list(leads_collection.find({
            "pro_id": pro["_id"],
            "status": "booked",
            "created_at": {"$gte": start_utc, "$lt": end_utc} # הנחה: created_at משמש כזמן העבודה כרגע
        }).sort("created_at", 1))
        
        if not booked_leads:
            return "📅 אין לך עבודות סגורות (Booked) להיום או מחר."
            
        response = "📅 **תוכנית עבודה (היום ומחר):**\n"
        for lead in booked_leads:
            # המרה חזרה לשעון ישראל לתצוגה
            lead_time = lead["created_at"].replace(tzinfo=pytz.utc).astimezone(IL_TZ)
            time_str = lead_time.strftime("%d/%m %H:%M")
            client_phone = lead["chat_id"].replace("@c.us", "")
            details = lead.get("details", "ללא פרטים")
            # קיצור פרטים אם ארוכים מדי
            short_details = (details[:30] + '..') if len(details) > 30 else details
            
            response += f"\n🔹 {time_str} - {short_details}\n   📞 לקוח: {client_phone}"
            
        return response
    
    elif intent == "FINISH_JOB":
        # שיפור: מחפשים ליד אחרון שהוא 'new' או 'booked' או 'חדש'
        last_lead = leads_collection.find_one({
            "pro_id": pro["_id"],
            "status": {"$in": ["New", "booked", "חדש"]}
        }, sort=[("created_at", -1)])
        
        if not last_lead:
            return "לא מצאתי עבודה פתוחה לסגור. אולי כבר סגרת אותה?"
        
        # עדכון הליד
        leads_collection.update_one(
            {"_id": last_lead["_id"]},
            {"$set": {"status": "completed", "completed_at": datetime.now(timezone.utc), "waiting_for_rating": True}}
        )
        
        # שליחת בקשת דירוג ללקוח
        client_chat_id = last_lead["chat_id"]
        # שליחת הודעה ללקוח (מנקה את ה-c.us אם צריך)
        # כאן אנחנו משתמשים בפונקציית העזר שלנו
        feedback_msg = (
            f"היי! 👋 שמחנו לתת לך שירות עם {pro['business_name']}.\n"
            f"איך היה? נשמח לדירוג מהיר מ-1 (גרוע) עד 5 (מצוין).\n"
            f"פשוט השב עם המספר כאן 👇"
        )
        # שים לב: אנחנו קוראים לפונקציה לשליחת הודעה, לא מחזירים סטרינג
        await send_whatsapp_message(client_chat_id, feedback_msg)        
        return f"✅ העבודה סומנה כהושלמה! שלחתי בקשת דירוג ללקוח."

    elif intent == "GET_WORK":
        # Find oldest lead with status="New"
        lead = leads_collection.find_one(
            {"status": "New"},
            sort=[("created_at", 1)]
        )
        
        if not lead:
            return "📭 אין עבודות חדשות כרגע. נעדכן כשייכנס משהו!"
            
        # Update Lead
        leads_collection.update_one(
            {"_id": lead["_id"]},
            {"$set": {"status": "booked", "pro_id": pro["_id"]}}
        )
        
        # Notify Pro
        lead_time = lead["created_at"].replace(tzinfo=pytz.utc).astimezone(IL_TZ)
        time_str = lead_time.strftime("%d/%m %H:%M")
        details = lead.get("details", "ללא פרטים")
        client_phone = lead["chat_id"].replace("@c.us", "")
        
        msg = (
            f"🚀 **קיבלת עבודה חדשה!**\n"
            f"📅 {time_str}\n"
            f"📝 {details}\n"
            f"📞 לקוח: {client_phone}\n\n"
            f"העבודה סומנה אצלך כ-Booked. בהצלחה!"
        )
        return msg

    return None

# ---  פונקציה לטיפול בדירוג מהלקוח  ---
async def handle_customer_rating(chat_id: str, text: str):
    """בודק אם הלקוח שלח מספר ומעדכן דירוג"""
    text = text.strip()
    if text not in ["1", "2", "3", "4", "5"]:
        return None # זה לא דירוג
        
    rating = int(text)
    
    # מחפש ליד שמחכה לדירוג מהלקוח הזה
    lead = leads_collection.find_one({
        "chat_id": chat_id,
        "waiting_for_rating": True
    })
    
    if not lead:
        return None # הלקוח סתם כתב מספר
        
    # עדכון ה-DB
    pro_id = lead["pro_id"]
    pro = users_collection.find_one({"_id": pro_id})
    
    # חישוב ממוצע חדש (פשוט)
    current_rating = pro.get("social_proof", {}).get("rating", 5.0)
    count = pro.get("social_proof", {}).get("review_count", 0)
    
    # נוסחה: (ישן * כמות + חדש) / (כמות + 1)
    # בגלל שהנתונים הראשוניים הם פיקטיביים, ניתן משקל נמוך יותר לחדש בהתחלה
    new_rating = round(((current_rating * 10) + rating) / 11, 1) 
    
    users_collection.update_one(
        {"_id": pro_id},
        {
            "$set": {
                "social_proof.rating": new_rating,
                "social_proof.review_count": count + 1
            }
        }
    )
    
    leads_collection.update_one(
        {"_id": lead["_id"]},
        {"$set": {"waiting_for_rating": False, "rating_given": rating}}
    )
    
    logger.success(f"⭐ Rating {rating} saved for {pro['business_name']}")
    return "תודה רבה על הדירוג! ⭐ שמחנו לעזור."

# --- Media Handler ---
async def download_and_store_media(url: str):
    """
    Downloads media from WhatsApp URL, uploads to Cloudinary,
    and returns tuple (local_temp_path,cloudinary_secure_url).
    """
    logger.info(f"📥 Downloading media...")
    temp_path = None
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url)
            resp.raise_for_status()
            
            suffix = ".tmp"
            content_type = resp.headers.get("content-type", "")
            if "image" in content_type: suffix = ".jpg"
            elif "audio" in content_type: suffix = ".ogg"
            elif "video" in content_type: suffix = ".mp4"
            
            # Create local temp file for Gemini
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(resp.content)
                temp_path = tmp.name
        
        # Upload to Cloudinary for persistence - Try/Except block specifically for upload
        secure_url = None
        try:
            logger.info(f"☁️ Uploading to Cloudinary...")
            upload_result = cloudinary.uploader.upload(temp_path, resource_type="auto")
            secure_url = upload_result.get("secure_url")
        except Exception as cloud_err:
            logger.warning(f"☁️ Cloudinary Upload Failed: {cloud_err}")
            # Continue without secure_url, but we have temp_path for Gemini
            
        return temp_path, secure_url
            
    except Exception as e:
        logger.error(f"Media Processing Error: {traceback.format_exc()}")
        return None, None

# --- CRM Logic ---
async def handle_new_lead(chat_id: str, details: str, pro_data: dict, media_url: str = None):
    # נעילה אוטומטית של סלוט לפי טקסט הליד (חכם יותר)
    try:
        # מחפשים שעה בפורמט HH:MM בטקסט
        time_match = re.search(r"(\d{1,2}:\d{2})", details)
        if time_match:
            time_str = time_match.group(1)
            # זה דורש לוגיקה מורכבת יותר להתאמת יום, ל-MVP נסתמך על התאמת שעה בלבד בטווח הקרוב
            # (בפרודקשן: הבוט צריך להחזיר Timestamp מדויק)
            pass 
    except: pass

    logger.success(f"💰 NEW LEAD! {details}")
    leads_collection.insert_one({
        "chat_id": chat_id, "pro_id": pro_data["_id"], "details": details, 
        "status": "New", "created_at": datetime.now(timezone.utc), "media_url": media_url
    })
    
    if pro_data.get("phone_number"):
        pro_chat = f"{pro_data['phone_number']}@c.us" if not pro_data['phone_number'].endswith("@c.us") else pro_data['phone_number']
        msg = f"""🔔 *ליד חדש!*
פרטים: {details}
לקוח: https://wa.me/{chat_id.replace('@c.us','')}"""
        
        # Always send the text details first to ensure they are seen
        await send_whatsapp_message(pro_chat, msg)
        
        if media_url:
             # Then send the media
            await send_whatsapp_file(pro_chat, media_url, caption="מדיה מצורפת מהלקוח")
            
# --- Main Logic ---
async def ask_fixi_ai(user_text: str, chat_id: str, media_url: str = None) -> str:

    # 1. קודם כל: האם זה דירוג מלקוח? (כדי שאיש מקצוע לא יחסום לעצמו יומן עם "5")
    rating_resp = await handle_customer_rating(chat_id, user_text or "")
    if rating_resp:
        logger.info(f"🤖 Rating Reply: {rating_resp}")
        return rating_resp

    # --- בדיקת פקודת איש מקצוע ---
    pro_response = await handle_pro_command(chat_id, user_text or "")
    if pro_response:
        # אם זו הייתה פקודה, מחזירים את התשובה ויוצאים (לא ממשיכים לבוט הרגיל)
        logger.info(f"🤖 Pro Command Reply: {pro_response}")
        return pro_response 
        
    # --- לוגיקת הבוט הראשית ---
    try:
        history, last_pro_id = get_chat_history(chat_id)
        safe_text = user_text or ""
        current_pro = determine_current_pro(safe_text, last_pro_id)
        
        if not current_pro:
            return "שגיאה: לא נמצא איש שירות פעיל במערכת."

        # --- Handle Media Upload & Persistence ---
        temp_path = None
        cloudinary_url = None
        
        if media_url:
            temp_path, cloudinary_url = await download_and_store_media(media_url)
            # temp_path might be valid even if cloudinary_url is None (upload failed)

        # שמירת הודעת משתמש
        log_text = user_text or "[Media Message]"
        if cloudinary_url: log_text += f" (URL: {cloudinary_url})"
        save_message(chat_id, "user", log_text, current_pro["_id"])
        
        model = genai.GenerativeModel('gemini-2.0-flash', system_instruction=get_dynamic_prompt(current_pro))
        chat = model.start_chat(history=history)
        
        content_parts = []
        
        # טיפול במדיה נכנסת עבור Gemini
        if temp_path and os.path.exists(temp_path):
            try:
                uploaded_file = genai.upload_file(temp_path)
                content_parts.append(uploaded_file)
                if temp_path.endswith(".ogg"):
                    content_parts.append("המשתמש שלח הקלטה. הקשב לה וענה.")
                else:
                    content_parts.append("המשתמש שלח תמונה. נתח אותה.")
            except Exception as e:
                logger.error(f"Gemini Upload Error: {e}")

        if user_text: content_parts.append(user_text)

        if not content_parts:
            return "סליחה, הייתה בעיה בקבלת הקובץ ששלחת. אנא נסה שוב או כתוב לי הודעה."

        # Use the helper with retry logic
        response = await _generate_with_retry(chat, content_parts)
        reply_text = response.text.strip()
        
        # Clean up temp file
        if temp_path and os.path.exists(temp_path): os.remove(temp_path)

        # זיהוי סגירה והפעלת ה-CRM
        deal_match = re.search(r"\[DEAL:(.*?)\]", reply_text)
        if deal_match:
            lead_details = deal_match.group(1).strip()
            
            # Use Cloudinary URL if available, otherwise fallback to original WhatsApp URL
            final_media_url = cloudinary_url if cloudinary_url else media_url
            
            await handle_new_lead(chat_id, lead_details, current_pro, media_url=final_media_url)
            
            # ניקוי הפקודה מהטקסט
            reply_text = reply_text.replace(deal_match.group(0), "").strip()
            
            # 🔥 התיקון: אם הטקסט נשאר ריק, נכניס הודעת אישור גנרית
            if not reply_text:
                reply_text = "✅ מעולה! התור נקבע בהצלחה ושלחתי את הפרטים לאיש המקצוע."

        save_message(chat_id, "model", reply_text, current_pro["_id"])
        logger.info(f"🤖 Bot Reply ({current_pro['business_name']}): {reply_text}")
        
        return reply_text
    
    except tenacity.RetryError:
        logger.warning(f"⚠️ AI Rate Limit Reached (after retries)")
        return "המערכת עמוסה כרגע בפניות רבות. אנא נסה שוב בעוד דקה. 🙏"

    except Exception as e:
        logger.error(f"❌ AI Error: {traceback.format_exc()}")
        return "סליחה, נתקעתי. נסה שוב."

# --- Whatsapp Logic ---
@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
async def send_whatsapp_message(to_chat_id: str, text: str):
    url = f"https://api.green-api.com/waInstance{settings.GREEN_API_ID}/sendMessage/{settings.GREEN_API_TOKEN}"
    payload = {"chatId": to_chat_id, "message": text}
    async with httpx.AsyncClient() as client:
        await client.post(url, json=payload)

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
async def send_whatsapp_file(to_chat_id: str, file_url: str, caption: str = ""):
    """שליחת קובץ (תמונה/הקלטה) דרך ה-API"""
    url = f"https://api.green-api.com/waInstance{settings.GREEN_API_ID}/sendFileByUrl/{settings.GREEN_API_TOKEN}"
    payload = {
        "chatId": to_chat_id,
        "urlFile": file_url,
        "fileName": "media_file",
        "caption": caption
    }
    async with httpx.AsyncClient() as client:
        await client.post(url, json=payload)    

# עוטף לפונקציה הראשית כדי לשמור על תאימות עם main.py
async def send_whatsapp(chat_id: str, text: str):
    await send_whatsapp_message(chat_id, text)