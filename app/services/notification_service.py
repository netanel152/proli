from app.services.whatsapp_client_service import WhatsAppClient
from app.core.database import leads_collection, users_collection
from app.core.logger import logger
from app.core.messages import Messages
from app.core.constants import LeadStatus, WorkerConstants
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

async def send_sos_alert(chat_id: str, last_message: str, pro_id: str = None):
    """
    Sends an SOS alert to the assigned professional or the system admin.
    """
    try:
        alert_sent = False
        
        # 1. Try to alert the Professional
        if pro_id:
            pro = await users_collection.find_one({"_id": pro_id})
            if pro and pro.get("phone_number"):
                pro_phone = pro["phone_number"]
                pro_chat_id = f"{pro_phone}@c.us" if not pro_phone.endswith('@c.us') else pro_phone
                
                msg = Messages.SOS.PRO_ALERT.format(chat_id=chat_id, last_message=last_message)
                await whatsapp.send_message(pro_chat_id, msg)
                alert_sent = True
                logger.info(f"SOS alert sent to Pro {pro_id} for user {chat_id}")

        # 2. If no pro or alert failed, alert Admin
        if not alert_sent:
            admin_phone = WorkerConstants.ADMIN_PHONE
            admin_chat_id = f"{admin_phone}@c.us" if not admin_phone.endswith('@c.us') else admin_phone
            
            msg = Messages.SOS.ADMIN_ALERT.format(chat_id=chat_id, last_message=last_message)
            await whatsapp.send_message(admin_chat_id, msg)
            logger.info(f"SOS alert sent to Admin for user {chat_id}")

    except Exception as e:
        logger.error(f"Error in send_sos_alert for user {chat_id}: {e}")
