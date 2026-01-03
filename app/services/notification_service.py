from app.services.whatsapp_client import WhatsAppClient
from app.core.database import leads_collection, users_collection
from app.core.logger import logger
from app.core.messages import Messages
from app.core.constants import LeadStatus
from bson import ObjectId

whatsapp = WhatsAppClient()

async def send_pro_reminder(lead_id: str, triggered_by: str = "auto"):
    """Sends a reminder to the pro to mark a job as finished."""
    try:
        lead = await leads_collection.find_one({"_id": ObjectId(lead_id)})
        if not lead or lead.get("status") != LeadStatus.BOOKED:
            logger.warning(f"send_pro_reminder called for invalid/non-booked lead: {lead_id}")
            return

        pro = await users_collection.find_one({"_id": lead["pro_id"]})
        if not pro or not pro.get("phone_number"):
            logger.error(f"Could not find pro or pro phone for lead {lead_id}")
            return

        pro_chat_id = f"{pro['phone_number']}@c.us" if not pro['phone_number'].endswith('@c.us') else pro['phone_number']
        message = Messages.Pro.REMINDER
        
        await whatsapp.send_message(pro_chat_id, message)
        logger.success(f"Sent pro reminder for lead {lead_id} (Trigger: {triggered_by})")
    except Exception as e:
        logger.error(f"Error in send_pro_reminder for lead {lead_id}: {e}")