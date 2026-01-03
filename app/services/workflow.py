from app.services.whatsapp_client import WhatsAppClient
from app.services.ai_engine import AIEngine, AIResponse
from app.services.lead_manager import LeadManager
from app.core.logger import logger
from app.core.database import users_collection, leads_collection, reviews_collection
from app.core.messages import Messages
from app.core.prompts import Prompts
from app.core.constants import LeadStatus
from app.services.matching_service import determine_best_pro, book_slot_for_lead
from app.services.notification_service import send_pro_reminder
from bson import ObjectId
from datetime import datetime, timezone
import re
import httpx

# Initialize services
whatsapp = WhatsAppClient()
ai = AIEngine()
lead_manager = LeadManager()

async def send_customer_completion_check(lead_id: str, triggered_by: str = "auto"):
    """Asks the customer if the job has been completed using interactive buttons."""
    try:
        lead = await leads_collection.find_one({"_id": ObjectId(lead_id)})
        if not lead or lead.get("status") != LeadStatus.BOOKED:
            logger.warning(f"send_customer_completion_check called for invalid/non-booked lead: {lead_id}")
            return

        customer_chat_id = lead["chat_id"]
        pro = await users_collection.find_one({"_id": lead["pro_id"]})
        pro_name = pro.get("business_name", "איש המקצוע") if pro else "איש המקצוע"

        await whatsapp.send_interactive_buttons(
            to_number=customer_chat_id.replace("@c.us", ""),
            text=Messages.Customer.COMPLETION_CHECK.format(pro_name=pro_name),
            buttons=[
                {"id": "confirm_finish", "title": "✅ כן, הסתיים"},
                {"id": "not_finished", "title": "❌ עדיין לא"}
            ]
        )
        logger.success(f"Sent customer completion check (buttons) for lead {lead_id} (Trigger: {triggered_by})")
    except Exception as e:
        logger.error(f"Error in send_customer_completion_check for lead {lead_id}: {e}")

async def handle_customer_completion_text(chat_id: str, text: str):
    """Checks if the user confirmed job completion via text."""
    text = text.strip()
    # Check for text response OR button ID interaction (simulated here if payload comes as text in some architectures, 
    # but GreenAPI usually sends separate webhook. Assuming text handling catches button titles or IDs if echoed).
    # For now, we stick to text keywords + adding button titles to keywords if needed.
    
    is_completion = False
    if Messages.Keywords.CUSTOMER_COMPLETION_INDICATOR in text:
        is_completion = True
    elif "confirm_finish" in text or "כן, הסתיים" in text:
        is_completion = True
        
    if not is_completion:
        return None

    lead = await leads_collection.find_one({
        "chat_id": chat_id,
        "status": LeadStatus.BOOKED
    }, sort=[("created_at", -1)])

    if not lead:
        return None

    pro = await users_collection.find_one({"_id": lead["pro_id"]})
    
    await leads_collection.update_one(
        {"_id": lead["_id"]},
        {
            "$set": {
                "status": LeadStatus.COMPLETED,
                "completed_at": datetime.now(timezone.utc),
                "waiting_for_rating": True
            }
        }
    )
    logger.success(f"✅ Lead {lead['_id']} marked as COMPLETED by customer.")

    if pro and pro.get("phone_number"):
        pro_chat_id = f"{pro['phone_number']}@c.us" if not pro['phone_number'].endswith('@c.us') else pro['phone_number']
        await whatsapp.send_message(pro_chat_id, Messages.Pro.CUSTOMER_REPORTED_COMPLETION)

    pro_name = pro.get('business_name', 'המקצוען') if pro else 'המקצוען'
    return Messages.Customer.COMPLETION_ACK.format(pro_name=pro_name)

async def handle_customer_rating_text(chat_id: str, text: str):
    """Checks if the user sent a rating (1-5)."""
    text = text.strip()
    if text not in Messages.Keywords.RATING_OPTIONS:
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
        {"$set": {
            "waiting_for_rating": False, 
            "rating_given": rating,
            "waiting_for_review_comment": True
        }}
    )
    
    business_name = pro['business_name'] if pro else "איש המקצוע"
    logger.success(f"⭐ Rating {rating} saved for {business_name}")
    return Messages.Customer.REVIEW_REQUEST

async def handle_customer_review_comment(chat_id: str, text: str):
    """Checks if the user sent a textual review after rating."""
    lead = await leads_collection.find_one({
        "chat_id": chat_id,
        "waiting_for_review_comment": True
    })

    if not lead:
        return None

    pro_id = lead["pro_id"]
    rating_given = lead.get("rating_given", 5) 

    review_doc = {
        "pro_id": pro_id,
        "customer_chat_id": chat_id,
        "rating": rating_given,
        "comment": text,
        "created_at": datetime.now(timezone.utc)
    }

    await reviews_collection.insert_one(review_doc)

    await leads_collection.update_one(
        {"_id": lead["_id"]},
        {"$set": {"waiting_for_review_comment": False}}
    )
    
    logger.success(f"📝 Review comment saved for lead {lead['_id']}")
    return Messages.Customer.REVIEW_SAVED

async def handle_pro_text_command(chat_id: str, text: str):
    """
    Handles text commands from Professionals.
    """
    # Identify Pro
    phone = chat_id.replace("@c.us", "")
    pro = await users_collection.find_one({"phone_number": {"$in": [phone, chat_id]}})
    if not pro:
        return None 

    text = text.strip().lower()
    
    APPROVE_KEYS = Messages.Keywords.APPROVE_COMMANDS
    REJECT_KEYS = Messages.Keywords.REJECT_COMMANDS
    FINISH_KEYS = Messages.Keywords.FINISH_COMMANDS

    response_text = None

    if text in APPROVE_KEYS:
        lead = await leads_collection.find_one(
            {"pro_id": pro["_id"], "status": LeadStatus.NEW},
            sort=[("created_at", -1)]
        )
        if lead:
            await lead_manager.update_lead_status(str(lead["_id"]), LeadStatus.BOOKED, pro["_id"])
            
            # Trigger Booking Logic
            booking_success = await book_slot_for_lead(pro["_id"], lead["created_at"])
            
            response_text = Messages.Pro.APPROVE_SUCCESS
            if booking_success:
                response_text += "\n📅 היומן עודכן בהצלחה!"
            
            pro_name = pro.get('business_name', 'מומחה')
            pro_phone = pro.get('phone_number', '').replace('972', '0')
            customer_msg = Messages.Customer.PRO_FOUND.format(pro_name=pro_name, pro_phone=pro_phone)
            await whatsapp.send_message(lead["chat_id"], customer_msg)
        else:
            response_text = Messages.Pro.NO_PENDING_APPROVE

    elif text in REJECT_KEYS:
        lead = await leads_collection.find_one(
            {"pro_id": pro["_id"], "status": LeadStatus.NEW},
            sort=[("created_at", -1)]
        )
        if lead:
            await lead_manager.update_lead_status(str(lead["_id"]), LeadStatus.REJECTED)
            response_text = Messages.Pro.REJECT_SUCCESS
        else:
            response_text = Messages.Pro.NO_PENDING_REJECT

    elif text in FINISH_KEYS:
        lead = await leads_collection.find_one(
            {"pro_id": pro["_id"], "status": LeadStatus.BOOKED},
            sort=[("created_at", -1)]
        )
        if lead:
            await leads_collection.update_one(
                {"_id": lead["_id"]},
                {"$set": {
                    "status": LeadStatus.COMPLETED, 
                    "completed_at": datetime.now(timezone.utc), 
                    "waiting_for_rating": True
                }}
            )
            response_text = Messages.Pro.FINISH_SUCCESS
            
            pro_name = pro.get('business_name', 'המקצוען')
            feedback_msg = Messages.Customer.RATE_SERVICE.format(pro_name=pro_name)
            await whatsapp.send_message(lead["chat_id"], feedback_msg)
        else:
            response_text = Messages.Pro.NO_ACTIVE_FINISH

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

    # 2. Check for Customer Completion or Rating
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

        review_resp = await handle_customer_review_comment(chat_id, user_text)
        if review_resp:
            await whatsapp.send_message(chat_id, review_resp)
            await lead_manager.log_message(chat_id, "model", review_resp)
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
    
    dispatcher_prompt = Prompts.DISPATCHER_SYSTEM

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
            "status": {"$in": [LeadStatus.NEW, LeadStatus.CONTACTED]}
        }, sort=[("created_at", -1)])

        if active_lead:
            current_lead_id = active_lead["_id"]
        else:
            # Create a provisional lead
            new_lead = await lead_manager.create_lead_from_dict(
                chat_id=chat_id,
                issue_type=extracted_issue,
                full_address=extracted_city, 
                status=LeadStatus.CONTACTED,
                appointment_time="Pending"
            )
            if new_lead:
                current_lead_id = new_lead["_id"]

        # Sufficient data gathered -> Find Pro
        best_pro = await determine_best_pro(issue_type=extracted_issue, location=extracted_city)
        
        if best_pro:
            if current_lead_id:
                await leads_collection.update_one(
                    {"_id": current_lead_id}, 
                    {"$set": {"pro_id": best_pro["_id"]}}
                )

            # Switch to Pro Persona
            pro_name = best_pro.get("business_name", "Fixi Pro")
            price_list = best_pro.get("price_list", "")
            base_system_prompt = best_pro.get("system_prompt", f"You are Fixi, an AI scheduler for {pro_name}.")
            
            rating = best_pro.get("social_proof", {}).get("rating", 5.0)
            count = best_pro.get("social_proof", {}).get("review_count", 0)
            social_proof_text = f"{rating} stars based on {count} reviews"

            full_system_prompt = Prompts.PRO_BASE_SYSTEM.format(
                base_system_prompt=base_system_prompt,
                pro_name=pro_name,
                price_list=price_list,
                social_proof_text=social_proof_text,
                extracted_city=extracted_city,
                extracted_issue=extracted_issue,
                transcription=transcription or "None"
            )
            
            pro_response_obj = await ai.analyze_conversation(
                history=history,
                user_text=user_text or "", 
                custom_system_prompt=full_system_prompt,
                require_json=True
            )
        else:
            pass

    # Select which response to send
    final_response = pro_response_obj if best_pro else dispatcher_response
    
    # Send Message to User
    await whatsapp.send_message(chat_id, final_response.reply_to_user)
    await lead_manager.log_message(chat_id, "model", final_response.reply_to_user)

    # 6. Check for [DEAL] or Structured Booking
    is_deal = final_response.is_deal
    
    deal_string_match = re.search(r"[DEAL:.*?]", final_response.reply_to_user)
    if deal_string_match:
        is_deal = True
    
    if is_deal and best_pro:
        # Finalize details
        d_time = final_response.extracted_data.appointment_time or "As soon as possible"
        d_address = final_response.extracted_data.full_address or extracted_city or "Unknown Address"
        d_issue = final_response.extracted_data.issue or extracted_issue or "Issue"
        
        if current_lead_id:
            await leads_collection.update_one(
                {"_id": current_lead_id},
                {"$set": {
                    "status": LeadStatus.NEW, 
                    "appointment_time": d_time,
                    "full_address": d_address,
                    "issue_type": d_issue,
                    "pro_id": best_pro["_id"]
                }}
            )
            lead = await leads_collection.find_one({"_id": current_lead_id})
        else:
            lead = await lead_manager.create_lead_from_dict(
                chat_id=chat_id,
                issue_type=d_issue,
                full_address=d_address,
                appointment_time=d_time,
                status=LeadStatus.NEW,
                pro_id=best_pro["_id"]
            )
        
        if lead:
            pro_phone = best_pro.get("phone_number")
            if pro_phone:
                if not pro_phone.endswith("@c.us"):
                    pro_phone = f"{pro_phone}@c.us"
                
                msg_to_pro = Messages.Pro.NEW_LEAD_HEADER + "\n\n"
                msg_to_pro += Messages.Pro.NEW_LEAD_DETAILS.format(
                    full_address=lead['full_address'],
                    issue_type=lead['issue_type'],
                    appointment_time=lead['appointment_time']
                )

                if transcription:
                    msg_to_pro += Messages.Pro.NEW_LEAD_TRANSCRIPTION.format(transcription=transcription)
                
                msg_to_pro += Messages.Pro.NEW_LEAD_FOOTER
                await whatsapp.send_message(pro_phone, msg_to_pro)
                
                await whatsapp.send_location_link(pro_phone, lead['full_address'], Messages.Pro.NAVIGATE_TO)
