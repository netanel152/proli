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
async def test_reset_menu_command(wf_mocks):
    mock_wa, mock_state, mock_ctx, _, _ = wf_mocks

    await process_incoming_message("972501111111@c.us", "תפריט")

    mock_state.clear_state.assert_called()
    mock_wa.send_message.assert_called_once_with("972501111111@c.us", Messages.System.RESET_SUCCESS)


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
    """User in AWAITING_ADDRESS sends valid address -> saves it."""
    mock_wa, mock_state, _, _, _ = wf_mocks
    mock_state.get_state.return_value = UserStates.AWAITING_ADDRESS

    lead_id = ObjectId()
    await mock_db.leads.insert_one({
        "_id": lead_id,
        "chat_id": "972501111111@c.us",
        "status": LeadStatus.NEW,
        "created_at": "2026-01-01",
    })

    await process_incoming_message("972501111111@c.us", "רחוב הרצל 15, תל אביב")

    mock_wa.send_message.assert_called_once_with("972501111111@c.us", Messages.Customer.ADDRESS_SAVED)
    mock_state.clear_state.assert_called()

    # Verify lead updated in DB
    updated = await mock_db.leads.find_one({"_id": lead_id})
    assert updated["full_address"] == "רחוב הרצל 15, תל אביב"


@pytest.mark.asyncio
async def test_awaiting_address_too_short(wf_mocks):
    """User in AWAITING_ADDRESS sends too-short text -> error."""
    mock_wa, mock_state, _, _, _ = wf_mocks
    mock_state.get_state.return_value = UserStates.AWAITING_ADDRESS

    await process_incoming_message("972501111111@c.us", "hi")

    mock_wa.send_message.assert_called_once_with("972501111111@c.us", Messages.Customer.ADDRESS_INVALID)


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
