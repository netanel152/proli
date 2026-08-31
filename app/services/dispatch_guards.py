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
from typing import Any, Awaitable, Callable, Optional, Tuple

from app.core.constants import UserStates, WorkerConstants
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


#: The chain, in execution order. **This ordering is the contract** — see the
#: module docstring and `tests/test_dispatch_guards.py`, which pins it. Each
#: entry is `(name, guard)`; the name exists so a failing order assertion names
#: the guard that moved rather than printing a function repr.
GUARD_CHAIN: Tuple[Tuple[str, Guard], ...] = (
    ("admin_wizard", guard_admin_wizard),
    ("global_reset", guard_global_reset),
    ("help_menu", guard_help_menu),
    ("inbound_rate_limit", guard_inbound_rate_limit),
    ("zero_touch_intent", guard_zero_touch_intent),
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
