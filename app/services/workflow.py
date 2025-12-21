from app.services.whatsapp_client import WhatsAppClient
from app.services.ai_engine import AIEngine
from app.services.lead_manager import LeadManager
from app.core.logger import logger
from app.core.database import users_collection
import re

# Initialize services
whatsapp = WhatsAppClient()
ai = AIEngine()
lead_manager = LeadManager()

async def process_incoming_message(chat_id: str, user_text: str, media_url: str = None):
    # 1. Log User Message
    log_text = user_text
    if media_url:
        log_text = f"{user_text or ''} [MEDIA: {media_url}]"
    await lead_manager.log_message(chat_id, "user", log_text)

    # 2. Get AI Response
    history = await lead_manager.get_chat_history(chat_id)
    ai_response = await ai.analyze_conversation(history, user_text or "")
    
    # 3. Check for [DEAL]
    deal_match = re.search(r"\[DEAL:.*?\]", ai_response)
    
    if deal_match:
        # Extract Clean Response for User
        clean_response = ai_response.replace(deal_match.group(0), "").strip()
        if not clean_response:
            clean_response = "תודה! אני בודק זמינות עם איש מקצוע ושולח לך אישור מיד."

        await whatsapp.send_message(chat_id, clean_response)
        await lead_manager.log_message(chat_id, "model", clean_response)

        # Create Lead
        deal_string = deal_match.group(0)
        lead = await lead_manager.create_lead(deal_string, chat_id)
        
        if lead:
            # Notify Pro (Simplified: Finding the first active pro for demo/MVP)
            pro = await users_collection.find_one({"is_active": True})
            
            if pro and pro.get("phone_number"):
                pro_phone = pro["phone_number"]
                if not pro_phone.endswith("@c.us"):
                    pro_phone = f"{pro_phone}@c.us"                
                
                msg_to_pro = f"""📢 *הצעת עבודה חדשה!*

📍 *כתובת:* {lead['address']}
🛠️ *תקלה:* {lead['issue']}
⏰ *זמן מועדף:* {lead['time_preference']}"""
                
                # Send Buttons
                buttons = [
                    {"buttonId": f"approve_{lead['_id']}", "buttonText": "אשר עבודה"},
                    {"buttonId": f"reject_{lead['_id']}", "buttonText": "דחה"}
                ]
                await whatsapp.send_buttons(pro_phone, msg_to_pro, buttons)
                
                # Send Waze Link
                await whatsapp.send_location_link(pro_phone, lead['address'], "🚗 נווט לכתובת:")
    
    else:
        # Standard AI Reply
        await whatsapp.send_message(chat_id, ai_response)
        await lead_manager.log_message(chat_id, "model", ai_response)


async def handle_pro_response(payload: dict):
    """
    Handles button clicks from the Pro.
    """
    msg_data = payload.get("messageData", {})
    button_reply = msg_data.get("buttonsResponseMessage", {})
    button_id = button_reply.get("selectedButtonId")
    sender = payload.get("senderData", {}).get("chatId")

    if not button_id:
        return

    logger.info(f"Pro clicked button: {button_id}")

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