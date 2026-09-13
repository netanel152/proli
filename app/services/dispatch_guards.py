"""The dispatch guard chain — the head of the inbound-message pipeline, as data.

PRO-139 slice A1 (PRO-179).

`workflow_service._process_incoming_message_inner` opens with a long run of
guard clauses, each of which either handles the message and returns, or falls
through to the next. Their **order is load-bearing**, and deliberately so:
PRO-121 hoisted the emergency check to a specific position and its comment says
"Position is the whole design" — after the admin wizard, reset, help, the rate
limiter, the consent gate and the SOS handoff (none of which an emergency may
bypass, and a live human outranks the bot), and before the first state that
would swallow it.

That is why this module is an ordered **tuple**, not the `dict` of
`UserStates -> handler` the parent issue first proposed. A dict lookup keyed on
state cannot express "runs before X, after Y", and most of these guards are not
state-keyed at all — the admin wizard keys on chat id, reset and help on the
message text, the rate limiter on neither. Order as data means order can be
asserted in a test, so a future reordering fails the build instead of silently
changing which gate wins.

The guards are not pure functions: they read and write locals that code further
down the pipeline depends on (`is_exempt` gates the daily AI-call cap at three
later call sites; `current_state` is re-read after any guard that clears state).
`DispatchContext` carries that shared state explicitly instead of relying on
closure over function locals.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional, Tuple

from bson import ObjectId
from bson.errors import InvalidId

from app.core.constants import LeadStatus, UserStates, WorkerConstants
from app.core.logger import logger
from app.core.messages import Messages
from app.core.phone import to_chat_id, strip_suffix
from app.core.text_matching import contains_keyword


class _Handled:
    """Sentinel type — see `HANDLED`."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "HANDLED"


#: Returned by a guard that has fully handled the message. Dispatch stops there.
#: A guard returning ``None`` falls through to the next one — the same semantics
#: the inline ``return`` / fall-through had, so behaviour is unchanged.
HANDLED = _Handled()


@dataclass
class DispatchContext:
    """Mutable state shared across the guard chain and the pipeline below it.

    Every field here was a local variable in the original function body. They
    are gathered into one object because guards mutate them for the benefit of
    code that runs later: the rate-limit guard computes `is_exempt`, the
    zero-touch guard can clear state and refresh `current_state`, and both are
    read hundreds of lines further down.
    """

    chat_id: str
    user_text: str
    media_url: Optional[str]
    normalized_text: str
    is_emergency_detected: bool
    current_state: Optional[str]
    is_exempt: bool = False
    #: PRO-121 / PRO-116 Q5 — set by the emergency hoist when it logs the
    #: inbound turn ahead of the emergency ack, so the pipeline's step 1 skips
    #: its own log instead of duplicating the turn in the AI's history window.
    emergency_inbound_logged: bool = False

    async def refresh_state(self, state_manager) -> Optional[str]:
        """Re-read the FSM state after a guard may have changed it.

        Replaces the ad-hoc ``current_state = await StateManager.get_state(...)``
        re-reads that were scattered through the function. Returning the value
        as well as storing it keeps call sites readable.
        """
        self.current_state = await state_manager.get_state(self.chat_id)
        return self.current_state


@dataclass
class GuardDeps:
    """Injected collaborators.

    Passed in rather than imported as module globals so a guard can be tested
    against fakes without monkeypatching, matching the dependency-injection
    convention already used by `customer_flow` and `pro_flow`.
    """

    whatsapp: Any
    state_manager: Any
    context_manager: Any
    users_collection: Any
    security: Any
    settings: Any


#: A guard: given the context and its dependencies, either handle the message
#: (return `HANDLED`) or fall through (return `None`), having possibly mutated
#: the context on the way past.
Guard = Callable[[DispatchContext, GuardDeps], Awaitable[Optional[_Handled]]]


async def guard_admin_wizard(ctx: DispatchContext, deps: GuardDeps):
    """Admin routing wizard (`ניהול`).

    First in the chain on purpose: the admin is also a registered pro, so any
    later gate — consent, SOS, paused-for-human — would trap them inside their
    own admin session.
    """
    admin_chat_id = to_chat_id(deps.settings.ADMIN_PHONE)
    if ctx.chat_id != admin_chat_id:
        return None

    if not (
        (ctx.user_text and ctx.user_text.strip() == "ניהול")
        or (ctx.current_state or "").startswith("admin_")
    ):
        return None

    # Imported inside the function to break a circular import: admin_flow
    # imports from workflow_service, which imports this module.
    from app.services import admin_flow
    from app.core.redis_client import get_redis_client

    redis_client = await get_redis_client()
    await admin_flow.handle_admin_message(
        ctx.chat_id,
        ctx.user_text,
        ctx.current_state,
        deps.state_manager,
        redis_client,
        deps.whatsapp,
        None,
    )
    return HANDLED


async def guard_global_reset(ctx: DispatchContext, deps: GuardDeps):
    """Global reset. Skipped for pros — they use `תפריט` for their menu."""
    if (
        ctx.normalized_text not in Messages.Keywords.RESET_COMMANDS
        or ctx.current_state == UserStates.PRO_MODE
    ):
        return None

    # Deliberately silent (operator decision, 2026-08-27): no confirmation
    # message — the customer's next message simply starts a fresh
    # conversation. The old RESET_SUCCESS confirmation was removed with it.
    await deps.state_manager.clear_state(ctx.chat_id)
    await deps.context_manager.clear_context(ctx.chat_id)
    return HANDLED


async def guard_help_menu(ctx: DispatchContext, deps: GuardDeps):
    """Help / menu — sends info without touching state or context."""
    help_words = Messages.Keywords.HELP_COMMANDS + Messages.Keywords.MENU_COMMANDS
    if (
        ctx.normalized_text not in help_words
        or ctx.current_state == UserStates.PRO_MODE
    ):
        return None

    await deps.whatsapp.send_message(ctx.chat_id, Messages.Customer.HELP_INFO)
    return HANDLED


async def guard_inbound_rate_limit(ctx: DispatchContext, deps: GuardDeps):
    """PRO-21 — per-customer abuse / cost protection.

    This guard has a side effect that outlives it: it resolves `ctx.is_exempt`
    on **every** run, not only when it limits. Pros and the admin are exempt,
    and that same flag gates the daily AI-call cap at three call sites much
    further down the pipeline — so computing it only on the limiting path would
    quietly subject pros to the customer cap.
    """
    ctx.is_exempt = (
        ctx.current_state == UserStates.PRO_MODE
        or ctx.chat_id == to_chat_id(deps.settings.ADMIN_PHONE)
    )
    if not ctx.is_exempt:
        phone = strip_suffix(ctx.chat_id)
        ctx.is_exempt = bool(
            await deps.users_collection.find_one(
                {
                    "phone_number": {"$in": [phone, ctx.chat_id]},
                    "role": "professional",
                }
            )
        )

    if ctx.is_exempt:
        return None

    allowed = await deps.security.check_sliding_window(
        ctx.chat_id,
        WorkerConstants.INBOUND_RATE_LIMIT_MAX,
        WorkerConstants.INBOUND_RATE_LIMIT_WINDOW_SECONDS,
    )
    if allowed:
        return None

    trips = await deps.security.record_trip(
        ctx.chat_id, WorkerConstants.INBOUND_RATE_LIMIT_WINDOW_SECONDS
    )
    logger.warning(
        f"⛔ Inbound rate limit hit for ...{ctx.chat_id[-8:]} (trip {trips})"
    )
    if trips >= WorkerConstants.RATE_LIMIT_ABUSE_TRIP_THRESHOLD:
        logger.error(
            f"🚨 Possible abuse: ...{ctx.chat_id[-8:]} tripped the rate limit {trips}x"
        )
    await deps.whatsapp.send_message(ctx.chat_id, Messages.Errors.RATE_LIMITED)
    return HANDLED


async def guard_zero_touch_intent(ctx: DispatchContext, deps: GuardDeps):
    """Zero-touch: transient confirmation after intent was detected in pro_flow.

    Note the third path: on a second unmatched reply this guard clears the
    transient state, refreshes `ctx.current_state`, and **falls through** rather
    than handling — the message goes on to normal routing.
    """
    if ctx.current_state != UserStates.AWAITING_INTENT_CONFIRMATION:
        return None

    if ctx.normalized_text == "1" or ctx.normalized_text in ("כן", "yes"):
        # set_state (not clear_state) leaves state_meta alive on its own 4h TTL,
        # so retire the re-prompt flag by hand or the next prompt inherits it.
        meta = await deps.state_manager.get_metadata(ctx.chat_id) or {}
        meta.pop("intent_reprompted", None)
        await deps.state_manager.set_metadata(ctx.chat_id, meta)
        await deps.state_manager.set_state(ctx.chat_id, UserStates.CUSTOMER_MODE)
        await deps.context_manager.clear_context(ctx.chat_id)
        await deps.whatsapp.send_message(ctx.chat_id, Messages.Pro.SWITCHED_TO_CUSTOMER)
        return HANDLED

    if ctx.normalized_text == "2" or ctx.normalized_text in ("לא", "no"):
        await deps.state_manager.clear_state(ctx.chat_id)
        await deps.whatsapp.send_message(ctx.chat_id, Messages.Pro.SWITCH_CANCELLED)
        return HANDLED

    # Unmatched reply: re-prompt once before giving up. Clearing the state on
    # the first miss dumped the pro back to the dashboard mid-question, which
    # read as the bot ignoring them. A cry for a human still gets out on the
    # first try — the SOS handler runs further down the dispatch.
    meta = await deps.state_manager.get_metadata(ctx.chat_id) or {}
    asking_for_human = contains_keyword(
        ctx.normalized_text,
        Messages.Keywords.SOS_COMMANDS,
        Messages.Keywords.SOS_EXCLUDE_PHRASES,
    )
    if not asking_for_human and not meta.get("intent_reprompted"):
        meta["intent_reprompted"] = True
        await deps.state_manager.set_metadata(ctx.chat_id, meta)
        await deps.state_manager.set_state(
            ctx.chat_id, UserStates.AWAITING_INTENT_CONFIRMATION, ttl=300
        )
        await deps.whatsapp.send_message(ctx.chat_id, Messages.Pro.INTENT_REPROMPT)
        return HANDLED

    # Second miss: clear transient state and fall through to normal routing.
    await deps.state_manager.clear_state(ctx.chat_id)
    await ctx.refresh_state(deps.state_manager)
    return None


# ---------------------------------------------------------------------------
# PRO-180 (PRO-139 slice A2): the holding-state cluster.
#
# These guards resolve workflow_service-owned collaborators (collections, the
# lead manager, flow helpers, keyword sets) through a call-time import of the
# module itself — the same pattern `guard_admin_wizard` uses for `admin_flow`.
# Two reasons: it breaks the circular import (workflow_service imports this
# module at its top), and attribute lookup at call time keeps the entire test
# suite's `monkeypatch.setattr(workflow_service, ...)` patch points working
# unchanged.
# ---------------------------------------------------------------------------


async def guard_consent_gate(ctx: DispatchContext, deps: GuardDeps):
    """Consent check (skip for professionals — they're added by admin).

    On fall-through this guard re-reads the FSM state and logs it — the
    original inline block did both unconditionally after the consent section,
    and every guard below (and the pipeline after them) takes the refreshed
    value.
    """
    from app.services import workflow_service as wf

    if ctx.current_state != UserStates.PRO_MODE:
        phone = strip_suffix(ctx.chat_id)
        is_pro = await deps.users_collection.find_one(
            {"phone_number": {"$in": [phone, ctx.chat_id]}, "role": "professional"}
        )

        if not is_pro:
            consent_status = await wf.has_consent(ctx.chat_id)

            # Handle consent response first (state takes priority over DB status)
            if ctx.current_state == UserStates.AWAITING_CONSENT:
                if ctx.normalized_text in Messages.Consent.ACCEPT_KEYWORDS:
                    await wf.record_consent(ctx.chat_id, accepted=True)
                    await deps.state_manager.clear_state(ctx.chat_id)
                    await deps.whatsapp.send_message(
                        ctx.chat_id, Messages.Consent.ACCEPTED
                    )
                    return HANDLED
                elif ctx.normalized_text in Messages.Consent.DECLINE_KEYWORDS:
                    await wf.record_consent(ctx.chat_id, accepted=False)
                    await deps.state_manager.clear_state(ctx.chat_id)
                    await deps.whatsapp.send_message(
                        ctx.chat_id, Messages.Consent.DECLINED
                    )
                    return HANDLED
                else:
                    # Repeat consent request if unclear response
                    await deps.whatsapp.send_message(
                        ctx.chat_id, Messages.Consent.REQUEST
                    )
                    return HANDLED

            if consent_status is None:
                # First contact — send consent request
                await deps.whatsapp.send_message(ctx.chat_id, Messages.Consent.REQUEST)
                await deps.state_manager.set_state(
                    ctx.chat_id, UserStates.AWAITING_CONSENT
                )
                return HANDLED

            if consent_status is False:
                # User previously declined — re-ask on new contact
                await deps.whatsapp.send_message(ctx.chat_id, Messages.Consent.REQUEST)
                await deps.state_manager.set_state(
                    ctx.chat_id, UserStates.AWAITING_CONSENT
                )
                return HANDLED

    # Refresh state after potential consent state changes
    await ctx.refresh_state(deps.state_manager)
    logger.info(f"🚦 User {ctx.chat_id} is in State: {ctx.current_state}")
    return None


async def guard_politeness(ctx: DispatchContext, deps: GuardDeps):
    """Politeness interceptor: handle "thank you" keywords without breaking state."""
    if (
        ctx.current_state != UserStates.PRO_MODE
        and ctx.normalized_text in Messages.Keywords.THANKS_KEYWORDS
    ):
        await deps.whatsapp.send_message(ctx.chat_id, Messages.Customer.YOU_ARE_WELCOME)
        return HANDLED
    return None


async def guard_customer_status_pull(ctx: DispatchContext, deps: GuardDeps):
    """Customer status pull ("סטטוס" / "status" / "?") — customers only, deterministic."""
    from app.services import workflow_service as wf

    if ctx.current_state not in (UserStates.PRO_MODE,) and not str(
        ctx.current_state
    ).startswith("ADMIN_"):
        stripped_text = (ctx.user_text or "").strip()
        is_status_cmd = (
            stripped_text in Messages.Keywords.STATUS_COMMANDS_EXACT
            or stripped_text.lower() in Messages.Keywords.STATUS_COMMANDS_WORDS
        )
        if is_status_cmd:
            reply = await wf._handle_status_query(ctx.chat_id)
            await deps.whatsapp.send_message(ctx.chat_id, reply)
            await wf.lead_manager.log_message(ctx.chat_id, "model", reply)
            return HANDLED
    return None


async def guard_sos_human_handoff(ctx: DispatchContext, deps: GuardDeps):
    """SOS / human handoff (customers only — pros have their own help menu).

    PRO-118: whole-token matching — "admin"/"מנהל" inside a longer word (or
    inside "מנהל עבודה", the profession) no longer pauses the bot.
    """
    from app.services import workflow_service as wf

    if not (
        ctx.user_text
        and ctx.current_state != UserStates.PRO_MODE
        and contains_keyword(
            ctx.normalized_text,
            Messages.Keywords.SOS_COMMANDS,
            Messages.Keywords.SOS_EXCLUDE_PHRASES,
        )
    ):
        return None

    # Pause bot with 15-minute auto-expiry (Task 1 updated constants)
    await deps.state_manager.set_state(
        ctx.chat_id, UserStates.PAUSED_FOR_HUMAN, ttl=WorkerConstants.PAUSE_TTL_SECONDS
    )
    logger.info(
        f"Bot paused for {ctx.chat_id} (triggered by: customer_sos, TTL: {WorkerConstants.PAUSE_TTL_SECONDS}s)"
    )

    active_lead = await wf.leads_collection.find_one(
        {
            "chat_id": ctx.chat_id,
            "status": {
                "$in": [LeadStatus.NEW, LeadStatus.CONTACTED, LeadStatus.BOOKED]
            },
        },
        sort=[("created_at", -1)],
    )

    if active_lead:
        await wf.leads_collection.update_one(
            {"_id": active_lead["_id"]},
            {"$set": {"is_paused": True, "paused_at": datetime.now(timezone.utc)}},
        )

    pro_id = active_lead["pro_id"] if active_lead and "pro_id" in active_lead else None
    await wf.send_sos_alert(ctx.chat_id, ctx.user_text, pro_id)

    # Notify the pro about the pause (if assigned)
    if pro_id:
        pro = await deps.users_collection.find_one({"_id": pro_id})
        if pro and pro.get("phone_number"):
            pro_phone = to_chat_id(pro["phone_number"])
            await deps.whatsapp.send_message(pro_phone, Messages.Pro.PAUSE_NOTIFICATION)

    await deps.whatsapp.send_message(
        ctx.chat_id, Messages.Customer.BOT_PAUSED_BY_CUSTOMER
    )
    return HANDLED


async def guard_emergency_hoist(ctx: DispatchContext, deps: GuardDeps):
    """PRO-121 — Emergency escalation, hoisted above every holding state.

    Each holding-state guard below handles unconditionally once its state
    matches, and `is_emergency_detected` was only ever read down at lead
    creation, so an emergency declared while the customer was already parked
    somewhere ("יש שריפה" mid address gate, while waiting for a pro's approval,
    against the loyalty menu) was answered with the holding question and never
    reached the lead.

    Position is the whole design: after the admin wizard, reset, help, the
    rate limiter, the consent gate and the SOS handoff — none of which an
    emergency may bypass, and a live human outranks the bot — and before the
    first state that would swallow it.
    """
    from app.services import workflow_service as wf

    if not (
        ctx.is_emergency_detected and ctx.current_state in wf.EMERGENCY_HOLDING_STATES
    ):
        return None

    emergency_action, emergency_ack = await wf._escalate_emergency(
        ctx.chat_id, ctx.current_state, ctx.user_text or ""
    )
    if emergency_action == "handled":
        return HANDLED
    if emergency_action == "released":
        await ctx.refresh_state(deps.state_manager)
        if emergency_ack:
            # Log the inbound *here*, ahead of the ack, and let step 1 skip
            # its own log. Two things depend on this placement: the ack must
            # never precede the turn that provoked it in the AI's history
            # window, and it must not be lost to an early return further
            # down (the PENDING_ADMIN_REVIEW short-circuit and the BOOKED
            # cancel interceptor both sit between here and step 1).
            await wf.lead_manager.log_message(
                ctx.chat_id, "user", wf._inbound_log_text(ctx.user_text, ctx.media_url)
            )
            ctx.emergency_inbound_logged = True
            await deps.whatsapp.send_message(ctx.chat_id, emergency_ack)
            await wf.lead_manager.log_message(ctx.chat_id, "model", emergency_ack)
    return None


async def guard_pro_approval_soft_hold(ctx: DispatchContext, deps: GuardDeps):
    """Soft Hold — customer is waiting for pro approval.

    A pro who ordered service for themselves parks here for up to an hour
    (PRO_APPROVAL_TTL_SECONDS), so an unconditional hold would lock them out of
    their own business for that whole window. Pro-only keywords escape; anything
    a customer prompt could plausibly mean does not.
    """
    from app.services import workflow_service as wf

    if ctx.current_state != UserStates.AWAITING_PRO_APPROVAL:
        return None

    # PRO-56: if the pro stayed silent and we offered the customer a
    # reassignment (reassign_offered), handle their 1/2 reply before the
    # generic soft-hold. 1 → find another pro; 2 → keep waiting (restart timer).
    if ctx.normalized_text in ("1", "2"):
        offered_lead = await wf.leads_collection.find_one(
            {
                "chat_id": ctx.chat_id,
                "status": LeadStatus.NEW,
                "reassign_offered": True,
            },
            sort=[("created_at", -1)],
        )
        if offered_lead:
            if ctx.normalized_text == "1":
                from app.services import monitor_service

                await monitor_service.reassign_lead(offered_lead)
            else:  # "2" — keep waiting; fully restart the SLA window so both
                # the pro nudge (T+10) and the offer (T+25) re-arm.
                await wf.leads_collection.update_one(
                    {"_id": offered_lead["_id"]},
                    {
                        "$set": {
                            "reassign_offered": False,
                            "approval_nudged": False,
                            "pro_notified_at": datetime.now(timezone.utc),
                        }
                    },
                )
                await deps.whatsapp.send_message(
                    ctx.chat_id, Messages.Customer.REASSIGN_WAIT_ACK
                )
            return HANDLED

    pro_escaping = ctx.normalized_text in wf.PRO_ONLY_KEYWORDS and (
        await wf._is_registered_pro(ctx.chat_id)
    )
    if not pro_escaping:
        await deps.whatsapp.send_message(ctx.chat_id, Messages.Customer.STILL_WAITING)
        return HANDLED
    return None


async def guard_paused_for_human(ctx: DispatchContext, deps: GuardDeps):
    """Bot Paused — pro or customer triggered human handoff (auto-expires via Redis TTL)."""
    from app.services import workflow_service as wf

    if ctx.current_state != UserStates.PAUSED_FOR_HUMAN:
        return None

    await wf.lead_manager.log_message(ctx.chat_id, "user", ctx.user_text or "")

    # PRO-121: PAUSED_FOR_HUMAN is deliberately absent from
    # EMERGENCY_HOLDING_STATES — a human owns this conversation and the bot
    # must not talk over them. But "a human owns it" only implies "someone
    # was paged" for the SOS entry; PRO-116's branch-13a "2" path parks the
    # customer here having merely messaged the assigned pro. An emergency
    # declared there was logged, rolled the TTL forward and reached nobody,
    # indefinitely. So: flag the lead and page — once — without un-pausing
    # and without sending the customer anything.
    if ctx.is_emergency_detected:
        # Find first, then claim by _id. Folding the "not yet alerted" guard
        # into the sorted query would make the *second* emergency claim the
        # next-newest lead instead of no-opping — flagging an unrelated
        # BOOKED job as an emergency and paging about the wrong one.
        newest_lead = await wf.leads_collection.find_one(
            {
                "chat_id": ctx.chat_id,
                "status": {
                    "$in": [LeadStatus.NEW, LeadStatus.CONTACTED, LeadStatus.BOOKED]
                },
            },
            sort=[("created_at", -1)],
        )
        paused_lead = (
            await wf.leads_collection.find_one_and_update(
                {
                    "_id": newest_lead["_id"],
                    "emergency_paused_alerted": {"$ne": True},
                },
                {"$set": {"is_emergency": True, "emergency_paused_alerted": True}},
            )
            if newest_lead
            else None
        )
        if paused_lead:
            logger.error(
                f"🚑 Emergency declared by ...{ctx.chat_id[-8:]} while paused for a "
                f"human (lead={paused_lead['_id']}) — alerting the operator"
            )
            await wf.send_sos_alert(
                ctx.chat_id, ctx.user_text or "", paused_lead.get("pro_id")
            )
    # Task 2: Reset 15-minute rolling window
    await deps.state_manager.set_state(
        ctx.chat_id, UserStates.PAUSED_FOR_HUMAN, ttl=WorkerConstants.PAUSE_TTL_SECONDS
    )

    # Update paused_at in lead doc to track activity for SLA monitor
    await wf.leads_collection.update_one(
        {
            "chat_id": ctx.chat_id,
            "is_paused": True,
            "status": {
                "$in": [LeadStatus.NEW, LeadStatus.CONTACTED, LeadStatus.BOOKED]
            },
        },
        {"$set": {"paused_at": datetime.now(timezone.utc)}},
    )

    logger.info(
        f"Bot paused for {ctx.chat_id} — message logged and timeout reset to {WorkerConstants.PAUSE_TTL_SECONDS}s"
    )
    return HANDLED


async def guard_cancel_confirmation(ctx: DispatchContext, deps: GuardDeps):
    """PRO-118: customer answering the "really cancel?" prompt for a BOOKED job.

    '1', 'כן' or a restated cancel keyword ("כן, בטל") confirms; anything
    else keeps the job (safe default for a destructive action) and restores
    whatever flow state the interceptor overwrote (e.g. AWAITING_ADDRESS on
    a second, in-flight lead) instead of dumping the customer to IDLE.
    """
    from app.services import workflow_service as wf

    if ctx.current_state != UserStates.AWAITING_CANCEL_CONFIRMATION:
        return None

    meta = await deps.state_manager.get_metadata(ctx.chat_id) or {}
    cancel_lead_id = meta.get("cancel_confirm_lead_id")
    resume_state = meta.get("cancel_confirm_resume_state")
    await deps.state_manager.clear_state(ctx.chat_id)
    confirmed = (
        ctx.normalized_text == "1"
        or ctx.normalized_text == "כן"
        or (contains_keyword(ctx.normalized_text, Messages.Keywords.CANCEL_KEYWORDS))
    )
    if confirmed and cancel_lead_id:
        try:
            cancel_oid = ObjectId(cancel_lead_id)
        except InvalidId:
            cancel_oid = None
        booked_lead = (
            await wf.leads_collection.find_one(
                {"_id": cancel_oid, "status": LeadStatus.BOOKED}
            )
            if cancel_oid
            else None
        )
        if booked_lead:
            await wf._execute_customer_cancel(booked_lead, ctx.chat_id)
        else:
            # The job moved on (finished / cancelled elsewhere) while the
            # confirmation prompt was open.
            await deps.whatsapp.send_message(
                ctx.chat_id, Messages.Customer.CANCEL_NO_ACTIVE
            )
    else:
        if resume_state:
            await deps.state_manager.set_state(ctx.chat_id, resume_state)
        await deps.whatsapp.send_message(ctx.chat_id, Messages.Customer.CANCEL_ABORTED)
    return HANDLED


async def guard_reschedule_selection(ctx: DispatchContext, deps: GuardDeps):
    """Reschedule selection — customer has been shown the slot menu and is picking."""
    from app.services import workflow_service as wf

    if ctx.current_state == UserStates.AWAITING_RESCHEDULE_TIME:
        await wf._handle_reschedule_selection(
            ctx.chat_id, ctx.user_text or "", deps.whatsapp
        )
        return HANDLED
    return None


async def guard_loyalty_confirmation(ctx: DispatchContext, deps: GuardDeps):
    """Loyalty confirmation — customer replied to the "previous pro?" offer.

    Note the fall-through: a second unclear reply clears the state, refreshes
    `ctx.current_state`, and releases the message to normal routing (PRO-119) —
    the old handler re-prompted forever with no budget and no fall-through.
    """
    from app.services import workflow_service as wf

    if ctx.current_state != UserStates.AWAITING_LOYALTY_CONFIRMATION:
        return None

    meta = await deps.state_manager.get_metadata(ctx.chat_id) or {}
    past_pro_id = meta.get("past_pro_id")
    active_lead = await wf.leads_collection.find_one(
        {
            "chat_id": ctx.chat_id,
            "status": {"$in": [LeadStatus.NEW, LeadStatus.CONTACTED]},
        },
        sort=[("created_at", -1)],
    )
    # PRO-119: accept natural language, not just the literal menu digits.
    # Whole-token matching (PRO-118) makes "כן בבקשה" / "לא תודה" work
    # while "בסדר גמור לא" — matching both sides — is treated as unclear.
    says_yes = ctx.normalized_text == "1" or contains_keyword(
        ctx.normalized_text, Messages.Keywords.AFFIRMATIVE_KEYWORDS
    )
    says_no = ctx.normalized_text == "2" or contains_keyword(
        ctx.normalized_text, Messages.Keywords.NEGATIVE_KEYWORDS
    )

    if says_yes and not says_no:
        handled = await wf._accept_loyalty_offer(
            ctx.chat_id, past_pro_id, active_lead, meta
        )
        if handled:
            return HANDLED
        if handled is None:
            # The lead moved under us mid-answer (a monitor escalation, most
            # likely). Don't answer a *yes* with "I'll go find you someone" —
            # a promise the race winner has already invalidated.
            await deps.state_manager.clear_state(ctx.chat_id)
            await deps.whatsapp.send_message(
                ctx.chat_id, Messages.Customer.LOYALTY_ALREADY_UPDATED
            )
            await wf.lead_manager.log_message(
                ctx.chat_id, "model", Messages.Customer.LOYALTY_ALREADY_UPDATED
            )
            return HANDLED
        # The past pro is gone or there is no live lead to attach them to —
        # answer with the decline copy rather than promising a check
        # against somebody we cannot reach.
        await wf._decline_loyalty_offer(ctx.chat_id)
        return HANDLED

    # A reply carrying *both* sides ("כן אבל בעצם לא") is unclear, not a
    # decline — it must reach the re-prompt below, so guard on `not
    # says_yes` here rather than letting says_no win by position.
    if says_no and not says_yes:
        await wf._decline_loyalty_offer(ctx.chat_id)
        return HANDLED

    # Unclear reply: re-prompt once, then let the message through to normal
    # routing. The old handler re-prompted forever with no budget and no
    # fall-through, so anything the parser missed trapped the customer.
    if not meta.get("loyalty_reprompted"):
        meta["loyalty_reprompted"] = True
        await deps.state_manager.set_metadata(ctx.chat_id, meta)
        await deps.state_manager.set_state(
            ctx.chat_id,
            UserStates.AWAITING_LOYALTY_CONFIRMATION,
            ttl=WorkerConstants.LOYALTY_CONFIRM_TTL_SECONDS,
        )
        await deps.whatsapp.send_message(
            ctx.chat_id, Messages.Customer.LOYALTY_REPROMPT
        )
        await wf.lead_manager.log_message(
            ctx.chat_id, "model", Messages.Customer.LOYALTY_REPROMPT
        )
        return HANDLED
    logger.info(
        f"Loyalty offer unanswered twice by ...{ctx.chat_id[-8:]} — releasing to "
        "normal routing"
    )
    await deps.state_manager.clear_state(ctx.chat_id)
    await ctx.refresh_state(deps.state_manager)
    return None


async def guard_new_or_existing(ctx: DispatchContext, deps: GuardDeps):
    """PRO-116: customer replied to "new request or about the existing job?"

    (sent when they have a confirmed BOOKED job — see the Q3 gate in the
    pipeline below).
    """
    from app.services import workflow_service as wf

    if ctx.current_state != UserStates.AWAITING_NEW_OR_EXISTING:
        return None

    reply = (ctx.user_text or "").strip()
    if reply in ("1", "כן"):
        # New request: release the gate so the next message runs normal intake
        # (the booked lead is already flagged new_request_prompted).
        await deps.state_manager.set_state(ctx.chat_id, UserStates.IDLE)
        await deps.whatsapp.send_message(ctx.chat_id, Messages.Customer.NEW_REQUEST_ACK)
        await wf.lead_manager.log_message(
            ctx.chat_id, "model", Messages.Customer.NEW_REQUEST_ACK
        )
        return HANDLED
    elif reply in ("2", "לא"):
        # About the existing job: hand off to the assigned pro and step the
        # bot back so the two can talk directly.
        meta = await deps.state_manager.get_metadata(ctx.chat_id)
        booked_lead_id = meta.get("booked_lead_id")
        pro_name = "איש המקצוע"
        try:
            booked_lead = (
                await wf.leads_collection.find_one({"_id": ObjectId(booked_lead_id)})
                if booked_lead_id
                else None
            )
            if booked_lead and booked_lead.get("pro_id"):
                pro = await deps.users_collection.find_one(
                    {"_id": booked_lead["pro_id"]}
                )
                if pro:
                    pro_name = pro.get("business_name") or pro_name
                    if pro.get("phone_number"):
                        await deps.whatsapp.send_message(
                            to_chat_id(pro["phone_number"]),
                            Messages.Pro.CUSTOMER_EXISTING_JOB_QUERY.format(
                                customer_name=booked_lead.get("customer_name")
                                or Messages.Fallbacks.CUSTOMER_NAME,
                                issue=booked_lead.get("issue_type") or "העבודה",
                                customer_phone=strip_suffix(ctx.chat_id),
                            ),
                        )
        except Exception as e:
            logger.error(f"Existing-job handoff to pro failed for {ctx.chat_id}: {e}")
        await deps.state_manager.set_state(
            ctx.chat_id,
            UserStates.PAUSED_FOR_HUMAN,
            ttl=WorkerConstants.PAUSE_TTL_SECONDS,
        )
        msg = Messages.Customer.EXISTING_JOB_HANDOFF.format(pro_name=pro_name)
        await deps.whatsapp.send_message(ctx.chat_id, msg)
        await wf.lead_manager.log_message(ctx.chat_id, "model", msg)
        return HANDLED
    else:
        await deps.whatsapp.send_message(
            ctx.chat_id, Messages.Customer.NEW_OR_EXISTING_REPROMPT
        )
        return HANDLED


async def guard_booked_cancel_reschedule(ctx: DispatchContext, deps: GuardDeps):
    """Interceptor: customer with a confirmed BOOKED lead sends cancel or reschedule.

    Placed before PRO_BUSINESS_KEYWORDS so Hebrew phrases are not misrouted.
    Guards against PRO_MODE so pros who happen to use these phrases are
    unaffected. Falls through to normal routing when there is no BOOKED lead.
    """
    from app.services import workflow_service as wf

    if not (
        ctx.current_state != UserStates.PRO_MODE
        and ctx.user_text
        and (
            contains_keyword(ctx.normalized_text, Messages.Keywords.RESCHEDULE_KEYWORDS)
            or contains_keyword(ctx.normalized_text, Messages.Keywords.CANCEL_KEYWORDS)
        )
    ):
        return None

    booked_lead = await wf.leads_collection.find_one(
        {"chat_id": ctx.chat_id, "status": LeadStatus.BOOKED},
        sort=[("created_at", -1)],
    )
    if not booked_lead:
        # booked_lead is None — fall through to normal routing
        return None

    pro_id = booked_lead.get("pro_id")

    if contains_keyword(ctx.normalized_text, Messages.Keywords.CANCEL_KEYWORDS):
        # PRO-118: cancelling a confirmed BOOKED job is destructive —
        # ask for an explicit confirmation instead of acting on the
        # first keyword hit. Expiry of the TTL = the job silently
        # stays. The customer's current flow state (e.g.
        # AWAITING_ADDRESS on a second in-flight lead) is stashed so
        # an aborted cancel restores it rather than dropping to IDLE.
        meta = await deps.state_manager.get_metadata(ctx.chat_id) or {}
        meta["cancel_confirm_lead_id"] = str(booked_lead["_id"])
        # getattr(.value) — get_state returns a plain Redis string on
        # every real path but the *enum member* UserStates.IDLE as its
        # default; str() on the member yields "UserStates.IDLE", which
        # would be persisted verbatim and match no state downstream.
        meta["cancel_confirm_resume_state"] = (
            getattr(ctx.current_state, "value", ctx.current_state)
            if ctx.current_state
            else None
        )
        await deps.state_manager.set_state(
            ctx.chat_id,
            UserStates.AWAITING_CANCEL_CONFIRMATION,
            ttl=WorkerConstants.CANCEL_CONFIRM_TTL_SECONDS,
        )
        await deps.state_manager.set_metadata(ctx.chat_id, meta)
        await deps.whatsapp.send_message(
            ctx.chat_id, Messages.Customer.CANCEL_CONFIRM_PROMPT
        )
        logger.info(
            f"Customer ...{ctx.chat_id[-8:]} asked to cancel BOOKED lead "
            f"{booked_lead['_id']} — awaiting confirmation"
        )
        return HANDLED

    # Reschedule keyword
    if not pro_id:
        await deps.whatsapp.send_message(
            ctx.chat_id, Messages.Customer.RESCHEDULE_NO_SLOTS
        )
        return HANDLED

    available_slots = await wf.get_available_slots(str(pro_id), limit=8)
    if not available_slots:
        await deps.whatsapp.send_message(
            ctx.chat_id, Messages.Customer.RESCHEDULE_NO_SLOTS
        )
        return HANDLED

    lines = []
    slots_context = {}
    for i, slot in enumerate(available_slots, 1):
        label = slot["start_time"].astimezone(wf._IL_TZ).strftime("%d/%m/%Y %H:%M")
        lines.append(f"{i}. {label}")
        slots_context[str(i)] = str(slot["_id"])

    await deps.state_manager.set_state(ctx.chat_id, UserStates.AWAITING_RESCHEDULE_TIME)
    await deps.state_manager.set_metadata(
        ctx.chat_id, {"reschedule_slots_context": slots_context}
    )
    await deps.whatsapp.send_message(
        ctx.chat_id,
        Messages.Customer.RESCHEDULE_OFFER.format(slots="\n".join(lines)),
    )
    logger.info(
        f"Customer {ctx.chat_id} offered reschedule for lead {booked_lead['_id']}"
    )
    return HANDLED


# ---------------------------------------------------------------------------
# PRO-181 (PRO-139 slice A3): the pro-routing cluster.
#
# Same call-time `workflow_service` lookup as A2 above, for the same two
# reasons (circular import, and the suite's monkeypatch points).
# ---------------------------------------------------------------------------


async def guard_customer_mode_switch(ctx: DispatchContext, deps: GuardDeps):
    """Explicit mode switch: a registered pro who types "לקוח".

    Deterministic — no AI, no confirmation prompt. Works from PRO_MODE and from
    IDLE (where auto-detect would otherwise force PRO_MODE).
    """
    from app.services import workflow_service as wf

    if (
        ctx.normalized_text in Messages.Keywords.CUSTOMER_MODE_COMMANDS
        and ctx.current_state in (UserStates.PRO_MODE, UserStates.IDLE)
    ):
        if await wf._is_registered_pro(ctx.chat_id):
            await deps.state_manager.set_state(ctx.chat_id, UserStates.CUSTOMER_MODE)
            await deps.context_manager.clear_context(ctx.chat_id)
            await deps.whatsapp.send_message(
                ctx.chat_id, Messages.Pro.SWITCHED_TO_CUSTOMER
            )
            logger.info(
                f"Pro ...{ctx.chat_id[-8:]} switched to CUSTOMER_MODE via keyword"
            )
            return HANDLED
    return None


async def guard_pro_business_keyword_bypass(ctx: DispatchContext, deps: GuardDeps):
    """Safety bypass — a registered pro typing a business keyword routes to
    pro_flow even from CUSTOMER_MODE; snap them back to PRO_MODE first.

    Never handles: it only mutates `ctx.current_state`, and the next guard
    (`pro_mode_routing`) is what acts on it. That is the inline code's shape
    preserved — the bypass and the routing were two separate `if`s, and keeping
    them separate is what lets the ambiguous-keyword yield fall through to the
    customer side rather than being swallowed here.

    Ambiguous keywords (bare digits, אשר/דחה, ...) yield to a customer-side
    question that is actually open: mid-reschedule, a "3" is a slot pick, not a
    job approval.

    PRO-186: the bypass rescues a pro stranded on the *customer* side; a pro
    already inside one of pro_flow's own prompts is not stranded. Overwriting
    PRO_SELECTING_JOB_TO_FINISH here is what made the job list unanswerable —
    pro_flow re-reads the state and saw PRO_MODE, so "1" ran approve. Hence
    PRO_DISPATCH_STATES rather than PRO_MODE alone.
    """
    from app.services import workflow_service as wf

    if ctx.normalized_text in wf.PRO_BUSINESS_KEYWORDS:
        is_pro_doc = await wf._is_registered_pro(ctx.chat_id)
        if is_pro_doc and ctx.current_state not in wf.PRO_DISPATCH_STATES:
            defer_to_customer_flow = (
                ctx.normalized_text in wf.AMBIGUOUS_PRO_KEYWORDS
                and (await wf._customer_prompt_pending(ctx.chat_id, ctx.current_state))
            )
            if defer_to_customer_flow:
                logger.info(
                    f"Pro ...{ctx.chat_id[-8:]} sent ambiguous keyword with a customer "
                    f"prompt open — staying in {ctx.current_state}"
                )
            else:
                await deps.state_manager.set_state(ctx.chat_id, UserStates.PRO_MODE)
                ctx.current_state = UserStates.PRO_MODE
    return None


async def guard_pro_mode_routing(ctx: DispatchContext, deps: GuardDeps):
    """Pro Mode — and every prompt pro_flow is holding open (PRO-186)."""
    from app.services import workflow_service as wf

    if ctx.current_state not in wf.PRO_DISPATCH_STATES:
        return None

    pro_resp = await wf._handle_pro_cmd(
        ctx.chat_id, ctx.user_text, deps.whatsapp, wf.lead_manager, ai=wf.ai
    )
    if pro_resp:
        await deps.whatsapp.send_message(ctx.chat_id, pro_resp)
    # empty string "" means pro_flow already sent everything internally
    return HANDLED


async def guard_pro_onboarding(ctx: DispatchContext, deps: GuardDeps):
    """Pro onboarding flow."""
    from app.services import workflow_service as wf

    if ctx.current_state not in wf.ONBOARDING_STATES:
        return None

    await wf.handle_onboarding_step(
        ctx.chat_id, ctx.user_text or "", ctx.current_state, deps.whatsapp
    )
    return HANDLED


async def guard_awaiting_address(ctx: DispatchContext, deps: GuardDeps):
    """AWAITING_ADDRESS re-entry after the finalization gate rejected an
    incomplete address.

    Re-run extraction on the customer's reply, merge with whatever we already
    stored, and only clear the state when all five fields (street, number, city,
    floor, apartment) are present.

    **Falls through without refreshing `ctx.current_state`** when there is no
    active lead — the inline original cleared Redis and left the local
    `current_state` at AWAITING_ADDRESS, so the two IDLE-keyed guards below
    (registration keyword, auto-detect) do not fire on that pass. Preserved
    exactly. Calling `ctx.refresh_state()` here would read IDLE and start firing
    them on a path where they never have, which is a dispatch change, not a
    migration. Recorded on PRO-139 for whoever owns the behaviour.
    """
    from app.services import workflow_service as wf

    if ctx.current_state != UserStates.AWAITING_ADDRESS:
        return None

    # Nevermind/cancel bailout: user wants out of the flow instead of fighting
    # the address gate. Match cancellation keywords BEFORE is_address_complete
    # so we never loop the user back through "אני צריך רחוב ומספר בית".
    if ctx.user_text and contains_keyword(
        ctx.normalized_text, Messages.Keywords.CANCEL_KEYWORDS
    ):
        cancelled_lead = await wf.leads_collection.find_one(
            {
                "chat_id": ctx.chat_id,
                "status": {"$in": [LeadStatus.NEW, LeadStatus.CONTACTED]},
            },
            sort=[("created_at", -1)],
        )
        if cancelled_lead:
            await wf.set_lead_status(
                cancelled_lead["_id"],
                LeadStatus.CANCELLED,
                wf.Actor.CUSTOMER,
                extra_set={
                    "cancelled_at": datetime.now(timezone.utc),
                    "cancel_reason": "user_bailout_awaiting_address",
                },
            )
            logger.info(
                f"🚪 AWAITING_ADDRESS cancelled by user for {ctx.chat_id} "
                f"(lead={cancelled_lead['_id']})"
            )
        await deps.state_manager.clear_state(ctx.chat_id)
        await deps.context_manager.clear_context(ctx.chat_id)
        await deps.whatsapp.send_message(
            ctx.chat_id, Messages.Customer.REQUEST_CANCELLED
        )
        return HANDLED

    if not ctx.user_text or len(ctx.user_text) <= 3:
        await deps.whatsapp.send_message(ctx.chat_id, Messages.Customer.ADDRESS_INVALID)
        return HANDLED

    active_lead_await = await wf.leads_collection.find_one(
        {
            "chat_id": ctx.chat_id,
            "status": {"$in": [LeadStatus.NEW, LeadStatus.CONTACTED]},
        },
        sort=[("created_at", -1)],
    )
    if not active_lead_await:
        await deps.state_manager.clear_state(ctx.chat_id)
        # Fall through to normal routing below — deliberately WITHOUT refreshing
        # ctx.current_state; see the docstring.
        return None

    lead_facts = active_lead_await
    await wf.lead_manager.log_message(ctx.chat_id, "user", ctx.user_text)
    follow_up_prompt = wf.Prompts.DISPATCHER_SYSTEM.format(
        known_customer_name=lead_facts.get("customer_name") or "none",
        known_city=lead_facts.get("city") or "none",
        known_issue=lead_facts.get("issue_type") or "none",
        known_street=lead_facts.get("street") or "none",
        known_street_number=lead_facts.get("street_number") or "none",
        known_floor=lead_facts.get("floor") or "none",
        known_apartment=lead_facts.get("apartment") or "none",
    )
    if not ctx.is_exempt and not await deps.security.check_and_increment_daily_ai_cap(
        ctx.chat_id, WorkerConstants.DAILY_AI_CALL_CAP
    ):
        logger.warning(f"⛔ Daily AI cap reached for ...{ctx.chat_id[-8:]}")
        await deps.whatsapp.send_message(
            ctx.chat_id, Messages.Errors.DAILY_AI_CAP_REACHED
        )
        return HANDLED
    try:
        follow_up = await wf.ai.analyze_conversation(
            history=await wf.lead_manager.get_chat_history(ctx.chat_id),
            user_text=ctx.user_text,
            custom_system_prompt=follow_up_prompt,
            require_json=True,
        )
    except Exception as e:
        logger.error(f"AWAITING_ADDRESS re-extraction failed for {ctx.chat_id}: {e}")
        await deps.whatsapp.send_message(ctx.chat_id, Messages.Errors.AI_OVERLOAD)
        return HANDLED

    merged = {
        "customer_name": follow_up.extracted_data.customer_name
        or lead_facts.get("customer_name"),
        "street": follow_up.extracted_data.street or lead_facts.get("street"),
        "street_number": follow_up.extracted_data.street_number
        or lead_facts.get("street_number"),
        "city": follow_up.extracted_data.city or lead_facts.get("city"),
        "floor": follow_up.extracted_data.floor or lead_facts.get("floor"),
        "apartment": follow_up.extracted_data.apartment or lead_facts.get("apartment"),
    }
    logger.info(
        f"🔍 AWAITING_ADDRESS re-extraction for {ctx.chat_id}: "
        f"new_from_ai={[k for k, v in merged.items() if v and not lead_facts.get(k)]}, "
        f"merged={ {k: v for k, v in merged.items() if v} }"
    )
    non_empty = {k: v for k, v in merged.items() if v}
    if non_empty:
        await wf.leads_collection.update_one(
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

    ok, reason = wf.is_address_complete(probe)
    if ok:
        full = wf.compose_full_address(probe)
        await wf.leads_collection.update_one(
            {"_id": active_lead_await["_id"]}, {"$set": {"full_address": full}}
        )
        await deps.state_manager.clear_state(ctx.chat_id)
        await deps.whatsapp.send_message(ctx.chat_id, Messages.Customer.ADDRESS_SAVED)
        logger.info(
            f"✅ AWAITING_ADDRESS complete for {ctx.chat_id}, full_address={full!r}"
        )
        return HANDLED
    else:
        await deps.whatsapp.send_message(ctx.chat_id, reason)
        logger.info(
            f"⏳ AWAITING_ADDRESS still missing parts for {ctx.chat_id}: {reason}"
        )
        return HANDLED


async def guard_pro_registration_keyword(ctx: DispatchContext, deps: GuardDeps):
    """Pro registration keyword check (before auto-detect)."""
    from app.services import workflow_service as wf

    if (
        ctx.current_state == UserStates.IDLE
        and ctx.normalized_text in Messages.Keywords.REGISTER_COMMANDS
    ):
        await wf.start_onboarding(ctx.chat_id, deps.whatsapp)
        return HANDLED
    return None


async def guard_pro_autodetect(ctx: DispatchContext, deps: GuardDeps):
    """Auto-detect a professional on first contact (only active/approved pros)."""
    from app.services import workflow_service as wf

    if ctx.current_state != UserStates.IDLE:
        return None

    # `phone` used to be a shared local computed by the consent gate; that
    # gate now lives here too (PRO-180), so derive it locally.
    phone = strip_suffix(ctx.chat_id)
    is_pro = await deps.users_collection.find_one(
        {
            "phone_number": {"$in": [phone, ctx.chat_id]},
            "role": "professional",
            "is_active": True,
        }
    )
    if not is_pro:
        return None

    # Redis TTL edge: a pro being served as a customer whose CUSTOMER_MODE
    # key expired lands here mid-request. Re-entering PRO_MODE would answer
    # their next message with the dashboard, so restore CUSTOMER_MODE while
    # their own lead is still open.
    if await wf._get_active_customer_lead(ctx.chat_id):
        await deps.state_manager.set_state(ctx.chat_id, UserStates.CUSTOMER_MODE)
        # Not read again on this pass — the customer dispatcher below is
        # already the correct destination. Kept so the local view of state
        # matches Redis for anyone extending this block.
        ctx.current_state = UserStates.CUSTOMER_MODE
        logger.info(
            f"Restored CUSTOMER_MODE for pro ...{ctx.chat_id[-8:]} — own lead still open"
        )
        return None

    await deps.state_manager.set_state(ctx.chat_id, UserStates.PRO_MODE)
    pro_resp = await wf._handle_pro_cmd(
        ctx.chat_id, ctx.user_text, deps.whatsapp, wf.lead_manager, ai=wf.ai
    )
    if pro_resp:
        await deps.whatsapp.send_message(ctx.chat_id, pro_resp)
    # empty string "" means pro_flow already sent everything internally
    return HANDLED


async def guard_pending_admin_review_shortcircuit(
    ctx: DispatchContext, deps: GuardDeps
):
    """Patch #2: short-circuit PENDING_ADMIN_REVIEW.

    If this chat has a lead already sitting in PENDING_ADMIN_REVIEW, an admin
    owns it — running the dispatcher again would create a DUPLICATE contacted
    lead for the same issue (observed on 2026-04-18 with lead
    69e375cb9a04cba45197e625 spawning 69e376679a04cba45197e63e 2 min later).
    Log the message for admin visibility, send a throttled ack, and stop.

    PRO-63: bounded by age. The short-circuit has no natural exit — a lead sits
    in PENDING_ADMIN_REVIEW until a human moves it, so an unworked escalation
    would silently brick this customer's chat forever, which is a worse dead
    end than the auto-CLOSED behaviour PRO-63 replaced. After
    PENDING_REVIEW_SHORTCIRCUIT_HOURS their next message starts a fresh
    request. Leads with no `updated_at` fall outside the window and therefore
    do not short-circuit — failing toward "customer can talk to us".
    """
    from app.services import workflow_service as wf

    shortcircuit_cutoff = datetime.now(timezone.utc) - timedelta(
        hours=WorkerConstants.PENDING_REVIEW_SHORTCIRCUIT_HOURS
    )
    pending_admin_lead = await wf.leads_collection.find_one(
        {
            "chat_id": ctx.chat_id,
            "status": LeadStatus.PENDING_ADMIN_REVIEW,
            "updated_at": {"$gte": shortcircuit_cutoff},
        },
        sort=[("created_at", -1)],
    )
    if not pending_admin_lead:
        return None

    log_text_pending = ctx.user_text or ""
    if ctx.media_url:
        log_text_pending = f"{log_text_pending} [MEDIA: {ctx.media_url}]"
    await wf.lead_manager.log_message(ctx.chat_id, "user", log_text_pending)

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
        await deps.whatsapp.send_message(
            ctx.chat_id, Messages.Customer.STILL_PENDING_REVIEW
        )
        await wf.lead_manager.log_message(
            ctx.chat_id, "model", Messages.Customer.STILL_PENDING_REVIEW
        )
        await wf.leads_collection.update_one(
            {"_id": pending_admin_lead["_id"]},
            {"$set": {"last_pending_ack_at": now}},
        )
    logger.info(
        f"🔒 PENDING_ADMIN_REVIEW short-circuit for {ctx.chat_id} "
        f"(lead={pending_admin_lead['_id']}, ack_sent={should_ack})"
    )
    # PRO-121: this short-circuit is a 24h hold keyed on lead status, so the
    # dispatch hoist above can never fire for it — an emergency declared here
    # would otherwise get STILL_PENDING_REVIEW and reach nobody. Page on the
    # same 30-minute throttle as the ack so a burst cannot spam the operator.
    if ctx.is_emergency_detected and should_ack:
        wf.notification_service.page_operator(
            f"EMERGENCY declared on lead {pending_admin_lead['_id']}, which is "
            "already PENDING_ADMIN_REVIEW — the customer is behind the 24h "
            "short-circuit and needs manual routing now"
        )
    return HANDLED


#: The chain, in execution order. **This ordering is the contract** — see the
#: module docstring and `tests/test_dispatch_guards.py`, which pins it. Each
#: entry is `(name, guard)`; the name exists so a failing order assertion names
#: the guard that moved rather than printing a function repr.
#:
#: PRO-180 (slice A2): the holding-state cluster runs after the A1 head.
#: `emergency_hoist` sits after `sos_human_handoff` and before every holding
#: state — PRO-121's "position is the whole design".
#:
#: PRO-181 (slice A3): the pro-routing cluster runs after the holding states.
GUARD_CHAIN: Tuple[Tuple[str, Guard], ...] = (
    ("admin_wizard", guard_admin_wizard),
    ("global_reset", guard_global_reset),
    ("help_menu", guard_help_menu),
    ("inbound_rate_limit", guard_inbound_rate_limit),
    ("zero_touch_intent", guard_zero_touch_intent),
    ("consent_gate", guard_consent_gate),
    ("politeness", guard_politeness),
    ("customer_status_pull", guard_customer_status_pull),
    ("sos_human_handoff", guard_sos_human_handoff),
    ("emergency_hoist", guard_emergency_hoist),
    ("pro_approval_soft_hold", guard_pro_approval_soft_hold),
    ("paused_for_human", guard_paused_for_human),
    ("cancel_confirmation", guard_cancel_confirmation),
    ("reschedule_selection", guard_reschedule_selection),
    ("loyalty_confirmation", guard_loyalty_confirmation),
    ("new_or_existing", guard_new_or_existing),
    ("booked_cancel_reschedule", guard_booked_cancel_reschedule),
    ("customer_mode_switch", guard_customer_mode_switch),
    ("pro_business_keyword_bypass", guard_pro_business_keyword_bypass),
    ("pro_mode_routing", guard_pro_mode_routing),
    ("pro_onboarding", guard_pro_onboarding),
    ("awaiting_address", guard_awaiting_address),
    ("pro_registration_keyword", guard_pro_registration_keyword),
    ("pro_autodetect", guard_pro_autodetect),
    ("pending_admin_review_shortcircuit", guard_pending_admin_review_shortcircuit),
)


async def run_guard_chain(ctx: DispatchContext, deps: GuardDeps):
    """Walk the chain in order; stop at the first guard that handles.

    Returns `HANDLED` if the message was fully dealt with, otherwise `None` —
    in which case `ctx` may still have been mutated by guards it passed through.
    """
    for name, guard in GUARD_CHAIN:
        result = await guard(ctx, deps)
        if result is HANDLED:
            return HANDLED
        if result is not None:
            # The `Guard` alias is only a type hint — nothing enforces it at
            # runtime. This module exists to receive the A2/A3 migrations, and a
            # guard that ended with a bare `return True` (or fell off the end of
            # a branch that meant to handle the message) would otherwise be read
            # as "fall through" and let a second guard answer the same message.
            # Fail loudly at the seam instead of double-handling silently.
            raise RuntimeError(
                f"dispatch guard {name!r} returned {result!r}; a guard must "
                f"return HANDLED or None"
            )
    return None
