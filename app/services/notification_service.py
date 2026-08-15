from app.providers.whatsapp import get_whatsapp
from app.core.database import leads_collection, users_collection
from app.core.logger import logger, page_critical
from app.core.messages import Messages
from app.core.constants import LeadStatus, WorkerConstants
from app.core.phone import to_chat_id, to_local_phone
from app.core.config import settings
from bson import ObjectId

whatsapp = get_whatsapp()


async def _send_best_effort(chat_id: str, message: str) -> bool:
    """Send a WhatsApp message, swallowing failures so one bad send doesn't
    abort a batch (e.g. SOS to pro then admin). Returns True on success."""
    try:
        await whatsapp.send_message(chat_id, message)
        return True
    except Exception as e:
        logger.error(f"WhatsApp send failed for {chat_id}: {e}; message not delivered.")
        return False


def page_operator(summary: str) -> None:
    """Page the operator without WhatsApp — ``page_critical`` → Sentry → email.

    PRO-88: the admin never sends the bot an inbound message, so under Meta
    Cloud API their 24-hour service window is *permanently* closed. Every
    operator-facing WhatsApp alert would therefore need its own approved
    template plus per-message fees, forever, to deliver something PRO-75
    already routes through Sentry as the guaranteed page. Three alerts
    (SOS admin leg, stuck-lead report, escalation notice) were deleted rather
    than templatized.

    Distinct from ``send_oncall_alert``: that one is for *infrastructure*
    alerts and still tries WhatsApp as a courtesy when the account is
    authorized. This is for *business* alerts and never transmits.

    **Callers must still keep PII out of ``summary``.** ``page_critical``
    scrubs phone numbers and secrets inline (PRO-113 — Sentry copies the
    record before the loguru sink filter), but that is a backstop: pass
    identifiers the operator can look up in the admin panel rather than the
    customer record itself.
    """
    page_critical(summary)


async def send_oncall_alert(message: str, *, assume_authorized: bool = False) -> bool:
    """Best-effort WhatsApp delivery of an operator alert — but only when the
    WhatsApp instance is actually authorized.

    WhatsApp is frequently the *failing* component for these alerts (Green API
    deauth / yellowCard). Sending an alert about WhatsApp over WhatsApp amplifies
    the outage instead of reporting it (PRO-75), so if the instance is not
    authorized we never send: we emit ``page_critical`` — the stdlib CRITICAL
    Sentry actually forwards to email, the guaranteed out-of-band page
    (PRO-113) — and return. When authorized this
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

    # PRO-86: a non-transmitting provider reports "authorized" (it can never be
    # deauthorized), which would send this page into the dry-run sink and return
    # True — losing the alert exactly when the runbook has dry-run switched on.
    # Sentry is the guaranteed channel, so go straight to it.
    if not whatsapp.provider.transmits:
        page_critical(
            f"WhatsApp provider {whatsapp.provider.name!r} cannot transmit — "
            f"on-call alert to {masked} NOT sent over WhatsApp; paging via "
            "Sentry email."
        )
        return False

    if not assume_authorized:
        state = await whatsapp.get_state_instance()
        if state != "authorized":
            page_critical(
                f"WhatsApp account not authorized (state={state or 'unreachable'}, "
                f"provider={whatsapp.provider.name}) — on-call alert to {masked} "
                "NOT sent over WhatsApp; paging via Sentry email."
            )
            return False

    chat_id = to_chat_id(oncall)
    try:
        await whatsapp.send_message(chat_id, message)
        logger.info(f"On-call alert delivered via WhatsApp to {masked}")
        return True
    except Exception as e:
        page_critical(
            f"On-call WhatsApp alert to {masked} failed: {e}. Relying on Sentry email."
        )
        return False


def format_lead_extra_info(lead: dict) -> str:
    """Floor/apartment line shown to the pro inside a lead offer."""
    return Messages.Pro.EXTRA_INFO_LINE.format(
        floor=lead.get("floor") or "-", apartment=lead.get("apartment") or "-"
    )


def format_media_links(lead: dict) -> str:
    """Customer media as a numbered text-links block, or "" when there is none.

    Media is always sent as text links, never re-sent as files. That decision
    was made once in the initial-offer path (re-sending files duplicates
    uploads the customer already made); the reassignment path used to re-send
    files and the admin path used to drop media entirely — this is now the one
    policy for all three.
    """
    media_urls = lead.get("media_urls") or []
    if not media_urls:
        return ""
    links = "".join(f"\n{i}. {url}" for i, url in enumerate(media_urls, 1))
    return "\n\n" + Messages.Pro.MEDIA_ATTACHED_HEADER + links


def build_new_lead_message(lead: dict) -> str:
    """The lead-offer text a pro receives on reassignment or admin assignment.

    Owns the header choice (emergency vs normal), the missing-field fallbacks
    (Hebrew, from Messages.Fallbacks) and the media policy. Pure — no I/O — so
    the message shape is testable without a database or a send.

    The *initial* offer (workflow_service) uses the richer APPROVAL_REQUEST
    template (customer phone, price line, loyalty header) and stays separate on
    purpose; it shares format_lead_extra_info and format_media_links so the
    pieces cannot drift.
    """
    header = (
        Messages.Pro.EMERGENCY_LEAD_HEADER
        if lead.get("is_emergency")
        else Messages.Pro.NEW_LEAD_HEADER
    )
    details = Messages.Pro.NEW_LEAD_DETAILS.format(
        customer_name=lead.get("customer_name") or Messages.Fallbacks.CUSTOMER_NAME,
        full_address=lead.get("full_address") or Messages.Fallbacks.UNKNOWN,
        extra_info=format_lead_extra_info(lead),
        issue_type=lead.get("issue_type") or Messages.Fallbacks.UNKNOWN,
        appointment_time=lead.get("appointment_time") or Messages.Fallbacks.TIME_ASAP,
    )
    return (
        header
        + "\n\n"
        + details
        + Messages.Pro.NEW_LEAD_FOOTER
        + format_media_links(lead)
    )


async def notify_pro_new_lead(lead: dict, pro: dict, whatsapp) -> bool:
    """Send the lead offer (and a navigation link) to a pro.

    Fails open: a failed send is logged at ERROR and returns False rather than
    raising — the callers (reassignment, admin assignment) have already updated
    the lead, and aborting mid-flow would leave the assignment half-applied
    with no offer *and* no error path. The False return lets a caller decide
    whether to compensate.

    ``whatsapp`` is injected (not the module-level facade) so flow callers pass
    the instance they already hold and tests pass a mock, per the DI convention.
    """
    phone = (pro or {}).get("phone_number")
    if not lead or not phone:
        logger.error(
            f"notify_pro_new_lead missing lead or pro phone (lead={bool(lead)})"
        )
        return False

    pro_chat_id = to_chat_id(phone)
    try:
        await whatsapp.send_message(pro_chat_id, build_new_lead_message(lead))
        if lead.get("full_address"):
            await whatsapp.send_location_link(
                pro_chat_id, lead["full_address"], Messages.Pro.NAVIGATE_TO
            )
        return True
    except Exception as e:
        logger.error(
            f"Failed to send lead offer to pro for lead {lead.get('_id')}: {e}"
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

        # PRO-88 removed the Hebrew lead-details block that used to be built
        # here. It existed solely to format the admin's WhatsApp message; the
        # operator now gets a page and opens the lead in the admin panel, which
        # renders those fields properly and does not retain them in Sentry.

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

        # 2. Always page the Admin — via Sentry, not WhatsApp (PRO-88).
        # The customer's message is deliberately NOT included: it is free-form
        # text from a distressed person and can contain anything. The operator
        # opens the lead in the admin panel, which is also where the full
        # phone number lives.
        page_operator(
            f"SOS from customer ***{customer_phone_display[-4:]} — "
            f"issue={(active_lead or {}).get('issue_type') or 'unknown'}, "
            f"lead={(active_lead or {}).get('_id') or 'none active'}, "
            f"pro_notified={bool(pro_id)}. Open the lead in the admin panel."
        )

    except Exception as e:
        logger.error(f"Error in send_sos_alert for user {chat_id}: {e}")
