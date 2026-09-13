"""Unit tests for the PRO-182 (PRO-139 slice B) customer conversation pipeline.

`app/services/conversation_pipeline.py` moved the ~659 line body of steps 1-7
out of `workflow_service._process_incoming_message_inner` verbatim. The
*behaviour* of that body — happy paths, address gates, deal finalization — is
already covered by `tests/e2e/test_e2e_flows.py`,
`tests/e2e/test_e2e_state_matrix.py` and `tests/test_workflow_orchestrator.py`,
and none of it changed. What is new is the seam the move created:

  1. the prologue resolves every collaborator through a call-time
     ``from app.services import workflow_service as wf`` rather than binding
     at import time, so `monkeypatch.setattr(workflow_service, ...)` stays the
     one patch point the rest of the suite already relies on;
  2. three fields travel from the guard chain to the pipeline on `ctx`
     (`is_exempt`, `current_state`, `emergency_inbound_logged`) rather than
     as this function's own locals;
  3. `_process_incoming_message_inner` hands the *same* mutated `ctx` object
     to the pipeline once the guard chain falls through.

These tests pin those three properties directly against
`run_customer_pipeline`; they do not re-derive the happy-path AI/matching
behaviour the e2e suite already owns.
"""

import ast
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.services.conversation_pipeline as conversation_pipeline
import app.services.workflow_service as workflow_service
from app.core.constants import LeadStatus, UserStates
from app.core.phone import to_chat_id
from app.services.ai_engine_service import AIResponse, ExtractedData
from app.services.conversation_pipeline import run_customer_pipeline
from app.services.dispatch_guards import DispatchContext, GuardDeps
from app.services.security_service import SecurityService
from app.services.workflow_service import process_incoming_message

CUSTOMER_1 = to_chat_id("972500000101")
CUSTOMER_2 = to_chat_id("972500000102")
CUSTOMER_3 = to_chat_id("972500000103")
CUSTOMER_4 = to_chat_id("972500000104")
CUSTOMER_5 = to_chat_id("972500000105")


def make_ctx(chat_id, **overrides):
    defaults = dict(
        chat_id=chat_id,
        user_text="יש לי בעיה כלשהי",
        media_url=None,
        normalized_text="יש לי בעיה כלשהי",
        is_emergency_detected=False,
        current_state=UserStates.IDLE,
        is_exempt=False,
        emergency_inbound_logged=False,
    )
    defaults.update(overrides)
    return DispatchContext(**defaults)


def make_deps(mock_db, security=None):
    state_manager = AsyncMock()
    context_manager = AsyncMock()
    # `SecurityService` is awaited by the pipeline
    # (`await SecurityService.check_and_increment_daily_ai_cap(...)`), so this
    # must be an AsyncMock, not a MagicMock — a MagicMock's return value isn't
    # awaitable, and a MagicMock has no `assert_awaited*`/`assert_not_awaited`
    # family at all (accessing one raises AttributeError, not a pass/fail).
    # `spec=SecurityService` on top of that means a typo'd assertion name (or
    # a typo'd attribute name) fails loudly instead of silently auto-creating
    # a new mock attribute.
    sec = security or AsyncMock(spec=SecurityService)
    sec.check_and_increment_daily_ai_cap.return_value = True
    deps = GuardDeps(
        whatsapp=workflow_service.whatsapp,
        state_manager=state_manager,
        context_manager=context_manager,
        users_collection=mock_db.users,
        security=sec,
        settings=SimpleNamespace(ADMIN_PHONE="972524828796"),
    )
    return deps, state_manager, context_manager, sec


def empty_ai_response():
    """An AI turn that extracts nothing and closes no deal — the minimal
    dispatcher-phase reply, so the pipeline exits without touching matching,
    loyalty, or lead creation and the test only exercises the seam."""
    return AIResponse(
        reply_to_user="תודה, אבדוק ואחזור אליך.",
        extracted_data=ExtractedData(city=None, issue=None, appointment_time=None),
        transcription=None,
        is_deal=False,
    )


def patch_pipeline_collaborators(monkeypatch, ai_response, build_pro_response=None):
    """Wire the `wf.<name>` collaborators the pipeline needs for a minimal,
    no-city/no-issue turn. Returns the `lead_manager` fake for assertions."""
    fake_ai = MagicMock()
    fake_ai.analyze_conversation = AsyncMock(return_value=ai_response)
    monkeypatch.setattr(workflow_service, "ai", fake_ai)

    lead_manager = AsyncMock()
    lead_manager.get_chat_history = AsyncMock(return_value=[])
    monkeypatch.setattr(workflow_service, "lead_manager", lead_manager)

    monkeypatch.setattr(
        workflow_service, "_handle_completion", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        workflow_service, "handle_customer_rating_text", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        workflow_service, "handle_customer_review_comment", AsyncMock(return_value=None)
    )
    if build_pro_response is not None:
        monkeypatch.setattr(workflow_service, "_build_pro_response", build_pro_response)
    return lead_manager, fake_ai


# --------------------------------------------------------------------------
# 1. The prologue's per-call resolution is the patch point.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_point_stays_live_for_late_monkeypatches(monkeypatch, mock_db):
    """Patching `workflow_service.ai` / `.leads_collection` AFTER both modules
    are already imported must still be what the pipeline uses.

    This is the property the call-time `from app.services import
    workflow_service as wf` import exists to preserve — bind it at import
    time (or snapshot it once into a dataclass) and this test starts using
    the real collaborators instead of the fakes, silently.
    """
    lead_manager, fake_ai = patch_pipeline_collaborators(
        monkeypatch, empty_ai_response()
    )

    fake_leads = AsyncMock()
    fake_leads.find_one = AsyncMock(return_value=None)
    monkeypatch.setattr(workflow_service, "leads_collection", fake_leads)

    ctx = make_ctx(CUSTOMER_1, is_exempt=True, emergency_inbound_logged=True)
    deps, _state_manager, _context_manager, _sec = make_deps(mock_db)

    await run_customer_pipeline(ctx, deps)

    fake_ai.analyze_conversation.assert_awaited_once()
    fake_leads.find_one.assert_awaited()


# --------------------------------------------------------------------------
# 2. Static guard: no module-level import of workflow_service.
# --------------------------------------------------------------------------


def test_no_module_level_import_of_workflow_service():
    """The prologue's call-time import must stay call-time — not hoisted to
    module level (which would both create the import cycle the module's
    docstring calls out and freeze the collaborators at import, defeating
    behaviour (1) above for every test in the suite, silently)."""
    source = inspect.getsource(conversation_pipeline)
    tree = ast.parse(source)

    def touches_workflow_service(node):
        if isinstance(node, ast.Import):
            return any("workflow_service" in alias.name for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            # Catches both `from app.services.workflow_service import x` (the
            # module path itself) and the prologue's actual shape, `from
            # app.services import workflow_service as wf` (the name is on the
            # imported alias, not the module path).
            if "workflow_service" in (node.module or ""):
                return True
            return any("workflow_service" in alias.name for alias in node.names)
        return False

    module_level_hits = [n for n in tree.body if touches_workflow_service(n)]
    assert module_level_hits == [], (
        "conversation_pipeline.py must not import workflow_service at module "
        "level — that would break the suite's "
        "monkeypatch.setattr(workflow_service, ...) patch points"
    )

    all_hits = [n for n in ast.walk(tree) if touches_workflow_service(n)]
    nested_hits = [n for n in all_hits if n not in tree.body]
    assert len(nested_hits) == 1, (
        "expected exactly one call-time `workflow_service` import (the "
        f"prologue's), found {len(nested_hits)}"
    )


# --------------------------------------------------------------------------
# 3. ctx values are read post-chain, not from pre-chain locals.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("emergency_inbound_logged", [True, False])
async def test_step1_logs_user_turn_only_when_not_already_logged(
    monkeypatch, mock_db, emergency_inbound_logged
):
    """PRO-116 Q5: when the emergency hoist already logged this turn
    (`ctx.emergency_inbound_logged`), step 1 must not log it a second time —
    a duplicated user turn in the AI's context window."""
    lead_manager, _fake_ai = patch_pipeline_collaborators(
        monkeypatch, empty_ai_response()
    )
    ctx = make_ctx(
        CUSTOMER_2, is_exempt=True, emergency_inbound_logged=emergency_inbound_logged
    )
    deps, _state_manager, _context_manager, _sec = make_deps(mock_db)

    await run_customer_pipeline(ctx, deps)

    user_turn_logs = [
        call
        for call in lead_manager.log_message.await_args_list
        if call.args[1] == "user"
    ]
    assert len(user_turn_logs) == (0 if emergency_inbound_logged else 1)


@pytest.mark.asyncio
async def test_ctx_is_exempt_and_current_state_read_post_chain(monkeypatch, mock_db):
    """`is_exempt` and `current_state` must come from the post-chain `ctx`,
    not from values resolved before the guard chain ran.

    Arranged so a regression to reading pre-chain locals shows up as a
    concrete, different side effect for each of the three fields:
      - `is_exempt=True` -> the daily AI-cap check is skipped entirely;
      - `current_state=PRO_MODE` -> the BOOKED-job "new or existing?" prompt
        (which explicitly excludes PRO_MODE) never fires, so no state
        transition happens;
      - `emergency_inbound_logged=True` -> step 1 does not log the turn.
    """
    lead_manager, _fake_ai = patch_pipeline_collaborators(
        monkeypatch, empty_ai_response()
    )

    # A BOOKED lead for this chat that step 4's guard would otherwise catch
    # and prompt about, if current_state weren't PRO_MODE.
    pro = await mock_db.users.insert_one(
        {"phone_number": "972500000301", "role": "professional", "is_active": True}
    )
    await mock_db.leads.insert_one(
        {
            "chat_id": CUSTOMER_3,
            "status": LeadStatus.BOOKED,
            "pro_id": pro.inserted_id,
            "issue_type": "נזילה",
            "appointment_time": "מחר 10:00",
        }
    )
    monkeypatch.setattr(workflow_service, "leads_collection", mock_db.leads)

    ctx = make_ctx(
        CUSTOMER_3,
        current_state=UserStates.PRO_MODE,
        is_exempt=True,
        emergency_inbound_logged=True,
    )
    deps, state_manager, _context_manager, sec = make_deps(mock_db)

    await run_customer_pipeline(ctx, deps)

    sec.check_and_increment_daily_ai_cap.assert_not_awaited()
    state_manager.set_state.assert_not_called()
    user_turn_logs = [
        call
        for call in lead_manager.log_message.await_args_list
        if call.args[1] == "user"
    ]
    assert user_turn_logs == []


# --------------------------------------------------------------------------
# 4. `is_exempt` gates the daily AI cap at both in-pipeline call sites.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("is_exempt", [True, False])
async def test_daily_ai_cap_gated_by_is_exempt_in_dispatcher_phase(
    monkeypatch, mock_db, is_exempt
):
    """No assigned pro yet -> the dispatcher-phase call site."""
    monkeypatch.setattr(workflow_service, "leads_collection", mock_db.leads)
    patch_pipeline_collaborators(monkeypatch, empty_ai_response())

    ctx = make_ctx(CUSTOMER_4, is_exempt=is_exempt, emergency_inbound_logged=True)
    deps, _state_manager, _context_manager, sec = make_deps(mock_db)

    await run_customer_pipeline(ctx, deps)

    assert sec.check_and_increment_daily_ai_cap.await_count == (0 if is_exempt else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("is_exempt", [True, False])
async def test_daily_ai_cap_gated_by_is_exempt_on_assigned_pro_fast_path(
    monkeypatch, mock_db, is_exempt
):
    """A pro is already assigned to the active lead -> the fast-path call site
    (`_build_pro_response` on every turn, skipping the dispatcher)."""
    pro = await mock_db.users.insert_one(
        {"phone_number": "972500000302", "role": "professional", "is_active": True}
    )
    await mock_db.leads.insert_one(
        {
            "chat_id": CUSTOMER_5,
            "status": LeadStatus.NEW,
            "pro_id": pro.inserted_id,
            "issue_type": "נזילה",
        }
    )
    monkeypatch.setattr(workflow_service, "leads_collection", mock_db.leads)

    build_pro_response = AsyncMock(
        return_value=AIResponse(
            reply_to_user="בטח, אני בדרך.",
            extracted_data=ExtractedData(city=None, issue=None, appointment_time=None),
            transcription=None,
            is_deal=False,
        )
    )
    patch_pipeline_collaborators(
        monkeypatch, empty_ai_response(), build_pro_response=build_pro_response
    )

    ctx = make_ctx(CUSTOMER_5, is_exempt=is_exempt, emergency_inbound_logged=True)
    deps, _state_manager, _context_manager, sec = make_deps(mock_db)

    await run_customer_pipeline(ctx, deps)

    # Sanity: the fast path was actually reached (otherwise the cap-count
    # assertion below would pass vacuously for the wrong reason).
    build_pro_response.assert_awaited_once()
    assert sec.check_and_increment_daily_ai_cap.await_count == (0 if is_exempt else 1)


# --------------------------------------------------------------------------
# 5. Delegation after fall-through is unconditional, on the SAME ctx object.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_receives_same_mutated_ctx_after_guard_fallthrough(monkeypatch):
    """A copy of `ctx` built for the pipeline call would silently drop
    whatever the guard chain mutated on the way past. Assert identity, not
    just that a call happened, by capturing the exact object a fake guard
    chain mutated and confirming the pipeline saw that same object."""
    captured = {}

    async def fake_guard_chain(ctx, deps):
        ctx.is_exempt = True  # a real guard's mutation (the rate limiter's)
        captured["ctx_from_guard"] = ctx
        return None  # falls through

    fake_pipeline = AsyncMock()
    monkeypatch.setattr(workflow_service, "run_guard_chain", fake_guard_chain)
    monkeypatch.setattr(workflow_service, "run_customer_pipeline", fake_pipeline)

    await process_incoming_message(to_chat_id("972500000401"), "שלום")

    fake_pipeline.assert_awaited_once()
    ctx_passed = fake_pipeline.await_args.args[0]
    assert ctx_passed is captured["ctx_from_guard"]
    assert ctx_passed.is_exempt is True
