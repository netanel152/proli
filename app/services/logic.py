import urllib.parse
from bson import ObjectId
import httpx
from google import genai
from google.genai import types
from app.core.config import settings
from app.core.database import users_collection, messages_collection, leads_collection, slots_collection
from app.core.logger import logger
from datetime import datetime, timedelta
from tenacity import retry, stop_after_attempt, wait_fixed, wait_exponential, retry_if_exception_type, wait_random_exponential
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
import asyncio

# Initialize Google GenAI Client
client = genai.Client(api_key=settings.GEMINI_API_KEY)

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
    wait=wait_random_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3)
)
async def _generate_with_retry(model_name, contents, config):
    return await client.models.generate_content(
        model=model_name,
        contents=contents,
        config=config
    )

async def save_message(chat_id: str, role: str, text: str, pro_id: str = None):
    msg_doc = {"chat_id": chat_id, "role": role, "text": text, "timestamp": datetime.now()}
    if pro_id: msg_doc["pro_id"] = pro_id
    await messages_collection.insert_one(msg_doc)

async def get_chat_history(chat_id: str, limit: int = 20):
    cursor = messages_collection.find({"chat_id": chat_id}).sort("timestamp", 1).limit(limit)
    history = await cursor.to_list(length=limit)
    
    formatted = []
    last_pro_id = None
    for msg in history:
        # Convert to strict string to avoid issues
        text = str(msg.get("text", ""))
        role = "user" if msg["role"] == "user" else "model"
        formatted.append({"role": role, "parts": [text]})
        if "pro_id" in msg: last_pro_id = msg["pro_id"]
    return formatted, last_pro_id

async def determine_current_pro(user_text: str, last_pro_id: str = None):
    """Async routing logic"""
    
    # 1. No text fallback
    if not user_text:
        if last_pro_id:
            return await users_collection.find_one({"_id": last_pro_id})
        return await users_collection.find_one({"business_name": "יוסי אינסטלציה"})

    # 2. Location Switch (Exact City Match)
    active_pros = await users_collection.find({"is_active": True}).to_list(length=None)
    
    for pro in active_pros:
        for area in pro.get("service_areas", []):
            if area.strip() in user_text:
                logger.info(f"📍 Location Switch: {area} -> {pro['business_name']}")
                return pro

    # 3. Sticky Session
    if last_pro_id:
        found = await users_collection.find_one({"_id": last_pro_id})
        if found: return found

    # 4. Keyword Match
    for pro in active_pros:
        for keyword in pro.get("keywords", []):
            if keyword.strip() in user_text:
                logger.info(f"🔧 Keyword Match: {keyword} -> {pro['business_name']}")
                return pro

    # 5. Default
    return None

async def get_dynamic_prompt(pro_data):
    if not pro_data: return "You are a general assistant."
    base = pro_data["system_prompt"]
    
    # Inject Availability
    slots_str = await get_available_slots(pro_data["_id"])
    base += f"\n\n*** יומן זמינות בזמן אמת ***\nהצע ללקוח אך ורק את השעות האלו:\n{slots_str}"
    
    instructions = """
*** הנחיות שפה וטון (Persona) ***
1. היה קצר ותמציתי (עד 2 משפטים). בלי הקדמות. השתמש באימוג'י אחד בלבד. 🛠️
2. טון: ישראלי, \"דוגרי\", יעיל ואדיב.

*** מדיניות מחירים והגנה משפטית ***
1. מחיר: ציין עלות ביקור והדגש שהיא מתקזזת בתיקון. 
2. הסתייגות: הבהר שכל מחיר הוא הערכה בלבד והמחיר המחייב ייקבע רק ע\"י איש המקצוע בשטח.
3. תיווך: ציין ש-Fixi היא פלטפורמת תיווך והאחריות על העבודה היא על איש המקצוע בלבד.

*** חילוץ פרטי עסקה [DEAL] ***
פורמט חובה: [DEAL: <יום ושעה> | <עיר/מיקום הלקוח> | <תיאור>]
- תיאור: אם נשלחה מדיה (תמונה/הקלטה), כלול כאן סיכום קצר של מה שראית/שמעת. במקרה של הקלטת אודיו, הוסף גם 'תמלול מלא:' ואחריו את התמלול המלא של השיחה (למשל: 'תיקון דוד - סיכום: אין מים חמים. תמלול מלא: ...').
"""
    base += instructions
    return base

# --- Availability & Pro Command Logic ---
def get_il_time():
    return datetime.now(pytz.timezone('Asia/Jerusalem'))

async def get_available_slots(pro_id):
    now_utc = datetime.now(pytz.utc)
    cursor = slots_collection.find({
        "pro_id": pro_id, "is_taken": False, "start_time": {"$gt": now_utc}
    }).sort("start_time", 1).limit(5)
    
    slots = await cursor.to_list(length=5)
    
    if not slots: return "אין תורים פנויים כרגע."
    
    lines = []
    for s in slots:
        local_start = s['start_time'].replace(tzinfo=pytz.utc).astimezone(IL_TZ)
        if 'end_time' in s and s['end_time']:
            local_end = s['end_time'].replace(tzinfo=pytz.utc).astimezone(IL_TZ)
            fmt_time = f"{local_start.strftime('%d/%m')} בשעות {local_start.strftime('%H:%M')}-{local_end.strftime('%H:%M')}"
        else:
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
    1. BLOCK: "busy at 4", "block 16:00", "hasom 10", "taken".
    2. FREE: "done", "finished", "available", "hitpaneti".
    3. SHOW: "my schedule", "yoman", "matay panuy", "torim".
    4. FINISH_JOB: "finished", "sayamti", "job done", "close ticket", "🏁 סיימתי".
    5. GET_WORK: "get work", "tavi avoda","avoda", "lead".
    6. VACATION: "vacation", "hofesh".
    7. UNKNOWN: regular chat.
    8. CONFIRM: "confirm", "approve", "ok", "measher", "מאשר", "סגור", "לקחתי".

    Logic: If hour < current hour, assume TOMORROW.
    Output JSON: {{ "intent": "...", "hour": int, "day": "TODAY"|"TOMORROW" }}
    """
    try:
        response = await client.models.generate_content(
            model='gemini-2.5-flash-lite',
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )
        return json.loads(response.text)
    except Exception as e:
        logger.error(f"Intent Analysis Error: {e}")
        return {"intent": "UNKNOWN"}

async def handle_pro_command(chat_id: str, text: str):
    clean_phone = chat_id.replace("@c.us", "").replace("+", "").replace("-", "")
    short_phone = clean_phone[-9:] 
    
    pro = await users_collection.find_one({
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
        
        now_il = datetime.now(IL_TZ)
        target_date = now_il
        if parsed.get("day") == "TOMORROW":
            target_date += timedelta(days=1)
            
        target_time_il = target_date.replace(hour=hour, minute=0, second=0, microsecond=0)
        search_utc = target_time_il.astimezone(pytz.utc)
        
        # Async Update
        result = await slots_collection.update_one(
            {
                "pro_id": pro["_id"], 
                "is_taken": False,
                "$or": [
                    {"start_time": {"$gte": search_utc - timedelta(minutes=1), "$lte": search_utc + timedelta(minutes=1)}}, 
                    {"start_time": {"$lte": search_utc}, "end_time": {"$gt": search_utc}}
                ]
            },
            {"$set": {"is_taken": True}}
        )
        
        display_date = target_time_il.strftime("%d/%m")
        if result.modified_count > 0:
            return f"✅ חסמתי לך את {hour}:00 ({display_date})."
        else:
            return f"❌ לא מצאתי תור פנוי ב-{hour}:00 ({display_date}). אולי הוא כבר תפוס?"

    elif intent == "FREE":
        await users_collection.update_one({"_id": pro["_id"]}, {"$set": {"is_available": True}})
        return "👍 מעולה. סימנתי שסיימת ואתה פנוי."

    elif intent == "SHOW":
        now = datetime.now(IL_TZ)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow_end = (today_start + timedelta(days=2)).replace(microsecond=0)
        
        start_utc = today_start.astimezone(pytz.utc)
        end_utc = tomorrow_end.astimezone(pytz.utc)
        
        cursor = leads_collection.find({
            "pro_id": pro["_id"],
            "status": "booked",
            "created_at": {"$gte": start_utc, "$lt": end_utc}
        }).sort("created_at", 1)
        
        booked_leads = await cursor.to_list(length=None)
        
        if not booked_leads:
            return "📅 אין לך עבודות סגורות (Booked) להיום או מחר."
            
        response = "📅 **תוכנית עבודה (היום ומחר):**\n"
        for lead in booked_leads:
            lead_time = lead["created_at"].replace(tzinfo=pytz.utc).astimezone(IL_TZ)
            time_str = lead_time.strftime("%d/%m %H:%M")
            client_phone = lead["chat_id"].replace("@c.us", "")
            details = lead.get("details", "ללא פרטים")
            short_details = (details[:30] + '..') if len(details) > 30 else details
            response += f"\n🔹 {time_str} - {short_details}\n   📞 לקוח: {client_phone}"
        return response
    
    elif intent == "FINISH_JOB":
        last_lead = await leads_collection.find_one({
            "pro_id": pro["_id"],
            "status": {"$in": ["New", "booked", "חדש"]}
        }, sort=[("created_at", -1)])
        
        if not last_lead:
            return "לא מצאתי עבודה פתוחה לסגור."
        
        await leads_collection.update_one(
            {"_id": last_lead["_id"]},
            {"$set": {"status": "completed", "completed_at": datetime.now(timezone.utc), "waiting_for_rating": True}}
        )
        
        client_chat_id = last_lead["chat_id"]
        feedback_msg = f"היי! 👋 איך היה השירות עם {pro['business_name']}? נשמח לדירוג 1-5."
        await send_whatsapp_message(client_chat_id, feedback_msg)        
        return f"✅ העבודה הושלמה! שלחתי בקשת דירוג ללקוח."

    elif intent == "GET_WORK":
        all_new_leads = await leads_collection.find({"status": "New"}).sort("created_at", 1).to_list()
        
        selected_lead = None
        my_areas = [area.strip().lower() for area in pro.get("service_areas", [])]

        if not my_areas:
            return "❌ לא הוגדרו אזורי שירות עבורך. לא ניתן למצוא עבודות."

        for lead in all_new_leads:
            if lead.get("pro_id") == pro["_id"]:
                selected_lead = lead
                break
            
            lead_details_lower = lead.get("details", "").lower()
            if any(area in lead_details_lower for area in my_areas):
                selected_lead = lead
                break
        
        if not selected_lead:
            areas_str = ", ".join(pro.get("service_areas", []))
            return f"📭 אין עבודות חדשות כרגע באזורים שלך ({areas_str})."

        await leads_collection.update_one(
            {"_id": selected_lead["_id"]},
            {"$set": {"status": "booked", "pro_id": pro["_id"]}}
        )
        
        details = selected_lead.get("details", "")
        client_phone = selected_lead['chat_id'].replace('@c.us', '')
        waze_url = f"https://waze.com/ul?q={urllib.parse.quote(details)}"
        
        msg = (
            f"🚀 *מצאתי עבודה באזור שלך!*\\n"
            f"📝 *תיאור:* {details}\\n"
            f"📞 *לקוח:* {client_phone}\\n"
            f"📍 *אזור:* תואם לאזורי השירות שלך\\n"
            f"🚗 *ניווט:* {waze_url}\\n\n"
            f"בהצלחה! סמן 'סיימתי' כשתסיים."
        )
        
        if selected_lead.get("media_url"):
            await send_whatsapp_file(chat_id, selected_lead["media_url"], caption="📷 תמונה מצורפת מהלקוח")
            
        return msg
    
    elif intent == "VACATION":
        now_il = datetime.now(IL_TZ)
        target_date = now_il
        if parsed.get("day") == "TOMORROW":
            target_date += timedelta(days=1)
            
        day_start = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        start_utc = day_start.astimezone(pytz.utc)
        end_utc = day_end.astimezone(pytz.utc)
        
        await slots_collection.update_many(
            {
                "pro_id": pro["_id"],
                "start_time": {"$gte": start_utc, "$lt": end_utc}
            },
            {"$set": {"is_taken": True}}
        )
        
        display_date = day_start.strftime("%d/%m")
        return f"🏝️ חסמתי לך את כל היום ב-{display_date}."
    
    elif intent == "CONFIRM":
        last_new_lead = await leads_collection.find_one(
            {"pro_id": pro["_id"], "status": "New"},
            sort=[("created_at", -1)]
        )

        if not last_new_lead:
            last_booked_lead = await leads_collection.find_one(
                {"pro_id": pro["_id"], "status": "booked"},
                sort=[("created_at", -1)]
            )
            if last_booked_lead:
                return "👍 העבודה כבר אצלך במערכת."
            return "🤔 לא מצאתי עבודה חדשה לאישור."

        await leads_collection.update_one(
            {"_id": last_new_lead["_id"]},
            {"$set": {"status": "booked"}}
        )

        lead_details = last_new_lead.get("details", "")
        time_match = re.search(r"(\d{1,2}:\d{2})", lead_details)
        date_match = re.search(r"(\d{1,2})/(\d{1,2})", lead_details)

        if time_match and date_match:
            try:
                hour_minute_str = time_match.group(1)
                day_str, month_str = date_match.group(1), date_match.group(2)
                
                hour, minute = map(int, hour_minute_str.split(':'))
                day, month = int(day_str), int(month_str)
                
                now_il = datetime.now(IL_TZ)
                target_time_il = now_il.replace(month=month, day=day, hour=hour, minute=minute, second=0, microsecond=0)
                
                if target_time_il < now_il and now_il.month == 12 and target_time_il.month == 1:
                    target_time_il = target_time_il.replace(year=now_il.year + 1)

                search_utc = target_time_il.astimezone(pytz.utc)

                slot_update_result = await slots_collection.update_one(
                    {
                        "pro_id": pro["_id"], 
                        "is_taken": False,
                        "start_time": {"$lte": search_utc}, 
                        "end_time": {"$gt": search_utc}
                    },
                    {"$set": {"is_taken": True}}
                )
                if slot_update_result.modified_count > 0:
                    logger.info(f"Slot at {target_time_il.strftime('%Y-%m-%d %H:%M')} taken for lead {last_new_lead['_id']}")
                else:
                    logger.warning(f"No available slot found to block for lead {last_new_lead['_id']} at {target_time_il}")

            except Exception as e:
                logger.error(f"Error blocking slot for lead {last_new_lead['_id']}: {e}")

        return "👍 מעולה! העבודה אצלך. שלחתי פרטים ללקוח."

    return None

async def handle_customer_completion_response(chat_id: str, text: str):
    text = text.strip()
    if "כן, הסתיים" not in text:
        return None

    lead = await leads_collection.find_one({
        "chat_id": chat_id,
        "status": "booked"
    }, sort=[("created_at", -1)])

    if not lead:
        return None

    pro = await users_collection.find_one({"_id": lead["pro_id"]})
    if not pro:
        logger.warning(f"Could not find pro for lead {lead['_id']} during customer completion confirmation.")
        return "תודה על העדכון, אבל היתה בעיה בעדכון המערכת."

    await leads_collection.update_one(
        {"_id": lead["_id"]},
        {
            "$set": {
                "status": "completed",
                "completed_at": datetime.now(timezone.utc),
                "waiting_for_rating": True
            }
        }
    )
    logger.success(f"✅ Lead {lead['_id']} marked as COMPLETED by customer.")

    if pro.get("phone_number"):
        pro_chat_id = f"{pro['phone_number']}@c.us" if not pro['phone_number'].endswith('@c.us') else pro['phone_number']
        await send_whatsapp_message(pro_chat_id, "👍 הלקוח דיווח שהעבודה הסתיימה. הסטטוס עודכן.")

    feedback_msg = f"מעולה! שמחים לשמוע. איך היה השירות עם {pro.get('business_name', 'המקצוען')}? נשמח אם תדרגו אותו מ-1 (גרוע) עד 5 (מעולה)."
    return feedback_msg


async def handle_customer_rating(chat_id: str, text: str):
    text = text.strip()
    if text not in ["1", "2", "3", "4", "5"]:
        return None
        
    rating = int(text)
    
    lead = await leads_collection.find_one({
        "chat_id": chat_id,
        "waiting_for_rating": True
    })
    
    if not lead:
        return None
        
    pro_id = lead["pro_id"]
    pro = await users_collection.find_one({"_id": pro_id})
    
    current_rating = pro.get("social_proof", {}).get("rating", 5.0)
    count = pro.get("social_proof", {}).get("review_count", 0)
    new_rating = round(((current_rating * 10) + rating) / 11, 1) 
    
    await users_collection.update_one(
        {"_id": pro_id},
        {"$set": {"social_proof.rating": new_rating, "social_proof.review_count": count + 1}}
    )
    
    await leads_collection.update_one(
        {"_id": lead["_id"]},
        {"$set": {"waiting_for_rating": False, "rating_given": rating}}
    )
    
    logger.success(f"⭐ Rating {rating} saved for {pro['business_name']}")
    return "תודה רבה על הדירוג! ⭐"

async def download_and_store_media(url: str):
    logger.info(f"📥 Downloading media...")
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url)
            resp.raise_for_status()
            suffix = ".tmp"
            ct = resp.headers.get("content-type", "")
            if "image" in ct: suffix = ".jpg"
            elif "audio" in ct: suffix = ".ogg"
            elif "video" in ct: suffix = ".mp4"
            
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(resp.content)
                temp_path = tmp.name
        
        secure_url = None
        try:
            logger.info(f"☁️ Uploading to Cloudinary...")
            import asyncio
            upload_result = await asyncio.to_thread(cloudinary.uploader.upload, temp_path, resource_type="auto")
            secure_url = upload_result.get("secure_url")
        except Exception as cloud_err:
            logger.warning(f"☁️ Cloudinary Upload Failed: {cloud_err}")
            
        return temp_path, secure_url
            
    except Exception as e:
        logger.error(f"Media Processing Error: {traceback.format_exc()}")
        return None, None

async def handle_new_lead(chat_id: str, details: str, pro_data: dict, media_url: str = None):
    time_match = re.search(r"(\d{1,2}:\d{2})", details)

    logger.success(f"💰 NEW LEAD! {details}")
    await leads_collection.insert_one({
        "chat_id": chat_id, "pro_id": pro_data["_id"], "details": details, 
        "status": "New", "created_at": datetime.now(timezone.utc), "media_url": media_url
    })
    
    if pro_data.get("phone_number"):
        pro_chat = f"{pro_data['phone_number']}@c.us" if not pro_data['phone_number'].endswith("@c.us") else pro_data['phone_number']
        msg = f"""
🔔 *ליד חדש!*
פרטים: {details}
לקוח: https://wa.me/{chat_id.replace('@c.us','')}"""
        
        await send_whatsapp_message(pro_chat, msg)
        if media_url:
            await send_whatsapp_file(pro_chat, media_url, caption="מדיה מצורפת")

async def ask_fixi_ai(user_text: str, chat_id: str, media_url: str = None) -> str:
    completion_resp = await handle_customer_completion_response(chat_id, user_text or "")
    if completion_resp: return completion_resp

    rating_resp = await handle_customer_rating(chat_id, user_text or "")
    if rating_resp: return rating_resp

    pro_response = await handle_pro_command(chat_id, user_text or "")
    if pro_response: return pro_response 
        
    try:
        history, last_pro_id = await get_chat_history(chat_id)
        
        temp_path = None
        cloudinary_url = None
        if media_url:
            temp_path, cloudinary_url = await download_and_store_media(media_url)

        current_pro = await determine_current_pro(user_text or "", last_pro_id)
        
        generic_sys_prompt = """Role: Service Dispatcher. Name: Fixi.
Goal: Ask for Location and Problem briefly to find the right professional.
Tone: Human, direct, efficient. Hebrew language.
Example response: 'היי, באיזו עיר אתם נמצאים ומה התקלה?'"""

        sys_prompt = ""
        pro_id_for_message = None

        if not current_pro:
            sys_prompt = generic_sys_prompt
            pro_id_for_message = None
        else:
            sys_prompt = await get_dynamic_prompt(current_pro)
            pro_id_for_message = current_pro["_id"]

        log_text = user_text or "[Media Message]"
        if cloudinary_url: log_text += f" (URL: {cloudinary_url})"
        await save_message(chat_id, "user", log_text, pro_id_for_message)
        
        legal_welcome = "היי! אני פיקסי. 👋 חשוב שתדעו: פיקסי היא פלטפורמת תיווך בלבד. האחריות על ביצוע העבודה והתשלום היא בינך לבין איש המקצוע. בהמשך השיחה אתה מאשר את תנאי השימוש שלנו. \n\nבאיזו עיר אתם ומה התקלה? 🔧"
        
        # Build contents for new API
        contents = []
        
        # 1. Add History
        for msg in history:
            contents.append(types.Content(
                role=msg["role"],
                parts=[types.Part(text=p) for p in msg.get("parts", [])]
            ))

        # 2. Add New Message Parts
        new_parts = []
        if temp_path and os.path.exists(temp_path):
            try:
                # Use client.files.upload
                uploaded_file = await client.files.upload(path=temp_path)
                
                new_parts.append(types.Part(
                    file_data=types.FileData(file_uri=uploaded_file.uri, mime_type=uploaded_file.mime_type)
                ))
                
                if temp_path.endswith(".ogg"): 
                    new_parts.append(types.Part(text="המשתמש שלח הקלטה. הקשב וענה."))
                else: 
                    new_parts.append(types.Part(text="המשתמש שלח תמונה. נתח אותה."))
            except Exception as e: logger.error(f"Gemini Upload Error: {e}")

        if user_text:
            new_parts.append(types.Part(text=user_text))

        if not new_parts: 
            return "סליחה, הייתה בעיה בקבלת הקובץ."

        contents.append(types.Content(role="user", parts=new_parts))

        # 3. Generate
        config = types.GenerateContentConfig(system_instruction=sys_prompt)
        response = await _generate_with_retry('gemini-2.0-flash-lite', contents, config)
        reply_text = response.text.strip()
        
        if temp_path and os.path.exists(temp_path): os.remove(temp_path)

        if len(history) == 0:
            reply_text = legal_welcome + "\n\n" + reply_text

        deal_match = re.search(r"(\[DEAL:.*?\])", reply_text)
        if deal_match:
            lead_details = deal_match.group(1).strip()
            final_media_url = cloudinary_url if cloudinary_url else media_url
            await handle_new_lead(chat_id, lead_details, current_pro, media_url=final_media_url)
            reply_text = reply_text.replace(deal_match.group(0), "").strip()
            if not reply_text: reply_text = "✅ מעולה! התור נקבע בהצלחה."

        await save_message(chat_id, "model", reply_text, pro_id_for_message)
        return reply_text
    
    except tenacity.RetryError:
        logger.warning(f"⚠️ AI Rate Limit Reached")
        return "המערכת עמוסה. נסה שוב בעוד דקה."
    except Exception as e:
        logger.error(f"❌ AI Error: {traceback.format_exc()}")
        return "סליחה, נתקעתי. נסה שוב."

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
async def send_whatsapp_message(to_chat_id: str, text: str):
    url = f"https://api.green-api.com/waInstance{settings.GREEN_API_ID}/sendMessage/{settings.GREEN_API_TOKEN}"
    payload = {"chatId": to_chat_id, "message": text}
    async with httpx.AsyncClient() as client:
        await client.post(url, json=payload)

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
async def send_whatsapp_file(to_chat_id: str, file_url: str, caption: str = ""):
    url = f"https://api.green-api.com/waInstance{settings.GREEN_API_ID}/sendFileByUrl/{settings.GREEN_API_TOKEN}"
    payload = {"chatId": to_chat_id, "urlFile": file_url, "fileName": "media_file", "caption": caption}
    async with httpx.AsyncClient() as client:
        await client.post(url, json=payload)    

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
async def send_whatsapp_buttons(to_chat_id: str, text: str, buttons: list):
    """
    Sends interactive buttons via Green API.
    'buttons' should be a list of dicts: [{"buttonId": "1", "buttonText": "Text"}]
    """
    url = f"https://api.green-api.com/waInstance{settings.GREEN_API_ID}/sendButtons/{settings.GREEN_API_TOKEN}"
    payload = {
        "chatId": to_chat_id,
        "message": text,
        "buttons": buttons
    }
    async with httpx.AsyncClient() as client:
        await client.post(url, json=payload)

async def send_whatsapp(chat_id: str, text: str):
    await send_whatsapp_message(chat_id, text)

async def send_pro_reminder(lead_id: str, triggered_by: str = "auto"):
    """Sends a reminder to the pro to mark a job as finished."""
    try:
        lead = await leads_collection.find_one({"_id": ObjectId(lead_id)})
        if not lead or lead.get("status") != "booked":
            logger.warning(f"send_pro_reminder called for invalid/non-booked lead: {lead_id}")
            return

        pro = await users_collection.find_one({"_id": lead["pro_id"]})
        if not pro or not pro.get("phone_number"):
            logger.error(f"Could not find pro or pro phone for lead {lead_id}")
            return

        pro_chat_id = f"{pro['phone_number']}@c.us" if not pro['phone_number'].endswith('@c.us') else pro['phone_number']
        message = "👋 היי, רק מוודא לגבי העבודה האחרונה. האם סיימת?"
        buttons = [
            {"buttonId": f"finish_job_confirm_{lead_id}", "buttonText": "🏁 סיימתי"},
            {"buttonId": f"finish_job_deny_{lead_id}", "buttonText": "⏳ עדיין עובד"}
        ]
        await send_whatsapp_buttons(pro_chat_id, message, buttons)
        logger.success(f"Sent pro reminder for lead {lead_id} (Trigger: {triggered_by})")
    except Exception as e:
        logger.error(f"Error in send_pro_reminder for lead {lead_id}: {e}")

async def send_customer_completion_check(lead_id: str, triggered_by: str = "auto"):
    """Asks the customer if the job has been completed."""
    try:
        lead = await leads_collection.find_one({"_id": ObjectId(lead_id)})
        if not lead or lead.get("status") != "booked":
            logger.warning(f"send_customer_completion_check called for invalid/non-booked lead: {lead_id}")
            return

        customer_chat_id = lead["chat_id"]
        pro = await users_collection.find_one({"_id": lead["pro_id"]})
        pro_name = pro.get("business_name", "איש המקצוע") if pro else "איש המקצוע"

        message = f"היי! 👋 אנחנו ב-Fixi רוצים לוודא שהכל תקין עם השירות מ-{pro_name}. האם העבודה הסתיימה לשביעות רצונך?"
        buttons = [
            {"buttonId": f"customer_confirm_completion_{lead_id}", "buttonText": "✅ כן, הסתיים"},
            {"buttonId": f"customer_deny_completion_{lead_id}", "buttonText": "❌ עדיין לא"}
        ]
        await send_whatsapp_buttons(customer_chat_id, message, buttons)
        logger.success(f"Sent customer completion check for lead {lead_id} (Trigger: {triggered_by})")
    except Exception as e:
        logger.error(f"Error in send_customer_completion_check for lead {lead_id}: {e}")
