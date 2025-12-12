import httpx
import google.generativeai as genai
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
    wait=wait_random_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3)
)
async def _generate_with_retry(chat_session, content_parts):
    return await chat_session.send_message_async(content_parts)

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
        formatted.append({"role": "user" if msg["role"] == "user" else "model", "parts": [msg["text"]]})
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
    # Note: Fetching all active pros to scan areas (could be optimized with DB query, but acceptable for small scale)
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
    return await users_collection.find_one({"business_name": "יוסי אינסטלציה"})

async def get_dynamic_prompt(pro_data):
    if not pro_data: return "You are a general assistant."
    base = pro_data["system_prompt"]
    
    # Inject Availability
    slots_str = await get_available_slots(pro_data["_id"])
    base += f"\n\n*** יומן זמינות בזמן אמת ***\nהצע ללקוח אך ורק את השעות האלו:\n{slots_str}"
    
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
    4. FINISH_JOB: "finished", "sayamti", "job done", "close ticket".
    5. GET_WORK: "get work", "tavi avoda","avoda", "lead".
    6. VACATION: "vacation", "hofesh".
    7. UNKNOWN: regular chat.
    
    Logic: If hour < current hour, assume TOMORROW.
    Output JSON: {{ "intent": "...", "hour": int, "day": "TODAY"|"TOMORROW" }}
    """
    try:
        model = genai.GenerativeModel('gemini-2.5-flash', generation_config={"response_mime_type": "application/json"})
        response = await model.generate_content_async(prompt)
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
        lead = await leads_collection.find_one({"status": "New"}, sort=[("created_at", 1)])
        
        if not lead:
            return "📭 אין עבודות חדשות כרגע."
            
        await leads_collection.update_one(
            {"_id": lead["_id"]},
            {"$set": {"status": "booked", "pro_id": pro["_id"]}}
        )
        
        lead_time = lead["created_at"].replace(tzinfo=pytz.utc).astimezone(IL_TZ)
        time_str = lead_time.strftime("%d/%m %H:%M")
        details = lead.get("details", "ללא פרטים")
        client_phone = lead["chat_id"].replace("@c.us", "")
        
        msg = f"🚀 **קיבלת עבודה חדשה!**\n📅 {time_str}\n📝 {details}\n📞 לקוח: {client_phone}"
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

    return None

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
            # Running synchronous Cloudinary upload in threadpool if needed, 
            # but for simplicity in this refactor keeping it direct or using asyncio.to_thread is better practice.
            # Assuming basic sync usage is acceptable for MVP, but to be strictly non-blocking:
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
    rating_resp = await handle_customer_rating(chat_id, user_text or "")
    if rating_resp: return rating_resp

    pro_response = await handle_pro_command(chat_id, user_text or "")
    if pro_response: return pro_response 
        
    try:
        history, last_pro_id = await get_chat_history(chat_id)
        current_pro = await determine_current_pro(user_text or "", last_pro_id)
        
        if not current_pro: return "שגיאה: לא נמצא איש שירות פעיל."

        temp_path = None
        cloudinary_url = None
        if media_url:
            temp_path, cloudinary_url = await download_and_store_media(media_url)

        log_text = user_text or "[Media Message]"
        if cloudinary_url: log_text += f" (URL: {cloudinary_url})"
        await save_message(chat_id, "user", log_text, current_pro["_id"])
        
        sys_prompt = await get_dynamic_prompt(current_pro)
        model = genai.GenerativeModel('gemini-2.0-flash', system_instruction=sys_prompt)
        chat = model.start_chat(history=history)
        
        content_parts = []
        if temp_path and os.path.exists(temp_path):
            try:
                uploaded_file = await asyncio.to_thread(genai.upload_file, temp_path)
                content_parts.append(uploaded_file)
                if temp_path.endswith(".ogg"): content_parts.append("המשתמש שלח הקלטה. הקשב וענה.")
                else: content_parts.append("המשתמש שלח תמונה. נתח אותה.")
            except Exception as e: logger.error(f"Gemini Upload Error: {e}")

        if user_text: content_parts.append(user_text)

        if not content_parts: return "סליחה, הייתה בעיה בקבלת הקובץ."

        response = await _generate_with_retry(chat, content_parts)
        reply_text = response.text.strip()
        
        if temp_path and os.path.exists(temp_path): os.remove(temp_path)

        deal_match = re.search(r"\[DEAL:(.*?)\]", reply_text)
        if deal_match:
            lead_details = deal_match.group(1).strip()
            final_media_url = cloudinary_url if cloudinary_url else media_url
            await handle_new_lead(chat_id, lead_details, current_pro, media_url=final_media_url)
            reply_text = reply_text.replace(deal_match.group(0), "").strip()
            if not reply_text: reply_text = "✅ מעולה! התור נקבע בהצלחה."

        await save_message(chat_id, "model", reply_text, current_pro["_id"])
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

async def send_whatsapp(chat_id: str, text: str):
    await send_whatsapp_message(chat_id, text)
