from app.core.database import users_collection, leads_collection, reviews_collection
from app.core.logger import logger
from app.core.messages import Messages
from app.core.constants import LeadStatus, Defaults, UserStates, WorkerConstants
from app.services.matching_service import book_slot_for_lead
from app.services.context_manager_service import ContextManager
from app.services.state_manager_service import StateManager
from datetime import datetime, timedelta, timezone

STATUS_LABELS = {
    LeadStatus.NEW: "ממתין",
    LeadStatus.CONTACTED: "ממתין",
    LeadStatus.BOOKED: "מאושר",
    LeadStatus.COMPLETED: "הושלם",
    LeadStatus.REJECTED: "נדחה",
    LeadStatus.CANCELLED: "בוטל",
    LeadStatus.CLOSED: "סגור",
    LeadStatus.PENDING_ADMIN_REVIEW: "ממתין לבדיקת מנהל",
}


def _normalize(text: str) -> str:
    """Normalize quotes and whitespace so commands like דו"ח match regardless of quote variant."""
    # Replace all quote variants with standard ASCII double-quote
    for ch in ["\u05f4", "\u201c", "\u201d", "\u2033", "\u275d", "\u275e"]:
        text = text.replace(ch, '"')
    return text.strip().lower()


async def handle_pro_text_command(chat_id: str, text: str, whatsapp, lead_manager, ai=None):
    """
    Handles text commands from Professionals.

    Return contract (tri-state):
      - None  → no match, caller should send PRO_HELP_MENU
      - ""    → already handled internally (intent prompt sent), caller sends nothing
      - str   → send verbatim to the pro
    """
    phone = chat_id.replace("@c.us", "")
    pro = await users_collection.find_one({"phone_number": {"$in": [phone, chat_id]}, "role": "professional"})
    if not pro:
        return None

    text = _normalize(text)

    if text in Messages.Keywords.RESUME_COMMANDS:
        return await _handle_resume(pro)

    if text in Messages.Keywords.PAUSE_COMMANDS:
        return await _handle_pause_bot(pro, whatsapp)

    if text in Messages.Keywords.APPROVE_COMMANDS:
        return await _handle_approve(pro, lead_manager, whatsapp)

    if text in Messages.Keywords.REJECT_COMMANDS:
        return await _handle_reject(pro, lead_manager)

    if text in Messages.Keywords.FINISH_COMMANDS:
        return await _handle_finish(pro, whatsapp)

    if text in Messages.Keywords.ACTIVE_JOBS_COMMANDS:
        return await _handle_active_jobs(pro)

    if text in Messages.Keywords.HISTORY_COMMANDS:
        return await _handle_history(pro)

    if text in Messages.Keywords.STATS_COMMANDS:
        return await _handle_stats(pro)

    if text in Messages.Keywords.REVIEWS_COMMANDS:
        return await _handle_reviews(pro)

    # No command match — try intent detection on free-text
    if ai is not None and text and len(text) > 3:
        try:
            is_service_intent = await ai.detect_service_intent(text)
        except Exception as e:
            logger.warning(f"Intent detection failed for {chat_id}: {e}")
            is_service_intent = False
        if is_service_intent:
            await whatsapp.send_message(chat_id, Messages.Pro.INTENT_DETECTED)
            # 5-minute TTL so a stale prompt doesn't linger
            await StateManager.set_state(chat_id, UserStates.AWAITING_INTENT_CONFIRMATION, ttl=300)
            logger.info(f"Pro {chat_id} received intent-switch prompt for: {text[:60]}")
            return ""  # sentinel: handled internally, caller must not send PRO_HELP_MENU

    # Task 2: Pro-side Dynamic Timeout Reset
    # If the Pro sends a message and there's a lead in PAUSED_FOR_HUMAN, reset the TTL.
    latest_lead = await leads_collection.find_one(
        {"pro_id": pro["_id"], "status": {"$in": [LeadStatus.NEW, LeadStatus.BOOKED]}},
        sort=[("created_at", -1)]
    )
    if latest_lead and latest_lead.get("chat_id"):
        customer_chat_id = latest_lead["chat_id"]
        customer_state = await StateManager.get_state(customer_chat_id)
        if customer_state == UserStates.PAUSED_FOR_HUMAN:
            await StateManager.set_state(customer_chat_id, UserStates.PAUSED_FOR_HUMAN, ttl=WorkerConstants.PAUSE_TTL_SECONDS)
            # Update paused_at for SLA monitor
            await leads_collection.update_one(
                {"_id": latest_lead["_id"]},
                {"$set": {"paused_at": datetime.now(timezone.utc)}}
            )
            logger.info(f"Pro {chat_id} sent a message; reset PAUSED_FOR_HUMAN TTL and updated paused_at for customer {customer_chat_id}")

    return None


# --- Handlers ---

_RESPONSE_WINDOW_SECONDS = 60 * 5  # "just responded" = within 5 minutes


async def _recently_responded_lead(pro_id) -> bool:
    """
    Returns True if this pro has a BOOKED or REJECTED lead that was touched
    within the last 5 minutes. Used to distinguish "fat-finger second press"
    from "no pending lead at all" when the pro sends approve/reject.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=_RESPONSE_WINDOW_SECONDS)
    recent = await leads_collection.find_one(
        {
            "pro_id": pro_id,
            "status": {"$in": [LeadStatus.BOOKED, LeadStatus.REJECTED]},
            "created_at": {"$gte": cutoff},
        },
        sort=[("created_at", -1)],
    )
    return bool(recent)


async def _handle_approve(pro, lead_manager, whatsapp):
    lead = await leads_collection.find_one(
        {"pro_id": pro["_id"], "status": LeadStatus.NEW},
        sort=[("created_at", -1)]
    )
    if not lead:
        # Fat-finger guard: if the pro JUST approved/rejected this lead and
        # the second button-press is still in-flight, send a clear "already
        # responded" message instead of the generic "no pending" copy.
        if await _recently_responded_lead(pro["_id"]):
            return Messages.Pro.ALREADY_RESPONDED
        return Messages.Pro.NO_PENDING_APPROVE

    await lead_manager.update_lead_status(str(lead["_id"]), LeadStatus.BOOKED, pro["_id"])
    booking_success = await book_slot_for_lead(pro["_id"], lead["created_at"])

    response_text = Messages.Pro.APPROVE_SUCCESS
    if booking_success:
        response_text += Messages.Pro.CALENDAR_UPDATE_SUCCESS

    pro_name = pro.get("business_name", Defaults.EXPERT_NAME)
    raw_phone = pro.get("phone_number", "")
    pro_phone = "0" + raw_phone[3:] if raw_phone.startswith("972") else raw_phone

    # Profession label
    from app.core.messages import Messages as M
    type_labels = M.Onboarding.TYPE_LABELS
    profession_line = ""
    if pro.get("profession_type"):
        label = type_labels.get(pro["profession_type"], pro["profession_type"])
        profession_line = f"🔧 *מקצוע:* {label}\n"

    # Price list
    price_line = ""
    raw_price = pro.get("price_list", "")
    if raw_price:
        if isinstance(raw_price, dict):
            price_str = "\n".join(f"  • {k}: {v}₪" for k, v in raw_price.items())
        else:
            price_str = str(raw_price)
        price_line = f"\n💰 *מחירון:*\n{price_str}\n"

    # Rating
    rating_line = ""
    sp = pro.get("social_proof", {})
    if sp.get("review_count", 0) > 0:
        rating_line = f"\n⭐ דירוג: {sp['rating']:.1f} ({sp['review_count']} ביקורות)"

    customer_msg = Messages.Customer.PRO_FOUND.format(
        pro_name=pro_name,
        pro_phone=pro_phone,
        profession_line=profession_line,
        issue_type=lead.get("issue_type", ""),
        full_address=lead.get("full_address", ""),
        appointment_time=lead.get("appointment_time", ""),
        price_line=price_line,
        rating_line=rating_line,
    )
    await whatsapp.send_message(lead["chat_id"], customer_msg)
    # Clear AWAITING_PRO_APPROVAL state so customer can continue normally
    await StateManager.clear_state(lead["chat_id"])
    logger.info(f"Pro {pro['_id']} approved lead {lead['_id']}")
    return response_text


async def _handle_reject(pro, lead_manager):
    lead = await leads_collection.find_one(
        {"pro_id": pro["_id"], "status": LeadStatus.NEW},
        sort=[("created_at", -1)]
    )
    if not lead:
        if await _recently_responded_lead(pro["_id"]):
            return Messages.Pro.ALREADY_RESPONDED
        return Messages.Pro.NO_PENDING_REJECT

    await lead_manager.update_lead_status(str(lead["_id"]), LeadStatus.REJECTED)
    # Clear cached context and customer state so next conversation starts fresh
    if lead.get("chat_id"):
        await ContextManager.clear_context(lead["chat_id"])
        await StateManager.clear_state(lead["chat_id"])
    return Messages.Pro.REJECT_SUCCESS


async def _handle_pause_bot(pro, whatsapp):
    """Pro clicked 'Pause Bot' — pause AI for the customer's chat."""
    lead = await leads_collection.find_one(
        {"pro_id": pro["_id"], "status": {"$in": [LeadStatus.NEW, LeadStatus.BOOKED]}},
        sort=[("created_at", -1)]
    )
    if not lead:
        return Messages.Pro.NO_PENDING_APPROVE

    customer_chat_id = lead["chat_id"]
    await StateManager.set_state(customer_chat_id, UserStates.PAUSED_FOR_HUMAN, ttl=WorkerConstants.PAUSE_TTL_SECONDS)

    # Set is_paused flag and paused_at for SLA monitor
    await leads_collection.update_one(
        {"_id": lead["_id"]},
        {"$set": {"is_paused": True, "paused_at": datetime.now(timezone.utc)}}
    )

    await whatsapp.send_message(customer_chat_id, Messages.Customer.BOT_PAUSED_BY_PRO)
    logger.info(f"Pro {pro['_id']} paused bot for customer {customer_chat_id}")
    return Messages.Pro.PAUSE_ACK


async def _handle_resume(pro):
    """Pro resumes the bot after a pause."""
    lead = await leads_collection.find_one(
        {"pro_id": pro["_id"], "status": {"$in": [LeadStatus.NEW, LeadStatus.BOOKED]}},
        sort=[("created_at", -1)]
    )
    if not lead:
        return "אין שיחה מושהית כרגע."

    customer_chat_id = lead["chat_id"]
    current_state = await StateManager.get_state(customer_chat_id)
    if current_state == UserStates.PAUSED_FOR_HUMAN:
        await StateManager.clear_state(customer_chat_id)
        # Clear is_paused flag
        await leads_collection.update_one(
            {"_id": lead["_id"]},
            {"$set": {"is_paused": False}}
        )
        logger.info(f"Pro {pro['_id']} resumed bot for customer {customer_chat_id}")
        return "✅ הבוט חזר לפעולה."
    return "הבוט כבר פעיל."


async def _handle_finish(pro, whatsapp):
    lead = await leads_collection.find_one(
        {"pro_id": pro["_id"], "status": LeadStatus.BOOKED},
        sort=[("created_at", -1)]
    )
    if not lead:
        return Messages.Pro.NO_ACTIVE_FINISH

    await leads_collection.update_one(
        {"_id": lead["_id"]},
        {"$set": {
            "status": LeadStatus.COMPLETED,
            "completed_at": datetime.now(timezone.utc),
            "waiting_for_rating": True,
            "is_paused": False
        }}
    )

    # Clear cached context — job is done, next conversation starts fresh
    if lead.get("chat_id"):
        await ContextManager.clear_context(lead["chat_id"])

    pro_name = pro.get("business_name", Defaults.GENERIC_PRO_NAME)
    feedback_msg = Messages.Customer.RATE_SERVICE.format(pro_name=pro_name)
    await whatsapp.send_message(lead["chat_id"], feedback_msg)
    return Messages.Pro.FINISH_SUCCESS


async def _handle_active_jobs(pro):
    cursor = leads_collection.find(
        {"pro_id": pro["_id"], "status": {"$in": [LeadStatus.NEW, LeadStatus.BOOKED]}},
        sort=[("created_at", -1)]
    )
    leads = await cursor.to_list(length=20)

    if not leads:
        return Messages.Pro.NO_ACTIVE_JOBS

    lines = ["🔄 *עבודות פעילות:*\n"]
    for i, lead in enumerate(leads, 1):
        status_label = STATUS_LABELS.get(lead.get("status"), lead.get("status", "?"))
        issue = lead.get("issue_type", "לא ידוע")
        # `or` covers both key-missing and key-present-with-None (nullable full_address)
        address = lead.get("full_address") or "לא ידוע"
        time = lead.get("appointment_time", "לא נקבע")
        lines.append(Messages.Pro.ACTIVE_JOB_ROW.format(
            num=i, status=status_label, issue=issue, address=address, time=time
        ))

    lines.append(f"\n*סה\"כ: {len(leads)} עבודות*")
    return "\n".join(lines)


async def _handle_history(pro):
    cursor = leads_collection.find(
        {"pro_id": pro["_id"], "status": LeadStatus.COMPLETED},
        sort=[("completed_at", -1)]
    )
    leads = await cursor.to_list(length=10)

    if not leads:
        return Messages.Pro.NO_HISTORY

    lines = ["📋 *10 עבודות אחרונות שהושלמו:*\n"]
    for i, lead in enumerate(leads, 1):
        issue = lead.get("issue_type", "לא ידוע")
        address = lead.get("full_address") or "לא ידוע"
        completed_at = lead.get("completed_at")
        if completed_at:
            if isinstance(completed_at, datetime):
                date_str = completed_at.strftime("%d/%m/%y")
            else:
                date_str = str(completed_at)[:10]
        else:
            date_str = "לא ידוע"
        lines.append(Messages.Pro.HISTORY_ROW.format(
            num=i, issue=issue, address=address, date=date_str
        ))

    return "\n".join(lines)


async def _handle_stats(pro):
    pro_id = pro["_id"]

    completed = await leads_collection.count_documents(
        {"pro_id": pro_id, "status": LeadStatus.COMPLETED}
    )
    active = await leads_collection.count_documents(
        {"pro_id": pro_id, "status": {"$in": [LeadStatus.NEW, LeadStatus.BOOKED]}}
    )

    social_proof = pro.get("social_proof", {})
    rating = social_proof.get("rating", 0.0)
    review_count = social_proof.get("review_count", 0)

    created_at = pro.get("created_at")
    if created_at and isinstance(created_at, datetime):
        joined = created_at.strftime("%d/%m/%Y")
    else:
        joined = "לא ידוע"

    rating_str = f"{rating:.1f} ⭐" if rating else "אין עדיין"

    return (
        Messages.Pro.STATS_HEADER
        + Messages.Pro.STATS_BODY.format(
            completed=completed,
            active=active,
            rating=rating_str,
            reviews=review_count,
            joined=joined,
        )
    )


async def _handle_reviews(pro):
    pro_id = pro["_id"]
    social_proof = pro.get("social_proof", {})
    rating_avg = social_proof.get("rating", 0.0)
    count = social_proof.get("review_count", 0)

    if count == 0:
        return Messages.Pro.NO_REVIEWS

    # Fetch all leads with a rating for this pro
    cursor = leads_collection.find(
        {"pro_id": pro_id, "rating_given": {"$exists": True, "$ne": None}},
        sort=[("completed_at", -1)]
    )
    rated_leads = await cursor.to_list(length=50)

    # Build a map of lead_id → comment from reviews_collection
    review_cursor = reviews_collection.find({"pro_id": pro_id})
    review_map = {}
    async for rev in review_cursor:
        # match by customer_chat_id + rating to link back to lead
        key = str(rev.get("customer_chat_id", "")) + str(rev.get("rating", ""))
        review_map[key] = rev.get("comment", "")

    lines = [f"⭐ *הביקורות שלך* (ממוצע {rating_avg:.1f} | {count} דירוגים):\n"]
    for lead in rated_leads:
        r = lead.get("rating_given", "?")
        # Try to find matching comment
        key = str(lead.get("chat_id", "")) + str(r)
        comment = review_map.get(key, "")
        if comment:
            lines.append(Messages.Pro.REVIEW_ROW.format(rating=r, comment=comment))
        else:
            lines.append(f"  ⭐{r}")

    return "\n".join(lines)
