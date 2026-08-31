"""Unit tests for the PRO-179 (PRO-139 slice A1) guard chain.

`tests/test_workflow_orchestrator.py` already exercises these guards
end-to-end through `process_incoming_message`, so this file is deliberately
narrower: it pins the ordering contract and isolates the two side effects the
end-to-end tests cannot see in one place — `guard_inbound_rate_limit`
resolving `ctx.is_exempt` on every run (read again ~700 lines later to gate
the daily AI-call cap) and `guard_zero_touch_intent`'s third path falling
through instead of handling.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.services.admin_flow as admin_flow_module
import app.services.dispatch_guards as dispatch_guards
import app.services.workflow_service as workflow_service
from app.core.constants import UserStates, WorkerConstants
from app.core.messages import Messages
from app.core.phone import to_chat_id
from app.services.dispatch_guards import (
    GUARD_CHAIN,
    HANDLED,
    DispatchContext,
    GuardDeps,
    guard_admin_wizard,
    guard_booked_cancel_reschedule,
    guard_cancel_confirmation,
    guard_emergency_hoist,
    guard_global_reset,
    guard_help_menu,
    guard_inbound_rate_limit,
    guard_loyalty_confirmation,
    guard_zero_touch_intent,
    run_guard_chain,
)

ADMIN_CHAT_ID = to_chat_id("972524828796")
CUSTOMER_CHAT_ID = to_chat_id("972500000001")


def make_ctx(
    chat_id=CUSTOMER_CHAT_ID,
    user_text="",
    normalized_text="",
    current_state=UserStates.IDLE,
    media_url=None,
    is_emergency_detected=False,
):
    return DispatchContext(
        chat_id=chat_id,
        user_text=user_text,
        media_url=media_url,
        normalized_text=normalized_text,
        is_emergency_detected=is_emergency_detected,
        current_state=current_state,
    )


@pytest.fixture
def deps():
    whatsapp = AsyncMock()
    state_manager = AsyncMock()
    state_manager.get_metadata = AsyncMock(return_value={})
    context_manager = AsyncMock()
    users_collection = AsyncMock()
    users_collection.find_one = AsyncMock(return_value=None)
    security = MagicMock()
    security.check_sliding_window = AsyncMock(return_value=True)
    security.record_trip = AsyncMock(return_value=1)
    settings = SimpleNamespace(ADMIN_PHONE="972524828796")
    return GuardDeps(
        whatsapp=whatsapp,
        state_manager=state_manager,
        context_manager=context_manager,
        users_collection=users_collection,
        security=security,
        settings=settings,
    )


# --------------------------------------------------------------------------
# Chain order — the contract
# --------------------------------------------------------------------------


def test_guard_chain_order_is_pinned():
    """PRO-121/PRO-180: position is the whole design. A reorder must fail the build."""
    assert [name for name, _guard in GUARD_CHAIN] == [
        "admin_wizard",
        "global_reset",
        "help_menu",
        "inbound_rate_limit",
        "zero_touch_intent",
        "consent_gate",
        "politeness",
        "customer_status_pull",
        "sos_human_handoff",
        "emergency_hoist",
        "pro_approval_soft_hold",
        "paused_for_human",
        "cancel_confirmation",
        "reschedule_selection",
        "loyalty_confirmation",
        "new_or_existing",
        "booked_cancel_reschedule",
    ]


def test_emergency_hoist_position_is_pinned():
    """PRO-121 ("position is the whole design"), pinned explicitly for PRO-180:
    `emergency_hoist` must run after `sos_human_handoff` (a live human outranks
    the bot) and before every holding-state guard it exists to hoist above.
    """
    names = [name for name, _guard in GUARD_CHAIN]
    sos_index = names.index("sos_human_handoff")
    hoist_index = names.index("emergency_hoist")
    assert hoist_index > sos_index

    for holding_guard in (
        "pro_approval_soft_hold",
        "paused_for_human",
        "cancel_confirmation",
        "reschedule_selection",
        "loyalty_confirmation",
        "new_or_existing",
    ):
        assert hoist_index < names.index(holding_guard), holding_guard


def test_emergency_holding_states_membership_is_pinned():
    """The other half of the PRO-121 invariant: *which* states the hoist covers.

    The exclusions are as deliberate as the inclusions (PRO-180):
    - AWAITING_RESCHEDULE_TIME — product call: "דחוף" mid slot-pick means "the
      earliest slot you have"; releasing would lose the menu.
    - PAUSED_FOR_HUMAN — a live human owns the conversation; the paused guard
      has its own emergency flag-and-page path instead.
    - AWAITING_CANCEL_CONFIRMATION — a destructive-action prompt must not be
      short-circuited out from under the customer.
    """
    assert workflow_service.EMERGENCY_HOLDING_STATES == (
        UserStates.AWAITING_PRO_APPROVAL,
        UserStates.AWAITING_ADDRESS,
        UserStates.AWAITING_LOYALTY_CONFIRMATION,
        UserStates.AWAITING_NEW_OR_EXISTING,
    )
    for excluded in (
        UserStates.AWAITING_RESCHEDULE_TIME,
        UserStates.PAUSED_FOR_HUMAN,
        UserStates.AWAITING_CANCEL_CONFIRMATION,
    ):
        assert excluded not in workflow_service.EMERGENCY_HOLDING_STATES, excluded


@pytest.mark.asyncio
async def test_run_guard_chain_stops_at_first_handled(monkeypatch):
    """A guard returning HANDLED short-circuits — later guards never run."""
    calls = []

    async def first(ctx, deps):
        calls.append("first")
        return HANDLED

    async def second(ctx, deps):
        # Must never run — asserted below via `calls`. Returns None (a legal
        # fall-through) rather than HANDLED so this stub also stays valid
        # under run_guard_chain's non-HANDLED/None contract check.
        calls.append("second")
        return None

    monkeypatch.setattr(
        dispatch_guards, "GUARD_CHAIN", (("first", first), ("second", second))
    )

    result = await run_guard_chain(make_ctx(), None)

    assert result is HANDLED
    assert calls == ["first"]


@pytest.mark.asyncio
async def test_run_guard_chain_raises_on_invalid_guard_return(monkeypatch):
    """A guard that returns anything other than HANDLED/None is a bug in the
    guard, not a valid fall-through — it must fail loudly (RuntimeError naming
    the offender) instead of silently letting a later guard double-handle the
    message. Guards against a migrated (A2/A3) guard ending in a bare
    `return True` being read as "fall through".
    """

    async def bad_guard(ctx, deps):
        return True

    monkeypatch.setattr(dispatch_guards, "GUARD_CHAIN", (("bad_guard", bad_guard),))

    with pytest.raises(RuntimeError, match="bad_guard"):
        await run_guard_chain(make_ctx(), None)


# --------------------------------------------------------------------------
# guard_admin_wizard
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_wizard_fires_on_menu_keyword(deps, monkeypatch):
    mock_handle = AsyncMock()
    monkeypatch.setattr(admin_flow_module, "handle_admin_message", mock_handle)
    ctx = make_ctx(
        chat_id=ADMIN_CHAT_ID,
        user_text="ניהול",
        normalized_text="ניהול",
        current_state=UserStates.IDLE,
    )

    result = await guard_admin_wizard(ctx, deps)

    assert result is HANDLED
    mock_handle.assert_awaited_once()


@pytest.mark.asyncio
async def test_admin_wizard_fires_while_inside_admin_state(deps, monkeypatch):
    mock_handle = AsyncMock()
    monkeypatch.setattr(admin_flow_module, "handle_admin_message", mock_handle)
    ctx = make_ctx(
        chat_id=ADMIN_CHAT_ID,
        user_text="1",
        normalized_text="1",
        current_state=UserStates.ADMIN_SELECTING_LEAD,
    )

    result = await guard_admin_wizard(ctx, deps)

    assert result is HANDLED
    mock_handle.assert_awaited_once()


@pytest.mark.asyncio
async def test_admin_wizard_ignores_non_admin_chat(deps, monkeypatch):
    mock_handle = AsyncMock()
    monkeypatch.setattr(admin_flow_module, "handle_admin_message", mock_handle)
    ctx = make_ctx(
        chat_id=CUSTOMER_CHAT_ID,
        user_text="ניהול",
        normalized_text="ניהול",
        current_state=UserStates.IDLE,
    )

    result = await guard_admin_wizard(ctx, deps)

    assert result is None
    mock_handle.assert_not_awaited()


# --------------------------------------------------------------------------
# guard_global_reset
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("keyword", Messages.Keywords.RESET_COMMANDS)
async def test_global_reset_clears_state_and_context_silently(deps, keyword):
    ctx = make_ctx(
        normalized_text=keyword,
        current_state=UserStates.AWAITING_ADDRESS,
    )

    result = await guard_global_reset(ctx, deps)

    assert result is HANDLED
    deps.state_manager.clear_state.assert_awaited_once_with(ctx.chat_id)
    deps.context_manager.clear_context.assert_awaited_once_with(ctx.chat_id)
    # Deliberately silent (2026-08-27 operator decision) — no confirmation.
    deps.whatsapp.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_global_reset_skipped_for_pro_mode(deps):
    ctx = make_ctx(normalized_text="reset", current_state=UserStates.PRO_MODE)

    result = await guard_global_reset(ctx, deps)

    assert result is None
    deps.state_manager.clear_state.assert_not_awaited()


# --------------------------------------------------------------------------
# guard_help_menu
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "keyword", Messages.Keywords.HELP_COMMANDS + Messages.Keywords.MENU_COMMANDS
)
async def test_help_menu_sends_help_info(deps, keyword):
    ctx = make_ctx(normalized_text=keyword, current_state=UserStates.IDLE)

    result = await guard_help_menu(ctx, deps)

    assert result is HANDLED
    deps.whatsapp.send_message.assert_awaited_once_with(
        ctx.chat_id, Messages.Customer.HELP_INFO
    )


@pytest.mark.asyncio
async def test_help_menu_skipped_for_pro_mode(deps):
    ctx = make_ctx(normalized_text="עזרה", current_state=UserStates.PRO_MODE)

    result = await guard_help_menu(ctx, deps)

    assert result is None
    deps.whatsapp.send_message.assert_not_awaited()


# --------------------------------------------------------------------------
# guard_inbound_rate_limit — ctx.is_exempt must resolve on every run
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_exempts_pro_mode_and_skips_the_check(deps):
    ctx = make_ctx(current_state=UserStates.PRO_MODE)

    result = await guard_inbound_rate_limit(ctx, deps)

    assert result is None
    assert ctx.is_exempt is True
    deps.security.check_sliding_window.assert_not_awaited()


@pytest.mark.asyncio
async def test_rate_limit_exempts_admin_phone(deps):
    ctx = make_ctx(chat_id=ADMIN_CHAT_ID, current_state=UserStates.IDLE)

    result = await guard_inbound_rate_limit(ctx, deps)

    assert result is None
    assert ctx.is_exempt is True
    deps.security.check_sliding_window.assert_not_awaited()


@pytest.mark.asyncio
async def test_rate_limit_exempts_professional_looked_up_in_db(deps):
    deps.users_collection.find_one = AsyncMock(return_value={"role": "professional"})
    ctx = make_ctx(current_state=UserStates.IDLE)

    result = await guard_inbound_rate_limit(ctx, deps)

    assert result is None
    assert ctx.is_exempt is True
    deps.security.check_sliding_window.assert_not_awaited()


@pytest.mark.asyncio
async def test_rate_limit_allows_customer_within_window(deps):
    deps.security.check_sliding_window = AsyncMock(return_value=True)
    ctx = make_ctx(current_state=UserStates.IDLE)

    result = await guard_inbound_rate_limit(ctx, deps)

    assert result is None
    assert ctx.is_exempt is False
    deps.security.check_sliding_window.assert_awaited_once_with(
        ctx.chat_id,
        WorkerConstants.INBOUND_RATE_LIMIT_MAX,
        WorkerConstants.INBOUND_RATE_LIMIT_WINDOW_SECONDS,
    )
    deps.whatsapp.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_rate_limit_blocks_customer_over_window(deps):
    deps.security.check_sliding_window = AsyncMock(return_value=False)
    deps.security.record_trip = AsyncMock(return_value=1)
    ctx = make_ctx(current_state=UserStates.IDLE)

    result = await guard_inbound_rate_limit(ctx, deps)

    assert result is HANDLED
    assert ctx.is_exempt is False
    deps.security.record_trip.assert_awaited_once()
    deps.whatsapp.send_message.assert_awaited_once_with(
        ctx.chat_id, Messages.Errors.RATE_LIMITED
    )


# --------------------------------------------------------------------------
# guard_zero_touch_intent
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zero_touch_skipped_outside_awaiting_confirmation(deps):
    ctx = make_ctx(normalized_text="1", current_state=UserStates.IDLE)

    result = await guard_zero_touch_intent(ctx, deps)

    assert result is None
    deps.state_manager.set_state.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ["1", "כן"])
async def test_zero_touch_confirm_switches_to_customer_mode(deps, reply):
    deps.state_manager.get_metadata = AsyncMock(
        return_value={"intent_reprompted": True}
    )
    ctx = make_ctx(
        normalized_text=reply, current_state=UserStates.AWAITING_INTENT_CONFIRMATION
    )

    result = await guard_zero_touch_intent(ctx, deps)

    assert result is HANDLED
    deps.state_manager.set_state.assert_awaited_once_with(
        ctx.chat_id, UserStates.CUSTOMER_MODE
    )
    deps.context_manager.clear_context.assert_awaited_once_with(ctx.chat_id)
    # The re-prompt flag must not survive into the customer session.
    saved_meta = deps.state_manager.set_metadata.await_args.args[1]
    assert "intent_reprompted" not in saved_meta
    deps.whatsapp.send_message.assert_awaited_once_with(
        ctx.chat_id, Messages.Pro.SWITCHED_TO_CUSTOMER
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ["2", "לא"])
async def test_zero_touch_decline_clears_state(deps, reply):
    ctx = make_ctx(
        normalized_text=reply, current_state=UserStates.AWAITING_INTENT_CONFIRMATION
    )

    result = await guard_zero_touch_intent(ctx, deps)

    assert result is HANDLED
    deps.state_manager.clear_state.assert_awaited_once_with(ctx.chat_id)
    deps.whatsapp.send_message.assert_awaited_once_with(
        ctx.chat_id, Messages.Pro.SWITCH_CANCELLED
    )


@pytest.mark.asyncio
async def test_zero_touch_first_unmatched_reply_reprompts_once(deps):
    deps.state_manager.get_metadata = AsyncMock(return_value={})
    ctx = make_ctx(
        normalized_text="מה זה",
        current_state=UserStates.AWAITING_INTENT_CONFIRMATION,
    )

    result = await guard_zero_touch_intent(ctx, deps)

    assert result is HANDLED
    saved_meta = deps.state_manager.set_metadata.await_args.args[1]
    assert saved_meta["intent_reprompted"] is True
    deps.state_manager.set_state.assert_awaited_once_with(
        ctx.chat_id,
        UserStates.AWAITING_INTENT_CONFIRMATION,
        ttl=300,
    )
    deps.whatsapp.send_message.assert_awaited_once_with(
        ctx.chat_id, Messages.Pro.INTENT_REPROMPT
    )


@pytest.mark.asyncio
async def test_zero_touch_second_unmatched_reply_falls_through(deps):
    deps.state_manager.get_metadata = AsyncMock(
        return_value={"intent_reprompted": True}
    )
    deps.state_manager.get_state = AsyncMock(return_value=UserStates.IDLE)
    ctx = make_ctx(
        normalized_text="מה זה",
        current_state=UserStates.AWAITING_INTENT_CONFIRMATION,
    )

    result = await guard_zero_touch_intent(ctx, deps)

    assert result is None
    deps.state_manager.clear_state.assert_awaited_once_with(ctx.chat_id)
    # ctx.current_state is refreshed from Redis so the rest of the pipeline
    # sees the post-clear state, not the stale AWAITING_INTENT_CONFIRMATION.
    deps.state_manager.get_state.assert_awaited_once_with(ctx.chat_id)
    assert ctx.current_state == UserStates.IDLE
    deps.whatsapp.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_zero_touch_sos_reply_falls_through_without_reprompting(deps):
    """A cry for a human must not be swallowed by the re-prompt — it has to
    reach the SOS handler further down the dispatch, even on the first miss.
    """
    deps.state_manager.get_metadata = AsyncMock(return_value={})
    deps.state_manager.get_state = AsyncMock(return_value=UserStates.IDLE)
    ctx = make_ctx(
        normalized_text=Messages.Keywords.SOS_COMMANDS[0],
        current_state=UserStates.AWAITING_INTENT_CONFIRMATION,
    )

    result = await guard_zero_touch_intent(ctx, deps)

    assert result is None
    deps.state_manager.clear_state.assert_awaited_once_with(ctx.chat_id)
    deps.state_manager.set_state.assert_not_awaited()
    deps.whatsapp.send_message.assert_not_awaited()


# --------------------------------------------------------------------------
# guard_emergency_hoist (PRO-180 slice of PRO-121)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emergency_hoist_released_logs_inbound_before_ack_and_falls_through(
    deps, monkeypatch
):
    """PRO-121: the inbound turn must be logged ahead of the deferred ack, the
    state re-read for the pipeline below, and the message still falls through
    to normal routing (the holding state was cleared, not answered here).
    """
    order = []

    async def fake_escalate(chat_id, current_state, user_text):
        return "released", "ack!"

    async def fake_log_message(chat_id, role, text):
        order.append(("log", role, text))

    async def fake_send_message(chat_id, text):
        order.append(("send", text))

    monkeypatch.setattr(workflow_service, "_escalate_emergency", fake_escalate)
    monkeypatch.setattr(workflow_service.lead_manager, "log_message", fake_log_message)
    deps.whatsapp.send_message = fake_send_message
    deps.state_manager.get_state = AsyncMock(return_value=UserStates.CUSTOMER_MODE)

    ctx = make_ctx(
        user_text="יש שריפה",
        current_state=UserStates.AWAITING_ADDRESS,
        is_emergency_detected=True,
    )

    result = await guard_emergency_hoist(ctx, deps)

    assert result is None
    assert ctx.current_state == UserStates.CUSTOMER_MODE
    assert ctx.emergency_inbound_logged is True
    assert order == [
        ("log", "user", "יש שריפה"),
        ("send", "ack!"),
        ("log", "model", "ack!"),
    ]


@pytest.mark.asyncio
async def test_emergency_hoist_handled_returns_handled(deps, monkeypatch):
    async def fake_escalate(chat_id, current_state, user_text):
        return "handled", None

    monkeypatch.setattr(workflow_service, "_escalate_emergency", fake_escalate)
    ctx = make_ctx(
        current_state=UserStates.AWAITING_LOYALTY_CONFIRMATION,
        is_emergency_detected=True,
    )

    result = await guard_emergency_hoist(ctx, deps)

    assert result is HANDLED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "current_state, is_emergency_detected",
    [
        (UserStates.IDLE, True),  # not one of the holding states
        (UserStates.AWAITING_ADDRESS, False),  # no emergency detected
    ],
)
async def test_emergency_hoist_falls_through_without_escalating(
    deps, monkeypatch, current_state, is_emergency_detected
):
    mock_escalate = AsyncMock()
    monkeypatch.setattr(workflow_service, "_escalate_emergency", mock_escalate)
    ctx = make_ctx(
        current_state=current_state, is_emergency_detected=is_emergency_detected
    )

    result = await guard_emergency_hoist(ctx, deps)

    assert result is None
    mock_escalate.assert_not_awaited()


# --------------------------------------------------------------------------
# guard_loyalty_confirmation (PRO-119)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loyalty_confirmation_double_miss_releases_to_normal_routing(
    deps, monkeypatch
):
    deps.state_manager.get_metadata = AsyncMock(
        return_value={"loyalty_reprompted": True}
    )
    deps.state_manager.get_state = AsyncMock(return_value=UserStates.IDLE)
    fake_leads = AsyncMock()
    fake_leads.find_one = AsyncMock(return_value=None)
    monkeypatch.setattr(workflow_service, "leads_collection", fake_leads)
    ctx = make_ctx(
        normalized_text="מה זה",
        current_state=UserStates.AWAITING_LOYALTY_CONFIRMATION,
    )

    result = await guard_loyalty_confirmation(ctx, deps)

    assert result is None
    deps.state_manager.clear_state.assert_awaited_once_with(ctx.chat_id)
    deps.state_manager.get_state.assert_awaited_once_with(ctx.chat_id)
    assert ctx.current_state == UserStates.IDLE
    deps.whatsapp.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_loyalty_confirmation_first_miss_reprompts_with_ttl(deps, monkeypatch):
    deps.state_manager.get_metadata = AsyncMock(return_value={})
    fake_leads = AsyncMock()
    fake_leads.find_one = AsyncMock(return_value=None)
    monkeypatch.setattr(workflow_service, "leads_collection", fake_leads)
    monkeypatch.setattr(workflow_service.lead_manager, "log_message", AsyncMock())
    ctx = make_ctx(
        normalized_text="מה זה",
        current_state=UserStates.AWAITING_LOYALTY_CONFIRMATION,
    )

    result = await guard_loyalty_confirmation(ctx, deps)

    assert result is HANDLED
    deps.state_manager.set_state.assert_awaited_once_with(
        ctx.chat_id,
        UserStates.AWAITING_LOYALTY_CONFIRMATION,
        ttl=WorkerConstants.LOYALTY_CONFIRM_TTL_SECONDS,
    )


# --------------------------------------------------------------------------
# guard_cancel_confirmation (PRO-118)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_confirmation_unmatched_reply_restores_resume_state(deps):
    deps.state_manager.get_metadata = AsyncMock(
        return_value={
            "cancel_confirm_lead_id": "605c5f3d3b1f2b0011a1b2c3",
            "cancel_confirm_resume_state": "awaiting_address",
        }
    )
    ctx = make_ctx(
        normalized_text="מה?",
        current_state=UserStates.AWAITING_CANCEL_CONFIRMATION,
    )

    result = await guard_cancel_confirmation(ctx, deps)

    assert result is HANDLED
    deps.state_manager.clear_state.assert_awaited_once_with(ctx.chat_id)
    deps.state_manager.set_state.assert_awaited_once_with(
        ctx.chat_id, "awaiting_address"
    )
    deps.whatsapp.send_message.assert_awaited_once_with(
        ctx.chat_id, Messages.Customer.CANCEL_ABORTED
    )


# --------------------------------------------------------------------------
# guard_booked_cancel_reschedule (PRO-118)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_booked_cancel_reschedule_cancel_arms_confirmation_with_ttl(
    deps, monkeypatch
):
    fake_leads = AsyncMock()
    fake_leads.find_one = AsyncMock(
        return_value={"_id": "lead1", "status": "booked", "pro_id": None}
    )
    monkeypatch.setattr(workflow_service, "leads_collection", fake_leads)
    ctx = make_ctx(
        user_text="אני רוצה לבטל",
        normalized_text="אני רוצה לבטל",
        current_state=UserStates.IDLE,
    )

    result = await guard_booked_cancel_reschedule(ctx, deps)

    assert result is HANDLED
    deps.state_manager.set_state.assert_awaited_once_with(
        ctx.chat_id,
        UserStates.AWAITING_CANCEL_CONFIRMATION,
        ttl=WorkerConstants.CANCEL_CONFIRM_TTL_SECONDS,
    )
