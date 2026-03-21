from app.core.database import users_collection, leads_collection
from app.core.logger import logger
from app.core.messages import Messages
from app.core.constants import LeadStatus, Defaults
from app.services.matching_service import book_slot_for_lead
from datetime import datetime, timezone


async def handle_pro_text_command(chat_id: str, text: str, whatsapp, lead_manager):
    """
    Handles text commands from Professionals.
    """
    # Identify Pro
    phone = chat_id.replace("@c.us", "")
    pro = await users_collection.find_one({"phone_number": {"$in": [phone, chat_id]}, "role": "professional"})
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

            pro_name = pro.get('business_name', Defaults.EXPERT_NAME)
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

            pro_name = pro.get('business_name', Defaults.GENERIC_PRO_NAME)
            feedback_msg = Messages.Customer.RATE_SERVICE.format(pro_name=pro_name)
            await whatsapp.send_message(lead["chat_id"], feedback_msg)
        else:
            response_text = Messages.Pro.NO_ACTIVE_FINISH

    return response_text
