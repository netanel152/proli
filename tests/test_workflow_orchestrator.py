"""
Tests for workflow_service.process_incoming_message routing branches.
Covers: reset, pro auto-detect, address collection, onboarding, deal finalization.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from bson import ObjectId
from app.core.constants import UserStates, LeadStatus
from app.core.messages import Messages
from app.services.workflow_service import process_incoming_message
from app.services.ai_engine_service import AIResponse, ExtractedData
import app.services.workflow_service


@pytest.fixture
def wf_mocks(monkeypatch, mock_db):
    """Common mocks for workflow orchestrator tests."""
    mock_wa = MagicMock()
    mock_wa.send_message = AsyncMock()
    mock_wa.send_location_link = AsyncMock()
    monkeypatch.setattr(app.services.workflow_service, "whatsapp", mock_wa)

    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=UserStates.IDLE)
    mock_state.set_state = AsyncMock()
    mock_state.clear_state = AsyncMock()
    monkeypatch.setattr(app.services.workflow_service, "StateManager", mock_state)

    mock_ctx = MagicMock()
    mock_ctx.clear_context = AsyncMock()
    monkeypatch.setattr(app.services.workflow_service, "ContextManager", mock_ctx)

    # Default: consent OK
    monkeypatch.setattr(app.services.workflow_service, "has_consent", AsyncMock(return_value=True))

    mock_ai = MagicMock()
    mock_ai.analyze_conversation = AsyncMock(return_value=AIResponse(
        reply_to_user="AI response",
        extracted_data=ExtractedData(city=None, issue=None, full_address=None, appointment_time=None),
        transcription=None, is_deal=False,
    ))
    mock_ai.detect_service_intent = AsyncMock(return_value=False)
    monkeypatch.setattr(app.services.workflow_service, "ai", mock_ai)

    mock_lm = MagicMock()
    mock_lm.log_message = AsyncMock()
    mock_lm.get_chat_history = AsyncMock(return_value=[])
    mock_lm.create_lead_from_dict = AsyncMock(return_value={"_id": ObjectId(), "full_address": "Test", "issue_type": "Leak", "appointment_time": "10:00", "chat_id": "user@c.us"})
    monkeypatch.setattr(app.services.workflow_service, "lead_manager", mock_lm)

    return mock_wa, mock_state, mock_ctx, mock_ai, mock_lm


# --- Reset Commands ---

@pytest.mark.asyncio
async def test_reset_command_clears_state(wf_mocks):
    mock_wa, mock_state, mock_ctx, _, _ = wf_mocks

    await process_incoming_message("972501111111@c.us", "התחלה")

    mock_state.clear_state.assert_called_with("972501111111@c.us")
    mock_ctx.clear_context.assert_called_with("972501111111@c.us")
    mock_wa.send_message.assert_called_once_with("972501111111@c.us", Messages.System.RESET_SUCCESS)


@pytest.mark.asyncio
async def test_help_command_sends_help_info_without_reset(wf_mocks):
    """תפריט is now a HELP command — must send HELP_INFO and leave state intact."""
    mock_wa, mock_state, mock_ctx, _, _ = wf_mocks

    await process_incoming_message("972501111111@c.us", "תפריט")

    mock_wa.send_message.assert_called_once_with("972501111111@c.us", Messages.Customer.HELP_INFO)
    mock_state.clear_state.assert_not_called()
    mock_ctx.clear_context.assert_not_called()


@pytest.mark.asyncio
async def test_reset_skipped_for_pro_mode(wf_mocks):
    """Pro in PRO_MODE sends reset keyword -> goes to pro handler, not reset."""
    mock_wa, mock_state, _, _, _ = wf_mocks
    mock_state.get_state.return_value = UserStates.PRO_MODE

    await process_incoming_message("972524828796@c.us", "עזרה")

    # Should NOT send RESET_SUCCESS
    for call in mock_wa.send_message.call_args_list:
        assert call.args[1] != Messages.System.RESET_SUCCESS


# --- Pro Auto-Detect ---

@pytest.mark.asyncio
async def test_pro_auto_detect_active(wf_mocks, mock_db):
    """Active pro auto-detected on first message -> PRO_MODE."""
    mock_wa, mock_state, _, _, _ = wf_mocks

    await mock_db.users.insert_one({
        "phone_number": "972509999999",
        "role": "professional",
        "is_active": True,
        "business_name": "Test Pro",
    })

    await process_incoming_message("972509999999@c.us", "שלום")

    mock_state.set_state.assert_any_call("972509999999@c.us", UserStates.PRO_MODE)


@pytest.mark.asyncio
async def test_pending_pro_not_auto_detected(wf_mocks, mock_db):
    """Pending pro (is_active=False) not auto-detected -> customer flow."""
    mock_wa, mock_state, _, mock_ai, _ = wf_mocks

    await mock_db.users.insert_one({
        "phone_number": "972503333333",
        "role": "professional",
        "is_active": False,
        "pending_approval": True,
    })

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
    mock_ai.analyze_conversation = AsyncMock(return_value=AIResponse(
        reply_to_user="",
        extracted_data=ExtractedData(
            city="תל אביב", issue="נזילה",
            street="הרצל", street_number="15", floor="2", apartment="4",
            appointment_time=None,
        ),
        transcription=None, is_deal=False,
    ))

    lead_id = ObjectId()
    await mock_db.leads.insert_one({
        "_id": lead_id,
        "chat_id": "972501111111@c.us",
        "status": LeadStatus.NEW,
        "created_at": "2026-01-01",
    })

    await process_incoming_message("972501111111@c.us", "הרצל 15, תל אביב קומה 2 דירה 4")

    mock_wa.send_message.assert_called_once_with("972501111111@c.us", Messages.Customer.ADDRESS_SAVED)
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

    mock_wa.send_message.assert_called_once_with("972501111111@c.us", Messages.Customer.ADDRESS_INVALID)


@pytest.mark.asyncio
@pytest.mark.parametrize("idx,cancel_text", list(enumerate(["בטל", "עזוב לא משנה", "טעות", "cancel", "nevermind"])))
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
    await mock_db.leads.insert_one({
        "_id": lead_id,
        "chat_id": chat_id,
        "status": LeadStatus.NEW,
        "street": "הרצל",
        "created_at": "2026-01-01",
    })

    await process_incoming_message(chat_id, cancel_text)

    mock_wa.send_message.assert_called_once_with(chat_id, Messages.Customer.REQUEST_CANCELLED)
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

    mock_wa.send_message.assert_called_once_with(chat_id, Messages.Customer.REQUEST_CANCELLED)
    mock_state.clear_state.assert_called_with(chat_id)
    mock_ctx.clear_context.assert_called_with(chat_id)
    mock_ai.analyze_conversation.assert_not_called()


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

    mock_wa.send_message.assert_any_call("972501111111@c.us", Messages.Errors.AI_OVERLOAD)


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
        extracted_data=ExtractedData(city="Tel Aviv", issue="Leak", full_address="Herzl 10", appointment_time="10:00"),
        transcription=None, is_deal=False,
    )
    pro_resp = AIResponse(
        reply_to_user="[DEAL: 10:00 | Herzl 10 | Leak]",
        extracted_data=ExtractedData(city="Tel Aviv", issue="Leak", full_address="Herzl 10", appointment_time="10:00"),
        transcription=None, is_deal=True,
    )
    mock_ai.analyze_conversation.side_effect = [dispatcher_resp, pro_resp]

    monkeypatch.setattr(app.services.workflow_service, "determine_best_pro", AsyncMock(return_value=pro_doc))

    mock_lm.create_lead_from_dict.return_value = {
        "_id": ObjectId(), "full_address": "Herzl 10", "issue_type": "Leak",
        "appointment_time": "10:00", "chat_id": "972501111111@c.us",
    }

    await process_incoming_message("972501111111@c.us", "יש לי נזילה ברחוב הרצל 10 בשעה 10")

    # Pro should have been notified
    pro_calls = [c for c in mock_wa.send_message.call_args_list if c.args[0] == "972500000000@c.us"]
    assert len(pro_calls) >= 1


@pytest.mark.asyncio
async def test_no_pro_found_dispatcher_response_only(wf_mocks, monkeypatch):
    """When city+issue extracted but no pro found -> only dispatcher response sent."""
    mock_wa, _, _, mock_ai, _ = wf_mocks

    resp = AIResponse(
        reply_to_user="לא מצאתי בעל מקצוע, אבל אני מחפש",
        extracted_data=ExtractedData(city="Eilat", issue="Leak", full_address=None, appointment_time=None),
        transcription=None, is_deal=False,
    )
    mock_ai.analyze_conversation.return_value = resp
    monkeypatch.setattr(app.services.workflow_service, "determine_best_pro", AsyncMock(return_value=None))

    await process_incoming_message("972501111111@c.us", "נזילה באילת")

    # AI called only once (dispatcher, no pro phase)
    assert mock_ai.analyze_conversation.call_count == 1
    mock_wa.send_message.assert_any_call("972501111111@c.us", "לא מצאתי בעל מקצוע, אבל אני מחפש")


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
    mock_wa.send_message.assert_any_call("972501111111@c.us", Messages.Customer.STILL_WAITING)


@pytest.mark.asyncio
async def test_paused_for_human_bypasses_ai(wf_mocks):
    """When bot is paused, messages are logged but AI is not invoked."""
    mock_wa, mock_state, _, mock_ai, mock_lm = wf_mocks
    mock_state.get_state = AsyncMock(return_value=UserStates.PAUSED_FOR_HUMAN)

    await process_incoming_message("972501111111@c.us", "I need help")

    # AI should NOT be called
    mock_ai.analyze_conversation.assert_not_called()
    # Message should be logged silently
    mock_lm.log_message.assert_called_once_with("972501111111@c.us", "user", "I need help")
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
        ttl=WorkerConstants.PAUSE_TTL_SECONDS
    )

    # Customer gets bot paused message
    mock_wa.send_message.assert_any_call("972501111111@c.us", Messages.Customer.BOT_PAUSED_BY_CUSTOMER)


# --- Zero-Touch Intent Confirmation Tests ---

@pytest.mark.asyncio
async def test_intent_confirmation_yes_sets_customer_mode(wf_mocks, mock_db):
    """State=AWAITING_INTENT_CONFIRMATION, reply '1' -> CUSTOMER_MODE set, context cleared."""
    mock_wa, mock_state, mock_ctx, mock_ai, _ = wf_mocks
    mock_state.get_state = AsyncMock(return_value=UserStates.AWAITING_INTENT_CONFIRMATION)

    # Insert a pro in DB so find_one won't fail in consent check
    pro_phone = "972501111111"
    await mock_db.users.insert_one({"phone_number": pro_phone, "role": "professional"})

    await process_incoming_message(f"{pro_phone}@c.us", "1")

    mock_state.set_state.assert_called_with(f"{pro_phone}@c.us", UserStates.CUSTOMER_MODE)
    mock_ctx.clear_context.assert_called_once()
    mock_wa.send_message.assert_called_with(f"{pro_phone}@c.us", Messages.Pro.SWITCHED_TO_CUSTOMER)


@pytest.mark.asyncio
async def test_intent_confirmation_no_clears_state(wf_mocks, mock_db):
    """State=AWAITING_INTENT_CONFIRMATION, reply '2' -> state cleared, SWITCH_CANCELLED sent."""
    mock_wa, mock_state, _, _, _ = wf_mocks
    mock_state.get_state = AsyncMock(return_value=UserStates.AWAITING_INTENT_CONFIRMATION)

    pro_phone = "972501111112"
    await mock_db.users.insert_one({"phone_number": pro_phone, "role": "professional"})

    await process_incoming_message(f"{pro_phone}@c.us", "2")

    mock_state.clear_state.assert_called_once_with(f"{pro_phone}@c.us")
    mock_wa.send_message.assert_called_with(f"{pro_phone}@c.us", Messages.Pro.SWITCH_CANCELLED)


@pytest.mark.asyncio
async def test_intent_confirmation_other_falls_through(wf_mocks, mock_db):
    """State=AWAITING_INTENT_CONFIRMATION, other text -> state cleared, falls through to routing."""
    mock_wa, mock_state, _, mock_ai, mock_lm = wf_mocks

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
    """Pro in CUSTOMER_MODE types 'אשר' -> snapped back to PRO_MODE, pro_flow called."""
    mock_wa, mock_state, _, mock_ai, _ = wf_mocks
    mock_state.get_state = AsyncMock(return_value=UserStates.CUSTOMER_MODE)

    pro_phone = "972501111114"
    await mock_db.users.insert_one({
        "phone_number": pro_phone,
        "role": "professional",
        "is_active": True,
    })

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
    await mock_db.leads.insert_one({
        "chat_id": chat_id,
        "status": LeadStatus.PENDING_ADMIN_REVIEW,
        "issue_type": "Leak",
        "full_address": "Unknown Address",
        "created_at": datetime.now(timezone.utc),
    })

    await process_incoming_message(chat_id, "שלום, אני עדיין מחכה")

    # 1. No new lead must be created
    mock_lm.create_lead_from_dict.assert_not_called()
    # 2. Dispatcher must not be invoked
    mock_ai.analyze_conversation.assert_not_called()
    # 3. Customer gets the STILL_PENDING_REVIEW ack
    mock_wa.send_message.assert_any_call(chat_id, Messages.Customer.STILL_PENDING_REVIEW)
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
    await mock_db.leads.insert_one({
        "chat_id": chat_id,
        "status": LeadStatus.PENDING_ADMIN_REVIEW,
        "issue_type": "Leak",
        "full_address": "Tel Aviv",
        "created_at": datetime.now(timezone.utc) - timedelta(hours=2),
        "last_pending_ack_at": datetime.now(timezone.utc) - timedelta(minutes=5),
    })

    await process_incoming_message(chat_id, "היי, יש עדכון?")

    # No new lead, no dispatcher, no ack resend
    mock_lm.create_lead_from_dict.assert_not_called()
    mock_ai.analyze_conversation.assert_not_called()
    for call in mock_wa.send_message.call_args_list:
        assert call.args[1] != Messages.Customer.STILL_PENDING_REVIEW, (
            "Ack was re-sent within the 30-minute throttle window"
        )
    # Message is still logged
    mock_lm.log_message.assert_any_call(chat_id, "user", "היי, יש עדכון?")
