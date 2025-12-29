from app.services.whatsapp_client import WhatsAppClient
from app.services.ai_engine import AIEngine, AIResponse
from app.services.lead_manager import LeadManager
from app.core.logger import logger
from app.core.database import users_collection, leads_collection
from bson import ObjectId
from datetime import datetime, timezone
import re
import httpx

# Initialize services
whatsapp = WhatsAppClient()
ai = AIEngine()
lead_manager = LeadManager()

async def determine_best_pro(issue_type: str = None, location: str = None) -> dict:
    """
    Intelligent Routing Engine:
    1. Active Status
    2. Location Match (if applicable)
    3. Rating (High to Low)
    4. Availability (Load Balancing)
    """
    try:
        # 1. Fetch all active pros
        query = {"is_active": True}
        
        cursor = users_collection.find(query)
        pros = await cursor.to_list(length=100)
        
        if not pros:
            return None

        # 2. Location Filtering
        matching_pros = []
        if location:
            for pro in pros:
                areas = pro.get("service_areas", []) # List of strings
                # Loose matching: check if location string contains area or vice versa
                if any(area in location or location in area for area in areas):
                    matching_pros.append(pro)
        
        # If no location match found, fall back to all active pros (or strictly return None?)
        # For Smart Dispatcher, if we have a location but no pro matches, we might want to tell the user?
        # But sticking to "Fallback to all" for MVP stability unless strictly requested otherwise.
        if not matching_pros:
            matching_pros = pros

        # 3. Sort by Rating (Descending)
        def get_rating(p):
            return p.get("social_proof", {}).get("rating", 0)
        
        matching_pros.sort(key=get_rating, reverse=True)

        # 4. Load Balancing
        MAX_LOAD = 3 
        selected_pro = None
        
        for pro in matching_pros:
            current_load = await leads_collection.count_documents({
                "pro_id": pro["_id"],
                "status": "booked"
            })
            
            if current_load < MAX_LOAD:
                selected_pro = pro
                break
        
        if not selected_pro and matching_pros:
            selected_pro = matching_pros[0]

        return selected_pro

    except Exception as e:
        logger.error(f"Error in determine_best_pro: {e}")
        return None

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
        message = "👋 היי, רק מוודא לגבי העבודה האחרונה. האם סיימת? \nהשב 'סיימתי' לאישור או 'עדיין עובד' לעדכון."
        
        await whatsapp.send_message(pro_chat_id, message)
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

        message = f"היי! 👋 אנחנו ב-Fixi רוצים לוודא שהכל תקין עם השירות מ-{pro_name}. האם העבודה הסתיימה לשביעות רצונך?\nהשב 'כן' לאישור או 'לא' אם טרם הסתיים."
        
        await whatsapp.send_message(customer_chat_id, message)
        logger.success(f"Sent customer completion check for lead {lead_id} (Trigger: {triggered_by})")
    except Exception as e:
        logger.error(f"Error in send_customer_completion_check for lead {lead_id}: {e}")

async def handle_customer_completion_text(chat_id: str, text: str):
    """Checks if the user confirmed job completion via text."""
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
    logger.success(f"✅ Lead {lead['_id']} marked as COMPLETED by customer (Text).")

    if pro and pro.get("phone_number"):
        pro_chat_id = f"{pro['phone_number']}@c.us" if not pro['phone_number'].endswith('@c.us') else pro['phone_number']
        await whatsapp.send_message(pro_chat_id, "👍 הלקוח דיווח שהעבודה הסתיימה. הסטטוס עודכן.")

    pro_name = pro.get('business_name', 'המקצוען') if pro else 'המקצוען'
    return f"מעולה! שמחים לשמוע. איך היה השירות עם {pro_name}? נשמח אם תדרגו אותו מ-1 (גרוע) עד 5 (מעולה)."

async def handle_customer_rating_text(chat_id: str, text: str):
    """Checks if the user sent a rating (1-5)."""
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
    
    business_name = pro['business_name'] if pro else "איש המקצוע"
    logger.success(f"⭐ Rating {rating} saved for {business_name}")
    return "תודה רבה על הדירוג! ⭐"

async def handle_pro_text_command(chat_id: str, text: str):
    """
    Handles text commands from Professionals:
    - 'אשר' / '1': Approve newest pending lead.
    - 'דחה' / '2': Reject newest pending lead.
    - 'סיימתי' / '3': Mark newest booked lead as completed.
    """
    # Identify Pro
    phone = chat_id.replace("@c.us", "")
    pro = await users_collection.find_one({"phone_number": {"$in": [phone, chat_id]}})
    if not pro:
        return None 

    text = text.strip().lower()
    
    APPROVE_KEYS = ["אשר", "1", "approve"]
    REJECT_KEYS = ["דחה", "2", "reject"]
    FINISH_KEYS = ["סיימתי", "3", "finish", "done"]

    response_text = None

    if text in APPROVE_KEYS:
        lead = await leads_collection.find_one(
            {"pro_id": pro["_id"], "status": "new"},
            sort=[("created_at", -1)]
        )
        if lead:
            await lead_manager.update_lead_status(str(lead["_id"]), "booked", pro["_id"])
            response_text = "✅ העבודה אושרה! שלחתי ללקוח את הפרטים שלך."
            
            pro_name = pro.get('business_name', 'מומחה')
            pro_phone = pro.get('phone_number', '').replace('972', '0')
            customer_msg = f"🎉 נמצא איש מקצוע! {pro_name} בדרך אליך. 📞 טלפון: {pro_phone}"
            await whatsapp.send_message(lead["chat_id"], customer_msg)
        else:
            # Silent fail or info? "No pending job found" might be annoying if they type generic text.
            # But specific keywords usually mean intent.
            response_text = "לא מצאתי עבודה חדשה לאישור."

    elif text in REJECT_KEYS:
        lead = await leads_collection.find_one(
            {"pro_id": pro["_id"], "status": "new"},
            sort=[("created_at", -1)]
        )
        if lead:
            await lead_manager.update_lead_status(str(lead["_id"]), "rejected")
            response_text = "העבודה נדחתה. נחפש איש מקצוע אחר."
        else:
            response_text = "לא מצאתי עבודה חדשה לדחייה."

    elif text in FINISH_KEYS:
        lead = await leads_collection.find_one(
            {"pro_id": pro["_id"], "status": "booked"},
            sort=[("created_at", -1)]
        )
        if lead:
            await leads_collection.update_one(
                {"_id": lead["_id"]},
                {"$set": {
                    "status": "completed", 
                    "completed_at": datetime.now(timezone.utc), 
                    "waiting_for_rating": True
                }}
            )
            response_text = "✅ עודכן שהעבודה הסתיימה. תודה!"
            
            feedback_msg = f"היי! 👋 איך היה השירות עם {pro.get('business_name')}? נשמח לדירוג 1-5."
            await whatsapp.send_message(lead["chat_id"], feedback_msg)
        else:
            response_text = "לא מצאתי עבודה פעילה לסיום."

    return response_text

async def process_incoming_message(chat_id: str, user_text: str, media_url: str = None):
    # 0. Check for Pro Text Command
    if user_text:
        pro_resp = await handle_pro_text_command(chat_id, user_text)
        if pro_resp:
            await whatsapp.send_message(chat_id, pro_resp)
            return

    # 1. Log User Message
    log_text = user_text
    if media_url:
        log_text = f"{user_text or ''} [MEDIA: {media_url}]"
    await lead_manager.log_message(chat_id, "user", log_text)

    # 2. Check for Customer Completion or Rating (Priority over AI)
    if user_text:
        completion_resp = await handle_customer_completion_text(chat_id, user_text)
        if completion_resp:
            await whatsapp.send_message(chat_id, completion_resp)
            await lead_manager.log_message(chat_id, "model", completion_resp)
            return

        rating_resp = await handle_customer_rating_text(chat_id, user_text)
        if rating_resp:
            await whatsapp.send_message(chat_id, rating_resp)
            await lead_manager.log_message(chat_id, "model", rating_resp)
            return

    # 3. Handle Media (Download if present)
    media_data = None
    media_mime = None
    if media_url:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(media_url)
                if resp.status_code == 200:
                    media_data = resp.content
                    media_mime = resp.headers.get("Content-Type", "image/jpeg")
                    logger.info(f"Downloaded media: {len(media_data)} bytes, type: {media_mime}")
                else:
                    logger.warning(f"Failed to download media from {media_url}, status: {resp.status_code}")
        except Exception as e:
            logger.error(f"Error downloading media: {e}")

    # 4. Smart Dispatcher Phase
    history = await lead_manager.get_chat_history(chat_id)
    
    dispatcher_prompt = """
    You are Fixi's Smart Dispatcher. 
    Your goal is to identify the customer's City and Issue Description.
    
    - If audio is present, trust the transcription.
    - If City or Issue is missing, ask the user specifically for them.
    - If both are present, extract them.
    
    Tone: Polite, helpful, Israeli Hebrew.
    """

    dispatcher_response: AIResponse = await ai.analyze_conversation(
        history=history, 
        user_text=user_text or "", 
        custom_system_prompt=dispatcher_prompt,
        media_data=media_data,
        media_mime_type=media_mime,
        require_json=True
    )
    
    extracted_city = dispatcher_response.extracted_data.city
    extracted_issue = dispatcher_response.extracted_data.issue
    transcription = dispatcher_response.transcription
    
    logger.info(f"Dispatcher analysis: City={extracted_city}, Issue={extracted_issue}, Transcr={transcription}")

    # 5. Logic Gate: Dispatcher vs Professional
    best_pro = None
    pro_response_obj = None

    # Track the active lead ID if we find/create one
    current_lead_id = None

    if extracted_city and extracted_issue:
        # Check for existing active lead
        active_lead = await leads_collection.find_one({
            "chat_id": chat_id,
            "status": {"$in": ["new", "contacted"]} 
        }, sort=[("created_at", -1)])

        if active_lead:
            current_lead_id = active_lead["_id"]
            # Optionally update details if they changed?
            # For now, we assume the conversation continues on this lead.
        else:
            # Create a provisional lead
            new_lead = await lead_manager.create_lead_from_dict(
                chat_id=chat_id,
                issue_type=extracted_issue,
                full_address=extracted_city, # Provisional address is just city
                status="contacted", # In progress
                appointment_time="Pending"
            )
            if new_lead:
                current_lead_id = new_lead["_id"]

        # Sufficient data gathered -> Find Pro
        best_pro = await determine_best_pro(issue_type=extracted_issue, location=extracted_city)
        
        if best_pro:
            # Update the lead with the pro candidate (even if not booked yet)
            if current_lead_id:
                await leads_collection.update_one(
                    {"_id": current_lead_id}, 
                    {"$set": {"pro_id": best_pro["_id"]}}
                )

            # Switch to Pro Persona
            pro_name = best_pro.get("business_name", "Fixi Pro")
            price_list = best_pro.get("price_list", "")
            base_system_prompt = best_pro.get("system_prompt", f"You are Fixi, an AI scheduler for {pro_name}.")
            
            full_system_prompt = f"""
{base_system_prompt}

You are representing '{pro_name}'.

*** PRICING / SERVICES ***
{price_list}

*** CONTEXT ***
Customer is located in: {extracted_city}
Issue: {extracted_issue}
Transcription (if any): {transcription or "None"}

*** CORE INSTRUCTIONS ***
1. Acknowledge the issue and location.
2. If you need the full street address for the booking, ask for it.
3. If the user provided a full address and time, SET 'is_deal' to true in the JSON output and fill 'full_address' and 'appointment_time'.
4. Output JSON matching the schema.

Tone: Professional, efficient, Israeli Hebrew.
            """
            
            # Call AI again with Pro Persona
            pro_response_obj = await ai.analyze_conversation(
                history=history,
                user_text=user_text or "", # Re-eval user text with Pro context
                custom_system_prompt=full_system_prompt,
                require_json=True
            )
        else:
            # Fallback if no pro matches (unlikely given logic)
            pass

    # Select which response to send
    final_response = pro_response_obj if best_pro else dispatcher_response
    
    # Send Message to User
    await whatsapp.send_message(chat_id, final_response.reply_to_user)
    await lead_manager.log_message(chat_id, "model", final_response.reply_to_user)

    # 6. Check for [DEAL] or Structured Booking
    is_deal = final_response.is_deal
    
    # Fallback: check for [DEAL] string in reply if model hallucinated format
    deal_string_match = re.search(r"\[DEAL:.*?\]", final_response.reply_to_user)
    if deal_string_match:
        is_deal = True
    
    if is_deal and best_pro:
        # Finalize details
        d_time = final_response.extracted_data.appointment_time or "As soon as possible"
        d_address = final_response.extracted_data.full_address or extracted_city or "Unknown Address"
        d_issue = final_response.extracted_data.issue or extracted_issue or "Issue"
        
        # If we have a current_lead, update it. If not, create new.
        if current_lead_id:
            await leads_collection.update_one(
                {"_id": current_lead_id},
                {"$set": {
                    "status": "new", # Ready for Pro
                    "appointment_time": d_time,
                    "full_address": d_address,
                    "issue_type": d_issue,
                    "pro_id": best_pro["_id"]
                }}
            )
            lead = await leads_collection.find_one({"_id": current_lead_id})
        else:
            # Should rarely happen if Step 5 worked, but safe fallback
            lead = await lead_manager.create_lead_from_dict(
                chat_id=chat_id,
                issue_type=d_issue,
                full_address=d_address,
                appointment_time=d_time,
                status="new",
                pro_id=best_pro["_id"]
            )
        
        if lead:
            pro_phone = best_pro.get("phone_number")
            if pro_phone:
                if not pro_phone.endswith("@c.us"):
                    pro_phone = f"{pro_phone}@c.us"                
                
                msg_to_pro = f"""📢 *הצעת עבודה חדשה!*

📍 *כתובת:* {lead['full_address']}
🛠️ *תקלה:* {lead['issue_type']}
⏰ *זמן מועדף:* {lead['appointment_time']}"""

                if transcription:
                    msg_to_pro += f"\n🎙️ *תמליל:* {transcription}"
                
                # Send Text Options
                msg_to_pro += "\n\nהשב 'אשר' לקבלת העבודה או 'דחה' לדחייה."
                await whatsapp.send_message(pro_phone, msg_to_pro)
                
                # Send Waze Link
                await whatsapp.send_location_link(pro_phone, lead['full_address'], "🚗 נווט לכתובת:")

