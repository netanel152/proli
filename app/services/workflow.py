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
        message = "👋 היי, רק מוודא לגבי העבודה האחרונה. האם סיימת?"
        buttons = [
            {"buttonId": f"finish_job_confirm_{lead_id}", "buttonText": "🏁 סיימתי"},
            {"buttonId": f"finish_job_deny_{lead_id}", "buttonText": "⏳ עדיין עובד"}
        ]
        await whatsapp.send_buttons(pro_chat_id, message, buttons)
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
        await whatsapp.send_buttons(customer_chat_id, message, buttons)
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

async def process_incoming_message(chat_id: str, user_text: str, media_url: str = None):
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

    if extracted_city and extracted_issue:
        # Sufficient data gathered -> Find Pro
        best_pro = await determine_best_pro(issue_type=extracted_issue, location=extracted_city)
        
        if best_pro:
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
        # If we need to parse it back from string, we can, but let's prefer structured if available.
        # But if is_deal is true from structure, we construct the deal string for create_lead
    
    if is_deal and best_pro:
        # Construct deal string for LeadManager (legacy support) or just pass fields
        # Ideally LeadManager should be updated, but we stick to constraints.
        # We construct the string expected by LeadManager: [DEAL: Time | Address | Issue]
        
        d_time = final_response.extracted_data.appointment_time or "As soon as possible"
        d_address = final_response.extracted_data.full_address or extracted_city or "Unknown Address"
        d_issue = final_response.extracted_data.issue or extracted_issue or "Issue"
        
        deal_string = f"[DEAL: {d_time} | {d_address} | {d_issue}]"
        
        lead = await lead_manager.create_lead(deal_string, chat_id, pro_id=best_pro["_id"])
        
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
                
                # Send Buttons
                buttons = [
                    {"buttonId": f"approve_{lead['_id']}", "buttonText": "אשר עבודה"},
                    {"buttonId": f"reject_{lead['_id']}", "buttonText": "דחה"}
                ]
                await whatsapp.send_buttons(pro_phone, msg_to_pro, buttons)
                
                # Send Waze Link
                await whatsapp.send_location_link(pro_phone, lead['full_address'], "🚗 נווט לכתובת:")


async def handle_pro_response(payload: dict):
    """
    Handles button clicks from the Pro or Customer.
    """
    msg_data = payload.get("messageData", {})
    button_reply = msg_data.get("buttonsResponseMessage", {})
    button_id = button_reply.get("selectedButtonId")
    sender = payload.get("senderData", {}).get("chatId")

    if not button_id:
        return

    logger.info(f"Button Clicked: {button_id} by {sender}")

    if button_id.startswith("approve_"):
        lead_id = button_id.replace("approve_", "")
        lead = await lead_manager.get_lead_by_id(lead_id)
        
        if lead:
            # Update Lead
            pro_phone_clean = sender.replace("@c.us", "")
            pro = await users_collection.find_one({"phone_number": pro_phone_clean})
            pro_id = pro["_id"] if pro else None
            
            await lead_manager.update_lead_status(lead_id, "booked", pro_id)
            
            # Notify Pro
            await whatsapp.send_message(sender, "✅ העבודה אושרה! שלחתי ללקוח את הפרטים שלך.")
            
            # Notify Customer
            pro_name = pro.get('business_name', 'מומחה')
            pro_phone = pro.get('phone_number', '').replace('972', '0')
            
            customer_msg = f"""🎉 נמצא איש מקצוע! {pro_name} בדרך אליך. 📞 טלפון: {pro_phone} ⭐ דירוג: 4.9/5"""
            await whatsapp.send_message(lead["chat_id"], customer_msg)

    elif button_id.startswith("reject_"):
        lead_id = button_id.replace("reject_", "")
        await lead_manager.update_lead_status(lead_id, "rejected")
        await whatsapp.send_message(sender, "העבודה נדחתה. נחפש איש מקצוע אחר.")
    
    elif button_id.startswith("finish_job_confirm_"):
        lead_id = button_id.replace("finish_job_confirm_", "")
        
        lead = await leads_collection.find_one({"_id": ObjectId(lead_id)})
        if lead:
             await leads_collection.update_one(
                {"_id": lead["_id"]},
                {"$set": {
                    "status": "completed", 
                    "completed_at": datetime.now(timezone.utc), 
                    "waiting_for_rating": True
                }}
            )
             await whatsapp.send_message(sender, "✅ עודכן שהעבודה הסתיימה. תודה!")
             
             # Ask Customer for feedback
             pro = await users_collection.find_one({"_id": lead["pro_id"]})
             pro_name = pro.get('business_name', 'המקצוען') if pro else 'המקצוען'
             feedback_msg = f"היי! 👋 איך היה השירות עם {pro_name}? נשמח לדירוג 1-5."
             await whatsapp.send_message(lead["chat_id"], feedback_msg)

    elif button_id.startswith("finish_job_deny_"):
        await whatsapp.send_message(sender, "👍 אין בעיה. תעדכן כשתסיים.")
    
    elif button_id.startswith("customer_confirm_completion_"):
         # Treat as "כן, הסתיים"
         lead_id = button_id.replace("customer_confirm_completion_", "")
         lead = await leads_collection.find_one({"_id": ObjectId(lead_id)})
         if lead:
             await leads_collection.update_one(
                {"_id": lead["_id"]},
                {"$set": {
                    "status": "completed", 
                    "completed_at": datetime.now(timezone.utc), 
                    "waiting_for_rating": True
                }}
            )
             # Notify Pro
             pro = await users_collection.find_one({"_id": lead["pro_id"]})
             if pro:
                 pro_chat = f"{pro['phone_number']}@c.us" if not pro['phone_number'].endswith('@c.us') else pro['phone_number']
                 await whatsapp.send_message(pro_chat, "👍 הלקוח אישר שהעבודה הסתיימה.")
                 
                 # Ask for rating
                 feedback_msg = f"מעולה! שמחים לשמוע. איך היה השירות עם {pro['business_name']}? נשמח אם תדרגו אותו מ-1 (גרוע) עד 5 (מעולה)."
                 await whatsapp.send_message(sender, feedback_msg)

    elif button_id.startswith("customer_deny_completion_"):
        await whatsapp.send_message(sender, "הבנתי. אנחנו כאן אם צריך משהו.")