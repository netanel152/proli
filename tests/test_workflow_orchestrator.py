"""
Tests for workflow_service.process_incoming_message routing branches.
Covers: reset, pro auto-detect, address collection, onboarding, deal finalization.
"""

import re

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from bson import ObjectId
from app.core.constants import UserStates, LeadStatus, WorkerConstants, Actor
from app.core.messages import Messages
from app.core.phone import mask_chat_id
from tests.copy_util import static_prefix
from app.services.workflow_service import (
    process_incoming_message,
    _strip_deal_marker,
    _build_pro_response,
)
from app.services.ai_engine_service import AIResponse, ExtractedData
import app.services.workflow_service
import app.core.background_tasks as background_tasks_module


@pytest.fixture
def wf_mocks(monkeypatch, mock_db):
    """Common mocks for workflow orchestrator tests."""
    mock_wa = MagicMock()
    mock_wa.send_message = AsyncMock()
    mock_wa.send_location_link = AsyncMock()
    mock_wa.send_chat_state_typing = AsyncMock()
    monkeypatch.setattr(app.services.workflow_service, "whatsapp", mock_wa)

    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=UserStates.IDLE)
    mock_state.set_state = AsyncMock()
    mock_state.clear_state = AsyncMock()
    mock_state.get_metadata = AsyncMock(return_value={})
    mock_state.set_metadata = AsyncMock()
    monkeypatch.setattr(app.services.workflow_service, "StateManager", mock_state)

    mock_ctx = MagicMock()
    mock_ctx.clear_context = AsyncMock()
    monkeypatch.setattr(app.services.workflow_service, "ContextManager", mock_ctx)

    # Default: consent OK
    monkeypatch.setattr(
        app.services.workflow_service, "has_consent", AsyncMock(return_value=True)
    )

    mock_ai = MagicMock()
    mock_ai.analyze_conversation = AsyncMock(
        return_value=AIResponse(
            reply_to_user="AI response",
            extracted_data=ExtractedData(
                city=None, issue=None, full_address=None, appointment_time=None
            ),
            transcription=None,
            is_deal=False,
        )
    )
    mock_ai.detect_service_intent = AsyncMock(return_value=False)
    monkeypatch.setattr(app.services.workflow_service, "ai", mock_ai)

    mock_lm = MagicMock()
    mock_lm.log_message = AsyncMock()
    mock_lm.get_chat_history = AsyncMock(return_value=[])
    mock_lm.create_lead_from_dict = AsyncMock(
        return_value={
            "_id": ObjectId(),
            "full_address": "Test",
            "issue_type": "Leak",
            "appointment_time": "10:00",
            "chat_id": "user@c.us",
        }
    )
    monkeypatch.setattr(app.services.workflow_service, "lead_manager", mock_lm)

    return mock_wa, mock_state, mock_ctx, mock_ai, mock_lm


# --- Reset Commands ---


@pytest.mark.asyncio
async def test_reset_command_clears_state(wf_mocks):
    mock_wa, mock_state, mock_ctx, _, _ = wf_mocks

    await process_incoming_message("972501111111@c.us", "התחלה")

    mock_state.clear_state.assert_called_with("972501111111@c.us")
    mock_ctx.clear_context.assert_called_with("972501111111@c.us")
    # The reset is deliberately silent (operator decision, 2026-08-27):
    # no confirmation message is sent.
    mock_wa.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_help_command_sends_help_info_without_reset(wf_mocks):
    """תפריט is now a HELP command — must send HELP_INFO and leave state intact."""
    mock_wa, mock_state, mock_ctx, _, _ = wf_mocks

    await process_incoming_message("972501111111@c.us", "תפריט")

    mock_wa.send_message.assert_called_once_with(
        "972501111111@c.us", Messages.Customer.HELP_INFO
    )
    mock_state.clear_state.assert_not_called()
    mock_ctx.clear_context.assert_not_called()


@pytest.mark.asyncio
async def test_politeness_interceptor_thanks_keyword(wf_mocks):
    """Customer sends 'תודה' -> receives YOU_ARE_WELCOME without touching state."""
    mock_wa, mock_state, mock_ctx, mock_ai, _ = wf_mocks
    mock_state.get_state = AsyncMock(return_value=UserStates.IDLE)

    await process_incoming_message("972501111111@c.us", "תודה")

    mock_wa.send_message.assert_called_once_with(
        "972501111111@c.us", Messages.Customer.YOU_ARE_WELCOME
    )
    # Ensure other logic was skipped
    mock_ai.analyze_conversation.assert_not_called()
    # State should NOT be cleared or changed (other than get_state)
    mock_state.clear_state.assert_not_called()
    mock_state.set_state.assert_not_called()


@pytest.mark.asyncio
async def test_reset_skipped_for_pro_mode(wf_mocks):
    """Pro in PRO_MODE sends reset keyword -> goes to pro handler, not reset."""
    mock_wa, mock_state, mock_ctx, _, _ = wf_mocks
    mock_state.get_state.return_value = UserStates.PRO_MODE

    await process_incoming_message("972524828796@c.us", "עזרה")

    # The global-reset branch (now silent) clears context; taking the pro
    # path instead must leave it untouched.
    mock_ctx.clear_context.assert_not_called()


# --- Pro Auto-Detect ---


@pytest.mark.asyncio
async def test_pro_auto_detect_active(wf_mocks, mock_db):
    """Active pro auto-detected on first message -> PRO_MODE."""
    mock_wa, mock_state, _, _, _ = wf_mocks

    await mock_db.users.insert_one(
        {
            "phone_number": "972509999999",
            "role": "professional",
            "is_active": True,
            "business_name": "Test Pro",
        }
    )

    await process_incoming_message("972509999999@c.us", "שלום")

    mock_state.set_state.assert_any_call("972509999999@c.us", UserStates.PRO_MODE)


@pytest.mark.asyncio
async def test_pending_pro_not_auto_detected(wf_mocks, mock_db):
    """Pending pro (is_active=False) not auto-detected -> customer flow."""
    mock_wa, mock_state, _, mock_ai, _ = wf_mocks

    await mock_db.users.insert_one(
        {
            "phone_number": "972503333333",
            "role": "professional",
            "is_active": False,
            "pending_approval": True,
        }
    )

    await process_incoming_message("972503333333@c.us", "שלום")

    # Should reach AI dispatcher (customer path, not pro)
    mock_ai.analyze_conversation.assert_called_once()


# --- Awaiting Address ---


@pytest.mark.asyncio
async def test_awaiting_address_saves_valid(wf_mocks, mock_db):
    """User in AWAITING_ADDRESS sends a full 5-field address -> re-extracts, saves composed address, clears state."""
    mock_wa, mock_state, _, mock_ai, _ = wf_mocks
    mock_state.get_state.return_value = UserStates.AWAITING_ADDRESS

    # The new handler re-runs the AI dispatcher on the customer's reply — mock it to
    # return all five address parts so is_address_complete passes.
    mock_ai.analyze_conversation = AsyncMock(
        return_value=AIResponse(
            reply_to_user="",
            extracted_data=ExtractedData(
                city="תל אביב",
                issue="נזילה",
                street="הרצל",
                street_number="15",
                floor="2",
                apartment="4",
                appointment_time=None,
            ),
            transcription=None,
            is_deal=False,
        )
    )

    lead_id = ObjectId()
    await mock_db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": "972501111111@c.us",
            "status": LeadStatus.NEW,
            "created_at": "2026-01-01",
        }
    )

    await process_incoming_message(
        "972501111111@c.us", "הרצל 15, תל אביב קומה 2 דירה 4"
    )

    mock_wa.send_message.assert_called_once_with(
        "972501111111@c.us", Messages.Customer.ADDRESS_SAVED
    )
    mock_state.clear_state.assert_called()

    # Verify lead updated in DB with a composed canonical full_address
    updated = await mock_db.leads.find_one({"_id": lead_id})
    assert updated["street"] == "הרצל"
    assert updated["street_number"] == "15"
    assert updated["floor"] == "2"
    assert updated["apartment"] == "4"
    assert "הרצל 15" in updated["full_address"]
    assert updated["floor"] == "2"
    assert updated["apartment"] == "4"


@pytest.mark.asyncio
async def test_awaiting_address_too_short(wf_mocks):
    """User in AWAITING_ADDRESS sends too-short text -> error."""
    mock_wa, mock_state, _, _, _ = wf_mocks
    mock_state.get_state.return_value = UserStates.AWAITING_ADDRESS

    await process_incoming_message("972501111111@c.us", "hi")

    mock_wa.send_message.assert_called_once_with(
        "972501111111@c.us", Messages.Customer.ADDRESS_INVALID
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "idx,cancel_text",
    list(enumerate(["בטל", "עזוב לא משנה", "בטלי", "cancel", "nevermind"])),
)
async def test_awaiting_address_cancel_bailout(wf_mocks, mock_db, idx, cancel_text):
    """
    Regression: a user stuck in AWAITING_ADDRESS who replies with a cancel
    keyword must NOT be re-routed into the address gate. The lead must flip to
    CANCELLED, FSM state must be cleared, Redis context must be cleared, and
    the user must receive the polite REQUEST_CANCELLED confirmation.

    Each parametrize case uses a unique chat_id so the module-scoped mock_db
    doesn't leak NEW leads between runs (the handler's sort-by-created_at would
    otherwise update the wrong document).
    """
    mock_wa, mock_state, mock_ctx, mock_ai, _ = wf_mocks
    mock_state.get_state.return_value = UserStates.AWAITING_ADDRESS

    chat_id = f"9725099{idx:05d}@c.us"
    lead_id = ObjectId()
    await mock_db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": chat_id,
            "status": LeadStatus.NEW,
            "street": "הרצל",
            "created_at": "2026-01-01",
        }
    )

    await process_incoming_message(chat_id, cancel_text)

    mock_wa.send_message.assert_called_once_with(
        chat_id, Messages.Customer.REQUEST_CANCELLED
    )
    mock_state.clear_state.assert_called_with(chat_id)
    mock_ctx.clear_context.assert_called_with(chat_id)

    # AI must not have been invoked — the bailout short-circuits before re-extraction
    mock_ai.analyze_conversation.assert_not_called()

    # Lead must now be CANCELLED with audit fields populated
    updated = await mock_db.leads.find_one({"_id": lead_id})
    assert updated["status"] == LeadStatus.CANCELLED
    assert updated.get("cancel_reason") == "user_bailout_awaiting_address"
    assert updated.get("cancelled_at") is not None


@pytest.mark.asyncio
async def test_awaiting_address_cancel_without_active_lead(wf_mocks, mock_db):
    """
    Cancel keyword arrives but there's no NEW/CONTACTED lead for this chat_id
    (already closed, or race with the janitor). Must still clear state +
    context and confirm to the user — never raise.
    """
    mock_wa, mock_state, mock_ctx, mock_ai, _ = wf_mocks
    mock_state.get_state.return_value = UserStates.AWAITING_ADDRESS

    chat_id = "972509988888@c.us"  # unique: no prior lead in mock_db

    await process_incoming_message(chat_id, "עזוב")

    mock_wa.send_message.assert_called_once_with(
        chat_id, Messages.Customer.REQUEST_CANCELLED
    )
    mock_state.clear_state.assert_called_with(chat_id)
    mock_ctx.clear_context.assert_called_with(chat_id)
    mock_ai.analyze_conversation.assert_not_called()


@pytest.mark.asyncio
async def test_awaiting_address_innocent_beteut_sentence_does_not_bail_out(
    wf_mocks, mock_db
):
    """PRO-118 regression: 'עשיתי טעות, הרחוב הוא הרצל 5' contains 'טעות' only
    as a substring of 'בטעות' — must NOT trip the cancel bailout. The lead
    stays open and normal address re-extraction (AI) runs instead."""
    mock_wa, mock_state, mock_ctx, mock_ai, _ = wf_mocks
    mock_state.get_state.return_value = UserStates.AWAITING_ADDRESS

    chat_id = "972509977777@c.us"
    lead_id = ObjectId()
    await mock_db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": chat_id,
            "status": LeadStatus.NEW,
            "street": "הרצל",
            "created_at": "2026-01-01",
        }
    )

    await process_incoming_message(chat_id, "עשיתי טעות, הרחוב הוא הרצל 5")

    # Bailout never fired: no REQUEST_CANCELLED, no state/context clear for
    # that reason, and the lead was never cancelled.
    sent_texts = [c.args[1] for c in mock_wa.send_message.call_args_list]
    assert Messages.Customer.REQUEST_CANCELLED not in sent_texts
    mock_ai.analyze_conversation.assert_called_once()

    updated = await mock_db.leads.find_one({"_id": lead_id})
    assert updated["status"] == LeadStatus.NEW


# --- PRO-32: customer cancel of a BOOKED lead must release the reserved slot ---


@pytest.mark.asyncio
async def test_customer_cancel_booked_lead_prompts_for_confirmation(
    wf_mocks, mock_db, monkeypatch
):
    """
    PRO-118: a cancel keyword on a confirmed BOOKED lead no longer cancels on
    the first hit — it must ask for explicit confirmation. The lead stays
    BOOKED, the slot stays taken, the pro is NOT notified yet, and the
    customer lands in AWAITING_CANCEL_CONFIRMATION with CANCEL_CONFIRM_PROMPT
    and the lead id stashed in state metadata.

    workflow_service imports slots_collection at module level but conftest
    only patches its users/leads/reviews (see test_reschedule_flow.py docstring
    for the equivalent gap in customer_flow), so slots_collection is
    monkeypatched here too.
    """
    mock_wa, mock_state, mock_ctx, mock_ai, _ = wf_mocks
    monkeypatch.setattr(
        app.services.workflow_service, "slots_collection", mock_db.slots
    )
    mock_state.get_state.return_value = UserStates.IDLE

    chat_id = "972509977001@c.us"
    pro_id = ObjectId()
    await mock_db.users.insert_one(
        {"_id": pro_id, "phone_number": "972505551234", "business_name": "יוסי"}
    )

    slot_id = ObjectId()
    await mock_db.slots.insert_one({"_id": slot_id, "is_taken": True})

    lead_id = ObjectId()
    await mock_db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": chat_id,
            "status": LeadStatus.BOOKED,
            "pro_id": pro_id,
            "booked_slot_id": slot_id,
            "customer_name": "דנה",
            "full_address": "הרצל 1, תל אביב",
            "created_at": "2026-01-01",
        }
    )

    await process_incoming_message(chat_id, "בטל את העבודה")

    updated_lead = await mock_db.leads.find_one({"_id": lead_id})
    updated_slot = await mock_db.slots.find_one({"_id": slot_id})
    # Nothing destructive happened yet
    assert updated_lead["status"] == LeadStatus.BOOKED
    assert updated_slot["is_taken"] is True

    mock_state.set_state.assert_called_once_with(
        chat_id,
        UserStates.AWAITING_CANCEL_CONFIRMATION,
        ttl=WorkerConstants.CANCEL_CONFIRM_TTL_SECONDS,
    )
    # Metadata carries the lead id and the resume state (whatever flow the
    # customer was in — IDLE here) so an aborted cancel can restore it.
    set_meta_call = mock_state.set_metadata.call_args_list
    assert len(set_meta_call) == 1
    meta_chat_id, meta_payload = set_meta_call[0].args
    assert meta_chat_id == chat_id
    assert meta_payload["cancel_confirm_lead_id"] == str(lead_id)
    assert "cancel_confirm_resume_state" in meta_payload
    mock_wa.send_message.assert_called_once_with(
        chat_id, Messages.Customer.CANCEL_CONFIRM_PROMPT
    )
    # Pro never notified — no cancellation has actually happened
    pro_calls = [
        c
        for c in mock_wa.send_message.call_args_list
        if c.args[0] == "972505551234@c.us"
    ]
    assert pro_calls == []


@pytest.mark.asyncio
async def test_customer_cancel_confirmation_yes_executes_cancel_and_releases_slot(
    wf_mocks, mock_db, monkeypatch
):
    """
    Regression PRO-32, now on the second step of the PRO-118 flow: customer
    replies '1' to CANCEL_CONFIRM_PROMPT. The lead must flip to CANCELLED AND
    the slot it held must be freed (is_taken -> False) so the pro regains
    that hour, state+context cleared, and the pro notified.
    """
    mock_wa, mock_state, mock_ctx, mock_ai, _ = wf_mocks
    monkeypatch.setattr(
        app.services.workflow_service, "slots_collection", mock_db.slots
    )
    mock_state.get_state.return_value = UserStates.AWAITING_CANCEL_CONFIRMATION

    chat_id = "972509977001@c.us"
    pro_id = ObjectId()
    await mock_db.users.insert_one(
        {"_id": pro_id, "phone_number": "972505551234", "business_name": "יוסי"}
    )

    slot_id = ObjectId()
    await mock_db.slots.insert_one({"_id": slot_id, "is_taken": True})

    lead_id = ObjectId()
    await mock_db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": chat_id,
            "status": LeadStatus.BOOKED,
            "pro_id": pro_id,
            "booked_slot_id": slot_id,
            "customer_name": "דנה",
            "full_address": "הרצל 1, תל אביב",
            "created_at": "2026-01-01",
        }
    )
    mock_state.get_metadata.return_value = {"cancel_confirm_lead_id": str(lead_id)}

    await process_incoming_message(chat_id, "1")

    updated_lead = await mock_db.leads.find_one({"_id": lead_id})
    updated_slot = await mock_db.slots.find_one({"_id": slot_id})
    assert updated_lead["status"] == LeadStatus.CANCELLED
    assert updated_slot["is_taken"] is False
    history = [entry.get("by") for entry in updated_lead.get("status_history", [])]
    assert Actor.CUSTOMER in history

    mock_wa.send_message.assert_any_call(
        chat_id, Messages.Customer.CANCELLED_ACTIVE_LEAD
    )
    mock_state.clear_state.assert_called_with(chat_id)
    mock_ctx.clear_context.assert_called_with(chat_id)

    # Pro must be notified of the cancellation
    mock_wa.send_message.assert_any_call(
        "972505551234@c.us",
        Messages.Pro.CUSTOMER_CANCELLED.format(
            customer_name="דנה", address="הרצל 1, תל אביב"
        ),
    )


@pytest.mark.asyncio
async def test_customer_cancel_confirmation_no_keeps_job_booked(
    wf_mocks, mock_db, monkeypatch
):
    """
    Any reply other than '1' to CANCEL_CONFIRM_PROMPT aborts the destructive
    action (safe default): the job stays BOOKED, the slot stays taken, and the
    customer gets CANCEL_ABORTED. The transient state is still cleared.
    """
    mock_wa, mock_state, mock_ctx, mock_ai, _ = wf_mocks
    monkeypatch.setattr(
        app.services.workflow_service, "slots_collection", mock_db.slots
    )
    mock_state.get_state.return_value = UserStates.AWAITING_CANCEL_CONFIRMATION

    chat_id = "972509977003@c.us"
    pro_id = ObjectId()
    await mock_db.users.insert_one(
        {"_id": pro_id, "phone_number": "972505551235", "business_name": "יוסי"}
    )
    slot_id = ObjectId()
    await mock_db.slots.insert_one({"_id": slot_id, "is_taken": True})
    lead_id = ObjectId()
    await mock_db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": chat_id,
            "status": LeadStatus.BOOKED,
            "pro_id": pro_id,
            "booked_slot_id": slot_id,
            "customer_name": "דנה",
            "full_address": "הרצל 1, תל אביב",
            "created_at": "2026-01-01",
        }
    )
    mock_state.get_metadata.return_value = {"cancel_confirm_lead_id": str(lead_id)}

    await process_incoming_message(chat_id, "2")

    updated_lead = await mock_db.leads.find_one({"_id": lead_id})
    updated_slot = await mock_db.slots.find_one({"_id": slot_id})
    assert updated_lead["status"] == LeadStatus.BOOKED
    assert updated_slot["is_taken"] is True

    mock_wa.send_message.assert_called_once_with(
        chat_id, Messages.Customer.CANCEL_ABORTED
    )
    mock_state.clear_state.assert_called_with(chat_id)


@pytest.mark.asyncio
async def test_customer_cancel_confirmation_lead_already_resolved(
    wf_mocks, mock_db, monkeypatch
):
    """
    '1' arrives but the lead was already resolved (COMPLETED/CANCELLED)
    elsewhere while the confirmation prompt was open — CANCEL_NO_ACTIVE, and
    no slot write is attempted.
    """
    mock_wa, mock_state, mock_ctx, mock_ai, _ = wf_mocks
    mock_slots = MagicMock()
    mock_slots.update_one = AsyncMock()
    monkeypatch.setattr(app.services.workflow_service, "slots_collection", mock_slots)
    mock_state.get_state.return_value = UserStates.AWAITING_CANCEL_CONFIRMATION

    chat_id = "972509977004@c.us"
    lead_id = ObjectId()
    await mock_db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": chat_id,
            "status": LeadStatus.COMPLETED,  # resolved elsewhere meanwhile
            "customer_name": "דנה",
            "full_address": "הרצל 1, תל אביב",
            "created_at": "2026-01-01",
        }
    )
    mock_state.get_metadata.return_value = {"cancel_confirm_lead_id": str(lead_id)}

    await process_incoming_message(chat_id, "1")

    mock_wa.send_message.assert_called_once_with(
        chat_id, Messages.Customer.CANCEL_NO_ACTIVE
    )
    mock_slots.update_one.assert_not_called()
    mock_state.clear_state.assert_called_with(chat_id)


@pytest.mark.asyncio
async def test_customer_cancel_confirmation_without_slot_id_is_noop_safe(
    wf_mocks, mock_db, monkeypatch
):
    """
    Re-added PRO-32 guard, now exercised through the confirm step of the
    PRO-118 two-step flow: a legacy/emergency BOOKED lead with no
    booked_slot_id must never raise when confirmed-cancelled. The
    slot-release branch must be skipped entirely (guarded), and the lead
    must still flip to CANCELLED normally.
    """
    mock_wa, mock_state, mock_ctx, mock_ai, _ = wf_mocks
    mock_slots = MagicMock()
    mock_slots.update_one = AsyncMock()
    monkeypatch.setattr(app.services.workflow_service, "slots_collection", mock_slots)
    mock_state.get_state.return_value = UserStates.AWAITING_CANCEL_CONFIRMATION

    chat_id = "972509977002@c.us"
    pro_id = ObjectId()
    await mock_db.users.insert_one(
        {"_id": pro_id, "phone_number": "972505559999", "business_name": "דני"}
    )

    lead_id = ObjectId()
    await mock_db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": chat_id,
            "status": LeadStatus.BOOKED,
            "pro_id": pro_id,
            # no booked_slot_id — legacy/emergency lead
            "customer_name": "רון",
            "full_address": "אלנבי 5, תל אביב",
            "created_at": "2026-01-01",
        }
    )
    mock_state.get_metadata.return_value = {"cancel_confirm_lead_id": str(lead_id)}

    await process_incoming_message(chat_id, "1")

    updated_lead = await mock_db.leads.find_one({"_id": lead_id})
    assert updated_lead["status"] == LeadStatus.CANCELLED

    # Guarded: no slot update was attempted since there's no booked_slot_id
    mock_slots.update_one.assert_not_called()

    mock_wa.send_message.assert_any_call(
        chat_id, Messages.Customer.CANCELLED_ACTIVE_LEAD
    )
    mock_state.clear_state.assert_called_with(chat_id)


@pytest.mark.asyncio
async def test_customer_cancel_confirmation_status_race_loses_no_slot_freed(
    wf_mocks, mock_db, monkeypatch
):
    """
    Race guard inside _execute_customer_cancel: the lead is still BOOKED when
    fetched, but the guarded set_lead_status(expected_status=BOOKED) loses the
    race (e.g. the pro finished the job a moment earlier) and returns None.
    The customer must get CANCEL_NO_ACTIVE and the slot must NOT be freed —
    the write never happened, so nothing should be undone.
    """
    mock_wa, mock_state, mock_ctx, mock_ai, _ = wf_mocks
    monkeypatch.setattr(
        app.services.workflow_service, "slots_collection", mock_db.slots
    )
    # Simulate the guarded status write losing the race.
    mock_set_status = AsyncMock(return_value=None)
    monkeypatch.setattr(
        app.services.workflow_service, "set_lead_status", mock_set_status
    )
    mock_state.get_state.return_value = UserStates.AWAITING_CANCEL_CONFIRMATION

    chat_id = "972509977005@c.us"
    pro_id = ObjectId()
    await mock_db.users.insert_one(
        {"_id": pro_id, "phone_number": "972505551236", "business_name": "יוסי"}
    )
    slot_id = ObjectId()
    await mock_db.slots.insert_one({"_id": slot_id, "is_taken": True})
    lead_id = ObjectId()
    await mock_db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": chat_id,
            "status": LeadStatus.BOOKED,  # still BOOKED at find_one time
            "pro_id": pro_id,
            "booked_slot_id": slot_id,
            "customer_name": "דנה",
            "full_address": "הרצל 1, תל אביב",
            "created_at": "2026-01-01",
        }
    )
    mock_state.get_metadata.return_value = {"cancel_confirm_lead_id": str(lead_id)}

    await process_incoming_message(chat_id, "1")

    mock_set_status.assert_awaited_once()
    updated_slot = await mock_db.slots.find_one({"_id": slot_id})
    assert updated_slot["is_taken"] is True  # untouched — the write never happened

    mock_wa.send_message.assert_called_once_with(
        chat_id, Messages.Customer.CANCEL_NO_ACTIVE
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply_text,should_confirm",
    [
        ("כן", True),
        ("כן, בטל", True),
        ("2", False),
        ("לא תודה", False),
    ],
)
async def test_customer_cancel_confirmation_reply_variants(
    wf_mocks, mock_db, monkeypatch, reply_text, should_confirm
):
    """
    Confirmation accepts '1', a bare 'כן', or any restated cancel keyword
    ("כן, בטל") — not just the literal '1'. Anything else (a plain '2' or
    unrelated text) aborts, keeping the job BOOKED.
    """
    mock_wa, mock_state, mock_ctx, mock_ai, _ = wf_mocks
    monkeypatch.setattr(
        app.services.workflow_service, "slots_collection", mock_db.slots
    )
    mock_state.get_state.return_value = UserStates.AWAITING_CANCEL_CONFIRMATION

    chat_id = f"97250997{abs(hash(reply_text)) % 10000:04d}@c.us"
    pro_id = ObjectId()
    await mock_db.users.insert_one(
        {"_id": pro_id, "phone_number": "972505551237", "business_name": "יוסי"}
    )
    slot_id = ObjectId()
    await mock_db.slots.insert_one({"_id": slot_id, "is_taken": True})
    lead_id = ObjectId()
    await mock_db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": chat_id,
            "status": LeadStatus.BOOKED,
            "pro_id": pro_id,
            "booked_slot_id": slot_id,
            "customer_name": "דנה",
            "full_address": "הרצל 1, תל אביב",
            "created_at": "2026-01-01",
        }
    )
    mock_state.get_metadata.return_value = {"cancel_confirm_lead_id": str(lead_id)}

    await process_incoming_message(chat_id, reply_text)

    updated_lead = await mock_db.leads.find_one({"_id": lead_id})
    if should_confirm:
        assert updated_lead["status"] == LeadStatus.CANCELLED
        mock_wa.send_message.assert_any_call(
            chat_id, Messages.Customer.CANCELLED_ACTIVE_LEAD
        )
    else:
        assert updated_lead["status"] == LeadStatus.BOOKED
        mock_wa.send_message.assert_called_once_with(
            chat_id, Messages.Customer.CANCEL_ABORTED
        )


@pytest.mark.asyncio
async def test_customer_cancel_confirmation_corrupt_lead_id_no_exception(
    wf_mocks, mock_db, monkeypatch
):
    """Corrupt metadata (cancel_confirm_lead_id isn't a valid ObjectId) must
    be handled gracefully — CANCEL_NO_ACTIVE, no exception raised."""
    mock_wa, mock_state, mock_ctx, mock_ai, _ = wf_mocks
    mock_slots = MagicMock()
    mock_slots.update_one = AsyncMock()
    monkeypatch.setattr(app.services.workflow_service, "slots_collection", mock_slots)
    mock_state.get_state.return_value = UserStates.AWAITING_CANCEL_CONFIRMATION
    mock_state.get_metadata.return_value = {"cancel_confirm_lead_id": "not-an-oid"}

    chat_id = "972509977006@c.us"

    await process_incoming_message(chat_id, "1")

    mock_wa.send_message.assert_called_once_with(
        chat_id, Messages.Customer.CANCEL_NO_ACTIVE
    )
    mock_slots.update_one.assert_not_called()
    mock_state.clear_state.assert_called_with(chat_id)


# --- Register / Onboarding ---


@pytest.mark.asyncio
async def test_register_command_starts_onboarding(wf_mocks, monkeypatch):
    mock_wa, mock_state, _, _, _ = wf_mocks
    mock_start = AsyncMock(return_value=True)
    monkeypatch.setattr(app.services.workflow_service, "start_onboarding", mock_start)

    await process_incoming_message("972501111111@c.us", "הרשמה")

    mock_start.assert_called_once_with("972501111111@c.us", mock_wa)


# --- AI Failure ---


@pytest.mark.asyncio
async def test_ai_failure_sends_overload(wf_mocks):
    _, mock_wa, _, mock_ai, _ = wf_mocks
    mock_wa = wf_mocks[0]
    mock_ai = wf_mocks[3]
    mock_ai.analyze_conversation.side_effect = Exception("API timeout")

    await process_incoming_message("972501111111@c.us", "יש לי בעיה")

    mock_wa.send_message.assert_any_call(
        "972501111111@c.us", Messages.Errors.AI_OVERLOAD
    )


# --- Deal Finalization ---


@pytest.mark.asyncio
async def test_deal_finalization_notifies_pro(wf_mocks, monkeypatch, mock_db):
    """When AI returns is_deal=True and pro matched -> pro gets deal notification."""
    mock_wa, mock_state, _, mock_ai, mock_lm = wf_mocks

    pro_id = ObjectId()
    pro_doc = {
        "_id": pro_id,
        "business_name": "Test Pro",
        "phone_number": "972500000000",
        "service_areas": ["Tel Aviv"],
        "is_active": True,
    }

    dispatcher_resp = AIResponse(
        reply_to_user="מצאתי לך בעל מקצוע",
        extracted_data=ExtractedData(
            city="Tel Aviv",
            issue="Leak",
            full_address="Herzl 10",
            appointment_time="10:00",
        ),
        transcription=None,
        is_deal=False,
    )
    pro_resp = AIResponse(
        reply_to_user="[DEAL: 10:00 | Herzl 10 | Leak]",
        extracted_data=ExtractedData(
            city="Tel Aviv",
            issue="Leak",
            full_address="Herzl 10",
            appointment_time="10:00",
        ),
        transcription=None,
        is_deal=True,
    )
    mock_ai.analyze_conversation.side_effect = [dispatcher_resp, pro_resp]

    monkeypatch.setattr(
        app.services.workflow_service,
        "determine_best_pro",
        AsyncMock(return_value=pro_doc),
    )

    mock_lm.create_lead_from_dict.return_value = {
        "_id": ObjectId(),
        "full_address": "Herzl 10",
        "issue_type": "Leak",
        "appointment_time": "10:00",
        "chat_id": "972501111111@c.us",
    }

    await process_incoming_message(
        "972501111111@c.us", "יש לי נזילה ברחוב הרצל 10 בשעה 10"
    )

    # Pro should have been notified
    pro_calls = [
        c
        for c in mock_wa.send_message.call_args_list
        if c.args[0] == "972500000000@c.us"
    ]
    assert len(pro_calls) >= 1


@pytest.mark.asyncio
async def test_no_pro_found_dispatcher_response_only(wf_mocks, monkeypatch):
    """When city+issue extracted but no pro found -> only dispatcher response sent."""
    mock_wa, _, _, mock_ai, _ = wf_mocks

    resp = AIResponse(
        reply_to_user="לא מצאתי בעל מקצוע, אבל אני מחפש",
        extracted_data=ExtractedData(
            city="Eilat", issue="Leak", full_address=None, appointment_time=None
        ),
        transcription=None,
        is_deal=False,
    )
    mock_ai.analyze_conversation.return_value = resp
    monkeypatch.setattr(
        app.services.workflow_service,
        "determine_best_pro",
        AsyncMock(return_value=None),
    )

    await process_incoming_message("972501111111@c.us", "נזילה באילת")

    # AI called only once (dispatcher, no pro phase)
    assert mock_ai.analyze_conversation.call_count == 1
    mock_wa.send_message.assert_any_call(
        "972501111111@c.us", "לא מצאתי בעל מקצוע, אבל אני מחפש"
    )


# --- PRO-55: AI-Quoted Price Shown to the Pro ---


@pytest.mark.asyncio
async def test_deal_with_quoted_price_shown_to_pro(wf_mocks, monkeypatch, mock_db):
    """
    Deal closes with a full address AND a quoted_price on the pro-persona turn ->
    the approval message sent to the pro contains the price line, and the lead
    persists quoted_price.
    """
    mock_wa, mock_state, _, mock_ai, mock_lm = wf_mocks
    chat_id = "972501110001@c.us"

    pro_id = ObjectId()
    pro_doc = {
        "_id": pro_id,
        "business_name": "Test Pro",
        "phone_number": "972500000001",
        "service_areas": ["Tel Aviv"],
        "is_active": True,
    }

    lead_id = ObjectId()
    await mock_db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": chat_id,
            "status": LeadStatus.CONTACTED,
            "issue_type": "נזילה",
            "full_address": "תל אביב",
            "created_at": "2026-01-01",
        }
    )

    dispatcher_resp = AIResponse(
        reply_to_user="רגע, מוצא לך בעל מקצוע",
        extracted_data=ExtractedData(
            city="תל אביב", issue="נזילה", full_address=None, appointment_time=None
        ),
        transcription=None,
        is_deal=False,
    )
    pro_resp = AIResponse(
        reply_to_user="[DEAL: 10:00 | הרצל 10, תל אביב | נזילה]",
        extracted_data=ExtractedData(
            city="תל אביב",
            issue="נזילה",
            street="הרצל",
            street_number="10",
            floor="2",
            apartment="4",
            appointment_time="10:00",
            quoted_price="400-600",
        ),
        transcription=None,
        is_deal=True,
    )
    mock_ai.analyze_conversation.side_effect = [dispatcher_resp, pro_resp]

    monkeypatch.setattr(
        app.services.workflow_service,
        "determine_best_pro",
        AsyncMock(return_value=pro_doc),
    )

    await process_incoming_message(chat_id, "בסביבות כמה זה יעלה? אפשר לקבוע ל-10?")

    approval_calls = [
        c
        for c in mock_wa.send_message.call_args_list
        if c.args[0] == "972500000001@c.us"
        and Messages.Pro.APPROVAL_PRICE_LINE.split("{")[0] in c.args[1]
    ]
    assert len(approval_calls) == 1
    approval_msg = approval_calls[0].args[1]
    assert "400-600" in approval_msg
    assert static_prefix(Messages.Pro.APPROVAL_PRICE_LINE) in approval_msg

    updated_lead = await mock_db.leads.find_one({"_id": lead_id})
    assert updated_lead["quoted_price"] == "400-600"


@pytest.mark.asyncio
async def test_deal_without_quoted_price_omits_price_line(
    wf_mocks, monkeypatch, mock_db
):
    """
    Deal closes with a full address but NO quoted_price anywhere (this turn or
    sticky) -> the approval message sent to the pro has no price line, no stray
    '₪', and no broken empty line.
    """
    mock_wa, mock_state, _, mock_ai, mock_lm = wf_mocks
    chat_id = "972501110002@c.us"

    pro_id = ObjectId()
    pro_doc = {
        "_id": pro_id,
        "business_name": "Test Pro",
        "phone_number": "972500000002",
        "service_areas": ["Tel Aviv"],
        "is_active": True,
    }

    lead_id = ObjectId()
    await mock_db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": chat_id,
            "status": LeadStatus.CONTACTED,
            "issue_type": "נזילה",
            "full_address": "תל אביב",
            "created_at": "2026-01-01",
        }
    )

    dispatcher_resp = AIResponse(
        reply_to_user="רגע, מוצא לך בעל מקצוע",
        extracted_data=ExtractedData(
            city="תל אביב", issue="נזילה", full_address=None, appointment_time=None
        ),
        transcription=None,
        is_deal=False,
    )
    pro_resp = AIResponse(
        reply_to_user="[DEAL: 10:00 | הרצל 10, תל אביב | נזילה]",
        extracted_data=ExtractedData(
            city="תל אביב",
            issue="נזילה",
            street="הרצל",
            street_number="10",
            floor="2",
            apartment="4",
            appointment_time="10:00",
            quoted_price=None,
        ),
        transcription=None,
        is_deal=True,
    )
    mock_ai.analyze_conversation.side_effect = [dispatcher_resp, pro_resp]

    monkeypatch.setattr(
        app.services.workflow_service,
        "determine_best_pro",
        AsyncMock(return_value=pro_doc),
    )

    await process_incoming_message(chat_id, "אפשר לקבוע ל-10?")

    approval_calls = [
        c
        for c in mock_wa.send_message.call_args_list
        if c.args[0] == "972500000002@c.us"
        and static_prefix(Messages.Pro.APPROVAL_REQUEST) in c.args[1]
    ]
    assert len(approval_calls) == 1
    approval_msg = approval_calls[0].args[1]
    assert static_prefix(Messages.Pro.APPROVAL_PRICE_LINE) not in approval_msg
    assert "₪" not in approval_msg
    # No leftover blank-line artifact from an empty {price_line} slot
    assert "\n\n\n" not in approval_msg

    updated_lead = await mock_db.leads.find_one({"_id": lead_id})
    assert "quoted_price" not in updated_lead


@pytest.mark.asyncio
async def test_estimate_turn_persists_quoted_price_sticky_without_deal(
    wf_mocks, monkeypatch, mock_db
):
    """
    PRO-55 sticky persist: the AI gives a price estimate (quoted_price set) on a
    turn where the deal is NOT yet closed (is_deal=False, incomplete address) ->
    the lead still persists quoted_price so it survives to the later deal-close
    turn.
    """
    mock_wa, mock_state, _, mock_ai, mock_lm = wf_mocks
    chat_id = "972501110003@c.us"

    pro_id = ObjectId()
    pro_doc = {
        "_id": pro_id,
        "business_name": "Test Pro",
        "phone_number": "972500000003",
        "service_areas": ["Tel Aviv"],
        "is_active": True,
    }

    lead_id = ObjectId()
    await mock_db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": chat_id,
            "status": LeadStatus.CONTACTED,
            "issue_type": "נזילה",
            "full_address": "תל אביב",
            "created_at": "2026-01-01",
        }
    )

    dispatcher_resp = AIResponse(
        reply_to_user="רגע, מוצא לך בעל מקצוע",
        extracted_data=ExtractedData(
            city="תל אביב", issue="נזילה", full_address=None, appointment_time=None
        ),
        transcription=None,
        is_deal=False,
    )
    # STEP 3 estimate turn: pro persona quotes a price but has no address yet ->
    # not a deal.
    estimate_resp = AIResponse(
        reply_to_user="זה יעלה בסביבות 400-600 שח, איפה אתה גר?",
        extracted_data=ExtractedData(
            city="תל אביב",
            issue="נזילה",
            appointment_time=None,
            quoted_price="400-600",
        ),
        transcription=None,
        is_deal=False,
    )
    mock_ai.analyze_conversation.side_effect = [dispatcher_resp, estimate_resp]

    monkeypatch.setattr(
        app.services.workflow_service,
        "determine_best_pro",
        AsyncMock(return_value=pro_doc),
    )

    await process_incoming_message(chat_id, "בסביבות כמה זה יעלה?")

    # Not a deal -> no approval request should have gone to the pro
    approval_calls = [
        c
        for c in mock_wa.send_message.call_args_list
        if c.args[0] == "972500000003@c.us"
        and static_prefix(Messages.Pro.APPROVAL_REQUEST) in c.args[1]
    ]
    assert len(approval_calls) == 0

    updated_lead = await mock_db.leads.find_one({"_id": lead_id})
    assert updated_lead["quoted_price"] == "400-600"


# --- Soft Hold & Pause State Tests ---


@pytest.mark.asyncio
async def test_awaiting_pro_approval_blocks_ai(wf_mocks):
    """When customer is in AWAITING_PRO_APPROVAL, AI should not be called."""
    mock_wa, mock_state, _, mock_ai, _ = wf_mocks
    mock_state.get_state = AsyncMock(return_value=UserStates.AWAITING_PRO_APPROVAL)

    await process_incoming_message("972501111111@c.us", "Hello, any update?")

    # AI should NOT be called
    mock_ai.analyze_conversation.assert_not_called()
    # Customer should get the waiting message
    mock_wa.send_message.assert_any_call(
        "972501111111@c.us", Messages.Customer.STILL_WAITING
    )


@pytest.mark.asyncio
async def test_paused_for_human_bypasses_ai(wf_mocks):
    """When bot is paused, messages are logged but AI is not invoked."""
    mock_wa, mock_state, _, mock_ai, mock_lm = wf_mocks
    mock_state.get_state = AsyncMock(return_value=UserStates.PAUSED_FOR_HUMAN)

    await process_incoming_message("972501111111@c.us", "I need help")

    # AI should NOT be called
    mock_ai.analyze_conversation.assert_not_called()
    # Message should be logged silently
    mock_lm.log_message.assert_called_once_with(
        "972501111111@c.us", "user", "I need help"
    )
    # No WhatsApp message sent (silent bypass)
    mock_wa.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_sos_sets_paused_state_with_custom_ttl(wf_mocks):
    """SOS keyword should set PAUSED_FOR_HUMAN state with 2-hour TTL."""
    mock_wa, mock_state, _, _, _ = wf_mocks
    mock_state.get_state = AsyncMock(return_value=UserStates.IDLE)

    from app.core.constants import WorkerConstants

    await process_incoming_message("972501111111@c.us", "אני צריך נציג")

    # Verify PAUSED_FOR_HUMAN state with correct TTL
    mock_state.set_state.assert_called_once_with(
        "972501111111@c.us",
        UserStates.PAUSED_FOR_HUMAN,
        ttl=WorkerConstants.PAUSE_TTL_SECONDS,
    )

    # Customer gets bot paused message
    mock_wa.send_message.assert_any_call(
        "972501111111@c.us", Messages.Customer.BOT_PAUSED_BY_CUSTOMER
    )


@pytest.mark.asyncio
async def test_sos_construction_foreman_phrase_does_not_pause(wf_mocks):
    """PRO-118: 'אני צריך מנהל עבודה' (a construction foreman, a profession a
    customer plausibly mentions) must NOT trigger the SOS handoff — the
    SOS_EXCLUDE_PHRASES entry strips it before matching 'מנהל'."""
    mock_wa, mock_state, _, mock_ai, _ = wf_mocks
    mock_state.get_state = AsyncMock(return_value=UserStates.IDLE)

    await process_incoming_message("972501111112@c.us", "אני צריך מנהל עבודה")

    # Never routed into the SOS pause — not the state, not the message
    paused_calls = [
        c
        for c in mock_state.set_state.call_args_list
        if len(c.args) > 1 and c.args[1] == UserStates.PAUSED_FOR_HUMAN
    ]
    assert paused_calls == []
    sent_texts = [c.args[1] for c in mock_wa.send_message.call_args_list]
    assert Messages.Customer.BOT_PAUSED_BY_CUSTOMER not in sent_texts


@pytest.mark.asyncio
async def test_sos_bare_representative_keyword_still_pauses(wf_mocks):
    """A bare SOS keyword ('נציג') must still pause the bot — the exclude
    phrase is scoped narrowly to 'מנהל עבודה', not to every SOS word."""
    mock_wa, mock_state, _, _, _ = wf_mocks
    mock_state.get_state = AsyncMock(return_value=UserStates.IDLE)

    await process_incoming_message("972501111113@c.us", "נציג")

    mock_state.set_state.assert_called_once_with(
        "972501111113@c.us",
        UserStates.PAUSED_FOR_HUMAN,
        ttl=WorkerConstants.PAUSE_TTL_SECONDS,
    )
    mock_wa.send_message.assert_any_call(
        "972501111113@c.us", Messages.Customer.BOT_PAUSED_BY_CUSTOMER
    )


@pytest.mark.asyncio
async def test_sos_handoff_phrasing_pauses_and_alerts(wf_mocks, monkeypatch):
    """PRO-118 sibling fix: 'תעבירו אותי לנציג' (natural handoff phrasing)
    must pause the bot and fire the SOS alert — 'לנציג' is now a whole-token
    SOS_COMMANDS entry, which substring matching used to catch by accident."""
    mock_wa, mock_state, _, _, _ = wf_mocks
    mock_state.get_state = AsyncMock(return_value=UserStates.IDLE)

    mock_page = MagicMock()
    monkeypatch.setattr("app.services.notification_service.page_operator", mock_page)

    await process_incoming_message("972501111114@c.us", "תעבירו אותי לנציג")

    mock_state.set_state.assert_called_once_with(
        "972501111114@c.us",
        UserStates.PAUSED_FOR_HUMAN,
        ttl=WorkerConstants.PAUSE_TTL_SECONDS,
    )
    mock_wa.send_message.assert_any_call(
        "972501111114@c.us", Messages.Customer.BOT_PAUSED_BY_CUSTOMER
    )
    # The SOS alert (paged to the operator) actually fired.
    mock_page.assert_called_once()
    assert "SOS from customer" in mock_page.call_args.args[0]


# --- Zero-Touch Intent Confirmation Tests ---


@pytest.mark.asyncio
async def test_intent_confirmation_yes_sets_customer_mode(wf_mocks, mock_db):
    """State=AWAITING_INTENT_CONFIRMATION, reply '1' -> CUSTOMER_MODE set, context cleared."""
    mock_wa, mock_state, mock_ctx, mock_ai, _ = wf_mocks
    mock_state.get_state = AsyncMock(
        return_value=UserStates.AWAITING_INTENT_CONFIRMATION
    )

    # Insert a pro in DB so find_one won't fail in consent check
    pro_phone = "972501111111"
    await mock_db.users.insert_one({"phone_number": pro_phone, "role": "professional"})

    await process_incoming_message(f"{pro_phone}@c.us", "1")

    mock_state.set_state.assert_called_with(
        f"{pro_phone}@c.us", UserStates.CUSTOMER_MODE
    )
    mock_ctx.clear_context.assert_called_once()
    mock_wa.send_message.assert_called_with(
        f"{pro_phone}@c.us", Messages.Pro.SWITCHED_TO_CUSTOMER
    )


@pytest.mark.asyncio
async def test_intent_confirmation_no_clears_state(wf_mocks, mock_db):
    """State=AWAITING_INTENT_CONFIRMATION, reply '2' -> state cleared, SWITCH_CANCELLED sent."""
    mock_wa, mock_state, _, _, _ = wf_mocks
    mock_state.get_state = AsyncMock(
        return_value=UserStates.AWAITING_INTENT_CONFIRMATION
    )

    pro_phone = "972501111112"
    await mock_db.users.insert_one({"phone_number": pro_phone, "role": "professional"})

    await process_incoming_message(f"{pro_phone}@c.us", "2")

    mock_state.clear_state.assert_called_once_with(f"{pro_phone}@c.us")
    mock_wa.send_message.assert_called_with(
        f"{pro_phone}@c.us", Messages.Pro.SWITCH_CANCELLED
    )


@pytest.mark.asyncio
async def test_intent_confirmation_other_reprompts_once(wf_mocks, mock_db):
    """PRO-69 FM-2: first unmatched reply re-prompts and keeps the state."""
    mock_wa, mock_state, _, mock_ai, mock_lm = wf_mocks
    mock_state.get_state = AsyncMock(
        return_value=UserStates.AWAITING_INTENT_CONFIRMATION
    )
    mock_state.get_metadata = AsyncMock(return_value={})

    pro_phone = "972501111113"
    await process_incoming_message(f"{pro_phone}@c.us", "לא יודע")

    mock_state.clear_state.assert_not_called()
    mock_wa.send_message.assert_called_with(
        f"{pro_phone}@c.us", Messages.Pro.INTENT_REPROMPT
    )


@pytest.mark.asyncio
async def test_intent_confirmation_second_miss_falls_through(wf_mocks, mock_db):
    """PRO-69 FM-2: after one re-prompt, a second miss clears state and routes normally."""
    mock_wa, mock_state, _, mock_ai, mock_lm = wf_mocks
    mock_state.get_metadata = AsyncMock(return_value={"intent_reprompted": True})

    call_count = [0]

    async def get_state_side_effect(chat_id):
        call_count[0] += 1
        if call_count[0] == 1:
            return UserStates.AWAITING_INTENT_CONFIRMATION
        return UserStates.IDLE

    mock_state.get_state = get_state_side_effect

    pro_phone = "972501111113"
    # Not a pro so it falls through to dispatcher after state clear
    await process_incoming_message(f"{pro_phone}@c.us", "לא יודע")

    mock_state.clear_state.assert_called()


@pytest.mark.asyncio
async def test_safety_bypass_from_customer_mode(wf_mocks, mock_db):
    """Pro in CUSTOMER_MODE types 'אשר' with no open lead of their own
    -> snapped back to PRO_MODE, pro_flow called (PRO-69 FM-4 negative case)."""
    mock_wa, mock_state, _, mock_ai, _ = wf_mocks
    mock_state.get_state = AsyncMock(return_value=UserStates.CUSTOMER_MODE)

    pro_phone = "972501111114"
    await mock_db.users.insert_one(
        {
            "phone_number": pro_phone,
            "role": "professional",
            "is_active": True,
        }
    )

    # We'll verify by checking state.set_state was called with PRO_MODE
    await process_incoming_message(f"{pro_phone}@c.us", "אשר")

    # Safety bypass: state should have been snapped to PRO_MODE
    mock_state.set_state.assert_any_call(f"{pro_phone}@c.us", UserStates.PRO_MODE)


# --- Patch #2: PENDING_ADMIN_REVIEW short-circuit ---


@pytest.mark.asyncio
async def test_pending_admin_review_does_not_create_duplicate_lead(wf_mocks, mock_db):
    """
    Regression for the 2026-04-18 log incident: a chat with a PENDING_ADMIN_REVIEW
    lead sends a new message, workflow_service must NOT call the dispatcher or
    create a second lead. It should log the message and send a throttled ack.
    """
    from datetime import datetime, timezone

    mock_wa, mock_state, _, mock_ai, mock_lm = wf_mocks

    chat_id = "972501234567@c.us"
    await mock_db.leads.delete_many({})
    await mock_db.leads.insert_one(
        {
            "chat_id": chat_id,
            "status": LeadStatus.PENDING_ADMIN_REVIEW,
            "issue_type": "Leak",
            "full_address": "Unknown Address",
            "created_at": datetime.now(timezone.utc),
            # PRO-63: short-circuit is now bounded by recency — this lead is
            # freshly escalated, well inside PENDING_REVIEW_SHORTCIRCUIT_HOURS.
            "updated_at": datetime.now(timezone.utc),
        }
    )

    await process_incoming_message(chat_id, "שלום, אני עדיין מחכה")

    # 1. No new lead must be created
    mock_lm.create_lead_from_dict.assert_not_called()
    # 2. Dispatcher must not be invoked
    mock_ai.analyze_conversation.assert_not_called()
    # 3. Customer gets the STILL_PENDING_REVIEW ack
    mock_wa.send_message.assert_any_call(
        chat_id, Messages.Customer.STILL_PENDING_REVIEW
    )
    # 4. The message still gets logged for admin visibility
    mock_lm.log_message.assert_any_call(chat_id, "user", "שלום, אני עדיין מחכה")


@pytest.mark.asyncio
async def test_pending_admin_review_ack_throttled(wf_mocks, mock_db):
    """
    If we've already acked within the last 30 minutes, don't send another ack
    — just log the message silently. Prevents ack-spam on rapid-fire messages.
    """
    from datetime import datetime, timedelta, timezone

    mock_wa, mock_state, _, mock_ai, mock_lm = wf_mocks

    chat_id = "972501234567@c.us"
    await mock_db.leads.delete_many({})
    await mock_db.leads.insert_one(
        {
            "chat_id": chat_id,
            "status": LeadStatus.PENDING_ADMIN_REVIEW,
            "issue_type": "Leak",
            "full_address": "Tel Aviv",
            "created_at": datetime.now(timezone.utc) - timedelta(hours=2),
            "last_pending_ack_at": datetime.now(timezone.utc) - timedelta(minutes=5),
            # PRO-63: still well inside the recency window (2h old, not 24h+).
            "updated_at": datetime.now(timezone.utc) - timedelta(hours=2),
        }
    )

    await process_incoming_message(chat_id, "היי, יש עדכון?")

    # No new lead, no dispatcher, no ack resend
    mock_lm.create_lead_from_dict.assert_not_called()
    mock_ai.analyze_conversation.assert_not_called()
    for call in mock_wa.send_message.call_args_list:
        assert (
            call.args[1] != Messages.Customer.STILL_PENDING_REVIEW
        ), "Ack was re-sent within the 30-minute throttle window"
    # Message is still logged
    mock_lm.log_message.assert_any_call(chat_id, "user", "היי, יש עדכון?")


@pytest.mark.asyncio
async def test_pending_admin_review_stale_lead_does_not_shortcircuit(wf_mocks, mock_db):
    """PRO-63 — the short-circuit has no natural exit, so it is bounded by
    ``PENDING_REVIEW_SHORTCIRCUIT_HOURS``. A PENDING_ADMIN_REVIEW lead whose
    ``updated_at`` is older than the window must NOT block the dispatcher —
    the customer's message proceeds normally. The stale lead itself is never
    auto-closed; it stays PENDING_ADMIN_REVIEW for a human to find."""
    from datetime import datetime, timedelta, timezone

    mock_wa, mock_state, _, mock_ai, mock_lm = wf_mocks

    chat_id = "972501234567@c.us"
    await mock_db.leads.delete_many({})
    stale_cutoff = datetime.now(timezone.utc) - timedelta(
        hours=WorkerConstants.PENDING_REVIEW_SHORTCIRCUIT_HOURS + 1
    )
    result = await mock_db.leads.insert_one(
        {
            "chat_id": chat_id,
            "status": LeadStatus.PENDING_ADMIN_REVIEW,
            "issue_type": "Leak",
            "full_address": "Tel Aviv",
            "created_at": stale_cutoff,
            "updated_at": stale_cutoff,
        }
    )

    await process_incoming_message(chat_id, "שלום, יש לי עוד בעיה")

    # Dispatcher proceeds — the stale short-circuit no longer blocks it.
    mock_ai.analyze_conversation.assert_awaited()
    # No throttled ack — this is not the short-circuit path.
    for call in mock_wa.send_message.call_args_list:
        assert call.args[1] != Messages.Customer.STILL_PENDING_REVIEW
    # The stale lead is never auto-closed — it is left PENDING_ADMIN_REVIEW
    # for a human to act on.
    stale_lead = await mock_db.leads.find_one({"_id": result.inserted_id})
    assert stale_lead["status"] == LeadStatus.PENDING_ADMIN_REVIEW


@pytest.mark.asyncio
async def test_pending_admin_review_missing_updated_at_does_not_shortcircuit(
    wf_mocks, mock_db
):
    """A PENDING_ADMIN_REVIEW lead with no ``updated_at`` field at all fails
    toward letting the customer talk — it must not match the recency-bounded
    short-circuit query."""
    from datetime import datetime, timezone

    mock_wa, mock_state, _, mock_ai, mock_lm = wf_mocks

    chat_id = "972501234567@c.us"
    await mock_db.leads.delete_many({})
    await mock_db.leads.insert_one(
        {
            "chat_id": chat_id,
            "status": LeadStatus.PENDING_ADMIN_REVIEW,
            "issue_type": "Leak",
            "full_address": "Tel Aviv",
            "created_at": datetime.now(timezone.utc),
            # No `updated_at` field at all.
        }
    )

    await process_incoming_message(chat_id, "שלום, יש לי עוד בעיה")

    mock_ai.analyze_conversation.assert_awaited()
    for call in mock_wa.send_message.call_args_list:
        assert call.args[1] != Messages.Customer.STILL_PENDING_REVIEW


# --- Emergency Logic Tests ---


@pytest.mark.asyncio
async def test_emergency_detection_and_ack(wf_mocks, mock_db):
    """Message with emergency keyword creates lead with is_emergency=True and sends EMERGENCY_ACK."""
    mock_wa, mock_state, _, mock_ai, mock_lm = wf_mocks
    chat_id = "972501112222@c.us"

    # Mock dispatcher to return city but no deal yet
    mock_ai.analyze_conversation.return_value = AIResponse(
        reply_to_user="זיהיתי מצב חירום",
        extracted_data=ExtractedData(
            city="תל אביב",
            issue="פיצוץ",
            full_address=None,
            appointment_time=None,
            street=None,
            street_number=None,
            floor=None,
            apartment=None,
            customer_name=None,
        ),
        transcription=None,
        is_deal=False,
    )

    await process_incoming_message(chat_id, "יש לי פיצוץ מים בבית!")

    # Verify EMERGENCY_ACK sent
    mock_wa.send_message.assert_any_call(chat_id, Messages.Customer.EMERGENCY_ACK)

    # Verify lead created with is_emergency=True
    mock_lm.create_lead_from_dict.assert_called()
    call_args = mock_lm.create_lead_from_dict.call_args.kwargs
    assert call_args["is_emergency"] is True


@pytest.mark.asyncio
async def test_emergency_bypass_address_gate(wf_mocks, monkeypatch, mock_db):
    """Emergency lead bypasses address gate with just a city."""
    mock_wa, mock_state, _, mock_ai, mock_lm = wf_mocks
    chat_id = "972501113333@c.us"

    # 1. Setup emergency lead in DB
    lead_id = ObjectId()
    await mock_db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": chat_id,
            "status": LeadStatus.CONTACTED,
            "is_emergency": True,
            "city": "תל אביב",
        }
    )

    # 2. Mock AI to return a [DEAL] with ONLY city (incomplete address)
    pro_id = ObjectId()
    pro_doc = {
        "_id": pro_id,
        "business_name": "Emergency Pro",
        "phone_number": "972500000001",
        "is_active": True,
    }
    monkeypatch.setattr(
        app.services.workflow_service,
        "determine_best_pro",
        AsyncMock(return_value=pro_doc),
    )

    # Mock analyze_conversation: first call is dispatcher, second is pro-persona
    dispatcher_resp = AIResponse(
        reply_to_user="זיהיתי חירום",
        extracted_data=ExtractedData(
            city="תל אביב",
            issue="הצפה",
            full_address=None,
            appointment_time=None,
            street=None,
            street_number=None,
            floor=None,
            apartment=None,
            customer_name=None,
        ),
        transcription=None,
        is_deal=False,
    )
    pro_resp = AIResponse(
        reply_to_user="[DEAL: בהקדם | תל אביב | הצפה]",
        extracted_data=ExtractedData(
            city="תל אביב",
            issue="הצפה",
            street=None,
            street_number=None,
            full_address="תל אביב",
            appointment_time="בהקדם",
            floor=None,
            apartment=None,
            customer_name=None,
        ),
        transcription=None,
        is_deal=True,
    )
    mock_ai.analyze_conversation.side_effect = [dispatcher_resp, pro_resp]

    await process_incoming_message(chat_id, "יש הצפה דחופה!")

    # Verify address gate bypass: customer gets AWAITING_APPROVAL_TRANSPARENT, NOT the address reason
    expected_msg = Messages.Customer.AWAITING_APPROVAL_TRANSPARENT.format(
        pro_name="Emergency Pro"
    )
    mock_wa.send_message.assert_any_call(chat_id, expected_msg)

    # Pro should get EMERGENCY_LEAD_HEADER
    pro_calls = [
        c
        for c in mock_wa.send_message.call_args_list
        if c.args[0] == "972500000001@c.us"
    ]
    found_emergency_header = any(
        Messages.Pro.EMERGENCY_LEAD_HEADER in str(call.args[1]) for call in pro_calls
    )
    assert found_emergency_header is True


# --- PRO-121: emergency escalation is no longer dropped in holding states ---
#
# `is_emergency_text` itself (exact keywords + clitic-prefixable stems, minus
# negations) is unit-tested directly in tests/test_text_matching.py — cheaper
# than driving the whole dispatcher for every keyword variant. The two cases
# below only pin that the dispatcher is wired to that detector at all.


def test_emergency_ack_no_longer_promises_summoned_pros():
    """PRO-121: EMERGENCY_ACK fires on a keyword alone, before any pro is
    matched — the old copy ("...ומזעיק עכשיו אנשי מקצוע פנויים") promised a
    match that hadn't happened yet."""
    assert "מזעיק" not in Messages.Customer.EMERGENCY_ACK


@pytest.mark.asyncio
async def test_emergency_detection_wiring_flags_a_new_lead(wf_mocks, mock_db):
    """PRO-121 wiring check: a message `is_emergency_text` matches flags the
    lead created for it. The matching truth table itself lives in
    test_text_matching.py."""
    _, _, _, _, mock_lm = wf_mocks

    await process_incoming_message("972501118888@c.us", "דחוף!")

    mock_lm.create_lead_from_dict.assert_called_once()
    assert mock_lm.create_lead_from_dict.call_args.kwargs["is_emergency"] is True


@pytest.mark.asyncio
async def test_emergency_detection_wiring_ignores_a_non_match(wf_mocks, mock_db):
    """PRO-121 wiring check: 'קצר' as a substring of 'בקצרה' is one of
    `is_emergency_text`'s documented non-matches — confirm the dispatcher
    doesn't flag (or even create) a lead over it, since nothing else on this
    turn was extracted either (default AI mock returns no city/issue)."""
    _, _, _, _, mock_lm = wf_mocks

    await process_incoming_message("972501119999@c.us", "תסביר לי בקצרה מה קרה")

    mock_lm.create_lead_from_dict.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("emergency_hold_acked", [False, True])
async def test_emergency_while_waiting_for_pro_approval(
    wf_mocks, mock_db, emergency_hold_acked
):
    """PRO-121: an emergency keyword typed while parked in
    AWAITING_PRO_APPROVAL answers with EMERGENCY_WHILE_WAITING once, then
    throttles on the lead's own `emergency_hold_acked` field. Throttling on
    `is_emergency` itself instead would be wrong: the mainline case is a lead
    already flagged at intake that then parks in this hold, so gating on the
    flag would make this copy unreachable for exactly the customer it targets
    — they would shout the keyword here and get back the generic soft-hold."""
    mock_wa, mock_state, _, _, _ = wf_mocks
    chat_id = "972501112121@c.us"
    mock_state.get_state = AsyncMock(return_value=UserStates.AWAITING_PRO_APPROVAL)

    lead_id = ObjectId()
    await mock_db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": chat_id,
            "status": LeadStatus.NEW,
            "city": "ירושלים",
            # Flagged at intake, already parked here — the mainline case.
            "is_emergency": True,
            "emergency_hold_acked": emergency_hold_acked,
        }
    )

    await process_incoming_message(chat_id, "יש שריפה, דחוף!")

    updated_lead = await mock_db.leads.find_one({"_id": lead_id})
    assert updated_lead["is_emergency"] is True
    # The hold itself is never released by an emergency declared here — only
    # answered.
    mock_state.clear_state.assert_not_called()

    sent_texts = [c.args[1] for c in mock_wa.send_message.call_args_list]
    if emergency_hold_acked:
        assert Messages.Customer.STILL_WAITING in sent_texts
        assert Messages.Customer.EMERGENCY_WHILE_WAITING not in sent_texts
    else:
        assert Messages.Customer.EMERGENCY_WHILE_WAITING in sent_texts
        assert Messages.Customer.STILL_WAITING not in sent_texts
        assert updated_lead["emergency_hold_acked"] is True


@pytest.mark.asyncio
async def test_emergency_releases_address_gate_when_city_known(wf_mocks, mock_db):
    """PRO-121: an emergency keyword typed mid address-gate, with a city
    already on the lead, clears the gate and lets routing continue — it must
    NOT re-ask for the missing street/floor/apartment parts."""
    mock_wa, mock_state, _, mock_ai, mock_lm = wf_mocks
    chat_id = "972501113131@c.us"

    lead_id = ObjectId()
    await mock_db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": chat_id,
            "status": LeadStatus.CONTACTED,
            "city": "תל אביב",
        }
    )

    # Mirror StateManager's real Redis-backed behavior: get_state must reflect
    # the clear_state that _escalate_emergency performs, or the old
    # AWAITING_ADDRESS handler further down would still fire on the stale value.
    live_state = {"value": UserStates.AWAITING_ADDRESS}

    async def get_state_effect(_chat_id):
        return live_state["value"]

    async def clear_state_effect(_chat_id):
        live_state["value"] = UserStates.IDLE

    mock_state.get_state = AsyncMock(side_effect=get_state_effect)
    mock_state.clear_state = AsyncMock(side_effect=clear_state_effect)

    await process_incoming_message(chat_id, "יש שריפה בבית!")

    mock_state.clear_state.assert_called_with(chat_id)

    sent_texts = [c.args[1] for c in mock_wa.send_message.call_args_list]
    assert Messages.Customer.EMERGENCY_ACK in sent_texts
    assert Messages.Customer.ADDRESS_INVALID not in sent_texts
    assert Messages.Customer.EMERGENCY_NEED_CITY not in sent_texts

    updated_lead = await mock_db.leads.find_one({"_id": lead_id})
    assert updated_lead["is_emergency"] is True


@pytest.mark.asyncio
async def test_emergency_needs_city_when_address_gate_has_no_city(wf_mocks, mock_db):
    """PRO-121: an emergency typed mid address-gate with no city yet asks for
    the city alone — never the five-part address gate's own prompt — and the
    turn is fully answered without reaching the AI dispatcher."""
    mock_wa, mock_state, _, mock_ai, mock_lm = wf_mocks
    chat_id = "972501114141@c.us"
    mock_state.get_state = AsyncMock(return_value=UserStates.AWAITING_ADDRESS)

    lead_id = ObjectId()
    await mock_db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": chat_id,
            "status": LeadStatus.CONTACTED,
        }
    )

    await process_incoming_message(chat_id, "יש הצפה, דחוף!")

    sent_texts = [c.args[1] for c in mock_wa.send_message.call_args_list]
    assert Messages.Customer.EMERGENCY_NEED_CITY in sent_texts
    assert Messages.Customer.ADDRESS_INVALID not in sent_texts
    assert Messages.Customer.EMERGENCY_ACK not in sent_texts

    # The turn is answered entirely inside the escalation branch.
    mock_ai.analyze_conversation.assert_not_called()

    updated_lead = await mock_db.leads.find_one({"_id": lead_id})
    assert updated_lead["is_emergency"] is True


@pytest.mark.asyncio
async def test_emergency_releases_loyalty_confirmation_menu(wf_mocks, mock_db):
    """PRO-121: an emergency keyword typed against the "want your previous
    pro?" menu drops the question — a preference menu must not outrank an
    emergency — and does not re-prompt the loyalty menu."""
    mock_wa, mock_state, _, mock_ai, mock_lm = wf_mocks
    chat_id = "972501115151@c.us"

    lead_id = ObjectId()
    await mock_db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": chat_id,
            "status": LeadStatus.NEW,
            "city": "חיפה",
        }
    )

    live_state = {"value": UserStates.AWAITING_LOYALTY_CONFIRMATION}

    async def get_state_effect(_chat_id):
        return live_state["value"]

    async def clear_state_effect(_chat_id):
        live_state["value"] = UserStates.IDLE

    mock_state.get_state = AsyncMock(side_effect=get_state_effect)
    mock_state.clear_state = AsyncMock(side_effect=clear_state_effect)

    await process_incoming_message(chat_id, "יש לי דחוף, תעזרו")

    mock_state.clear_state.assert_called_with(chat_id)

    sent_texts = [c.args[1] for c in mock_wa.send_message.call_args_list]
    assert Messages.Customer.LOYALTY_REPROMPT not in sent_texts
    assert Messages.Customer.EMERGENCY_ACK in sent_texts

    updated_lead = await mock_db.leads.find_one({"_id": lead_id})
    assert updated_lead["is_emergency"] is True


@pytest.mark.asyncio
async def test_emergency_expedites_dispatch_without_deal_marker(
    wf_mocks, monkeypatch, mock_db
):
    """PRO-121: once a pro is actually matched, an emergency lead finalizes
    even though the AI never emitted a [DEAL] marker — waiting for one would
    stall a "get me someone now" request behind ordinary small talk.

    No lead/pro_id exists yet for this chat_id, so this exercises the Smart
    Dispatcher Phase specifically, not the assigned-pro fast path (see
    ``determine_best_pro_mock.assert_called_once()`` below — that call only
    happens on this branch)."""
    mock_wa, mock_state, _, mock_ai, mock_lm = wf_mocks
    chat_id = "972501116161@c.us"

    pro_doc = {
        "_id": ObjectId(),
        "business_name": "Fast Pro",
        "phone_number": "972500000099",
        "is_active": True,
    }
    determine_best_pro_mock = AsyncMock(return_value=pro_doc)
    monkeypatch.setattr(
        app.services.workflow_service, "determine_best_pro", determine_best_pro_mock
    )
    mock_finalize = AsyncMock()
    monkeypatch.setattr(app.services.workflow_service, "_finalize_deal", mock_finalize)

    mock_ai.analyze_conversation.return_value = AIResponse(
        reply_to_user="בסדר, אני שולח מישהו",
        extracted_data=ExtractedData(
            city="חיפה",
            issue="נזילה",
            full_address=None,
            appointment_time=None,
            street=None,
            street_number=None,
            floor=None,
            apartment=None,
            customer_name=None,
        ),
        transcription=None,
        is_deal=False,
    )

    # "דחופה" (not the bare "דחוף" exact keyword) matches only via the
    # clitic-prefixable stem list — confirms the branch is reached the same
    # way a real customer's phrasing would trigger it.
    await process_incoming_message(chat_id, "יש לי נזילה דחופה בחיפה!")

    determine_best_pro_mock.assert_called_once()
    mock_finalize.assert_called_once()
    assert mock_finalize.call_args.args[1] == pro_doc


@pytest.mark.asyncio
@pytest.mark.parametrize("finalize_fails", [False, True])
async def test_emergency_expedites_assigned_pro_fast_path(
    wf_mocks, mock_db, monkeypatch, finalize_fails
):
    """PRO-121 (review fix): every route into AWAITING_ADDRESS sets `pro_id`
    first, so a released emergency actually lands in the assigned-pro fast
    path (`if existing_pro and active_lead:`), not the Smart Dispatcher Phase
    below it. That fast path must expedite too: finalize despite
    `is_deal=False` and withhold the AI's mid-intake reply — unless finalize
    itself raises, in which case the withheld reply is sent as a fallback so
    the customer isn't left silent."""
    mock_wa, mock_state, _, mock_ai, _ = wf_mocks
    chat_id = f"97250111717{1 if finalize_fails else 0}@c.us"

    pro_id = ObjectId()
    await mock_db.users.insert_one(
        {
            "_id": pro_id,
            "business_name": "Fast Pro",
            "phone_number": "972500000098",
            "is_active": True,
            "role": "professional",
        }
    )

    lead_id = ObjectId()
    await mock_db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": chat_id,
            "status": LeadStatus.CONTACTED,
            "city": "תל אביב",
            "pro_id": pro_id,
        }
    )

    # Mirror StateManager's real Redis-backed behavior — see the address-gate
    # tests above for why a plain return_value would be wrong here.
    live_state = {"value": UserStates.AWAITING_ADDRESS}

    async def get_state_effect(_chat_id):
        return live_state["value"]

    async def clear_state_effect(_chat_id):
        live_state["value"] = UserStates.IDLE

    mock_state.get_state = AsyncMock(side_effect=get_state_effect)
    mock_state.clear_state = AsyncMock(side_effect=clear_state_effect)

    mock_finalize = AsyncMock(
        side_effect=RuntimeError("boom") if finalize_fails else None
    )
    monkeypatch.setattr(app.services.workflow_service, "_finalize_deal", mock_finalize)

    mid_intake_reply = "באיזו קומה הנזילה?"
    mock_ai.analyze_conversation.return_value = AIResponse(
        reply_to_user=mid_intake_reply,
        extracted_data=ExtractedData(
            city="תל אביב",
            issue="נזילה",
            full_address=None,
            appointment_time=None,
            street=None,
            street_number=None,
            floor=None,
            apartment=None,
            customer_name=None,
        ),
        transcription=None,
        is_deal=False,
    )

    await process_incoming_message(chat_id, "יש הצפה, דחוף!")

    mock_finalize.assert_called_once()
    assert mock_finalize.call_args.args[1]["_id"] == pro_id

    sent_texts = [c.args[1] for c in mock_wa.send_message.call_args_list]
    if finalize_fails:
        assert mid_intake_reply in sent_texts
    else:
        assert mid_intake_reply not in sent_texts


@pytest.mark.asyncio
async def test_emergency_while_paused_for_human_pages_once_without_reply(
    wf_mocks, mock_db, monkeypatch
):
    """PRO-121: PAUSED_FOR_HUMAN is deliberately absent from
    EMERGENCY_HOLDING_STATES — a human already owns the conversation and the
    bot must not talk over them — but an emergency declared there still flags
    the lead and pages the operator once, without un-pausing and without
    answering the customer. A second emergency message does not page again."""
    mock_wa, mock_state, _, _, _ = wf_mocks
    chat_id = "972501118181@c.us"
    mock_state.get_state = AsyncMock(return_value=UserStates.PAUSED_FOR_HUMAN)

    lead_id = ObjectId()
    await mock_db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": chat_id,
            "status": LeadStatus.BOOKED,
        }
    )

    mock_alert = AsyncMock()
    monkeypatch.setattr(app.services.workflow_service, "send_sos_alert", mock_alert)

    await process_incoming_message(chat_id, "יש שריפה, דחוף!")

    updated_lead = await mock_db.leads.find_one({"_id": lead_id})
    assert updated_lead["is_emergency"] is True
    mock_alert.assert_called_once()
    mock_wa.send_message.assert_not_called()
    mock_state.set_state.assert_any_call(
        chat_id, UserStates.PAUSED_FOR_HUMAN, ttl=WorkerConstants.PAUSE_TTL_SECONDS
    )

    # A second emergency message from the same customer does not page again.
    await process_incoming_message(chat_id, "עדיין דחוף, בבקשה!")
    mock_alert.assert_called_once()


# --- Customer status command routing ---


@pytest.mark.asyncio
async def test_question_mark_alone_triggers_status(wf_mocks, mock_db):
    """Sending '?' returns the status reply and never reaches the AI dispatcher."""
    import asyncio

    mock_wa, mock_state, _, mock_ai, mock_lm = wf_mocks
    mock_state.get_state = AsyncMock(return_value=UserStates.IDLE)

    await process_incoming_message("972511111115@c.us", "?")
    await asyncio.sleep(0)

    mock_wa.send_message.assert_called_once()
    sent_text = mock_wa.send_message.call_args.args[1]
    # No active lead -> status_no_active_lead message
    assert sent_text == Messages.Customer.STATUS_NO_ACTIVE_LEAD
    mock_ai.analyze_conversation.assert_not_called()


@pytest.mark.asyncio
async def test_question_mark_inside_sentence_does_not_trigger_status(
    wf_mocks, monkeypatch
):
    """'מה השעה?' must NOT trigger status — only exact '?' should match."""
    import asyncio

    mock_wa, mock_state, _, mock_ai, _ = wf_mocks
    mock_state.get_state = AsyncMock(return_value=UserStates.IDLE)

    mock_status = AsyncMock(return_value="STATUS_RESPONSE")
    monkeypatch.setattr(
        app.services.workflow_service, "_handle_status_query", mock_status
    )

    await process_incoming_message("972511111116@c.us", "מה השעה?")
    await asyncio.sleep(0)

    mock_status.assert_not_called()


@pytest.mark.asyncio
async def test_status_command_skipped_in_pro_mode(wf_mocks, monkeypatch):
    """Pro in PRO_MODE sending 'סטטוס' must not hit the customer status handler."""
    import asyncio

    mock_wa, mock_state, _, _, _ = wf_mocks
    mock_state.get_state = AsyncMock(return_value=UserStates.PRO_MODE)

    mock_status = AsyncMock(return_value="STATUS_RESPONSE")
    monkeypatch.setattr(
        app.services.workflow_service, "_handle_status_query", mock_status
    )

    await process_incoming_message("972524828796@c.us", "סטטוס")
    await asyncio.sleep(0)

    mock_status.assert_not_called()


# --- PRO-44: [DEAL:...] marker must never reach the customer ---


def test_strip_deal_marker():
    """One pure function, six input shapes — a table, not six test names.

    The last case documents current behaviour rather than a requirement: a
    malformed marker with no closing ']' is not matched by the non-greedy regex,
    so it is left in place.
    """
    cases = [
        # (label, raw, expected fragments present, fragments absent, exact)
        (
            "marker mid-string, surrounding text kept",
            "מעולה, נקבע! [DEAL: 10:00 | הרצל 10 | נזילה] נתראה מחר!",
            ["מעולה, נקבע!", "נתראה מחר!"],
            None,
        ),
        (
            "more than one marker is fully stripped, not just the first",
            "התחלה [DEAL: A] אמצע [DEAL: B] סוף",
            ["התחלה", "אמצע", "סוף"],
            None,
        ),
        (
            "surrounding whitespace trimmed",
            "   שלום, מה שלומך?   ",
            [],
            "שלום, מה שלומך?",
        ),
        (
            "no marker at all: trimmed, otherwise untouched",
            "  אין כאן שום סימון מיוחד  ",
            [],
            "אין כאן שום סימון מיוחד",
        ),
        ("empty string", "", [], ""),
        ("None — the `text or ''` guard", None, [], ""),
        (
            "unclosed bracket is left in place (documented behaviour)",
            "סיימנו [DEAL: no closing bracket",
            [],
            "סיימנו [DEAL: no closing bracket",
        ),
    ]

    for label, raw, present, exact in cases:
        cleaned = _strip_deal_marker(raw)
        if exact is not None:
            assert cleaned == exact, f"{label}: got {cleaned!r}"
        else:
            assert "[DEAL" not in cleaned, f"{label}: marker survived in {cleaned!r}"
        for fragment in present:
            assert fragment in cleaned, f"{label}: lost {fragment!r} from {cleaned!r}"


@pytest.mark.asyncio
async def test_assigned_pro_fast_path_strips_deal_marker_and_finalizes(
    wf_mocks, mock_db
):
    """
    Regression PRO-44: on the "pro already assigned" fast path, a raw [DEAL:...]
    marker embedded in reply_to_user (fallback signal because the AI didn't set
    the structured is_deal flag) must be stripped before the text is sent/logged,
    while the surrounding customer-facing text is preserved. The deal must still
    be finalized (booking side effects fire) because detection runs on the raw
    text before cleaning.
    """
    mock_wa, mock_state, mock_ctx, mock_ai, mock_lm = wf_mocks

    chat_id = "972501119999@c.us"
    pro_id = ObjectId()
    lead_id = ObjectId()

    await mock_db.users.insert_one(
        {
            "_id": pro_id,
            "business_name": "Marker Pro",
            "phone_number": "972500009999",
            "is_active": True,
        }
    )
    await mock_db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": chat_id,
            "status": LeadStatus.CONTACTED,
            "pro_id": pro_id,
            "created_at": "2026-01-01",
        }
    )

    raw_reply = "מעולה, נקבע! [DEAL: 10:00 | הרצל 10 | נזילה] נתראה מחר!"
    mock_ai.analyze_conversation = AsyncMock(
        return_value=AIResponse(
            reply_to_user=raw_reply,
            extracted_data=ExtractedData(
                city="תל אביב",
                issue="נזילה",
                street="הרצל",
                street_number="10",
                floor="2",
                apartment="4",
                appointment_time="10:00",
            ),
            # Structured flag missed by the AI — the marker is the fallback signal.
            transcription=None,
            is_deal=False,
        )
    )

    await process_incoming_message(chat_id, "אפשר מחר בעשר")

    expected_cleaned = _strip_deal_marker(raw_reply)
    assert "[DEAL" not in expected_cleaned
    assert "מעולה, נקבע!" in expected_cleaned
    assert "נתראה מחר!" in expected_cleaned

    mock_wa.send_message.assert_any_call(chat_id, expected_cleaned)
    mock_lm.log_message.assert_any_call(chat_id, "model", expected_cleaned)

    # Marker must never leak to the customer (or anywhere) via WhatsApp
    for call in mock_wa.send_message.call_args_list:
        assert "[DEAL" not in str(call.args[1])
    for call in mock_lm.log_message.call_args_list:
        assert "[DEAL" not in str(call.args[-1])

    # Deal still finalized despite is_deal=False: customer moved to
    # AWAITING_PRO_APPROVAL and the pro received the approval request.
    mock_state.set_state.assert_any_call(
        chat_id,
        UserStates.AWAITING_PRO_APPROVAL,
        ttl=WorkerConstants.PRO_APPROVAL_TTL_SECONDS,
    )
    pro_calls = [
        c
        for c in mock_wa.send_message.call_args_list
        if c.args[0] == "972500009999@c.us"
    ]
    assert len(pro_calls) >= 1

    updated_lead = await mock_db.leads.find_one({"_id": lead_id})
    assert updated_lead["status"] == LeadStatus.NEW
    assert updated_lead["pro_id"] == pro_id


@pytest.mark.asyncio
async def test_dispatcher_path_with_best_pro_strips_deal_marker_and_finalizes(
    wf_mocks, monkeypatch, mock_db
):
    """
    Regression PRO-44: same marker-stripping guarantee on the main dispatcher
    path (no pro assigned yet -> matching finds best_pro -> pro-persona reply
    used as final_response). Marker must be stripped from the sent/logged text
    but the deal must still be finalized.
    """
    mock_wa, mock_state, _, mock_ai, mock_lm = wf_mocks

    chat_id = "972501115555@c.us"
    pro_id = ObjectId()
    lead_id = ObjectId()

    pro_doc = {
        "_id": pro_id,
        "business_name": "Test Pro",
        "phone_number": "972500001111",
        "service_areas": ["Tel Aviv"],
        "is_active": True,
    }
    await mock_db.users.insert_one(pro_doc)

    # Pre-existing CONTACTED lead with no pro assigned yet, so the dispatcher
    # phase updates this real document instead of routing through the
    # (mocked) create_lead_from_dict — letting _finalize_deal's real
    # find_one/update_one calls resolve against an actual document.
    await mock_db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": chat_id,
            "status": LeadStatus.CONTACTED,
            "created_at": "2026-01-01",
        }
    )

    dispatcher_resp = AIResponse(
        reply_to_user="מצאתי לך בעל מקצוע",
        extracted_data=ExtractedData(
            city="Tel Aviv", issue="Leak", appointment_time=None
        ),
        transcription=None,
        is_deal=False,
    )
    raw_pro_reply = "מעולה! [DEAL: 10:00 | Herzl 10 | Leak] מחכה לך!"
    pro_resp = AIResponse(
        reply_to_user=raw_pro_reply,
        extracted_data=ExtractedData(
            city="Tel Aviv",
            issue="Leak",
            street="Herzl",
            street_number="10",
            floor="2",
            apartment="4",
            appointment_time="10:00",
        ),
        # Structured flag missed by the AI — the marker is the fallback signal.
        transcription=None,
        is_deal=False,
    )
    mock_ai.analyze_conversation.side_effect = [dispatcher_resp, pro_resp]

    monkeypatch.setattr(
        app.services.workflow_service,
        "determine_best_pro",
        AsyncMock(return_value=pro_doc),
    )

    await process_incoming_message(chat_id, "יש לי נזילה בתל אביב")

    expected_cleaned = _strip_deal_marker(raw_pro_reply)
    assert "[DEAL" not in expected_cleaned
    assert "מעולה!" in expected_cleaned
    assert "מחכה לך!" in expected_cleaned

    mock_wa.send_message.assert_any_call(chat_id, expected_cleaned)
    mock_lm.log_message.assert_any_call(chat_id, "model", expected_cleaned)

    # Marker must never leak to the customer (or anywhere) via WhatsApp
    for call in mock_wa.send_message.call_args_list:
        assert "[DEAL" not in str(call.args[1])

    # Deal still finalized despite is_deal=False: customer moved to
    # AWAITING_PRO_APPROVAL and the pro received the approval request.
    mock_state.set_state.assert_any_call(
        chat_id,
        UserStates.AWAITING_PRO_APPROVAL,
        ttl=WorkerConstants.PRO_APPROVAL_TTL_SECONDS,
    )
    pro_calls = [
        c
        for c in mock_wa.send_message.call_args_list
        if c.args[0] == "972500001111@c.us"
    ]
    assert len(pro_calls) >= 1

    updated_lead = await mock_db.leads.find_one({"_id": lead_id})
    assert updated_lead["status"] == LeadStatus.NEW
    assert updated_lead["pro_id"] == pro_id


# --- PRO-55: quoted-price sanitizer/validator (injection guard) ---


def test_clean_quoted_price_accepts_price_shapes_and_rejects_free_text():
    """`_clean_quoted_price` normalizes real price shapes and rejects anything
    else, so AI/customer-influenced free text can never reach the pro's approval
    message verbatim (PRO-55 trust guard)."""
    from app.services.workflow_service import _clean_quoted_price

    # Accepted → normalized
    assert _clean_quoted_price("400-600") == "400-600"
    assert _clean_quoted_price("500") == "500"
    assert _clean_quoted_price("400 - 600") == "400-600"
    assert _clean_quoted_price("400-600₪") == "400-600"

    # Rejected → None
    assert _clean_quoted_price(None) is None
    assert _clean_quoted_price("") is None
    assert _clean_quoted_price("צור קשר 050-1234567") is None  # injected free text
    assert _clean_quoted_price("about 500 shekels") is None
    assert _clean_quoted_price("0501234567") is None  # phone, not a price


@pytest.mark.asyncio
async def test_pro_persona_prompt_uses_prices_for_prompt(monkeypatch):
    """PRO-89 regression: the scheduler prompt must carry the pro's real prices.

    Onboarding stores prices in ``prices_for_prompt``; the builder used to read
    only ``price_list``, leaving the AI with an empty price list so it invented
    figures. The pro's actual prices must reach the system prompt.
    """
    pro = {
        "_id": ObjectId(),
        "business_name": "אבי אינסטלציה",
        "prices_for_prompt": "פתיחת סתימה: 200-350₪ | ביקור: 150₪",
        "social_proof": {"rating": 5.0, "review_count": 3},
    }
    await _build_pro_response(pro, [], "היי", "רמת גן", "נזילה במטבח", None)

    prompt = app.services.workflow_service.ai.analyze_conversation.call_args.kwargs[
        "custom_system_prompt"
    ]
    assert "פתיחת סתימה: 200-350₪" in prompt
    assert "150₪" in prompt


@pytest.mark.asyncio
async def test_pro_persona_prompt_falls_back_to_price_list(monkeypatch):
    """A pro with the legacy ``price_list`` field (no ``prices_for_prompt``)
    still gets its prices into the prompt."""
    pro = {
        "_id": ObjectId(),
        "business_name": "אבי אינסטלציה",
        "price_list": "החלפת ברז: 250-400₪",
        "social_proof": {"rating": 5.0, "review_count": 3},
    }
    await _build_pro_response(pro, [], "היי", "רמת גן", "נזילה", None)

    prompt = app.services.workflow_service.ai.analyze_conversation.call_args.kwargs[
        "custom_system_prompt"
    ]
    assert "החלפת ברז: 250-400₪" in prompt


@pytest.mark.asyncio
async def test_pro_persona_empty_system_prompt_falls_back_to_default_role(monkeypatch):
    """PRO-170 regression: WhatsApp-onboarded pros store ``system_prompt: ""``
    (pro_onboarding_service), and an empty string used to win over the ``.get``
    default — so every onboarded pro ran the scheduler with an EMPTY persona
    block. Empty or missing must fall back to PROLI_SCHEDULER_ROLE."""
    default_role = Messages.AISystemPrompts.PROLI_SCHEDULER_ROLE.format(
        pro_name="אבי אינסטלציה"
    )
    for stored in ({"system_prompt": ""}, {}):
        pro = {
            "_id": ObjectId(),
            "business_name": "אבי אינסטלציה",
            "social_proof": {"rating": 5.0, "review_count": 3},
            **stored,
        }
        await _build_pro_response(pro, [], "היי", "רמת גן", "נזילה", None)

        prompt = app.services.workflow_service.ai.analyze_conversation.call_args.kwargs[
            "custom_system_prompt"
        ]
        assert default_role in prompt, f"stored={stored!r}"


@pytest.mark.asyncio
async def test_pro_persona_custom_system_prompt_still_wins(monkeypatch):
    """The PRO-170 fix must not flatten admin-generated personas: a non-empty
    stored system_prompt reaches the prompt and the default role does not."""
    pro = {
        "_id": ObjectId(),
        "business_name": "אבי אינסטלציה",
        "system_prompt": "אתה 'פרולי', העוזר האישי של 'אבי אינסטלציה'.",
        "social_proof": {"rating": 5.0, "review_count": 3},
    }
    await _build_pro_response(pro, [], "היי", "רמת גן", "נזילה", None)

    prompt = app.services.workflow_service.ai.analyze_conversation.call_args.kwargs[
        "custom_system_prompt"
    ]
    assert "העוזר האישי של 'אבי אינסטלציה'" in prompt
    assert (
        Messages.AISystemPrompts.PROLI_SCHEDULER_ROLE.format(pro_name="אבי אינסטלציה")
        not in prompt
    )


# --- PRO-116: booked-customer gate, single logging, name persistence ---
# NOTE: mock_db is module-scoped (shared across this file), so each test below
# uses a UNIQUE chat_id to stay isolated from the others' leads/users.

from datetime import datetime, timezone  # noqa: E402


@pytest.mark.asyncio
async def test_booked_customer_new_message_asks_new_or_existing(wf_mocks, mock_db):
    """A customer with a confirmed BOOKED job who writes something new is asked
    'new request or existing?' instead of silently spawning a second lead."""
    mock_wa, mock_state, _, mock_ai, mock_lm = wf_mocks
    mock_state.get_state.return_value = UserStates.IDLE
    chat = "972500116001@c.us"
    pro_id = ObjectId()
    await mock_db.users.insert_one(
        {
            "_id": pro_id,
            "business_name": "אבי אינסטלציה",
            "phone_number": "972500000000",
            "is_active": True,
            "role": "professional",
        }
    )
    await mock_db.leads.insert_one(
        {
            "_id": ObjectId(),
            "chat_id": chat,
            "status": LeadStatus.BOOKED,
            "pro_id": pro_id,
            "issue_type": "נזילה",
            "appointment_time": "מחר 08:00",
            "created_at": datetime.now(timezone.utc),
        }
    )

    await process_incoming_message(chat, "יש לי בעיה אחרת")

    mock_state.set_state.assert_any_call(chat, UserStates.AWAITING_NEW_OR_EXISTING)
    sent = " ".join(str(c.args[1]) for c in mock_wa.send_message.call_args_list)
    assert (
        static_prefix(Messages.Customer.EXISTING_JOB_PROMPT) in sent
    )  # the new-or-existing prompt was shown
    mock_lm.create_lead_from_dict.assert_not_called()  # no second lead
    mock_ai.analyze_conversation.assert_not_called()  # dispatcher never ran


@pytest.mark.asyncio
async def test_booked_gate_fires_once_per_lead(wf_mocks, mock_db):
    """The gate marks the booked lead so it doesn't re-prompt every message."""
    mock_wa, mock_state, _, _, _ = wf_mocks
    mock_state.get_state.return_value = UserStates.IDLE
    chat = "972500116002@c.us"
    lead_id = ObjectId()
    await mock_db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": chat,
            "status": LeadStatus.BOOKED,
            "issue_type": "נזילה",
            "created_at": datetime.now(timezone.utc),
        }
    )
    await process_incoming_message(chat, "משהו חדש")
    updated = await mock_db.leads.find_one({"_id": lead_id})
    assert updated.get("new_request_prompted") is True


@pytest.mark.asyncio
async def test_assigned_pro_fast_path_logs_inbound_once(wf_mocks, mock_db):
    """PRO-116 Q5: the inbound must be logged exactly once on the assigned-pro
    fast path (was logged twice, polluting history + AI context)."""
    mock_wa, mock_state, _, mock_ai, mock_lm = wf_mocks
    mock_state.get_state.return_value = UserStates.IDLE
    chat = "972500116003@c.us"
    pro_id = ObjectId()
    await mock_db.users.insert_one(
        {
            "_id": pro_id,
            "business_name": "אבי",
            "phone_number": "972500000003",
            "is_active": True,
            "role": "professional",
        }
    )
    await mock_db.leads.insert_one(
        {
            "_id": ObjectId(),
            "chat_id": chat,
            "status": LeadStatus.CONTACTED,
            "pro_id": pro_id,
            "issue_type": "נזילה",
            "full_address": "תל אביב",
        }
    )
    mock_ai.analyze_conversation.return_value = AIResponse(
        reply_to_user="שלום, כאן אבי",
        extracted_data=ExtractedData(
            city=None, issue=None, full_address=None, appointment_time=None
        ),
        transcription=None,
        is_deal=False,
    )

    await process_incoming_message(chat, "טפטוף קטן")

    user_logs = [
        c
        for c in mock_lm.log_message.call_args_list
        if c.args[0] == chat and c.args[1] == "user"
    ]
    assert len(user_logs) == 1


@pytest.mark.asyncio
async def test_returning_customer_name_seeded_from_prior_lead(wf_mocks, mock_db):
    """PRO-116 Q4: a returning customer's name is injected into the dispatcher
    prompt from a prior lead, so they aren't asked their name again."""
    mock_wa, mock_state, _, mock_ai, _ = wf_mocks
    mock_state.get_state.return_value = UserStates.IDLE
    chat = "972500116004@c.us"
    await mock_db.leads.insert_one(
        {
            "_id": ObjectId(),
            "chat_id": chat,
            "status": LeadStatus.COMPLETED,
            "customer_name": "מוטי",
            "created_at": datetime.now(timezone.utc),
        }
    )

    await process_incoming_message(chat, "שלום")

    prompt = mock_ai.analyze_conversation.call_args.kwargs.get(
        "custom_system_prompt", ""
    )
    assert "מוטי" in prompt


# --- PRO-143: typing indicator via spawn_background_task ---


@pytest.mark.asyncio
async def test_typing_indicator_task_name_carries_masked_phone_suffix(
    wf_mocks, monkeypatch
):
    """process_incoming_message spawns the typing indicator through
    spawn_background_task (PRO-143) rather than a bare create_task, naming it
    with app.core.phone.mask_chat_id(chat_id) rather than a raw chat_id[-4:]
    slice. A raw slice on a '<digits>@c.us' id always yields the literal
    string 'c.us' -- every recipient's failure log line would read the same,
    telling the operator nothing (PRO-89 review finding; see
    mask_chat_id's docstring). mask_chat_id strips the suffix first, so the
    task name both omits the full phone number and carries a genuinely
    per-recipient last-4-digits suffix -- pinning both halves of the fix.
    """
    mock_state = wf_mocks[1]
    mock_state.get_state.return_value = UserStates.IDLE
    chat_id = "972501234567@c.us"

    captured = {}
    real_spawn = app.services.workflow_service.spawn_background_task

    def spy(coro, *, name):
        captured["name"] = name
        return real_spawn(coro, name=name)

    monkeypatch.setattr(app.services.workflow_service, "spawn_background_task", spy)

    await process_incoming_message(chat_id, "שלום")

    for t in list(background_tasks_module.pending_background_tasks()):
        await t

    assert captured["name"] == f"typing:{mask_chat_id(chat_id)}"
    assert "972501234567" not in captured["name"]


# --- PRO-168 review follow-up: REMINDER's sibling options must route alike ---


def test_every_reminder_keyword_is_a_pro_business_keyword():
    """PRO-168 regression guard: Pro.REMINDER advertises *סיימתי* and
    *עדיין עובד* as equal options, so both must route the same way -- always to
    pro_flow, even mid-CUSTOMER_MODE or under the AWAITING_PRO_APPROVAL soft
    hold. STILL_WORKING_COMMANDS was missing from PRO_BUSINESS_KEYWORDS until
    this fix: the finish keyword reached pro_flow while its sibling option was
    swallowed by the customer AI instead. Parses the *token* tokens straight
    out of the message so a future reword of REMINDER re-checks itself instead
    of pinning today's literal wording."""
    tokens = re.findall(r"\*(.+?)\*", Messages.Pro.REMINDER)
    assert (
        tokens
    ), f"Messages.Pro.REMINDER has no *token* to check: {Messages.Pro.REMINDER!r}"

    for token in tokens:
        assert token in app.services.workflow_service.PRO_BUSINESS_KEYWORDS, (
            f"Messages.Pro.REMINDER advertises {token!r} but it is missing from "
            f"PRO_BUSINESS_KEYWORDS -- its sibling option would route differently."
        )
