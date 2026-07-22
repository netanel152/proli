from app.services.whatsapp_client_service import WhatsAppClient
from app.core.database import leads_collection, users_collection
from app.core.logger import logger
from app.core.messages import Messages
from app.core.constants import LeadStatus, WorkerConstants
from app.core.phone import to_chat_id, to_local_phone
from app.core.config import settings
from bson import ObjectId

whatsapp = WhatsAppClient()


async def _send_best_effort(chat_id: str, message: str) -> bool:
    """Send a WhatsApp message, swallowing failures so one bad send doesn't
    abort a batch (e.g. SOS to pro then admin). Returns True on success."""
    try:
        await whatsapp.send_message(chat_id, message)
        return True
    except Exception as e:
        logger.error(f"WhatsApp send failed for {chat_id}: {e}; message not delivered.")
        return False


async def send_oncall_alert(message: str, *, assume_authorized: bool = False) -> bool:
    """Best-effort WhatsApp delivery of an operator alert — but only when the
    WhatsApp instance is actually authorized.

    WhatsApp is frequently the *failing* component for these alerts (Green API
    deauth / yellowCard). Sending an alert about WhatsApp over WhatsApp amplifies
    the outage instead of reporting it (PRO-75), so if the instance is not
    authorized we never send: we emit ``logger.critical`` — forwarded to Sentry
    → email, the guaranteed out-of-band page — and return. When authorized this
    is a courtesy channel (e.g. the recovery notice); Sentry stays the guaranteed
    page regardless of the return value. Routes to ONCALL_PHONE when set, else
    ADMIN_PHONE.

    ``assume_authorized`` lets a caller that has *just* confirmed the instance is
    authorized (the monitor's recovery path) skip the state probe — avoiding a
    redundant call that could transiently misfire and drop the recovery notice
    while falsely re-paging that WhatsApp is down. The instance id is masked; do
    not log the raw ``message`` here (callers must keep it PII-free anyway)."""
    oncall = settings.ONCALL_PHONE or settings.ADMIN_PHONE
    # Mask to last 4 digits, matching the project-wide PII logging convention.
    masked = "***" + oncall[-4:]

    if not assume_authorized:
        state = await whatsapp.get_state_instance()
        if state != "authorized":
            logger.critical(
                f"WhatsApp instance not authorized (state={state or 'unreachable'}, "
                f"instance=***{str(settings.GREEN_API_INSTANCE_ID)[-4:]}) — on-call "
                f"alert to {masked} NOT sent over WhatsApp; paging via Sentry email."
            )
            return False

    chat_id = to_chat_id(oncall)
    try:
        await whatsapp.send_message(chat_id, message)
        logger.info(f"On-call alert delivered via WhatsApp to {masked}")
        return True
    except Exception as e:
        logger.critical(
            f"On-call WhatsApp alert to {masked} failed: {e}. Relying on Sentry email."
        )
        return False


async def send_pro_reminder(lead_id: str, triggered_by: str = "auto"):
    """Sends a reminder to the pro to mark a job as finished. Capped at MAX_PRO_REMINDERS."""
    try:
        lead = await leads_collection.find_one({"_id": ObjectId(lead_id)})
        if not lead or lead.get("status") != LeadStatus.BOOKED:
            logger.warning(
                f"send_pro_reminder called for invalid/non-booked lead: {lead_id}"
            )
            return

        # Enforce reminder cap to avoid spamming the pro
        reminder_count = lead.get("reminder_sent_count", 0)
        if reminder_count >= WorkerConstants.MAX_PRO_REMINDERS:
            logger.info(
                f"[Reminder] Lead {lead_id} already hit max reminders ({reminder_count}), skipping."
            )
            return

        pro = await users_collection.find_one({"_id": lead["pro_id"]})
        if not pro or not pro.get("phone_number"):
            logger.error(f"Could not find pro or pro phone for lead {lead_id}")
            return

        pro_chat_id = to_chat_id(pro["phone_number"])
        message = Messages.Pro.REMINDER

        await _send_best_effort(pro_chat_id, message)

        # Increment counter atomically
        await leads_collection.update_one(
            {"_id": ObjectId(lead_id)}, {"$inc": {"reminder_sent_count": 1}}
        )
        logger.success(
            f"Sent pro reminder for lead {lead_id} ({reminder_count + 1}/{WorkerConstants.MAX_PRO_REMINDERS}, Trigger: {triggered_by})"
        )
    except Exception as e:
        logger.error(f"Error in send_pro_reminder for lead {lead_id}: {e}")


async def send_sos_alert(chat_id: str, last_message: str, pro_id: str = None):
    """
    Sends an SOS alert to the assigned professional (if any) and always to the admin.
    Admin message is in Hebrew with full customer and lead details.
    """
    try:
        customer_phone_display = to_local_phone(chat_id)

        # Fetch active lead for context
        active_lead = await leads_collection.find_one(
            {
                "chat_id": chat_id,
                "status": {
                    "$in": [LeadStatus.NEW, LeadStatus.CONTACTED, LeadStatus.BOOKED]
                },
            },
            sort=[("created_at", -1)],
        )

        if active_lead:
            STATUS_HE = {
                "new": "ממתין לאישור",
                "contacted": "בתהליך",
                "booked": "מאושר",
                "completed": "הושלם",
                "rejected": "נדחה",
                "cancelled": "בוטל",
                "pending_admin_review": "ממתין לבדיקת מנהל",
            }
            issue = active_lead.get("issue_type", "לא ידוע")
            # `or` covers nullable full_address (None value, not just missing key)
            address = active_lead.get("full_address") or "לא ידוע"
            apt_time = active_lead.get("appointment_time", "לא נקבע")
            status_he = STATUS_HE.get(
                active_lead.get("status", ""), active_lead.get("status", "")
            )
            # Bold the field labels so this block matches the bold labels in the
            # ADMIN_ALERT header above it (📞 *טלפון:*, 💬 *הודעה:*) — one message
            # shouldn't mix bold and plain labels.
            lead_details = (
                f"📋 *פרטי הפנייה:*\n"
                f"🛠️ *בעיה:* {issue}\n"
                f"📍 *כתובת:* {address}\n"
                f"⏰ *זמן:* {apt_time}\n"
                f"📊 *סטטוס:* {status_he}"
            )
        else:
            lead_details = "📋 אין פנייה פעילה במערכת"

        # 1. Alert the Pro (if assigned)
        if pro_id:
            if isinstance(pro_id, str):
                pro_id = ObjectId(pro_id)
            pro = await users_collection.find_one({"_id": pro_id})
            if pro and pro.get("phone_number"):
                pro_chat_id = to_chat_id(pro["phone_number"])
                pro_msg = Messages.SOS.PRO_ALERT.format(
                    phone=customer_phone_display, last_message=last_message
                )
                await _send_best_effort(pro_chat_id, pro_msg)
                logger.info(f"SOS alert sent to Pro {pro_id} for user {chat_id}")

        # 2. Always alert Admin with full details
        admin_chat_id = to_chat_id(settings.ADMIN_PHONE)
        admin_msg = Messages.SOS.ADMIN_ALERT.format(
            phone=customer_phone_display,
            last_message=last_message,
            lead_details=lead_details,
        )
        await _send_best_effort(admin_chat_id, admin_msg)
        logger.info(f"SOS alert sent to Admin for user {chat_id}")

    except Exception as e:
        logger.error(f"Error in send_sos_alert for user {chat_id}: {e}")
