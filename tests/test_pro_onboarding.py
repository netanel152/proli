"""
Tests for pro_onboarding_service.py: multi-step self-signup flow.
Covers: start, each step, cancel, validation, duplicate detection.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.core.constants import UserStates
from app.core.messages import Messages
from app.services.pro_onboarding_service import start_onboarding, handle_onboarding_step
import app.services.pro_onboarding_service


@pytest.fixture
def onboard_mocks(monkeypatch, mock_db):
    """Setup mocks for onboarding tests."""
    mock_wa = MagicMock()
    mock_wa.send_message = AsyncMock()

    mock_state = MagicMock()
    mock_state.set_state = AsyncMock()
    mock_state.clear_state = AsyncMock()
    mock_state.get_metadata = AsyncMock(return_value={})
    mock_state.set_metadata = AsyncMock()
    monkeypatch.setattr(app.services.pro_onboarding_service, "StateManager", mock_state)

    return mock_wa, mock_state, mock_db


# --- start_onboarding ---

@pytest.mark.asyncio
async def test_start_onboarding_new_user(onboard_mocks):
    mock_wa, mock_state, _ = onboard_mocks

    result = await start_onboarding("972501111111@c.us", mock_wa)

    assert result is True
    mock_state.set_state.assert_called_with("972501111111@c.us", UserStates.ONBOARDING_NAME)
    mock_wa.send_message.assert_called_once_with("972501111111@c.us", Messages.Onboarding.WELCOME)


@pytest.mark.asyncio
async def test_start_onboarding_already_registered(onboard_mocks):
    mock_wa, _, db = onboard_mocks
    await db.users.insert_one({
        "phone_number": "972501111111",
        "role": "professional",
        "is_active": True,
    })

    result = await start_onboarding("972501111111@c.us", mock_wa)

    assert result is False
    mock_wa.send_message.assert_called_once_with("972501111111@c.us", Messages.Onboarding.ALREADY_REGISTERED)


@pytest.mark.asyncio
async def test_start_onboarding_pending_approval(onboard_mocks):
    mock_wa, _, db = onboard_mocks
    await db.users.insert_one({
        "phone_number": "972504444444",
        "role": "professional",
        "pending_approval": True,
        "is_active": False,
    })

    result = await start_onboarding("972504444444@c.us", mock_wa)

    assert result is False
    mock_wa.send_message.assert_called_once_with("972504444444@c.us", Messages.Onboarding.PENDING_ALREADY)


# --- handle_onboarding_step: Name ---

@pytest.mark.asyncio
async def test_step_name_valid(onboard_mocks):
    mock_wa, mock_state, _ = onboard_mocks
    mock_state.get_metadata.return_value = {"onboarding": {}}

    await handle_onboarding_step("972501111111@c.us", "יוסי אינסטלציה", UserStates.ONBOARDING_NAME, mock_wa)

    # Should save name and advance to TYPE
    mock_state.set_metadata.assert_called()
    saved_data = mock_state.set_metadata.call_args.args[1]
    assert saved_data["onboarding"]["name"] == "יוסי אינסטלציה"
    mock_state.set_state.assert_called_with("972501111111@c.us", UserStates.ONBOARDING_TYPE)
    mock_wa.send_message.assert_called_once_with("972501111111@c.us", Messages.Onboarding.ASK_TYPE)


@pytest.mark.asyncio
async def test_step_name_too_short(onboard_mocks):
    mock_wa, mock_state, _ = onboard_mocks

    await handle_onboarding_step("972501111111@c.us", "א", UserStates.ONBOARDING_NAME, mock_wa)

    mock_state.set_state.assert_not_called()
    mock_wa.send_message.assert_called_once()
    assert "2" in mock_wa.send_message.call_args.args[1]  # "between 2 and 100"


# --- handle_onboarding_step: Type ---

@pytest.mark.asyncio
async def test_step_type_valid_number(onboard_mocks):
    mock_wa, mock_state, _ = onboard_mocks
    mock_state.get_metadata.return_value = {"onboarding": {"name": "Test"}}

    await handle_onboarding_step("972501111111@c.us", "1", UserStates.ONBOARDING_TYPE, mock_wa)

    saved_data = mock_state.set_metadata.call_args.args[1]
    assert saved_data["onboarding"]["type"] == "plumber"
    mock_state.set_state.assert_called_with("972501111111@c.us", UserStates.ONBOARDING_AREAS)


@pytest.mark.asyncio
async def test_step_type_valid_hebrew(onboard_mocks):
    mock_wa, mock_state, _ = onboard_mocks
    mock_state.get_metadata.return_value = {"onboarding": {"name": "Test"}}

    await handle_onboarding_step("972501111111@c.us", "חשמלאי", UserStates.ONBOARDING_TYPE, mock_wa)

    saved_data = mock_state.set_metadata.call_args.args[1]
    assert saved_data["onboarding"]["type"] == "electrician"


@pytest.mark.asyncio
async def test_step_type_invalid(onboard_mocks):
    mock_wa, mock_state, _ = onboard_mocks
    mock_state.get_metadata.return_value = {"onboarding": {"name": "Test"}}

    await handle_onboarding_step("972501111111@c.us", "99", UserStates.ONBOARDING_TYPE, mock_wa)

    mock_state.set_state.assert_not_called()
    mock_wa.send_message.assert_called_once_with("972501111111@c.us", Messages.Onboarding.INVALID_TYPE)


# --- handle_onboarding_step: Areas ---

@pytest.mark.asyncio
async def test_step_areas(onboard_mocks):
    mock_wa, mock_state, _ = onboard_mocks
    mock_state.get_metadata.return_value = {"onboarding": {"name": "Test", "type": "plumber"}}

    await handle_onboarding_step("972501111111@c.us", "תל אביב, חיפה, ירושלים", UserStates.ONBOARDING_AREAS, mock_wa)

    saved_data = mock_state.set_metadata.call_args.args[1]
    assert saved_data["onboarding"]["areas"] == ["תל אביב", "חיפה", "ירושלים"]
    mock_state.set_state.assert_called_with("972501111111@c.us", UserStates.ONBOARDING_PRICES)


@pytest.mark.asyncio
async def test_step_areas_empty(onboard_mocks):
    mock_wa, mock_state, _ = onboard_mocks
    mock_state.get_metadata.return_value = {"onboarding": {}}

    await handle_onboarding_step("972501111111@c.us", "  ", UserStates.ONBOARDING_AREAS, mock_wa)

    mock_state.set_state.assert_not_called()


# --- handle_onboarding_step: Prices ---

@pytest.mark.asyncio
async def test_step_prices_with_text(onboard_mocks):
    mock_wa, mock_state, _ = onboard_mocks
    mock_state.get_metadata.return_value = {
        "onboarding": {"name": "Test", "type": "plumber", "areas": ["תל אביב"]}
    }

    await handle_onboarding_step("972501111111@c.us", "תיקון 250₪", UserStates.ONBOARDING_PRICES, mock_wa)

    saved_data = mock_state.set_metadata.call_args.args[1]
    assert saved_data["onboarding"]["prices"] == "תיקון 250₪"
    mock_state.set_state.assert_called_with("972501111111@c.us", UserStates.ONBOARDING_CONFIRM)


@pytest.mark.asyncio
async def test_step_prices_skip(onboard_mocks):
    mock_wa, mock_state, _ = onboard_mocks
    mock_state.get_metadata.return_value = {
        "onboarding": {"name": "Test", "type": "plumber", "areas": ["תל אביב"]}
    }

    await handle_onboarding_step("972501111111@c.us", "דלג", UserStates.ONBOARDING_PRICES, mock_wa)

    saved_data = mock_state.set_metadata.call_args.args[1]
    assert saved_data["onboarding"]["prices"] == ""
    mock_state.set_state.assert_called_with("972501111111@c.us", UserStates.ONBOARDING_CONFIRM)


# --- handle_onboarding_step: Confirm ---

@pytest.mark.asyncio
async def test_step_confirm_yes(onboard_mocks):
    mock_wa, mock_state, db = onboard_mocks
    mock_state.get_metadata.return_value = {
        "onboarding": {
            "name": "יוסי הצנרת", "type": "plumber",
            "areas": ["תל אביב"], "prices": "250₪",
        }
    }

    await handle_onboarding_step("972505555555@c.us", "אשר", UserStates.ONBOARDING_CONFIRM, mock_wa)

    mock_state.clear_state.assert_called_with("972505555555@c.us")
    mock_wa.send_message.assert_called_once_with("972505555555@c.us", Messages.Onboarding.SUCCESS)

    # Verify pro created in DB
    pro = await db.users.find_one({"phone_number": "972505555555", "role": "professional"})
    assert pro is not None
    assert pro["business_name"] == "יוסי הצנרת"
    assert pro["is_active"] is False
    assert pro["pending_approval"] is True


@pytest.mark.asyncio
async def test_step_confirm_cancel(onboard_mocks):
    mock_wa, mock_state, _ = onboard_mocks
    mock_state.get_metadata.return_value = {"onboarding": {"name": "Test"}}

    await handle_onboarding_step("972501111111@c.us", "ביטול", UserStates.ONBOARDING_CONFIRM, mock_wa)

    mock_state.clear_state.assert_called()
    mock_wa.send_message.assert_called_once_with("972501111111@c.us", Messages.Onboarding.CANCELLED)


# --- Cancel at any step ---

@pytest.mark.asyncio
async def test_cancel_at_type_step(onboard_mocks):
    mock_wa, mock_state, _ = onboard_mocks

    await handle_onboarding_step("972501111111@c.us", "ביטול", UserStates.ONBOARDING_TYPE, mock_wa)

    mock_state.clear_state.assert_called_with("972501111111@c.us")
    mock_wa.send_message.assert_called_once_with("972501111111@c.us", Messages.Onboarding.CANCELLED)


@pytest.mark.asyncio
async def test_cancel_at_areas_step(onboard_mocks):
    mock_wa, mock_state, _ = onboard_mocks

    await handle_onboarding_step("972501111111@c.us", "cancel", UserStates.ONBOARDING_AREAS, mock_wa)

    mock_state.clear_state.assert_called()
    mock_wa.send_message.assert_called_once_with("972501111111@c.us", Messages.Onboarding.CANCELLED)
