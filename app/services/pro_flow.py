import math
import re
import urllib.parse
from bson import ObjectId
from app.core.database import users_collection, leads_collection, reviews_collection
from app.core.logger import logger
from app.core.messages import Messages
from app.core.constants import LeadStatus, Defaults, UserStates, WorkerConstants, Actor
from app.core.phone import strip_suffix, to_local_phone
from app.services.lead_manager_service import set_lead_status
from app.core.redis_client import get_redis_client
from app.services.matching_service import book_slot_for_lead, is_pro_eligible_for_lead
from app.services.context_manager_service import ContextManager
from app.services.state_manager_service import StateManager
from datetime import datetime, timedelta, timezone

# PRO-166: label vocabulary lives in the catalog; LeadStatus is a str Enum,
# so the plain-string keys there match enum members transparently.
STATUS_LABELS = Messages.Pro.STATUS_LABELS


# PRO-123: the exact keyword lists the dispatcher in `handle_pro_text_command`
# matches, in the same order. Kept next to the dispatcher so the two cannot
# drift: a command added below must be added here, or it starts being swallowed
# by the PRO_AWAITING_FINAL_PRICE prompt again. SKIP_COMMANDS is deliberately
# absent — "דלג" is an answer to the price prompt, not a command that abandons it.
_PRO_COMMAND_LISTS = (
    "PAUSE_COMMANDS",
    "RESUME_COMMANDS",
    "BOT_PAUSE_COMMANDS",
    "BOT_RESUME_COMMANDS",
    "HELP_COMMANDS",
    "MENU_COMMANDS",
    "APPROVE_COMMANDS",
    "REJECT_COMMANDS",
    "FINISH_COMMANDS",
    "STILL_WORKING_COMMANDS",
    "DETAILS_COMMANDS",
    "CANCEL_BOOKED_COMMANDS",
    "ACTIVE_JOBS_COMMANDS",
    "HISTORY_COMMANDS",
    "SUMMARY_COMMANDS",
    "STATS_COMMANDS",
    "REVIEWS_COMMANDS",
    "SEARCH_COMMANDS",
)


def _is_pro_command(text: str) -> bool:
    """True if `text` (already `_normalize`d) is a keyword the pro dispatcher handles."""
    return any(text in getattr(Messages.Keywords, name) for name in _PRO_COMMAND_LISTS)


def _normalize(text: str) -> str:
    """Normalize quotes and whitespace so commands like דו"ח match regardless of quote variant."""
    # Replace all quote variants with standard ASCII double-quote
    for ch in ["\u05f4", "\u201c", "\u201d", "\u2033", "\u275d", "\u275e"]:
        text = text.replace(ch, '"')
    return text.strip().lower()


async def handle_pro_text_command(
    chat_id: str, text: str, whatsapp, lead_manager, ai=None
):
    """
    Handles text commands from Professionals.

    Return contract (tri-state):
      - None  → no match, caller should send PRO_HELP_MENU
      - ""    → already handled internally (intent prompt sent), caller sends nothing
      - str   → send verbatim to the pro
    """
    phone = strip_suffix(chat_id)
    pro = await users_collection.find_one(
        {"phone_number": {"$in": [phone, chat_id]}, "role": "professional"}
    )
    if not pro:
        return None

    current_state = await StateManager.get_state(chat_id)
    text = _normalize(text)

    # State: selecting job to finish or cancel
    if current_state in (
        UserStates.PRO_SELECTING_JOB_TO_FINISH,
        UserStates.PRO_SELECTING_JOB_TO_CANCEL,
    ):
        return await _handle_job_selection(chat_id, text, pro, whatsapp, current_state)

    # State: awaiting the final charged price after a job was completed (PRO-33).
    # The lead is already COMPLETED — this only records final_price, never gates.
    #
    # PRO-123: a real command must not be eaten as a price. The prompt has a
    # 10-minute TTL, so a fresh lead offer lands inside it routinely and the pro
    # answers "אשר"/"דחה"/"פרטים" — which used to be parsed as a (non-numeric)
    # price and lost. The prompt is optional and never gates COMPLETED, so a
    # command simply abandons it: clear the state, then dispatch normally.
    if current_state == UserStates.PRO_AWAITING_FINAL_PRICE:
        if not _is_pro_command(text):
            return await _handle_final_price_reply(chat_id, text, pro)
        await StateManager.clear_state(chat_id)
        logger.info(
            f"Pro {pro['_id']} sent command {text!r} while awaiting a final "
            "price — abandoning the price prompt and handling the command."
        )

    # Availability Toggles (Vacation Mode)
    if text in Messages.Keywords.PAUSE_COMMANDS:
        await users_collection.update_one(
            {"_id": pro["_id"]}, {"$set": {"is_active": False}}
        )
        return Messages.Pro.STATUS_PAUSED

    if text in Messages.Keywords.RESUME_COMMANDS:
        await users_collection.update_one(
            {"_id": pro["_id"]}, {"$set": {"is_active": True}}
        )
        return Messages.Pro.STATUS_RESUMED

    # Bot Pause/Resume
    if text in Messages.Keywords.BOT_PAUSE_COMMANDS:
        return await _handle_pause_bot(pro, whatsapp)

    if text in Messages.Keywords.BOT_RESUME_COMMANDS:
        return await _handle_resume(pro)

    if text in Messages.Keywords.HELP_COMMANDS:
        return Messages.Pro.HELP_MENU

    if text in Messages.Keywords.MENU_COMMANDS:
        return await _show_pro_dashboard(pro)

    if text in Messages.Keywords.APPROVE_COMMANDS:
        return await _handle_approve(pro, lead_manager, whatsapp)

    if text in Messages.Keywords.REJECT_COMMANDS:
        return await _handle_reject(pro, whatsapp)

    if text in Messages.Keywords.FINISH_COMMANDS:
        return await _handle_finish(pro, whatsapp, chat_id)

    if text in Messages.Keywords.STILL_WORKING_COMMANDS:
        return await _handle_still_working(pro)

    if text in Messages.Keywords.DETAILS_COMMANDS:
        return await _handle_details(pro)

    if text in Messages.Keywords.CANCEL_BOOKED_COMMANDS:
        return await _handle_cancel(pro, whatsapp, chat_id)

    if text in Messages.Keywords.ACTIVE_JOBS_COMMANDS:
        return await _handle_active_jobs(pro)

    if text in Messages.Keywords.HISTORY_COMMANDS:
        return await _handle_history(pro)

    if text in Messages.Keywords.SUMMARY_COMMANDS:
        return await _handle_summary(pro)

    if text in Messages.Keywords.STATS_COMMANDS:
        return await _handle_stats(pro)

    if text in Messages.Keywords.REVIEWS_COMMANDS:
        return await _handle_reviews(pro)

    if text in Messages.Keywords.SEARCH_COMMANDS:
        return await _handle_search(pro, chat_id, whatsapp)

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
            await StateManager.set_state(
                chat_id, UserStates.AWAITING_INTENT_CONFIRMATION, ttl=300
            )
            # Each fresh prompt gets its own re-prompt budget
            meta = await StateManager.get_metadata(chat_id) or {}
            meta.pop("intent_reprompted", None)
            await StateManager.set_metadata(chat_id, meta)
            logger.info(f"Pro {chat_id} received intent-switch prompt for: {text[:60]}")
            return (
                ""  # sentinel: handled internally, caller must not send PRO_HELP_MENU
            )

    # Dynamic Timeout Reset
    latest_lead = await leads_collection.find_one(
        {"pro_id": pro["_id"], "status": {"$in": [LeadStatus.NEW, LeadStatus.BOOKED]}},
        sort=[("created_at", -1)],
    )
    if latest_lead and latest_lead.get("chat_id"):
        customer_chat_id = latest_lead["chat_id"]
        customer_state = await StateManager.get_state(customer_chat_id)
        if customer_state == UserStates.PAUSED_FOR_HUMAN:
            await StateManager.set_state(
                customer_chat_id,
                UserStates.PAUSED_FOR_HUMAN,
                ttl=WorkerConstants.PAUSE_TTL_SECONDS,
            )
            # Update paused_at for SLA monitor
            await leads_collection.update_one(
                {"_id": latest_lead["_id"]},
                {"$set": {"paused_at": datetime.now(timezone.utc)}},
            )
            logger.info(
                f"Pro {chat_id} sent a message; reset PAUSED_FOR_HUMAN TTL and updated paused_at for customer {customer_chat_id}"
            )

    # Fallback: show dashboard if no match
    return await _show_pro_dashboard(pro)


# --- Handlers ---


async def _show_pro_dashboard(pro):
    pro_name = pro.get("business_name") or Defaults.GENERIC_PRO_NAME
    rating = pro.get("candidate_score", 5.0)
    is_active = pro.get("is_active", True)
    status_emoji = "🟢" if is_active else "🔴"
    status_text = (
        Messages.Pro.STATUS_AVAILABLE if is_active else Messages.Pro.STATUS_ON_BREAK
    )

    pending_count = await leads_collection.count_documents(
        {"pro_id": pro["_id"], "status": LeadStatus.NEW}
    )
    booked_count = await leads_collection.count_documents(
        {"pro_id": pro["_id"], "status": LeadStatus.BOOKED}
    )

    lines = [
        Messages.Pro.PRO_DASHBOARD_HEADER.format(
            pro_name=pro_name,
            rating=rating,
            status_emoji=status_emoji,
            status_text=status_text,
            active_jobs=booked_count,
            max_jobs=WorkerConstants.MAX_PRO_LOAD,
        )
    ]

    # PRO-168: the dashboard and HELP_MENU both render the canonical Pro.CMD_*
    # rows, so the two menus can no longer advertise different keywords. The
    # dashboard shows the contextual subset; HELP_MENU shows all of them.
    if pending_count > 0:
        lines.append(Messages.Pro.CMD_APPROVE)
        lines.append(Messages.Pro.CMD_REJECT)
    if booked_count > 0:
        lines.append(Messages.Pro.CMD_FINISH)
        lines.append(Messages.Pro.CMD_DETAILS)
        lines.append(Messages.Pro.CMD_CANCEL)
    lines.append(Messages.Pro.CMD_SEARCH)
    lines.append(Messages.Pro.CMD_PAUSE if is_active else Messages.Pro.CMD_RESUME)
    lines.append(Messages.Pro.DASHBOARD_TIP)

    return "\n".join(lines)


async def _handle_job_selection(chat_id, text, pro, whatsapp, current_state):
    if text in Messages.Keywords.CANCEL_KEYWORDS or text == "ביטול":
        await StateManager.clear_state(chat_id)
        return Messages.Pro.ACTION_CANCELLED

    meta = await StateManager.get_metadata(chat_id)
    is_cancel_flow = current_state == UserStates.PRO_SELECTING_JOB_TO_CANCEL
    context_key = (
        "cancelling_jobs_context" if is_cancel_flow else "finishing_jobs_context"
    )
    mapping = meta.get(context_key, {})

    if text not in mapping:
        return Messages.Pro.INVALID_JOB_SELECTION

    lead_id = mapping[text]
    lead = await leads_collection.find_one(
        {"_id": ObjectId(lead_id), "pro_id": pro["_id"]}
    )
    if not lead:
        await StateManager.clear_state(chat_id)
        return Messages.Errors.GENERIC_ERROR

    await StateManager.clear_state(chat_id)
    if is_cancel_flow:
        await _execute_cancel(lead, pro, whatsapp)
        return Messages.Pro.CANCEL_SUCCESS
    else:
        return await _execute_finish(lead, pro, whatsapp, chat_id)


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
            "$or": [
                {
                    "pro_id": pro_id,
                    "status": {"$in": [LeadStatus.BOOKED, LeadStatus.REJECTED]},
                    "created_at": {"$gte": cutoff},
                },
                # PRO-117: after reject → rematch the lead's pro_id, status and
                # created_at are all rewritten for the new pro — this pro's
                # trace survives only in rejected_by/last_rejected_at.
                {"rejected_by": pro_id, "last_rejected_at": {"$gte": cutoff}},
            ]
        },
        sort=[("created_at", -1)],
    )
    return bool(recent)


async def _handle_approve(pro, lead_manager, whatsapp):
    lead = await leads_collection.find_one(
        {"pro_id": pro["_id"], "status": LeadStatus.NEW}, sort=[("created_at", -1)]
    )
    if not lead:
        # Fat-finger guard
        if await _recently_responded_lead(pro["_id"]):
            return Messages.Pro.ALREADY_RESPONDED
        return Messages.Pro.NO_PENDING_APPROVALS

    # PRO-123: guard the write on both the status we read *and* the ownership
    # we read. Status alone is not enough — `monitor_service.reassign_lead`
    # leaves the lead at NEW under the *new* pro, so a stale "אשר" arriving
    # after a reassignment would still match on status and hand the lead back
    # to the pro who never answered, on top of the pro already offered it.
    booked = await lead_manager.update_lead_status(
        str(lead["_id"]),
        LeadStatus.BOOKED,
        pro["_id"],
        actor=Actor.PRO,
        expected_status=LeadStatus.NEW,
        expected_pro_id=pro["_id"],
    )
    if booked is None:
        # Lost the race: the lead moved on between our read and our write.
        # Nothing is booked and nobody is messaged — the pro is told the truth.
        logger.info(
            f"Pro {pro['_id']} approved lead {lead['_id']} but it had already "
            "moved on (reassigned or answered elsewhere) — no booking made."
        )
        if await _recently_responded_lead(pro["_id"]):
            return Messages.Pro.ALREADY_RESPONDED
        return Messages.Pro.NO_PENDING_APPROVALS

    # PRO-120: center the slot search on the customer's requested time; the
    # created_at fallback (inside book_slot_for_lead) only applies to ASAP
    # leads that carry no concrete appointment_datetime.
    booked_slot_id = await book_slot_for_lead(
        pro["_id"],
        lead["created_at"],
        appointment_datetime=lead.get("appointment_datetime"),
    )
    booking_success = booked_slot_id is not None

    if booked_slot_id is not None:
        await leads_collection.update_one(
            {"_id": lead["_id"]},
            {"$set": {"booked_slot_id": booked_slot_id}},
        )

    response_text = Messages.Pro.APPROVE_SUCCESS
    if booking_success:
        response_text += Messages.Pro.CALENDAR_UPDATE_SUCCESS

    pro_name = pro.get("business_name", Defaults.EXPERT_NAME)
    raw_phone = pro.get("phone_number", "")
    pro_phone = to_local_phone(raw_phone)

    # Profession label
    from app.core.messages import Messages as M

    type_labels = M.Onboarding.TYPE_LABELS
    profession_line = ""
    if pro.get("profession_type"):
        label = type_labels.get(pro["profession_type"], pro["profession_type"])
        profession_line = Messages.Customer.PROFESSION_LINE.format(label=label)

    # PRO-55: show the customer the exact price the AI quoted them (persisted on the
    # lead) — the single source of truth shared with the pro's approval request,
    # not the pro's generic price list.
    price_line = ""
    quoted_price = lead.get("quoted_price")
    if quoted_price:
        price_line = Messages.Customer.QUOTED_PRICE_LINE.format(
            quoted_price=quoted_price
        )

    # Rating
    rating_line = ""
    sp = pro.get("social_proof", {})
    if sp.get("review_count", 0) > 0:
        rating_line = Messages.Customer.PRO_RATING_LINE.format(
            rating=sp["rating"], review_count=sp["review_count"]
        )

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


async def _handle_reject(pro, whatsapp):
    lead = await leads_collection.find_one(
        {"pro_id": pro["_id"], "status": LeadStatus.NEW}, sort=[("created_at", -1)]
    )
    if not lead:
        if await _recently_responded_lead(pro["_id"]):
            return Messages.Pro.ALREADY_RESPONDED
        return Messages.Pro.NO_PENDING_APPROVALS

    # PRO-117: claim the rejection atomically. expected_status=NEW makes a
    # concurrent writer (the Healer's own reassignment tick, or a double-tapped
    # דחה) lose cleanly instead of double-assigning the lead. rejected_by
    # accumulates every pro who explicitly declined, so the rematch below (and
    # any later Healer pass) never re-offers the lead to one of them —
    # excluding only the current pro would ping-pong A→B→A within
    # MAX_REASSIGNMENTS. last_rejected_at keeps the fat-finger guard working
    # after the rematch rewrites pro_id/status/created_at for the new pro.
    #
    # Known-accepted race: the guard covers status, not pro_id — a Healer
    # reassignment landing between the find_one above and this claim leaves
    # status at NEW, so this pro can "reject" a lead that just moved to a
    # third pro. Millisecond window; the SLA monitor self-heals it, and an
    # expected_pro_id guard on set_lead_status is not worth the churn today.
    rejected_by = list(lead.get("rejected_by") or []) + [pro["_id"]]
    claimed = await set_lead_status(
        lead["_id"],
        LeadStatus.REJECTED,
        Actor.PRO,
        extra_set={
            "rejected_by": rejected_by,
            "last_rejected_at": datetime.now(timezone.utc),
        },
        expected_status=LeadStatus.NEW,
    )
    if claimed is None:
        return Messages.Pro.ALREADY_RESPONDED
    lead = claimed

    # A rejected lead must never dead-end. Hand it straight to the shared
    # reassignment path — it excludes everyone in rejected_by, messages the
    # customer (CUSTOMER_REASSIGNING → AWAITING_APPROVAL_TRANSPARENT, or
    # PENDING_REVIEW on escalation to admin review) and counts the attempt
    # toward MAX_REASSIGNMENTS. Customer context is deliberately NOT cleared
    # here: the conversation continues with the next pro, and the escalation
    # branches inside reassign_lead clear state/context themselves — always
    # with a message, never a silent wipe.
    from app.services import monitor_service

    try:
        reassigned = await monitor_service.reassign_lead(lead, notify_old_pro=False)
    except Exception as e:
        logger.error(
            f"Reject rematch failed for lead {lead['_id']}: {e} — "
            "escalating to PENDING_ADMIN_REVIEW."
        )
        return await _escalate_rejected_lead(lead, whatsapp)

    if reassigned:
        return Messages.Pro.REJECT_SUCCESS

    # reassign_lead returned False: usually it escalated (with a customer
    # message) itself, but its idempotency/concurrency guards return False
    # without writing anything — verify the lead did not stay in terminal
    # REJECTED before telling the pro it was handed off.
    fresh = await leads_collection.find_one({"_id": lead["_id"]}, {"status": 1})
    fresh_status = (fresh or {}).get("status")
    if fresh_status == LeadStatus.REJECTED:
        return await _escalate_rejected_lead(lead, whatsapp)
    if fresh_status == LeadStatus.NEW:
        # A concurrent reassignment already moved it to the next pro.
        return Messages.Pro.REJECT_SUCCESS
    return Messages.Pro.REJECT_SUCCESS_ESCALATED


async def _escalate_rejected_lead(lead, whatsapp):
    """PRO-117 fallback: the rematch failed or no-opped — never leave the lead
    in terminal REJECTED. Escalates to admin review, pages the admin (the
    4-hourly Reporter is too slow for a branch that means something already
    broke), and releases the customer with a message. The expected_status
    guard keeps a concurrent writer from being clobbered; if it fails, the
    lead is already alive under someone else's transition."""
    from app.services import monitor_service

    escalated = await set_lead_status(
        lead["_id"],
        LeadStatus.PENDING_ADMIN_REVIEW,
        Actor.SYSTEM,
        extra_set={"escalation_reason": "reject_rematch_failed"},
        expected_status=LeadStatus.REJECTED,
    )
    if escalated:
        await monitor_service._alert_admin_lead_escalated(
            lead, lead.get("reassignment_count", 0)
        )
        chat_id = lead.get("chat_id")
        if chat_id:
            try:
                await whatsapp.send_message(chat_id, Messages.Customer.PENDING_REVIEW)
            except Exception as notify_err:
                logger.error(
                    f"Failed to notify customer of pending review after reject "
                    f"rematch failure (lead {lead['_id']}): {notify_err}"
                )
            await StateManager.clear_state(chat_id)
            await ContextManager.clear_context(chat_id)
    return Messages.Pro.REJECT_SUCCESS_ESCALATED


async def _handle_pause_bot(pro, whatsapp):
    """Pro clicked 'Pause Bot' — pause AI for the customer's chat."""
    lead = await leads_collection.find_one(
        {"pro_id": pro["_id"], "status": {"$in": [LeadStatus.NEW, LeadStatus.BOOKED]}},
        sort=[("created_at", -1)],
    )
    if not lead:
        return Messages.Pro.NO_PENDING_APPROVALS

    customer_chat_id = lead["chat_id"]
    await StateManager.set_state(
        customer_chat_id,
        UserStates.PAUSED_FOR_HUMAN,
        ttl=WorkerConstants.PAUSE_TTL_SECONDS,
    )

    # Set is_paused flag and paused_at for SLA monitor
    await leads_collection.update_one(
        {"_id": lead["_id"]},
        {"$set": {"is_paused": True, "paused_at": datetime.now(timezone.utc)}},
    )

    await whatsapp.send_message(customer_chat_id, Messages.Customer.BOT_PAUSED_BY_PRO)
    logger.info(f"Pro {pro['_id']} paused bot for customer {customer_chat_id}")
    return Messages.Pro.PAUSE_ACK


async def _handle_resume(pro):
    """Pro resumes the bot after a pause."""
    lead = await leads_collection.find_one(
        {"pro_id": pro["_id"], "status": {"$in": [LeadStatus.NEW, LeadStatus.BOOKED]}},
        sort=[("created_at", -1)],
    )
    if not lead:
        return Messages.Pro.NO_PAUSED_CONVERSATION

    customer_chat_id = lead["chat_id"]
    current_state = await StateManager.get_state(customer_chat_id)
    if current_state == UserStates.PAUSED_FOR_HUMAN:
        await StateManager.clear_state(customer_chat_id)
        # Clear is_paused flag
        await leads_collection.update_one(
            {"_id": lead["_id"]}, {"$set": {"is_paused": False}}
        )
        logger.info(f"Pro {pro['_id']} resumed bot for customer {customer_chat_id}")
        return Messages.Pro.BOT_RESUMED
    return Messages.Pro.BOT_ALREADY_ACTIVE


async def _handle_finish(pro, whatsapp, chat_id):
    cursor = leads_collection.find(
        {"pro_id": pro["_id"], "status": LeadStatus.BOOKED}
    ).sort("created_at", -1)
    leads = await cursor.to_list(length=10)

    if not leads:
        return Messages.Pro.NO_ACTIVE_JOBS

    if len(leads) == 1:
        return await _execute_finish(leads[0], pro, whatsapp, chat_id)

    # Multiple leads: Ask to select
    lines = []
    mapping = {}
    for i, lead in enumerate(leads, 1):
        name = lead.get("customer_name") or Messages.Fallbacks.CUSTOMER_NAME
        city = lead.get("city") or Messages.Fallbacks.UNKNOWN
        issue = lead.get("issue_type") or Messages.Fallbacks.ISSUE_UNKNOWN
        lines.append(
            Messages.Pro.JOB_SELECT_ROW.format(num=i, name=name, city=city, issue=issue)
        )
        mapping[str(i)] = str(lead["_id"])

    await StateManager.set_state(chat_id, UserStates.PRO_SELECTING_JOB_TO_FINISH)
    await StateManager.set_metadata(chat_id, {"finishing_jobs_context": mapping})

    return Messages.Pro.SELECT_JOB_TO_FINISH.format(jobs_list="\n".join(lines))


async def _handle_still_working(pro):
    """PRO-168: the answer `Pro.REMINDER` advertises for "not finished yet".

    The reminder used to offer 'עדיין עובד' and no keyword list contained it,
    so the reply fell through to the dashboard and the nudges kept arriving.
    The only thing the pro is asking for is quiet, and quiet is exactly what
    the reminder cap already expresses: both counters — `reminder_sent_count`
    (the Tier-1 finish reminder in `notification_service.send_pro_reminder`)
    and `reminders_sent` (the 24h stale-lead nudger in `monitor_service`) —
    are moved to `MAX_PRO_REMINDERS`, which each sender checks before writing.

    Applied to every BOOKED lead of this pro, because the reminder does not
    name a lead, so neither can the answer to it. The lead stays BOOKED and
    *סיימתי* still closes it; nothing else about the job changes.
    """
    result = await leads_collection.update_many(
        {"pro_id": pro["_id"], "status": LeadStatus.BOOKED},
        {
            "$set": {
                "reminder_sent_count": WorkerConstants.MAX_PRO_REMINDERS,
                "reminders_sent": WorkerConstants.MAX_PRO_REMINDERS,
            }
        },
    )
    logger.info(
        f"Pro {pro['_id']} replied 'still working' — silenced finish reminders "
        f"on {result.modified_count} booked lead(s)."
    )
    return Messages.Pro.STILL_WORKING_ACK


async def _execute_finish(lead, pro, whatsapp, pro_chat_id) -> str:
    """Mark a lead COMPLETED, notify the customer for a rating, then ask the pro
    what they charged (PRO-33). Returns the message to send the pro — completion
    is done *before* the price ask, so recording the price can never block it."""
    await set_lead_status(
        lead["_id"],
        LeadStatus.COMPLETED,
        Actor.PRO,
        extra_set={
            "completed_at": datetime.now(timezone.utc),
            "waiting_for_rating": True,
            "is_paused": False,
        },
    )

    # Clear cached context
    if lead.get("chat_id"):
        await ContextManager.clear_context(lead["chat_id"])

    pro_name = pro.get("business_name", Defaults.GENERIC_PRO_NAME)
    feedback_msg = Messages.Customer.RATE_SERVICE.format(pro_name=pro_name)
    await whatsapp.send_message(lead["chat_id"], feedback_msg)
    logger.info(f"Lead {lead['_id']} marked as COMPLETED by pro {pro['_id']}")

    # PRO-33: job is COMPLETED; now ask (optionally) what was charged. The lead id
    # rides in state metadata so the pro's next reply is attributed to this job.
    await StateManager.set_state(
        pro_chat_id,
        UserStates.PRO_AWAITING_FINAL_PRICE,
        ttl=WorkerConstants.FINAL_PRICE_TTL_SECONDS,
    )
    await StateManager.set_metadata(
        pro_chat_id, {"final_price_lead_id": str(lead["_id"])}
    )
    return Messages.Pro.FINISH_SUCCESS_ASK_PRICE


def _parse_final_price(text: str):
    """Parse a charged amount from a free-text pro reply. Returns an int/float in
    ILS, or None when the input isn't an unambiguous single positive number.

    Strips currency symbols and thousands separators. A range or a phone number
    (multiple digit groups) is rejected → caller leaves final_price null."""
    if not text:
        return None
    cleaned = text.replace(",", "").replace("₪", " ")
    nums = re.findall(r"\d+(?:\.\d+)?", cleaned)
    if len(nums) != 1:  # zero, or ambiguous (range / phone) → not a clean price
        return None
    val = float(nums[0])
    if val <= 0 or val > 1_000_000:  # sanity bounds
        return None
    return int(val) if val.is_integer() else round(val, 2)


async def _handle_final_price_reply(chat_id, text, pro) -> str:
    """Record (or skip) the final charged price for the just-completed job (PRO-33).
    Always clears the PRO_AWAITING_FINAL_PRICE state so the pro is never trapped —
    the lead is already COMPLETED, so any outcome here is non-blocking."""
    meta = await StateManager.get_metadata(chat_id) or {}
    lead_id = meta.get("final_price_lead_id")
    await StateManager.clear_state(chat_id)

    if not lead_id:
        # State without context (e.g. expired metadata) — nothing to attribute.
        return Messages.Pro.FINAL_PRICE_SKIPPED

    if text in Messages.Keywords.SKIP_COMMANDS:
        return Messages.Pro.FINAL_PRICE_SKIPPED

    price = _parse_final_price(text)
    if price is None:
        return Messages.Pro.FINAL_PRICE_INVALID

    commission = round(price * WorkerConstants.COMMISSION_RATE, 2)
    await leads_collection.update_one(
        {"_id": ObjectId(lead_id), "pro_id": pro["_id"]},
        {"$set": {"final_price": price, "commission_amount": commission}},
    )
    logger.info(
        f"Lead {lead_id}: final_price={price} commission={commission} "
        f"recorded by pro {pro['_id']}"
    )
    return Messages.Pro.FINAL_PRICE_RECORDED.format(price=price)


async def _handle_active_jobs(pro):
    cursor = leads_collection.find(
        {"pro_id": pro["_id"], "status": {"$in": [LeadStatus.NEW, LeadStatus.BOOKED]}},
        sort=[("created_at", -1)],
    )
    leads = await cursor.to_list(length=20)

    if not leads:
        return Messages.Pro.NO_ACTIVE_JOBS_LIST

    lines = [Messages.Pro.ACTIVE_JOBS_HEADER]
    for i, lead in enumerate(leads, 1):
        status_label = STATUS_LABELS.get(lead.get("status"), lead.get("status", "?"))
        issue = lead.get("issue_type", Messages.Fallbacks.UNKNOWN)
        # `or` covers both key-missing and key-present-with-None (nullable full_address)
        address = lead.get("full_address") or Messages.Fallbacks.UNKNOWN
        time = lead.get("appointment_time", Messages.Fallbacks.TIME_UNSET)
        lines.append(
            Messages.Pro.ACTIVE_JOB_ROW.format(
                num=i, status=status_label, issue=issue, address=address, time=time
            )
        )

    lines.append(Messages.Pro.ACTIVE_JOBS_TOTAL.format(count=len(leads)))
    return "\n".join(lines)


async def _handle_details(pro):
    cursor = leads_collection.find(
        {"pro_id": pro["_id"], "status": LeadStatus.BOOKED}, sort=[("created_at", -1)]
    )
    leads = await cursor.to_list(length=20)

    if not leads:
        return Messages.Pro.NO_ACTIVE_JOBS_LIST

    lines = [Messages.Pro.DETAILS_HEADER]
    for i, lead in enumerate(leads, 1):
        raw_phone = lead.get("customer_phone") or strip_suffix(lead.get("chat_id", ""))
        # Local display (0XX) and international for wa.me link (972XX)
        customer_phone = to_local_phone(raw_phone)
        customer_phone_intl = raw_phone
        city = lead.get("city") or ""
        street = lead.get("street") or ""
        issue = lead.get("issue_type") or Messages.Fallbacks.UNKNOWN
        appt = lead.get("appointment_time") or Messages.Fallbacks.TIME_UNSET
        address_query = (
            f"{street} {city}".strip()
            if (street or city)
            else (lead.get("full_address") or "ישראל")
        )
        address_encoded = urllib.parse.quote(address_query)
        lines.append(
            Messages.Pro.DETAILS_ROW.format(
                num=i,
                customer_phone=customer_phone,
                customer_phone_intl=customer_phone_intl,
                city=city or Messages.Fallbacks.UNKNOWN,
                issue=issue,
                appointment_time=appt,
                address_encoded=address_encoded,
            )
        )

    return "\n".join(lines)


async def _handle_cancel(pro, whatsapp, chat_id):
    cursor = leads_collection.find(
        {"pro_id": pro["_id"], "status": LeadStatus.BOOKED}
    ).sort("created_at", -1)
    leads = await cursor.to_list(length=10)

    if not leads:
        return Messages.Pro.NO_ACTIVE_JOBS_LIST

    if len(leads) == 1:
        await _execute_cancel(leads[0], pro, whatsapp)
        return Messages.Pro.CANCEL_SUCCESS

    lines = []
    mapping = {}
    for i, lead in enumerate(leads, 1):
        name = lead.get("customer_name") or Messages.Fallbacks.CUSTOMER_NAME
        city = lead.get("city") or Messages.Fallbacks.UNKNOWN
        issue = lead.get("issue_type") or Messages.Fallbacks.ISSUE_UNKNOWN
        lines.append(
            Messages.Pro.JOB_SELECT_ROW.format(num=i, name=name, city=city, issue=issue)
        )
        mapping[str(i)] = str(lead["_id"])

    await StateManager.set_state(chat_id, UserStates.PRO_SELECTING_JOB_TO_CANCEL)
    await StateManager.set_metadata(chat_id, {"cancelling_jobs_context": mapping})

    return Messages.Pro.SELECT_JOB_TO_CANCEL.format(jobs_list="\n".join(lines))


async def _execute_cancel(lead, pro, whatsapp):
    from app.core.database import slots_collection

    await set_lead_status(
        lead["_id"],
        LeadStatus.CANCELLED,
        Actor.PRO,
        extra_set={
            "cancelled_at": datetime.now(timezone.utc),
            "cancel_reason": "pro_cancelled",
            "is_paused": False,
        },
    )

    if lead.get("booked_slot_id"):
        await slots_collection.update_one(
            {"_id": lead["booked_slot_id"]},
            {"$set": {"is_taken": False}},
        )

    if lead.get("chat_id"):
        await ContextManager.clear_context(lead["chat_id"])
        await StateManager.clear_state(lead["chat_id"])
        await whatsapp.send_message(
            lead["chat_id"], Messages.Customer.PRO_CANCELLED_BOOKING
        )

    logger.info(f"Pro {pro['_id']} cancelled BOOKED lead {lead['_id']}")


async def _handle_history(pro):
    cursor = leads_collection.find(
        {"pro_id": pro["_id"], "status": LeadStatus.COMPLETED},
        sort=[("completed_at", -1)],
    )
    leads = await cursor.to_list(length=10)

    if not leads:
        return Messages.Pro.NO_HISTORY

    lines = [Messages.Pro.HISTORY_HEADER]
    for i, lead in enumerate(leads, 1):
        issue = lead.get("issue_type", Messages.Fallbacks.UNKNOWN)
        address = lead.get("full_address") or Messages.Fallbacks.UNKNOWN
        completed_at = lead.get("completed_at")
        if completed_at:
            if isinstance(completed_at, datetime):
                date_str = completed_at.strftime("%d/%m/%y")
            else:
                date_str = str(completed_at)[:10]
        else:
            date_str = Messages.Fallbacks.UNKNOWN
        lines.append(
            Messages.Pro.HISTORY_ROW.format(
                num=i, issue=issue, address=address, date=date_str
            )
        )

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
        joined = Messages.Fallbacks.UNKNOWN

    rating_str = f"{rating:.1f} ⭐" if rating else Messages.Pro.RATING_NONE

    return Messages.Pro.STATS_HEADER + Messages.Pro.STATS_BODY.format(
        completed=completed,
        active=active,
        rating=rating_str,
        reviews=review_count,
        joined=joined,
    )


async def _handle_summary(pro):
    pro_id = pro["_id"]
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    total_completed = await leads_collection.count_documents(
        {"pro_id": pro_id, "status": LeadStatus.COMPLETED}
    )
    this_month = await leads_collection.count_documents(
        {
            "pro_id": pro_id,
            "status": LeadStatus.COMPLETED,
            "completed_at": {"$gte": month_start},
        }
    )
    active = await leads_collection.count_documents(
        {"pro_id": pro_id, "status": {"$in": [LeadStatus.NEW, LeadStatus.BOOKED]}}
    )

    social_proof = pro.get("social_proof", {})
    rating = social_proof.get("rating", 0.0)
    rating_str = f"{rating:.1f} ⭐" if rating else Messages.Pro.RATING_NONE

    if total_completed >= 20:
        motivation = Messages.Pro.SUMMARY_MOTIVATION_GREAT
    elif total_completed >= 5:
        motivation = Messages.Pro.SUMMARY_MOTIVATION_GOOD
    else:
        motivation = Messages.Pro.SUMMARY_MOTIVATION_START

    return Messages.Pro.SUMMARY_BODY.format(
        this_month=this_month,
        total_completed=total_completed,
        active=active,
        rating=rating_str,
        motivation=motivation,
    )


async def _handle_reviews(pro):
    pro_id = pro["_id"]
    social_proof = pro.get("social_proof", {})
    rating_avg = social_proof.get("rating", 0.0)
    count = social_proof.get("review_count", 0)

    # Query reviews_collection directly for the last 3 entries with non-empty text
    review_cursor = reviews_collection.find(
        {"pro_id": pro_id, "comment": {"$exists": True, "$nin": [None, ""]}},
        sort=[("created_at", -1)],
    )
    text_reviews = await review_cursor.to_list(length=3)

    if not text_reviews:
        return Messages.Pro.NO_REVIEWS_WITH_TEXT

    lines = [Messages.Pro.REVIEWS_HEADER.format(rating=rating_avg, count=count)]
    for rev in text_reviews:
        lines.append(
            Messages.Pro.REVIEW_TEXT_ROW.format(
                rating=rev.get("rating", "?"),
                comment=rev.get("comment", ""),
            )
        )

    return "\n".join(lines)


async def _handle_search(pro, chat_id: str, whatsapp):
    """
    Proactive stuck-lead search for pros. Rate-limited per chat_id via Redis
    (PRO_SEARCH_RATE_LIMIT_SECONDS). Assigns the oldest PENDING_ADMIN_REVIEW
    lead *this pro is eligible for* to them as NEW so the existing "אשר"/"דחה"
    flow takes over.

    PRO-123: eligibility is the same predicate routing itself applies
    (`matching_service.is_pro_eligible_for_lead`) — a pro-initiated search must
    not be a back door to work `determine_best_pro` would never have offered
    them (out of area, over `MAX_PRO_LOAD`, paused, or already rejected by them).
    """
    # PRO-123: pro-level gates first, before the cool-down is consumed — these
    # are not a failed search, they are "you cannot take work right now", and
    # locking a 10-minute cool-down on them would punish the pro for asking.
    if not pro.get("is_active", True):
        return Messages.Pro.SEARCH_WHILE_PAUSED

    active_load = await leads_collection.count_documents(
        {
            "pro_id": pro["_id"],
            "status": {
                "$in": [LeadStatus.NEW, LeadStatus.CONTACTED, LeadStatus.BOOKED]
            },
        }
    )
    if active_load >= WorkerConstants.MAX_PRO_LOAD:
        return Messages.Pro.SEARCH_LOAD_FULL.format(
            active=active_load, max_jobs=WorkerConstants.MAX_PRO_LOAD
        )

    redis_client = await get_redis_client()
    rate_limit_key = f"rate_limit:pro_search:{chat_id}"

    ttl = await redis_client.ttl(rate_limit_key)
    if ttl > 0:
        minutes_left = math.ceil(ttl / 60)
        await whatsapp.send_message(
            chat_id,
            Messages.Pro.SEARCH_RATE_LIMITED.format(minutes=minutes_left),
        )
        # sentinel: handled internally, caller must not send PRO_HELP_MENU
        return ""

    # PRO-123: scan the queue oldest-first and take the first lead this pro is
    # actually eligible for, instead of handing over whatever happens to be at
    # the head of it. Checking only the oldest would also let one permanently
    # ineligible lead block the feature for everyone.
    candidates = await leads_collection.find(
        {"status": LeadStatus.PENDING_ADMIN_REVIEW},
        sort=[("created_at", 1)],
    ).to_list(length=WorkerConstants.DB_QUERY_LIMIT)

    # Lock the cool-down regardless of outcome
    await redis_client.setex(
        rate_limit_key,
        WorkerConstants.PRO_SEARCH_RATE_LIMIT_SECONDS,
        "1",
    )

    # Same fresh-start reset as the admin assignment path (PRO-63): a pro
    # claiming a stuck lead must clear the exhausted-reassignment state, or the
    # next Healer sweep re-escalates the lead off them and re-pages the admin.
    # `created_at` is reset for the same reason as there — the Healer measures
    # staleness from it, so leaving it stale would let the sweep yank the lead
    # back off the pro who just claimed it.
    stuck = None
    for candidate in candidates:
        if not await is_pro_eligible_for_lead(pro, candidate):
            continue
        now = datetime.now(timezone.utc)
        # expected_status makes the claim atomic: two pros sending מצא at once
        # (or the admin assigning from the panel) cannot both take one lead —
        # the loser gets None and moves on to the next candidate.
        claimed = await set_lead_status(
            candidate["_id"],
            LeadStatus.NEW,
            Actor.PRO,
            extra_set={
                "pro_id": pro["_id"],
                "assigned_by_admin_at": now,
                "created_at": now,
                "pro_notified_at": now,
                "reassignment_count": 0,
                "approval_nudged": False,
                "reassign_offered": False,
            },
            extra_unset={"escalation_reason": ""},
            expected_status=LeadStatus.PENDING_ADMIN_REVIEW,
        )
        if claimed is not None:
            stuck = candidate
            break

    if not stuck:
        return Messages.Pro.NO_STUCK_LEADS

    wait_minutes = 0
    created_at = stuck.get("created_at")
    if created_at:
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        wait_minutes = int(
            (datetime.now(timezone.utc) - created_at).total_seconds() / 60
        )

    logger.info(f"Pro {pro['_id']} claimed stuck lead {stuck['_id']} via search")

    return Messages.Pro.STUCK_LEAD_FOUND.format(
        issue=stuck.get("issue_type") or Messages.Fallbacks.UNKNOWN,
        city=stuck.get("city")
        or stuck.get("full_address")
        or Messages.Fallbacks.UNKNOWN,
        wait_minutes=wait_minutes,
    )
