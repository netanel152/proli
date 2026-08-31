from types import SimpleNamespace
from app.providers.whatsapp import get_whatsapp
from app.services.ai_engine_service import AIEngine, AIResponse
from app.services.lead_manager_service import (
    LeadManager,
    is_address_complete,
    compose_full_address,
)
from app.services.state_manager_service import StateManager
from app.services.context_manager_service import ContextManager
from app.core.background_tasks import spawn_background_task
from app.core.logger import logger
from app.core.database import users_collection, leads_collection, slots_collection
from app.core.messages import Messages
from app.core.prompts import Prompts
from app.core.constants import LeadStatus, Defaults, UserStates, WorkerConstants, Actor
from app.core.phone import to_chat_id, strip_suffix, mask_chat_id
from app.services.lead_manager_service import set_lead_status
from app.core.datetime_utils import parse_iso_to_utc
from app.core.redis_client import (
    acquire_chat_lock,
    release_chat_lock,
    ChatLockBusyError,
)
from app.core.text_matching import contains_keyword, is_emergency_text
from app.services.dispatch_guards import (
    HANDLED,
    DispatchContext,
    GuardDeps,
    run_guard_chain,
)
from app.services.matching_service import determine_best_pro

# The next few imports look unused since PRO-180 moved the holding-state
# cluster into dispatch_guards, but they are load-bearing: the guards resolve
# them at call time as `workflow_service.<name>` (keeping this module the
# single patch point the test suite monkeypatches), so they must stay
# importable here.
from app.services.notification_service import send_sos_alert  # noqa: F401
from app.services import notification_service
from app.services.data_management_service import (  # noqa: F401
    has_consent,
    record_consent,
)
from app.services.customer_flow import (  # noqa: F401
    send_customer_completion_check as _send_completion_check,
    handle_customer_completion_text as _handle_completion,
    handle_customer_rating_text,
    handle_customer_review_comment,
    handle_reschedule_selection as _handle_reschedule_selection,
    handle_status_query as _handle_status_query,
)
from app.services.scheduling_service import get_available_slots  # noqa: F401
import pytz
from app.services.pro_flow import handle_pro_text_command as _handle_pro_cmd
from app.services.pro_onboarding_service import (
    start_onboarding,
    handle_onboarding_step,
    ONBOARDING_STATES,
)
from app.services.media_handler import detect_and_fetch_media
from app.services.security_service import SecurityService
from app.core.config import settings
from bson import ObjectId
from bson.errors import InvalidId
import re
from datetime import datetime, timedelta, timezone

# Initialize services
whatsapp = get_whatsapp()
ai = AIEngine()
lead_manager = LeadManager()

_IL_TZ = pytz.timezone("Asia/Jerusalem")

# Internal deal marker the AI sometimes embeds in reply_to_user as a fallback
# deal-detection signal. It must never reach the customer.
DEAL_MARKER_RE = re.compile(r"\[DEAL:.*?\]", re.DOTALL)

# PRO-169: personas generated before that fix asked the model to open an
# emergency reply with a literal "[URGENT]" tag that nothing ever parsed, so it
# reached the customer verbatim. Approved pros keep the persona stored on their
# document at approval time, so the tag can still be emitted until PRO-177
# regenerates them. Stripped at the same seam as the deal marker — and kept as
# a *separate* pattern, because DEAL_MARKER_RE.search() doubles as the
# fallback deal-detection signal and an emergency tag must never count as one.
URGENT_TAG_RE = re.compile(r"\[URGENT\]")

# PRO-55: a price quote is a plain number or number-range. The value is
# AI-extracted from a conversation that contains customer-controlled text and is
# rendered verbatim into the pro's trust-critical approval message, so it must be
# validated to a price shape — never let free text (prompt injection) land there.
_QUOTED_PRICE_RE = re.compile(r"^\d{1,6}(\s*[-–]\s*\d{1,6})?$")


def _clean_quoted_price(value) -> "str | None":
    """Sanitize + validate an AI-extracted price quote. Returns a normalized
    'NNN' or 'NNN-NNN' string, or None if the value is missing or is not a plain
    price shape (defends the pro approval message against injected text)."""
    if not value:
        return None
    cleaned = str(value).replace("₪", "").strip()
    if not _QUOTED_PRICE_RE.match(cleaned):
        return None
    return re.sub(r"\s*[-–]\s*", "-", cleaned)


def _strip_deal_marker(text: str) -> str:
    """Strip the internal [DEAL:...] marker from customer-facing text.

    Detection must run on the original (raw) text before calling this —
    this only cleans the copy that gets sent/logged.
    """
    cleaned = DEAL_MARKER_RE.sub("", text or "")
    cleaned = URGENT_TAG_RE.sub("", cleaned)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


# Pro business keywords that must always route to pro_flow, even mid-CUSTOMER_MODE
PRO_BUSINESS_KEYWORDS = (
    set(Messages.Keywords.APPROVE_COMMANDS)
    | set(Messages.Keywords.REJECT_COMMANDS)
    | set(Messages.Keywords.FINISH_COMMANDS)
    # PRO-168: `Pro.REMINDER` advertises *סיימתי* and *עדיין עובד* as equals,
    # so they need equal routing. Without this, a pro who ordered service for
    # themselves — exactly the pro parked in CUSTOMER_MODE / the
    # AWAITING_PRO_APPROVAL hold — answers the reminder and reaches the
    # customer AI instead of `pro_flow._handle_still_working`.
    | set(Messages.Keywords.STILL_WORKING_COMMANDS)
    | set(Messages.Keywords.ACTIVE_JOBS_COMMANDS)
    | set(Messages.Keywords.HISTORY_COMMANDS)
    | set(Messages.Keywords.STATS_COMMANDS)
    | set(Messages.Keywords.REVIEWS_COMMANDS)
    | set(Messages.Keywords.RESUME_COMMANDS)
    | set(Messages.Keywords.PAUSE_COMMANDS)
    | set(Messages.Keywords.BOT_RESUME_COMMANDS)
    | set(Messages.Keywords.BOT_PAUSE_COMMANDS)
)

# Subset of the above that a *customer-side* prompt may legitimately expect:
# every bare digit (menu picks — slot lists, rating 1-5, yes/no prompts) plus the
# words that read the same in both roles. In CUSTOMER_MODE these must not blindly
# snap a pro back to PRO_MODE while a customer-side interaction is pending;
# everything else (סיימתי, חפש, הפסקה, זמין, ...) still bypasses unconditionally.
AMBIGUOUS_PRO_KEYWORDS = {kw for kw in PRO_BUSINESS_KEYWORDS if kw.isdigit()} | {
    "אשר",
    "דחה",
    "המשך",
    "resume",
    "השהה",
    "pause",
}

# The rest bypass unconditionally — no customer-side prompt ever expects them.
PRO_ONLY_KEYWORDS = PRO_BUSINESS_KEYWORDS - AMBIGUOUS_PRO_KEYWORDS

# States in which the *customer* side of the conversation is holding a numbered
# question open, so a bare digit belongs to it rather than to the pro dashboard.
CUSTOMER_PROMPT_STATES = (
    UserStates.AWAITING_RESCHEDULE_TIME,
    UserStates.AWAITING_LOYALTY_CONFIRMATION,
    UserStates.AWAITING_NEW_OR_EXISTING,
    UserStates.AWAITING_CANCEL_CONFIRMATION,
)

# Lead statuses that mean the pro-as-customer still has a live request of their
# own. COMPLETED counts until rating *and* free-text review are collected.
CUSTOMER_ACTIVE_STATUSES = [
    LeadStatus.CONTACTED,
    LeadStatus.NEW,
    LeadStatus.BOOKED,
]


async def _is_registered_pro(chat_id: str):
    """Return the professional doc for this chat, or None."""
    phone = strip_suffix(chat_id)
    return await users_collection.find_one(
        {
            "phone_number": {"$in": [phone, chat_id]},
            "role": "professional",
            "is_active": True,
        }
    )


async def _get_active_customer_lead(chat_id: str):
    """Return this chat's own open lead (as a customer), or None.

    Keeps a pro who is currently being served as a customer inside CUSTOMER_MODE:
    the post-dispatch auto-return and the IDLE auto-detect both defer to it.
    """
    return await leads_collection.find_one(
        {
            "chat_id": chat_id,
            "$or": [
                {"status": {"$in": CUSTOMER_ACTIVE_STATUSES}},
                {"status": LeadStatus.COMPLETED, "waiting_for_rating": True},
                {"status": LeadStatus.COMPLETED, "waiting_for_review_comment": True},
            ],
        },
        sort=[("created_at", -1)],
    )


async def _customer_prompt_pending(chat_id: str, current_state: str) -> bool:
    """True when a customer-side question is open and expecting this reply.

    Deliberately narrower than "has an open lead": a BOOKED lead can sit for days,
    and blocking אשר/דחה for that whole window would stop the pro from answering
    incoming job offers — the very lockout this ticket is fixing.
    """
    if current_state in CUSTOMER_PROMPT_STATES:
        return True
    return bool(
        await leads_collection.find_one(
            {
                "chat_id": chat_id,
                "$or": [
                    {"waiting_for_rating": True},
                    {"waiting_for_review_comment": True},
                ],
            }
        )
    )


# --- Public API (used by scheduler, admin panel, arq_worker) ---


async def send_customer_completion_check(lead_id: str, triggered_by: str = "auto"):
    """Public wrapper — delegates to customer_flow with shared whatsapp instance."""
    await _send_completion_check(lead_id, whatsapp, triggered_by)


async def send_pro_reminder(lead_id: str, triggered_by: str = "auto"):
    """Re-export from notification_service for scheduler compatibility."""
    from app.services.notification_service import send_pro_reminder as _reminder

    await _reminder(lead_id, triggered_by)


# --- Main Orchestrator ---


async def process_incoming_message(chat_id: str, user_text: str, media_url: str = None):
    """
    Entry point for all incoming customer/pro messages.

    Wraps the actual handler in a Redis-backed per-chat lock so concurrent ARQ
    tasks for the same chat_id (e.g. rapid-fire messages) don't race on state /
    lead creation. On lock contention we raise ChatLockBusyError so the ARQ
    task wrapper can requeue; on Redis failure the helper returns True and we
    proceed in degraded mode.
    """
    acquired = await acquire_chat_lock(chat_id, ttl=10)
    if not acquired:
        logger.info(
            f"🔒 Chat lock held for {chat_id} — another task is mid-flight; deferring"
        )
        raise ChatLockBusyError(chat_id)

    try:
        spawn_background_task(
            whatsapp.send_chat_state_typing(chat_id),
            name=f"typing:{mask_chat_id(chat_id)}",
        )
        await _process_incoming_message_inner(chat_id, user_text, media_url)
    finally:
        await release_chat_lock(chat_id)


async def _decline_loyalty_offer(chat_id) -> None:
    """Customer declined the loyalty offer, or it could not be honoured —
    release the state and fall back to normal matching (PRO-119).

    ``clear_state`` (not ``set_state(IDLE)``) so the transient
    ``loyalty_reprompted`` / ``past_pro_id`` metadata goes with it rather than
    lingering on its own 4h TTL.
    """
    await StateManager.clear_state(chat_id)
    await whatsapp.send_message(chat_id, Messages.Customer.LOYALTY_DECLINED)
    await lead_manager.log_message(chat_id, "model", Messages.Customer.LOYALTY_DECLINED)


async def _accept_loyalty_offer(chat_id, past_pro_id, active_lead, meta) -> bool:
    """Customer accepted the loyalty offer (PRO-119). Returns False when the
    offer can't be honoured, so the caller falls back to the decline copy.

    The old handler always answered "אני בודק מולו ומעדכן" and went IDLE
    without contacting anyone — the pro only heard about it if the customer
    happened to write again. The promise is now only made when it is true:
    the offer fires mid-intake, and the PRO-43 hard address gate forbids
    dispatching a pro without a complete address, so a lead that isn't ready
    gets an honest "saved your preference, here's what's still missing"
    instead.
    """
    if not past_pro_id or not active_lead:
        return False
    try:
        past_pro = await users_collection.find_one(
            {"_id": ObjectId(past_pro_id), "is_active": True}
        )
    except InvalidId:
        past_pro = None
    if not past_pro:
        # Offered while active, deactivated since (or corrupt metadata).
        logger.info(
            f"Loyalty offer accepted by ...{chat_id[-8:]} but pro {past_pro_id} is "
            "no longer available — falling back to normal matching"
        )
        return False

    pro_name = past_pro.get("business_name", Defaults.EXPERT_NAME)
    meta.pop("loyalty_reprompted", None)
    await StateManager.set_metadata(chat_id, meta)

    # Does the lead already carry a dispatchable address? Judged on the five
    # persisted parts only — deliberately NOT on `full_address`, which an
    # intake lead carries as the bare city (`full_address=extracted_city` at
    # lead creation) and which would wave a city-only dispatch straight past
    # the PRO-43 gate. Every route that composes a real `full_address` writes
    # the parts first (the AWAITING_ADDRESS merge persists `non_empty` before
    # composing), so nothing legitimate is lost by ignoring it here.
    probe = SimpleNamespace(
        **{
            field: active_lead.get(field)
            for field in ("street", "street_number", "city", "floor", "apartment")
        }
    )
    ok, reason = is_address_complete(probe)
    if not ok and active_lead.get("is_emergency") and active_lead.get("city"):
        # Same bypass _finalize_deal grants an emergency: a known city is
        # enough when someone's home is flooding.
        ok, reason = True, ""

    if not ok:
        await leads_collection.update_one(
            {"_id": active_lead["_id"]}, {"$set": {"pro_id": past_pro["_id"]}}
        )
        # AWAITING_ADDRESS, not IDLE: the ack below *is* the address gate's own
        # missing-parts question, and that state owns the re-extract/merge/
        # compose answer path. Routed through IDLE the reply would land on the
        # generic persona turn instead.
        await StateManager.set_state(chat_id, UserStates.AWAITING_ADDRESS)
        ack = Messages.Customer.LOYALTY_ACCEPTED_NEED_DETAILS.format(
            pro_name=pro_name, missing=reason
        )
        await whatsapp.send_message(chat_id, ack)
        await lead_manager.log_message(chat_id, "model", ack)
        logger.info(
            f"Loyalty accepted by ...{chat_id[-8:]} — pro {past_pro['_id']} saved, "
            "address incomplete so intake continues"
        )
        return True

    # Dispatchable: hand the lead to the chosen pro for real, arming the PRO-56
    # approval SLA (and therefore PRO-117's rematch) exactly like the initial
    # assignment path does. Actor.CUSTOMER (not SYSTEM, as _finalize_deal uses
    # for the same edge) because this transition is the customer's own choice
    # of pro, and the funnel history should say so.
    dispatch_fields = {
        "pro_id": past_pro["_id"],
        "pro_notified_at": datetime.now(timezone.utc),
        "approval_nudged": False,
        "reassign_offered": False,
    }
    # The offer builder reads these straight off the lead: an intake lead's
    # `full_address` is the bare city and its `appointment_time` is the English
    # "Pending" sentinel, both of which would land verbatim in a Hebrew offer.
    if active_lead.get("street") and active_lead.get("street_number"):
        dispatch_fields["full_address"] = compose_full_address(probe)
    elif not active_lead.get("full_address") and active_lead.get("city"):
        # Emergency bypass: no composable parts (or a street with no number,
        # which would render "הרצל None, תל אביב"). Same fallback
        # _finalize_deal uses there, so an emergency offer never prints
        # "לא ידוע" for a lead whose city we do know.
        dispatch_fields["full_address"] = active_lead["city"]
    if (active_lead.get("appointment_time") or Defaults.PENDING_TIME) in (
        Defaults.PENDING_TIME,
        Defaults.ASAP_TIME,
    ):
        dispatch_fields["appointment_time"] = Messages.Fallbacks.TIME_ASAP
    # expected_status guards the read-then-write gap: a monitor escalation or a
    # concurrent assignment must not be silently resurrected under this pro.
    # CONTACTED specifically — a lead that reached NEW is already assigned to and
    # notified at some pro, and yanking it here would skip the old-pro notice
    # that monitor_service.reassign_lead sends.
    updated = await set_lead_status(
        active_lead["_id"],
        LeadStatus.NEW,
        Actor.CUSTOMER,
        extra_set=dispatch_fields,
        expected_status=LeadStatus.CONTACTED,
    )
    if updated is None:
        logger.info(
            f"Loyalty dispatch for ...{chat_id[-8:]} lost the status race on lead "
            f"{active_lead['_id']} — leaving it to the current owner"
        )
        # None (not False) so the caller can tell "the lead moved under you"
        # from "this offer can't be honoured" — answering a *yes* with "I'll go
        # find you someone" would be a promise the winner already invalidated.
        return None
    notified = await notification_service.notify_pro_new_lead(
        updated, past_pro, whatsapp
    )
    if not notified:
        # Deliberately fail-open — unlike reassign_lead, which now escalates a
        # failed offer to PENDING_ADMIN_REVIEW: here the customer just said
        # "yes" to their previous pro, the SLA clock below is armed, and its
        # customer-side recovery (nudge at 10m, reassignment offer at 25m)
        # doesn't depend on the pro's closed window. Escalating would discard
        # a valid loyalty preference over what may be a transient failure.
        logger.error(
            f"Loyalty dispatch to pro {past_pro['_id']} failed to send for "
            f"...{chat_id[-8:]} — SLA monitor will recover the lead"
        )
    await StateManager.set_state(
        chat_id,
        UserStates.AWAITING_PRO_APPROVAL,
        ttl=WorkerConstants.PRO_APPROVAL_TTL_SECONDS,
    )
    ack = Messages.Customer.LOYALTY_ACCEPTED_NOTIFYING.format(pro_name=pro_name)
    await whatsapp.send_message(chat_id, ack)
    await lead_manager.log_message(chat_id, "model", ack)
    logger.info(
        f"Loyalty accepted by ...{chat_id[-8:]} — lead {active_lead['_id']} "
        f"dispatched to pro {past_pro['_id']}"
    )
    return True


async def _execute_customer_cancel(booked_lead, chat_id: str) -> None:
    """Execute a customer-confirmed cancel of a BOOKED lead (PRO-118 step 2).

    Runs only after the customer answered '1' to CANCEL_CONFIRM_PROMPT. The
    status write is guarded on BOOKED so a concurrent transition (pro finished
    or cancelled meanwhile) turns this into an honest "already updated" reply
    instead of a double-fire.
    """
    pro_id = booked_lead.get("pro_id")
    cancelled = await set_lead_status(
        booked_lead["_id"],
        LeadStatus.CANCELLED,
        Actor.CUSTOMER,
        extra_set={
            "cancelled_at": datetime.now(timezone.utc),
            "cancel_reason": "customer_requested",
        },
        expected_status=LeadStatus.BOOKED,
    )
    if cancelled is None:
        await whatsapp.send_message(chat_id, Messages.Customer.CANCEL_NO_ACTIVE)
        return
    # Free the reserved slot so the pro regains that hour.
    # Mirrors the release in pro_flow._execute_cancel; guarded so
    # legacy/emergency leads with no booked_slot_id are a no-op.
    if booked_lead.get("booked_slot_id"):
        await slots_collection.update_one(
            {"_id": booked_lead["booked_slot_id"]},
            {"$set": {"is_taken": False}},
        )
    await StateManager.clear_state(chat_id)
    await ContextManager.clear_context(chat_id)
    await whatsapp.send_message(chat_id, Messages.Customer.CANCELLED_ACTIVE_LEAD)
    logger.info(
        f"Customer ...{chat_id[-8:]} cancelled BOOKED lead {booked_lead['_id']}"
    )
    if pro_id:
        pro = await users_collection.find_one({"_id": pro_id})
        if pro and pro.get("phone_number"):
            pro_phone = to_chat_id(pro["phone_number"])
            await whatsapp.send_message(
                pro_phone,
                Messages.Pro.CUSTOMER_CANCELLED.format(
                    customer_name=booked_lead.get("customer_name") or "הלקוח",
                    address=booked_lead.get("full_address") or "לא ידועה",
                ),
            )


# PRO-121: the holding states whose handler `return`s unconditionally, so an
# emergency declared while parked in one used to be swallowed entirely.
# `_escalate_emergency` either re-answers or releases each of them.
#
# Deliberately absent, each for its own reason:
#   PAUSED_FOR_HUMAN — a live human owns the conversation and the bot must not
#     talk over them. Handled separately in that branch: the lead is flagged and
#     the operator paged once, with no customer-facing message and no un-pause.
#   AWAITING_CANCEL_CONFIRMATION — costs one turn, not an escalation. An
#     emergency message reads as "not confirmed", so the job is kept, the
#     transient state clears and the *next* message escalates normally.
#   AWAITING_RESCHEDULE_TIME — a product call, not an oversight: the customer is
#     picking a slot, and "דחוף" there almost always means "the earliest one you
#     have", not a new emergency. Releasing the menu on that word would lose the
#     slot list far more often than it would catch a real fire.
#   PRO_MODE / ONBOARDING_* / ADMIN_* — no emergency keyword collides with the
#     pro or admin vocabularies.
EMERGENCY_HOLDING_STATES = (
    UserStates.AWAITING_PRO_APPROVAL,
    UserStates.AWAITING_ADDRESS,
    UserStates.AWAITING_LOYALTY_CONFIRMATION,
    UserStates.AWAITING_NEW_OR_EXISTING,
)


def _inbound_log_text(user_text, media_url) -> str:
    """The single rendering of an inbound turn for the chat history."""
    if media_url:
        return f"{user_text or ''} [MEDIA: {media_url}]"
    return user_text


def _state_label(state) -> str:
    """Render a state for a log line (PRO-121).

    `StateManager.get_state` returns a plain Redis string on every real path but
    the *enum member* `UserStates.IDLE` as its default, which f-strings render as
    "UserStates.IDLE" — the same trap PRO-118 documents for persisted state.
    """
    return getattr(state, "value", state)


def _emergency_ack_for(city) -> str:
    """Pick the emergency ack that matches reality (PRO-121).

    Matching is a `$geoNear`/city lookup, so with no city there is nothing to
    summon anyone with. Promise the shortened intake only when we can act on
    it; otherwise ask for the one field that unblocks everything.
    """
    return (
        Messages.Customer.EMERGENCY_ACK
        if city
        else Messages.Customer.EMERGENCY_NEED_CITY
    )


async def _escalate_emergency(chat_id: str, current_state, user_text: str = ""):
    """Honour an emergency declared *inside* a holding state (PRO-121).

    `is_emergency_detected` used to be read only where a lead is created or
    updated, far below every holding branch — so "יש שריפה" typed while waiting
    for a pro's approval, mid address gate, or against the loyalty menu was
    answered with the holding question and never reached the lead at all.

    Returns ``(action, deferred_ack)``. ``action`` is ``"handled"`` (the
    dispatcher must stop here), ``"released"`` (the holding state was cleared,
    carry on with normal routing) or ``None`` (nothing to escalate — leave the
    existing branch to answer).

    ``deferred_ack`` is copy the *caller* must send, and only ever accompanies
    ``"released"``. Sending it here would put a ``"model"`` turn into the
    history ahead of the ``"user"`` turn that provoked it — the dispatcher logs
    the inbound further down — and that inverted window is what
    ``get_chat_history`` hands Gemini. The ``"handled"`` branches answer the
    turn themselves and log both sides in order, so they send inline.
    """
    active_lead = await leads_collection.find_one(
        {"chat_id": chat_id, "status": {"$in": [LeadStatus.NEW, LeadStatus.CONTACTED]}},
        sort=[("created_at", -1)],
    )
    if not active_lead:
        # No live request to carry the flag. There is still something to fix in
        # the menu states: AWAITING_NEW_OR_EXISTING in particular is reached with
        # only a BOOKED lead, and its re-prompt would swallow the emergency
        # outright. Release them and let intake below create the lead with
        # is_emergency and send the ack — double-answering here would just add a
        # second message. AWAITING_PRO_APPROVAL is left alone: with no
        # NEW/CONTACTED lead that hold is already stale and its reply is harmless.
        if current_state == UserStates.AWAITING_PRO_APPROVAL:
            return None, None
        await StateManager.clear_state(chat_id)
        logger.info(
            f"🚑 Emergency released {_state_label(current_state)} for ...{chat_id[-8:]} "
            "with no "
            "active lead — routing to intake"
        )
        return "released", None

    already_flagged = bool(active_lead.get("is_emergency"))
    if not already_flagged:
        await leads_collection.update_one(
            {"_id": active_lead["_id"]}, {"$set": {"is_emergency": True}}
        )
        logger.warning(
            f"🚑 Emergency declared mid-conversation by ...{chat_id[-8:]} in state "
            f"{_state_label(current_state)} (lead={active_lead['_id']}) — escalating"
        )

    if current_state == UserStates.AWAITING_PRO_APPROVAL:
        # The offer is already with a pro, so the useful action is speed, not a
        # rematch: monitor_service halves APPROVAL_NUDGE_MINUTES and
        # APPROVAL_REASSIGN_OFFER_MINUTES for an is_emergency lead, and the flag
        # is now set.
        #
        # Throttled on its own lead field rather than on `already_flagged`: the
        # mainline emergency is flagged back at intake and *then* parks here, so
        # gating on the flag transition would make this copy unreachable for
        # exactly the customer it is written for — they would shout "שריפה!"
        # and get the generic soft-hold reply, which is the bug this issue is
        # about. Claimed atomically so two workers can't both answer.
        claimed = await leads_collection.find_one_and_update(
            {"_id": active_lead["_id"], "emergency_hold_acked": {"$ne": True}},
            {"$set": {"emergency_hold_acked": True}},
        )
        if not claimed:
            return None, None
        await lead_manager.log_message(chat_id, "user", user_text or "")
        await whatsapp.send_message(chat_id, Messages.Customer.EMERGENCY_WHILE_WAITING)
        await lead_manager.log_message(
            chat_id, "model", Messages.Customer.EMERGENCY_WHILE_WAITING
        )
        return "handled", None

    if current_state == UserStates.AWAITING_ADDRESS:
        # The emergency address bypass existed only in _finalize_deal, which
        # this state never reaches — the re-entry handler kept asking for the
        # missing parts instead. Release the gate: with a city we already have
        # everything an emergency dispatch needs.
        await StateManager.clear_state(chat_id)
        # `city` OR `full_address`: create_lead_from_dict stores the intake city
        # as `full_address` and leaves `city` unset (the "Unknown Address"
        # migration left it that way), so reading `city` alone would ask a
        # customer who already told us their city where they are.
        known_city = active_lead.get("city") or active_lead.get("full_address")
        if known_city:
            logger.info(
                f"🚑 Emergency released the address gate for ...{chat_id[-8:]} "
                f"(city={known_city!r}) — routing to matching"
            )
            return "released", (
                None if already_flagged else Messages.Customer.EMERGENCY_ACK
            )
        # No city at all: nothing to match on. Ask for that one field rather
        # than letting the five-part gate re-ask for street, floor and
        # apartment. Sent on every pass through here (not just the first),
        # because it is this turn's only answer.
        #
        # This branch answers the turn itself, so the inbound never reaches the
        # dispatcher's single log_message — record it here, exactly as the
        # AWAITING_ADDRESS handler it replaces does.
        await lead_manager.log_message(chat_id, "user", user_text or "")
        await whatsapp.send_message(chat_id, Messages.Customer.EMERGENCY_NEED_CITY)
        await lead_manager.log_message(
            chat_id, "model", Messages.Customer.EMERGENCY_NEED_CITY
        )
        return "handled", None

    # AWAITING_LOYALTY_CONFIRMATION / AWAITING_NEW_OR_EXISTING — menu questions
    # about *preference*, which must not outrank an emergency. Drop the question
    # and let normal routing find whoever is closest and free.
    await StateManager.clear_state(chat_id)
    logger.info(
        f"🚑 Emergency dropped the {_state_label(current_state)} menu for "
        f"...{chat_id[-8:]} — "
        "routing normally"
    )
    return "released", (
        None
        if already_flagged
        else _emergency_ack_for(
            active_lead.get("city") or active_lead.get("full_address")
        )
    )


async def _process_incoming_message_inner(
    chat_id: str, user_text: str, media_url: str = None
):
    normalized_text = (user_text or "").strip().lower()

    # PRO-179 (PRO-139 slice A1): the head of the dispatch is an ordered guard
    # chain, defined as data in app/services/dispatch_guards.py. The ordering is
    # the contract — PRO-121's "position is the whole design" — and is now pinned
    # by a test instead of by the sequence these clauses happened to be written in.
    ctx = DispatchContext(
        chat_id=chat_id,
        user_text=user_text,
        media_url=media_url,
        normalized_text=normalized_text,
        # PRO-121: one shared detector (`customer_flow` calls the same one) —
        # exact whole-token keywords plus clitic-prefixable stems, minus the
        # negations. Substring matching read "קצר" out of "בקצרה", which was
        # harmless while the flag only tagged a lead at creation and is not now
        # that it short-circuits a holding state.
        is_emergency_detected=is_emergency_text(normalized_text),
        # Get state early — needed to skip global checks for pros
        current_state=await StateManager.get_state(chat_id),
    )
    deps = GuardDeps(
        whatsapp=whatsapp,
        state_manager=StateManager,
        context_manager=ContextManager,
        users_collection=users_collection,
        security=SecurityService,
        settings=settings,
    )

    if await run_guard_chain(ctx, deps) is HANDLED:
        return

    # Read the shared locals back out. The guards mutate them on the way past —
    # the rate limiter resolves `is_exempt` (still read at the three daily
    # AI-cap call sites below), the consent gate, zero-touch's second miss,
    # the emergency hoist's "released" path and loyalty's double-miss release
    # all refresh `current_state` before falling through, and the emergency
    # hoist (PRO-180 slice A2) sets `emergency_inbound_logged` so step 1 below
    # doesn't log the same inbound turn twice — so the rest of this function
    # must take the post-chain values, not the pre-chain ones.
    is_emergency_detected = ctx.is_emergency_detected
    current_state = ctx.current_state
    is_exempt = ctx.is_exempt
    emergency_inbound_logged = ctx.emergency_inbound_logged

    # Explicit mode switch: a registered pro who types "לקוח" needs service for
    # themselves. Deterministic — no AI, no confirmation prompt. Works from
    # PRO_MODE and from IDLE (where auto-detect would otherwise force PRO_MODE).
    if (
        normalized_text in Messages.Keywords.CUSTOMER_MODE_COMMANDS
        and current_state in (UserStates.PRO_MODE, UserStates.IDLE)
    ):
        if await _is_registered_pro(chat_id):
            await StateManager.set_state(chat_id, UserStates.CUSTOMER_MODE)
            await ContextManager.clear_context(chat_id)
            await whatsapp.send_message(chat_id, Messages.Pro.SWITCHED_TO_CUSTOMER)
            logger.info(f"Pro ...{chat_id[-8:]} switched to CUSTOMER_MODE via keyword")
            return

    # Safety Bypass: a registered pro typing a business keyword always routes to pro_flow,
    # even if they're currently in CUSTOMER_MODE — snap them back to PRO_MODE first.
    # Ambiguous keywords (bare digits, אשר/דחה, ...) yield to a customer-side question
    # that is actually open: mid-reschedule, a "3" is a slot pick, not a job approval.
    if normalized_text in PRO_BUSINESS_KEYWORDS:
        is_pro_doc = await _is_registered_pro(chat_id)
        if is_pro_doc and current_state != UserStates.PRO_MODE:
            defer_to_customer_flow = normalized_text in AMBIGUOUS_PRO_KEYWORDS and (
                await _customer_prompt_pending(chat_id, current_state)
            )
            if defer_to_customer_flow:
                logger.info(
                    f"Pro ...{chat_id[-8:]} sent ambiguous keyword with a customer "
                    f"prompt open — staying in {current_state}"
                )
            else:
                await StateManager.set_state(chat_id, UserStates.PRO_MODE)
                current_state = UserStates.PRO_MODE

    # Handle Pro Mode
    if current_state == UserStates.PRO_MODE:
        pro_resp = await _handle_pro_cmd(
            chat_id, user_text, whatsapp, lead_manager, ai=ai
        )
        if pro_resp:
            await whatsapp.send_message(chat_id, pro_resp)
        # empty string "" means pro_flow already sent everything internally
        return

    # Handle Pro Onboarding Flow
    if current_state in ONBOARDING_STATES:
        await handle_onboarding_step(chat_id, user_text or "", current_state, whatsapp)
        return

    # Handle Awaiting Address — re-entry after the finalization gate rejected an
    # incomplete address. Re-run extraction on the customer's reply, merge with
    # whatever we already stored, and only clear the state when all five fields
    # (street, number, city, floor, apartment) are present.
    if current_state == UserStates.AWAITING_ADDRESS:
        # Nevermind/cancel bailout: user wants out of the flow instead of fighting
        # the address gate. Match cancellation keywords BEFORE is_address_complete
        # so we never loop the user back through "אני צריך רחוב ומספר בית".
        if user_text and contains_keyword(
            normalized_text, Messages.Keywords.CANCEL_KEYWORDS
        ):
            cancelled_lead = await leads_collection.find_one(
                {
                    "chat_id": chat_id,
                    "status": {"$in": [LeadStatus.NEW, LeadStatus.CONTACTED]},
                },
                sort=[("created_at", -1)],
            )
            if cancelled_lead:
                await set_lead_status(
                    cancelled_lead["_id"],
                    LeadStatus.CANCELLED,
                    Actor.CUSTOMER,
                    extra_set={
                        "cancelled_at": datetime.now(timezone.utc),
                        "cancel_reason": "user_bailout_awaiting_address",
                    },
                )
                logger.info(
                    f"🚪 AWAITING_ADDRESS cancelled by user for {chat_id} (lead={cancelled_lead['_id']})"
                )
            await StateManager.clear_state(chat_id)
            await ContextManager.clear_context(chat_id)
            await whatsapp.send_message(chat_id, Messages.Customer.REQUEST_CANCELLED)
            return

        if not user_text or len(user_text) <= 3:
            await whatsapp.send_message(chat_id, Messages.Customer.ADDRESS_INVALID)
            return

        active_lead_await = await leads_collection.find_one(
            {
                "chat_id": chat_id,
                "status": {"$in": [LeadStatus.NEW, LeadStatus.CONTACTED]},
            },
            sort=[("created_at", -1)],
        )
        if not active_lead_await:
            await StateManager.clear_state(chat_id)
            # Fall through to normal routing below
        else:
            lead_facts = active_lead_await
            await lead_manager.log_message(chat_id, "user", user_text)
            follow_up_prompt = Prompts.DISPATCHER_SYSTEM.format(
                known_customer_name=lead_facts.get("customer_name") or "none",
                known_city=lead_facts.get("city") or "none",
                known_issue=lead_facts.get("issue_type") or "none",
                known_street=lead_facts.get("street") or "none",
                known_street_number=lead_facts.get("street_number") or "none",
                known_floor=lead_facts.get("floor") or "none",
                known_apartment=lead_facts.get("apartment") or "none",
            )
            if (
                not is_exempt
                and not await SecurityService.check_and_increment_daily_ai_cap(
                    chat_id, WorkerConstants.DAILY_AI_CALL_CAP
                )
            ):
                logger.warning(f"⛔ Daily AI cap reached for ...{chat_id[-8:]}")
                await whatsapp.send_message(
                    chat_id, Messages.Errors.DAILY_AI_CAP_REACHED
                )
                return
            try:
                follow_up = await ai.analyze_conversation(
                    history=await lead_manager.get_chat_history(chat_id),
                    user_text=user_text,
                    custom_system_prompt=follow_up_prompt,
                    require_json=True,
                )
            except Exception as e:
                logger.error(
                    f"AWAITING_ADDRESS re-extraction failed for {chat_id}: {e}"
                )
                await whatsapp.send_message(chat_id, Messages.Errors.AI_OVERLOAD)
                return

            merged = {
                "customer_name": follow_up.extracted_data.customer_name
                or lead_facts.get("customer_name"),
                "street": follow_up.extracted_data.street or lead_facts.get("street"),
                "street_number": follow_up.extracted_data.street_number
                or lead_facts.get("street_number"),
                "city": follow_up.extracted_data.city or lead_facts.get("city"),
                "floor": follow_up.extracted_data.floor or lead_facts.get("floor"),
                "apartment": follow_up.extracted_data.apartment
                or lead_facts.get("apartment"),
            }
            logger.info(
                f"🔍 AWAITING_ADDRESS re-extraction for {chat_id}: "
                f"new_from_ai={[k for k, v in merged.items() if v and not lead_facts.get(k)]}, "
                f"merged={ {k: v for k, v in merged.items() if v} }"
            )
            non_empty = {k: v for k, v in merged.items() if v}
            if non_empty:
                await leads_collection.update_one(
                    {"_id": active_lead_await["_id"]}, {"$set": non_empty}
                )

            class _AddrProbe:
                pass

            probe = _AddrProbe()
            probe.street = merged.get("street")
            probe.street_number = merged.get("street_number")
            probe.city = merged.get("city")
            probe.floor = merged.get("floor")
            probe.apartment = merged.get("apartment")

            ok, reason = is_address_complete(probe)
            if ok:
                full = compose_full_address(probe)
                await leads_collection.update_one(
                    {"_id": active_lead_await["_id"]}, {"$set": {"full_address": full}}
                )
                await StateManager.clear_state(chat_id)
                await whatsapp.send_message(chat_id, Messages.Customer.ADDRESS_SAVED)
                logger.info(
                    f"✅ AWAITING_ADDRESS complete for {chat_id}, full_address={full!r}"
                )
                return
            else:
                await whatsapp.send_message(chat_id, reason)
                logger.info(
                    f"⏳ AWAITING_ADDRESS still missing parts for {chat_id}: {reason}"
                )
                return

    # Pro Registration keyword check (before auto-detect)
    if (
        current_state == UserStates.IDLE
        and normalized_text in Messages.Keywords.REGISTER_COMMANDS
    ):
        await start_onboarding(chat_id, whatsapp)
        return

    # Auto-detect Professional on first contact (only active/approved pros)
    if current_state == UserStates.IDLE:
        # `phone` used to be a shared local computed by the consent gate; that
        # gate now lives in dispatch_guards (PRO-180), so derive it here.
        phone = strip_suffix(chat_id)
        is_pro = await users_collection.find_one(
            {
                "phone_number": {"$in": [phone, chat_id]},
                "role": "professional",
                "is_active": True,
            }
        )
        if is_pro:
            # Redis TTL edge: a pro being served as a customer whose CUSTOMER_MODE
            # key expired lands here mid-request. Re-entering PRO_MODE would answer
            # their next message with the dashboard, so restore CUSTOMER_MODE while
            # their own lead is still open.
            if await _get_active_customer_lead(chat_id):
                await StateManager.set_state(chat_id, UserStates.CUSTOMER_MODE)
                # Not read again on this pass — the customer dispatcher below is
                # already the correct destination. Kept so the local view of state
                # matches Redis for anyone extending this block.
                current_state = UserStates.CUSTOMER_MODE
                logger.info(
                    f"Restored CUSTOMER_MODE for pro ...{chat_id[-8:]} — own lead still open"
                )
            else:
                await StateManager.set_state(chat_id, UserStates.PRO_MODE)
                pro_resp = await _handle_pro_cmd(
                    chat_id, user_text, whatsapp, lead_manager, ai=ai
                )
                if pro_resp:
                    await whatsapp.send_message(chat_id, pro_resp)
                # empty string "" means pro_flow already sent everything internally
                return

    # Patch #2: Short-circuit PENDING_ADMIN_REVIEW.
    # If this chat has a lead already sitting in PENDING_ADMIN_REVIEW, an admin
    # owns it — running the dispatcher again would create a DUPLICATE contacted
    # lead for the same issue (observed on 2026-04-18 with lead
    # 69e375cb9a04cba45197e625 spawning 69e376679a04cba45197e63e 2 min later).
    # Log the message for admin visibility, send a throttled ack, and stop.
    #
    # PRO-63: bounded by age. The short-circuit has no natural exit — a lead sits
    # in PENDING_ADMIN_REVIEW until a human moves it, so an unworked escalation
    # would silently brick this customer's chat forever, which is a worse dead
    # end than the auto-CLOSED behaviour PRO-63 replaced. After
    # PENDING_REVIEW_SHORTCIRCUIT_HOURS their next message starts a fresh
    # request. Leads with no `updated_at` fall outside the window and therefore
    # do not short-circuit — failing toward "customer can talk to us".
    shortcircuit_cutoff = datetime.now(timezone.utc) - timedelta(
        hours=WorkerConstants.PENDING_REVIEW_SHORTCIRCUIT_HOURS
    )
    pending_admin_lead = await leads_collection.find_one(
        {
            "chat_id": chat_id,
            "status": LeadStatus.PENDING_ADMIN_REVIEW,
            "updated_at": {"$gte": shortcircuit_cutoff},
        },
        sort=[("created_at", -1)],
    )
    if pending_admin_lead:
        log_text_pending = user_text or ""
        if media_url:
            log_text_pending = f"{log_text_pending} [MEDIA: {media_url}]"
        await lead_manager.log_message(chat_id, "user", log_text_pending)

        # Throttle the ack: at most once per 30 minutes so the customer isn't
        # spammed if they send a burst of messages while waiting for admin.
        now = datetime.now(timezone.utc)
        last_ack = pending_admin_lead.get("last_pending_ack_at")
        should_ack = True
        if last_ack:
            if last_ack.tzinfo is None:
                last_ack = last_ack.replace(tzinfo=timezone.utc)
            if (now - last_ack) < timedelta(minutes=30):
                should_ack = False

        if should_ack:
            await whatsapp.send_message(chat_id, Messages.Customer.STILL_PENDING_REVIEW)
            await lead_manager.log_message(
                chat_id, "model", Messages.Customer.STILL_PENDING_REVIEW
            )
            await leads_collection.update_one(
                {"_id": pending_admin_lead["_id"]},
                {"$set": {"last_pending_ack_at": now}},
            )
        logger.info(
            f"🔒 PENDING_ADMIN_REVIEW short-circuit for {chat_id} "
            f"(lead={pending_admin_lead['_id']}, ack_sent={should_ack})"
        )
        # PRO-121: this short-circuit is a 24h hold keyed on lead status, so the
        # dispatch hoist above can never fire for it — an emergency declared here
        # would otherwise get STILL_PENDING_REVIEW and reach nobody. Page on the
        # same 30-minute throttle as the ack so a burst cannot spam the operator.
        if is_emergency_detected and should_ack:
            notification_service.page_operator(
                f"EMERGENCY declared on lead {pending_admin_lead['_id']}, which is "
                "already PENDING_ADMIN_REVIEW — the customer is behind the 24h "
                "short-circuit and needs manual routing now"
            )
        return

    # 1. Log User Message — unless the emergency hoist above already did, in
    #    which case logging again would duplicate the turn (PRO-116 Q5).
    if not emergency_inbound_logged:
        await lead_manager.log_message(
            chat_id, "user", _inbound_log_text(user_text, media_url)
        )

    # 2. Check for Customer Completion, Rating, or Review
    if user_text:
        completion_resp = await _handle_completion(chat_id, user_text, whatsapp)
        if completion_resp:
            await whatsapp.send_message(chat_id, completion_resp)
            await lead_manager.log_message(chat_id, "model", completion_resp)
            return

        # `has_media` so an unreadable *caption* on a photo doesn't earn a
        # re-prompt: media is fetched in step 3, below this block, and a
        # re-prompt here would return before the photo is ever downloaded.
        rating_resp = await handle_customer_rating_text(
            chat_id, user_text, has_media=bool(media_url)
        )
        if rating_resp:
            await whatsapp.send_message(chat_id, rating_resp)
            await lead_manager.log_message(chat_id, "model", rating_resp)
            return

        review_resp = await handle_customer_review_comment(chat_id, user_text)
        if review_resp:
            await whatsapp.send_message(chat_id, review_resp)
            await lead_manager.log_message(chat_id, "model", review_resp)
            return

    # 3. Handle Media
    media_data = None
    media_mime = None
    if media_url:
        try:
            media_data, media_mime = await detect_and_fetch_media(media_url)
        except Exception as e:
            logger.warning(f"Media fetch failed for {chat_id}: {e}")

    # 4. Check for existing active lead with assigned pro (skip dispatcher if so)
    active_lead = await leads_collection.find_one(
        {"chat_id": chat_id, "status": {"$in": [LeadStatus.NEW, LeadStatus.CONTACTED]}},
        sort=[("created_at", -1)],
    )

    existing_pro = None
    if active_lead and active_lead.get("pro_id"):
        existing_pro = await users_collection.find_one(
            {"_id": active_lead["pro_id"], "is_active": True}
        )

    # PRO-116 Q3: a customer with a CONFIRMED (BOOKED) job who writes about
    # something else must not silently spawn a second parallel lead — that was
    # the root of the "3 leads / too many approvals" incident (BOOKED is absent
    # from the active_lead query above by design). Recognize the booked job and
    # ask whether this is a new request or about the existing one. Fires once per
    # booked lead (`new_request_prompted`), mirroring `loyalty_offered`.
    # Emergencies bypass — they must reach matching immediately.
    if (
        not active_lead
        and user_text
        and not is_emergency_detected
        and current_state != UserStates.PRO_MODE
    ):
        booked_lead = await leads_collection.find_one(
            {
                "chat_id": chat_id,
                "status": LeadStatus.BOOKED,
                "new_request_prompted": {"$ne": True},
            },
            sort=[("created_at", -1)],
        )
        if booked_lead:
            booked_pro = await users_collection.find_one(
                {"_id": booked_lead.get("pro_id")}
            )
            pro_name = (booked_pro or {}).get("business_name") or "איש המקצוע"
            await leads_collection.update_one(
                {"_id": booked_lead["_id"]},
                {"$set": {"new_request_prompted": True}},
            )
            await StateManager.set_metadata(
                chat_id, {"booked_lead_id": str(booked_lead["_id"])}
            )
            await StateManager.set_state(chat_id, UserStates.AWAITING_NEW_OR_EXISTING)
            prompt = Messages.Customer.EXISTING_JOB_PROMPT.format(
                pro_name=pro_name,
                issue=booked_lead.get("issue_type") or "העבודה",
                appointment=booked_lead.get("appointment_time") or "בקרוב",
            )
            await whatsapp.send_message(chat_id, prompt)
            await lead_manager.log_message(chat_id, "model", prompt)
            return

    # Fresh-start guard: if there's no active lead, the user is starting a new
    # conversation. Drop any stale Redis context from a previously-closed lead
    # so the AI doesn't see turns that belong to a different request.
    if not active_lead:
        await ContextManager.clear_context(chat_id)
        logger.info(
            f"🧼 No active lead for {chat_id} — cleared stale context before new dispatcher run"
        )

    history = await lead_manager.get_chat_history(chat_id)

    # --- OPTIMIZATION 1: Skip dispatcher if pro already assigned ---
    if existing_pro and active_lead:
        logger.info(f"⚡ Skipping dispatcher — pro already assigned for {chat_id}")
        # NOTE: the inbound was already logged once at the top of this function
        # (step 1). Do NOT log it again here — a second log_message duplicated
        # every user turn in history and in the AI context window (PRO-116 Q5).

        if is_emergency_detected and not active_lead.get("is_emergency"):
            await leads_collection.update_one(
                {"_id": active_lead["_id"]}, {"$set": {"is_emergency": True}}
            )
            ack = _emergency_ack_for(
                active_lead.get("city") or active_lead.get("full_address")
            )
            await whatsapp.send_message(chat_id, ack)
            await lead_manager.log_message(chat_id, "model", ack)
            active_lead["is_emergency"] = True

        if media_url:
            await leads_collection.update_one(
                {"_id": active_lead["_id"]}, {"$addToSet": {"media_urls": media_url}}
            )

        extracted_city = active_lead.get("full_address", "")
        extracted_issue = active_lead.get("issue_type", "")
        transcription = None

        # Daily AI cost cap also applies to the assigned-pro fast path — this is
        # the highest-volume conversation path and _build_pro_response makes a
        # Gemini call on every turn. Pros/admins are exempt (is_exempt above).
        if (
            not is_exempt
            and not await SecurityService.check_and_increment_daily_ai_cap(
                chat_id, WorkerConstants.DAILY_AI_CALL_CAP
            )
        ):
            logger.warning(f"⛔ Daily AI cap reached for ...{chat_id[-8:]}")
            await whatsapp.send_message(chat_id, Messages.Errors.DAILY_AI_CAP_REACHED)
            return

        try:
            pro_response_obj = await _build_pro_response(
                existing_pro,
                history,
                user_text,
                extracted_city,
                extracted_issue,
                transcription,
                media_data=media_data,
                media_mime=media_mime,
                media_url=media_url,
            )
        except Exception as e:
            logger.error(f"Pro response failed for {chat_id}: {e}")
            await whatsapp.send_message(chat_id, Messages.Errors.AI_OVERLOAD)
            return

        # Check for deal on the raw text, then send/log the cleaned copy —
        # the [DEAL:...] marker must never reach the customer.
        is_deal = pro_response_obj.is_deal or bool(
            DEAL_MARKER_RE.search(pro_response_obj.reply_to_user)
        )
        # PRO-121: this is where an emergency released from the address gate
        # actually lands. Every route into AWAITING_ADDRESS writes `pro_id`
        # first — the matching block below does it before _finalize_deal runs,
        # and so does _accept_loyalty_offer — so `existing_pro` resolves and
        # this fast path returns long before the dispatcher's expedited branch.
        # Without the same widening here, releasing the gate only swapped one
        # unanswered question for another.
        emergency_expedite = bool(active_lead.get("is_emergency") and not is_deal)
        cleaned_reply = _strip_deal_marker(pro_response_obj.reply_to_user)
        if emergency_expedite:
            logger.info(
                f"🚑 Suppressing mid-intake reply for ...{chat_id[-8:]} — the "
                f"assigned pro is being asked to approve this turn "
                f"({len(cleaned_reply)} chars withheld)"
            )
        else:
            await whatsapp.send_message(chat_id, cleaned_reply)
            await lead_manager.log_message(chat_id, "model", cleaned_reply)

        if is_deal or emergency_expedite:
            try:
                await _finalize_deal(
                    chat_id,
                    existing_pro,
                    pro_response_obj,
                    extracted_city,
                    extracted_issue,
                    transcription,
                    active_lead["_id"],
                    media_url=media_url,
                    extracted_name=active_lead.get("customer_name"),
                )
            except Exception as e:
                logger.error(f"Deal finalization failed for {chat_id}: {e}")
                # The suppressed reply was this turn's only customer-facing
                # message; finalization failing must not leave them with silence.
                if emergency_expedite:
                    await whatsapp.send_message(chat_id, cleaned_reply)
                    await lead_manager.log_message(chat_id, "model", cleaned_reply)
        return

    # 5. Smart Dispatcher Phase (only when no pro assigned yet)
    # Context window trimming is centralized in ai_engine_service.py
    # Inject sticky facts from the active lead so extractions survive the 10-message window.
    lead_facts = active_lead or {}
    # PRO-116 Q4: a returning customer whose prior lead is booked/closed has no
    # active_lead, so their name would be re-asked cold every time. Seed ONLY the
    # name (not city/issue — those are per-request) from their most recent prior
    # lead that captured one, so we greet them by name instead of re-interrogating.
    if not active_lead:
        prior_named = await leads_collection.find_one(
            {"chat_id": chat_id, "customer_name": {"$nin": [None, ""]}},
            sort=[("created_at", -1)],
        )
        if prior_named and prior_named.get("customer_name"):
            lead_facts = {"customer_name": prior_named["customer_name"]}
    sticky = {
        "customer_name": lead_facts.get("customer_name") or "none",
        "city": lead_facts.get("city") or lead_facts.get("full_address") or "none",
        "issue": lead_facts.get("issue_type") or "none",
        "street": lead_facts.get("street") or "none",
        "street_number": lead_facts.get("street_number") or "none",
        "floor": lead_facts.get("floor") or "none",
        "apartment": lead_facts.get("apartment") or "none",
    }
    logger.info(
        f"📌 Sticky facts injected for {chat_id}: "
        f"name={sticky['customer_name']}, city={sticky['city']}, issue={sticky['issue']}, "
        f"street={sticky['street']} {sticky['street_number']}, "
        f"floor={sticky['floor']}, apt={sticky['apartment']}"
    )
    dispatcher_history = history
    dispatcher_prompt = Prompts.DISPATCHER_SYSTEM.format(
        known_customer_name=sticky["customer_name"],
        known_city=sticky["city"],
        known_issue=sticky["issue"],
        known_street=sticky["street"],
        known_street_number=sticky["street_number"],
        known_floor=sticky["floor"],
        known_apartment=sticky["apartment"],
    )

    if not is_exempt and not await SecurityService.check_and_increment_daily_ai_cap(
        chat_id, WorkerConstants.DAILY_AI_CALL_CAP
    ):
        logger.warning(f"⛔ Daily AI cap reached for {chat_id}")
        await whatsapp.send_message(chat_id, Messages.Errors.DAILY_AI_CAP_REACHED)
        return
    try:
        dispatcher_response: AIResponse = await ai.analyze_conversation(
            history=dispatcher_history,
            user_text=user_text or "",
            custom_system_prompt=dispatcher_prompt,
            media_data=media_data,
            media_mime_type=media_mime,
            media_url=media_url,
            require_json=True,
        )
    except Exception as e:
        logger.error(f"AI dispatcher failed for {chat_id}: {e}")
        await whatsapp.send_message(chat_id, Messages.Errors.AI_OVERLOAD)
        return

    # Merge: prefer fresh AI output, fall back to stored lead facts so a trimmed
    # window or a silent parse-failure can't erase a previously-confirmed fact.
    ai_city = dispatcher_response.extracted_data.city
    ai_issue = dispatcher_response.extracted_data.issue
    ai_name = dispatcher_response.extracted_data.customer_name
    extracted_city = ai_city or lead_facts.get("city") or lead_facts.get("full_address")
    extracted_issue = ai_issue or lead_facts.get("issue_type")
    extracted_name = ai_name or lead_facts.get("customer_name")
    transcription = dispatcher_response.transcription

    if (not ai_city and extracted_city) or (not ai_issue and extracted_issue):
        logger.warning(
            f"🩹 Sticky-facts fallback used for {chat_id}: "
            f"AI returned city={ai_city!r}/issue={ai_issue!r}, "
            f"lead facts filled in city={extracted_city!r}/issue={extracted_issue!r}"
        )

    logger.info(
        f"Dispatcher analysis: City={extracted_city}, Issue={extracted_issue}, Transcr={transcription}"
    )

    # --- NEW: Sticky Persistence Gate ---
    # Create or update a "contacted" lead as soon as we have ANY info.
    # This ensures that if the AI forgets to repeat a field in the next turn,
    # it's still preserved in the DB and injected as a 'sticky' fact.
    #
    # Bound before the gate, not only inside it: every *later* reader used to
    # be nested under `if extracted_city and extracted_issue...`, which implied
    # the gate had run, but that is a fragile guarantee — a reader outside that
    # nesting (PRO-119's parts persistence below) would otherwise hit an
    # UnboundLocalError on any turn the gate skips.
    current_lead_id = active_lead["_id"] if active_lead else None
    if extracted_city or extracted_issue or media_url or is_emergency_detected:
        if not active_lead:
            active_lead = await lead_manager.create_lead_from_dict(
                chat_id=chat_id,
                issue_type=extracted_issue or Defaults.UNKNOWN_ISSUE,
                # full_address stays None until the address gate collects a
                # real address. Persisting the "Unknown Address" sentinel used
                # to confuse matching_service (see 2026-04-18 Unknown Address
                # incident) — see migration script migrate_unknown_address.py.
                full_address=extracted_city or None,
                status=LeadStatus.CONTACTED,
                appointment_time=Defaults.PENDING_TIME,
                media_url=media_url,
                customer_name=extracted_name,
                is_emergency=is_emergency_detected,
            )
            current_lead_id = active_lead["_id"] if active_lead else None
            if is_emergency_detected:
                ack = _emergency_ack_for(extracted_city)
                await whatsapp.send_message(chat_id, ack)
                await lead_manager.log_message(chat_id, "model", ack)
        else:
            current_lead_id = active_lead["_id"]
            update_data = {}
            if ai_city and ai_city != lead_facts.get("city"):
                update_data["city"] = ai_city
            if ai_issue and ai_issue != lead_facts.get("issue_type"):
                update_data["issue_type"] = ai_issue
            if ai_name and ai_name != lead_facts.get("customer_name"):
                update_data["customer_name"] = ai_name

            if is_emergency_detected and not lead_facts.get("is_emergency"):
                update_data["is_emergency"] = True
                ack = _emergency_ack_for(extracted_city)
                await whatsapp.send_message(chat_id, ack)
                await lead_manager.log_message(chat_id, "model", ack)

            mongo_ops = {}
            if update_data:
                mongo_ops["$set"] = update_data

            if media_url:
                mongo_ops["$addToSet"] = {"media_urls": media_url}

            if mongo_ops:
                await leads_collection.update_one({"_id": current_lead_id}, mongo_ops)
                # Refresh facts for the matching block below
                extracted_city = ai_city or extracted_city
                extracted_issue = ai_issue or extracted_issue

    # PRO-119: persist whatever address parts this turn's extraction produced.
    # The sticky gate above keeps only city/issue/name, so a customer who
    # front-loads their whole address ("נזילה ברחוב הרצל 10 קומה 2 דירה 5 בתל
    # אביב") had the parts dropped and got asked for them again — including
    # immediately after accepting the loyalty offer, which can only dispatch
    # when the parts are on the lead.
    if current_lead_id:
        extracted_parts = {
            field: getattr(dispatcher_response.extracted_data, field, None)
            for field in ("street", "street_number", "floor", "apartment")
        }
        extracted_parts = {k: v for k, v in extracted_parts.items() if v}
        if extracted_parts:
            await leads_collection.update_one(
                {"_id": current_lead_id}, {"$set": extracted_parts}
            )

    # PRO-121: is this turn acting on an emergency? Bound once here so the
    # loyalty gate, the PRO-116 Q1 early-notice suppression and the finalize
    # call site below all answer the question the same way.
    lead_is_emergency = bool(
        is_emergency_detected or (active_lead or {}).get("is_emergency")
    )

    # Loyalty Check: offer returning customers their previous pro before running
    # normal matching.
    #
    # PRO-121: never for an emergency. LOYALTY_OFFER parks the customer in
    # AWAITING_LOYALTY_CONFIRMATION — one of the holding states this issue is
    # about — to ask a question about *preference*. Escalating out of that state
    # afterwards (the hoisted branch above) is the cure; not entering it while
    # someone's home is flooding is the prevention.
    if (
        extracted_city
        and extracted_issue
        and extracted_issue != Defaults.UNKNOWN_ISSUE
        and current_lead_id
        and not lead_is_emergency
    ):
        current_lead_doc = await leads_collection.find_one({"_id": current_lead_id})
        if current_lead_doc and not current_lead_doc.get("loyalty_offered"):
            past_lead = await leads_collection.find_one(
                {"chat_id": chat_id, "status": LeadStatus.COMPLETED},
                sort=[("created_at", -1)],
            )
            if past_lead and past_lead.get("pro_id"):
                past_pro = await users_collection.find_one(
                    {"_id": past_lead["pro_id"], "is_active": True}
                )
                if past_pro:
                    await leads_collection.update_one(
                        {"_id": current_lead_id},
                        {"$set": {"loyalty_offered": True}},
                    )
                    await StateManager.set_metadata(
                        chat_id, {"past_pro_id": str(past_pro["_id"])}
                    )
                    # PRO-119: bounded TTL — without one this inherited the 4h
                    # default and a customer whose reply we failed to parse
                    # was trapped with no way out but a reset keyword.
                    await StateManager.set_state(
                        chat_id,
                        UserStates.AWAITING_LOYALTY_CONFIRMATION,
                        ttl=WorkerConstants.LOYALTY_CONFIRM_TTL_SECONDS,
                    )
                    loyalty_msg = Messages.Customer.LOYALTY_OFFER.format(
                        pro_name=past_pro.get("business_name", "איש המקצוע")
                    )
                    await whatsapp.send_message(chat_id, loyalty_msg)
                    await lead_manager.log_message(chat_id, "model", loyalty_msg)
                    return

    # 6. Logic Gate: Dispatcher vs Professional
    best_pro = None
    pro_response_obj = None

    # Note: the explicit `!= UNKNOWN_ADDRESS` check is gone because we no longer
    # persist that sentinel. `extracted_city` is either a real city string or None.
    if extracted_city and extracted_issue and extracted_issue != Defaults.UNKNOWN_ISSUE:
        try:
            best_pro = await determine_best_pro(
                issue_type=extracted_issue, location=extracted_city
            )
        except Exception as e:
            logger.error(f"Pro matching failed for {chat_id}: {e}")

        # If no pro found, escalate to admin review instead of closing.
        if not best_pro and current_lead_id:
            existing_lead = await leads_collection.find_one({"_id": current_lead_id})
            if existing_lead and not existing_lead.get("pro_id"):
                await set_lead_status(
                    current_lead_id, LeadStatus.PENDING_ADMIN_REVIEW, Actor.SYSTEM
                )
                # WARNING, not CRITICAL: no-pro-available is a routine coverage gap
                # (admin handles it via PENDING_ADMIN_REVIEW), not an infra page. The
                # worker is Sentry CRITICAL-only, so this keeps no-pro leads out of the
                # operator's email (PRO-77). chat_id masked per PII convention.
                logger.warning(
                    f"Lead {current_lead_id} for ...{chat_id[-8:]} requires admin "
                    "review — no pro available"
                )
                # PRO-121: a routine coverage gap is deliberately not a page
                # (see above), but an *emergency* with no pro is the one case
                # where nobody is coming and nobody has been told. The lead
                # would otherwise sit behind the 24h PENDING_ADMIN_REVIEW
                # short-circuit answering STILL_PENDING_REVIEW every 30 min.
                if lead_is_emergency:
                    notification_service.page_operator(
                        f"EMERGENCY lead {current_lead_id} has no available pro "
                        f"(city={extracted_city!r}, issue={extracted_issue!r}) — "
                        "PENDING_ADMIN_REVIEW, needs manual routing now"
                    )
                await whatsapp.send_message(chat_id, Messages.Customer.PENDING_REVIEW)
                await lead_manager.log_message(
                    chat_id, "model", Messages.Customer.PENDING_REVIEW
                )
                return

        if best_pro:
            is_new_assignment = False
            if current_lead_id:
                existing_lead = await leads_collection.find_one(
                    {"_id": current_lead_id}
                )
                had_pro = existing_lead and existing_lead.get("pro_id")
                await leads_collection.update_one(
                    {"_id": current_lead_id}, {"$set": {"pro_id": best_pro["_id"]}}
                )
                if not had_pro:
                    is_new_assignment = True
            else:
                is_new_assignment = True

            # Build the persona response FIRST so we know whether this same turn
            # already closes the deal — PRO-116 Q1: if it does, sending the pro
            # the "שיחה בתהליך — אין צורך לפעול" early notice milliseconds before
            # the actual approval request is confusing noise. Suppress it then.
            try:
                pro_response_obj = await _build_pro_response(
                    best_pro,
                    history,
                    user_text,
                    extracted_city,
                    extracted_issue,
                    transcription,
                    media_data=media_data,
                    media_mime=media_mime,
                    media_url=media_url,
                )
            except Exception as e:
                logger.error(f"Pro response build failed for {chat_id}: {e}")
                pro_response_obj = None

            # A [DEAL] marker alone is not enough — _finalize_deal's address gate
            # rejects an incomplete address, in which case the deal does NOT
            # finalize this turn and the pro still needs the EARLY_LEAD notice.
            # Only treat the turn as finalizing when the address is actually
            # complete (same gate helper), so we never suppress the notice on a
            # deal that will be rejected (PRO-116 Q1).
            _deal_flagged = bool(
                pro_response_obj
                and (
                    pro_response_obj.is_deal
                    or DEAL_MARKER_RE.search(pro_response_obj.reply_to_user)
                )
            )
            # PRO-121: an emergency finalizes on this turn with no [DEAL]
            # marker and with an incomplete address (_finalize_deal grants it a
            # city-only bypass), so deriving this from _deal_flagged alone sent
            # the pro "אין צורך לפעול עכשיו" milliseconds before the emergency
            # approval request — telling them to stand down on the one lead
            # that cannot wait.
            # The emergency arm is deliberately independent of
            # `pro_response_obj`: when _build_pro_response raises it is None,
            # yet the finalize call site below still dispatches on
            # `lead_is_emergency` — so gating both arms on it would send the
            # stand-down notice and the emergency approval request back to back.
            turn_finalizes = lead_is_emergency or (
                bool(pro_response_obj)
                and _deal_flagged
                and is_address_complete(pro_response_obj.extracted_data)[0]
            )

            if is_new_assignment and not turn_finalizes:
                try:
                    pro_phone = best_pro.get("phone_number")
                    if pro_phone:
                        pro_phone = to_chat_id(pro_phone)
                        notify_msg = (
                            Messages.Pro.EARLY_LEAD_HEADER
                            + "\n\n"
                            + Messages.Pro.EARLY_LEAD_DETAILS.format(
                                issue_type=extracted_issue, city=extracted_city
                            )
                            + Messages.Pro.EARLY_LEAD_FOOTER
                        )
                        # Send the CURRENT media only if it's new (to avoid duplicate spam)
                        if media_url:
                            await whatsapp.send_file_by_url(
                                pro_phone, media_url, caption=notify_msg
                            )
                        else:
                            await whatsapp.send_message(pro_phone, notify_msg)

                        logger.info(
                            f"📢 Notified pro {pro_phone} about lead status from {chat_id}"
                        )
                except Exception as e:
                    logger.error(f"Failed to notify pro about new lead: {e}")

    # Select which response to send
    final_response = (
        pro_response_obj if (best_pro and pro_response_obj) else dispatcher_response
    )

    # 7. Check for [DEAL] or Structured Booking — detect on the raw text
    # before stripping the marker for the customer-facing send.
    is_deal = final_response.is_deal

    deal_string_match = DEAL_MARKER_RE.search(final_response.reply_to_user)
    if deal_string_match:
        is_deal = True

    # PRO-121: this turn dispatches the emergency even though the AI did not
    # close the deal, so its reply is still mid-intake ("איזו קומה?"). Sending it
    # would contradict EMERGENCY_ACK's promise to ask only what is essential and
    # then be contradicted in turn by AWAITING_APPROVAL_TRANSPARENT a moment
    # later. The pro gets the full picture either way — _finalize_deal reads the
    # lead, not this reply.
    emergency_expedite = bool(best_pro and lead_is_emergency and not is_deal)

    # Send Message to User — cleaned copy, marker must never leak to the customer.
    cleaned_reply = _strip_deal_marker(final_response.reply_to_user)
    if emergency_expedite:
        logger.info(
            f"🚑 Suppressing mid-intake reply for ...{chat_id[-8:]} — emergency "
            f"dispatches this turn ({len(cleaned_reply)} chars withheld; the text "
            "is conversation content and stays out of the log)"
        )
    else:
        await whatsapp.send_message(chat_id, cleaned_reply)
        await lead_manager.log_message(chat_id, "model", cleaned_reply)

    # PRO-55: persist the AI's quoted price stickily the moment it's given (STEP 3),
    # so it reaches the pro approval request even though the estimate turn precedes
    # the deal close. Strip the ₪ symbol we re-add at display time.
    _qp_clean = _clean_quoted_price(
        getattr(final_response.extracted_data, "quoted_price", None)
    )
    if _qp_clean and current_lead_id:
        await leads_collection.update_one(
            {"_id": current_lead_id}, {"$set": {"quoted_price": _qp_clean}}
        )

    # PRO-121: an emergency does not wait for the AI to volunteer a [DEAL]
    # marker. Once a pro is actually matched, finalize on the spot — and only
    # then does the customer hear that someone has their call
    # (AWAITING_APPROVAL_TRANSPARENT, sent by _finalize_deal). Its address gate
    # already grants an emergency a city-only bypass, so the offer goes out with
    # whatever we have rather than stalling on floor and apartment.
    if best_pro and (is_deal or lead_is_emergency):
        if emergency_expedite:
            logger.warning(
                f"🚑 Emergency expedited dispatch for ...{chat_id[-8:]} — finalizing "
                f"without a [DEAL] marker (pro={best_pro['_id']})"
            )
        try:
            await _finalize_deal(
                chat_id,
                best_pro,
                final_response,
                extracted_city,
                extracted_issue,
                transcription,
                current_lead_id,
                media_url=media_url,
                extracted_name=extracted_name,
            )
        except Exception as e:
            logger.error(f"Deal finalization failed for {chat_id}: {e}")
            # Same reasoning as the fast path above: on an expedited emergency
            # the AI's reply was withheld because _finalize_deal was going to
            # answer instead. It didn't, so send it rather than say nothing.
            if emergency_expedite:
                await whatsapp.send_message(chat_id, cleaned_reply)
                await lead_manager.log_message(chat_id, "model", cleaned_reply)


# --- Private Helpers ---


async def _build_pro_response(
    best_pro,
    history,
    user_text,
    extracted_city,
    extracted_issue,
    transcription,
    media_data=None,
    media_mime=None,
    media_url=None,
):
    """Build the pro persona AI response."""
    pro_name = best_pro.get("business_name", Defaults.PROLI_PRO_NAME)
    # Price source of truth: WhatsApp onboarding stores the pro's prices in
    # `prices_for_prompt` (see pro_onboarding_service); some admin/seeded pros
    # use `price_list`. Read the former first, fall back to the latter. Reading
    # only `price_list` left the scheduler with an EMPTY price list for every
    # onboarded pro, so the AI invented figures instead of quoting real prices.
    raw_price_list = best_pro.get("prices_for_prompt") or best_pro.get("price_list", "")
    if isinstance(raw_price_list, dict):
        price_list = ", ".join(f"{k}: {v} ILS" for k, v in raw_price_list.items())
    else:
        price_list = str(raw_price_list) if raw_price_list else ""
    # PRO-170: `or`, not a .get default — WhatsApp-onboarded pros store
    # system_prompt as "" (pro_onboarding_service), and an empty string wins
    # over a .get default, which left every onboarded pro running the
    # scheduler with an EMPTY persona block. Admin-created pros keep their
    # generated persona; empty/missing falls back to the default role.
    base_system_prompt = best_pro.get(
        "system_prompt"
    ) or Messages.AISystemPrompts.PROLI_SCHEDULER_ROLE.format(pro_name=pro_name)

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
        transcription=transcription or Defaults.DEFAULT_TRANSCRIPTION,
        current_datetime=datetime.now(_IL_TZ).strftime("%Y-%m-%d %H:%M (%A)"),
    )

    return await ai.analyze_conversation(
        history=history,
        user_text=user_text or "",
        custom_system_prompt=full_system_prompt,
        media_data=media_data,
        media_mime_type=media_mime,
        media_url=media_url,
        require_json=True,
        pro_id=str(best_pro["_id"]),
    )


async def _finalize_deal(
    chat_id,
    best_pro,
    final_response,
    extracted_city,
    extracted_issue,
    transcription,
    current_lead_id,
    media_url=None,
    extracted_name=None,
):
    """Finalize a deal: create/update lead, set customer to AWAITING_PRO_APPROVAL, send pro interactive buttons."""
    # Hard address gate: never dispatch a pro without street+number+city+floor+apartment.
    ed = final_response.extracted_data
    logger.info(
        f"🚧 Address gate check for {chat_id}: "
        f"street={ed.street!r}, number={ed.street_number!r}, city={ed.city!r}, "
        f"floor={ed.floor!r}, apt={ed.apartment!r}, time={ed.appointment_time!r}"
    )

    # Fetch lead to check for emergency status
    active_lead_doc = (
        await leads_collection.find_one({"_id": current_lead_id})
        if current_lead_id
        else None
    )
    is_emergency = (
        active_lead_doc.get("is_emergency", False) if active_lead_doc else False
    )

    # PRO-55: the AI-quoted price shown to the pro at approval. Prefer this turn's
    # (validated) value, else the sticky value persisted when the estimate was
    # first given (already validated at persist time).
    quoted_price = _clean_quoted_price(ed.quoted_price) or (
        active_lead_doc.get("quoted_price") if active_lead_doc else None
    )

    ok, reason = is_address_complete(ed)

    # Task 3: Bypass for emergency
    bypass_address_logic = False
    if not ok and is_emergency and (ed.city or extracted_city):
        logger.info(
            f"🚑 EMERGENCY BYPASS: allowing incomplete address for {chat_id} (city={ed.city or extracted_city})"
        )
        ok = True
        bypass_address_logic = True

    if not ok:
        # Persist whatever partial address parts we already have so the sticky
        # facts survive the next turn and the customer doesn't re-state them.
        partial_update = {
            "street": ed.street,
            "street_number": ed.street_number,
            "city": ed.city or extracted_city,
            "floor": ed.floor,
            "apartment": ed.apartment,
            "issue_type": ed.issue or extracted_issue,
        }
        partial_update = {k: v for k, v in partial_update.items() if v}
        if current_lead_id and partial_update:
            await leads_collection.update_one(
                {"_id": current_lead_id}, {"$set": partial_update}
            )
            logger.info(
                f"💾 Persisted partial address parts for {chat_id} (lead={current_lead_id}): {list(partial_update.keys())}"
            )

        await StateManager.set_state(chat_id, UserStates.AWAITING_ADDRESS)
        await whatsapp.send_message(chat_id, reason)
        logger.warning(f"🚫 Address gate REJECTED finalization for {chat_id}: {reason}")
        return
    logger.info(f"✅ Address gate PASSED for {chat_id}")

    d_time = final_response.extracted_data.appointment_time or Defaults.ASAP_TIME
    # Resolved absolute datetime (UTC) — used by the pro agenda and stale-lead
    # nudger. None when the customer gave no concrete time; read paths tolerate that.
    d_datetime = parse_iso_to_utc(final_response.extracted_data.appointment_datetime)

    if bypass_address_logic and not (ed.street and ed.street_number):
        d_address = ed.city or extracted_city
    else:
        d_address = compose_full_address(ed)

    d_issue = (
        final_response.extracted_data.issue or extracted_issue or Defaults.UNKNOWN_ISSUE
    )

    d_name = final_response.extracted_data.customer_name or extracted_name

    lead_update = {
        "appointment_time": d_time,
        "appointment_datetime": d_datetime,
        "full_address": d_address,
        "street": final_response.extracted_data.street,
        "street_number": final_response.extracted_data.street_number,
        "city": final_response.extracted_data.city,
        "floor": final_response.extracted_data.floor,
        "apartment": final_response.extracted_data.apartment,
        "issue_type": d_issue,
        "pro_id": best_pro["_id"],
        # PRO-56 approval-SLA clock (the pro is notified right after this) + the
        # idempotency flags the SLA job flips.
        "pro_notified_at": datetime.now(timezone.utc),
        "approval_nudged": False,
        "reassign_offered": False,
    }
    if d_name:
        lead_update["customer_name"] = d_name
    if quoted_price:
        lead_update["quoted_price"] = quoted_price

    if current_lead_id:
        # CONTACTED -> NEW once the routing engine has matched a pro. Goes
        # through set_lead_status so the transition lands in status_history.
        lead = await set_lead_status(
            current_lead_id, LeadStatus.NEW, Actor.SYSTEM, extra_set=lead_update
        )
        if media_url:
            await leads_collection.update_one(
                {"_id": current_lead_id},
                {"$addToSet": {"media_urls": media_url}},
            )
            lead = await leads_collection.find_one({"_id": current_lead_id})
    else:
        lead = await lead_manager.create_lead_from_dict(
            chat_id=chat_id,
            issue_type=d_issue,
            full_address=d_address,
            appointment_time=d_time,
            appointment_datetime=d_datetime,
            status=LeadStatus.NEW,
            pro_id=best_pro["_id"],
            street=final_response.extracted_data.street,
            street_number=final_response.extracted_data.street_number,
            city=final_response.extracted_data.city,
            floor=final_response.extracted_data.floor,
            apartment=final_response.extracted_data.apartment,
            media_url=media_url,
        )
        # create_lead_from_dict has no quoted_price param — persist it directly so
        # the customer PRO_FOUND and analytics get it, matching the current_lead
        # branch's lead_update (PRO-55).
        if lead and quoted_price:
            await leads_collection.update_one(
                {"_id": lead["_id"]}, {"$set": {"quoted_price": quoted_price}}
            )
            lead["quoted_price"] = quoted_price

    if lead:
        # 1. Set customer state to AWAITING_PRO_APPROVAL with a bounded TTL so
        #    the customer is never silently stuck if the pro misses the notification.
        await StateManager.set_state(
            chat_id,
            UserStates.AWAITING_PRO_APPROVAL,
            ttl=WorkerConstants.PRO_APPROVAL_TTL_SECONDS,
        )
        await whatsapp.send_message(
            chat_id,
            Messages.Customer.AWAITING_APPROVAL_TRANSPARENT.format(
                pro_name=best_pro.get("business_name", "איש המקצוע")
            ),
        )
        logger.info(f"Customer {chat_id} entered AWAITING_PRO_APPROVAL state")

        # 2. Send pro approval request with interactive buttons
        pro_phone = best_pro.get("phone_number")
        if pro_phone:
            pro_phone = to_chat_id(pro_phone)

            customer_phone = strip_suffix(chat_id)
            extra_info = notification_service.format_lead_extra_info(lead)

            emergency_header = (
                Messages.Pro.EMERGENCY_LEAD_HEADER + "\n\n" if is_emergency else ""
            )
            loyalty_header = ""
            if lead.get("loyalty_offered"):
                meta = await StateManager.get_metadata(chat_id)
                past_pro_id_str = meta.get("past_pro_id")
                if past_pro_id_str and str(best_pro["_id"]) == past_pro_id_str:
                    loyalty_header = Messages.Pro.LOYALTY_LEAD_HEADER + "\n\n"

            price_line = (
                Messages.Pro.APPROVAL_PRICE_LINE.format(quoted_price=quoted_price)
                if quoted_price
                else ""
            )
            approval_msg = (
                emergency_header
                + loyalty_header
                + Messages.Pro.APPROVAL_REQUEST.format(
                    customer_name=lead.get("customer_name") or "לקוח",
                    customer_phone=customer_phone,
                    full_address=lead["full_address"],
                    extra_info=extra_info,
                    issue_type=lead["issue_type"],
                    appointment_time=lead["appointment_time"],
                    price_line=price_line,
                )
            )

            if transcription:
                approval_msg += Messages.Pro.NEW_LEAD_TRANSCRIPTION.format(
                    transcription=transcription
                )

            # Media links as text — the one policy, owned by notification_service
            approval_msg += notification_service.format_media_links(lead)

            await whatsapp.send_message(pro_phone, approval_msg)

            await whatsapp.send_location_link(
                pro_phone, lead["full_address"], Messages.Pro.NAVIGATE_TO
            )

    # PRO-69 FM-3: a pro-as-customer used to be snapped back to PRO_MODE right here,
    # the instant their own lead was dispatched. That is the worst possible moment —
    # their assigned pro is about to start messaging them, so every follow-up
    # ("מתי הוא מגיע?") got answered with the pro dashboard. They now stay on the
    # customer side for the life of their own request; the way back to PRO_MODE is a
    # pro keyword (Safety Bypass) or the IDLE auto-detect once the lead is closed.
    if await _is_registered_pro(chat_id):
        logger.info(
            f"Keeping pro-as-customer ...{chat_id[-8:]} on the customer side — "
            f"own lead {lead['_id'] if lead else 'n/a'} just dispatched"
        )
