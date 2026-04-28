"""
Tests for the consent flow logic in workflow_service.py.
Covers: first contact, accept, decline, unclear response, re-ask, pro bypass.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.core.constants import UserStates
from app.core.messages import Messages
from app.services.workflow_service import process_incoming_message
import app.services.workflow_service


@pytest.fixture
def consent_mocks(monkeypatch, mock_db):
    """Setup mocks specific to consent flow testing."""
    mock_whatsapp = MagicMock()
    mock_whatsapp.send_message = AsyncMock()
    monkeypatch.setattr(app.services.workflow_service, "whatsapp", mock_whatsapp)

    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=UserStates.IDLE)
    mock_state.set_state = AsyncMock()
    mock_state.clear_state = AsyncMock()
    monkeypatch.setattr(app.services.workflow_service, "StateManager", mock_state)

    mock_has_consent = AsyncMock(return_value=None)
    monkeypatch.setattr(app.services.workflow_service, "has_consent", mock_has_consent)

    mock_record_consent = AsyncMock()
    monkeypatch.setattr(app.services.workflow_service, "record_consent", mock_record_consent)

    return mock_whatsapp, mock_state, mock_has_consent, mock_record_consent


@pytest.mark.asyncio
async def test_first_contact_sends_consent_request(consent_mocks):
    """New user with no consent record gets consent request."""
    mock_wa, mock_state, mock_has_consent, _ = consent_mocks
    mock_has_consent.return_value = None
    mock_state.get_state.return_value = UserStates.IDLE

    await process_incoming_message("972501111111@c.us", "שלום")

    mock_wa.send_message.assert_called_once_with("972501111111@c.us", Messages.Consent.REQUEST)
    mock_state.set_state.assert_called_with("972501111111@c.us", UserStates.AWAITING_CONSENT)


@pytest.mark.asyncio
async def test_accept_consent(consent_mocks):
    """User in AWAITING_CONSENT sends 'כן' -> consent accepted."""
    mock_wa, mock_state, mock_has_consent, mock_record = consent_mocks
    mock_state.get_state.return_value = UserStates.AWAITING_CONSENT
    mock_has_consent.return_value = None  # No record yet

    await process_incoming_message("972501111111@c.us", "כן")

    mock_record.assert_called_once_with("972501111111@c.us", accepted=True)
    mock_state.clear_state.assert_called_with("972501111111@c.us")
    mock_wa.send_message.assert_called_once_with("972501111111@c.us", Messages.Consent.ACCEPTED)


@pytest.mark.asyncio
async def test_accept_consent_alternative_keyword(consent_mocks):
    """User in AWAITING_CONSENT sends 'אישור' -> consent accepted."""
    mock_wa, mock_state, mock_has_consent, mock_record = consent_mocks
    mock_state.get_state.return_value = UserStates.AWAITING_CONSENT
    mock_has_consent.return_value = None

    await process_incoming_message("972501111111@c.us", "אישור")

    mock_record.assert_called_once_with("972501111111@c.us", accepted=True)
    mock_wa.send_message.assert_called_once_with("972501111111@c.us", Messages.Consent.ACCEPTED)


@pytest.mark.asyncio
async def test_decline_consent(consent_mocks):
    """User in AWAITING_CONSENT sends 'לא' -> consent declined."""
    mock_wa, mock_state, mock_has_consent, mock_record = consent_mocks
    mock_state.get_state.return_value = UserStates.AWAITING_CONSENT
    mock_has_consent.return_value = None

    await process_incoming_message("972501111111@c.us", "לא")

    mock_record.assert_called_once_with("972501111111@c.us", accepted=False)
    mock_state.clear_state.assert_called_with("972501111111@c.us")
    mock_wa.send_message.assert_called_once_with("972501111111@c.us", Messages.Consent.DECLINED)


@pytest.mark.asyncio
async def test_unclear_consent_response(consent_mocks):
    """User in AWAITING_CONSENT sends unclear text -> re-sends request."""
    mock_wa, mock_state, mock_has_consent, mock_record = consent_mocks
    mock_state.get_state.return_value = UserStates.AWAITING_CONSENT
    mock_has_consent.return_value = None

    await process_incoming_message("972501111111@c.us", "מה זה?")

    mock_record.assert_not_called()
    mock_wa.send_message.assert_called_once_with("972501111111@c.us", Messages.Consent.REQUEST)


@pytest.mark.asyncio
async def test_declined_user_contacts_again(consent_mocks):
    """User who previously declined -> re-sends consent request."""
    mock_wa, mock_state, mock_has_consent, _ = consent_mocks
    mock_state.get_state.return_value = UserStates.IDLE
    mock_has_consent.return_value = False

    await process_incoming_message("972501111111@c.us", "שלום שוב")

    mock_wa.send_message.assert_called_once_with("972501111111@c.us", Messages.Consent.REQUEST)
    mock_state.set_state.assert_called_with("972501111111@c.us", UserStates.AWAITING_CONSENT)


@pytest.mark.asyncio
async def test_consented_user_passes_through(consent_mocks, monkeypatch):
    """User with consent=True passes through to dispatcher."""
    mock_wa, mock_state, mock_has_consent, _ = consent_mocks
    mock_state.get_state.return_value = UserStates.IDLE
    mock_has_consent.return_value = True

    # Mock AI so dispatcher path doesn't fail
    mock_ai = MagicMock()
    from app.services.ai_engine_service import AIResponse, ExtractedData
    mock_ai.analyze_conversation = AsyncMock(return_value=AIResponse(
        reply_to_user="שלום! איך אפשר לעזור?",
        extracted_data=ExtractedData(city=None, issue=None, full_address=None, appointment_time=None),
        transcription=None, is_deal=False,
    ))
    monkeypatch.setattr(app.services.workflow_service, "ai", mock_ai)

    mock_lm = MagicMock()
    mock_lm.log_message = AsyncMock()
    mock_lm.get_chat_history = AsyncMock(return_value=[])
    monkeypatch.setattr(app.services.workflow_service, "lead_manager", mock_lm)

    await process_incoming_message("972501111111@c.us", "שלום")

    # Should have called AI (reached dispatcher), not sent consent request
    mock_ai.analyze_conversation.assert_called_once()
    # Final message is the AI response, not consent request
    assert any(
        call.args[1] == "שלום! איך אפשר לעזור?"
        for call in mock_wa.send_message.call_args_list
    )


@pytest.mark.asyncio
async def test_pro_skips_consent(consent_mocks, mock_db):
    """Known professional bypasses consent entirely."""
    mock_wa, mock_state, mock_has_consent, _ = consent_mocks
    mock_state.get_state.return_value = UserStates.PRO_MODE
    
    # Setup pro in DB
    await mock_db.users.insert_one({
        "phone_number": "972524828796",
        "role": "professional",
        "business_name": "Test Pro",
        "is_active": True
    })

    # Pro in PRO_MODE -> goes to pro handler, not consent
    await process_incoming_message("972524828796@c.us", "תפריט")


    # has_consent should NOT have been called
    mock_has_consent.assert_not_called()
    # Should get pro dashboard (fallback or direct match for 'תפריט')
    mock_wa.send_message.assert_called_once()
    msg = mock_wa.send_message.call_args.args[1]
    assert "שלום" in msg
    assert "דירוג" in msg

