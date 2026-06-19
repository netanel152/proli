"""
Tests for the AWAITING_LOYALTY_CONFIRMATION branch in workflow_service.

Returning customers are offered their previous professional. When in this state
a reply of 1/כן re-assigns the active lead to the past pro; 2/לא declines and
falls back to normal matching; anything else re-prompts.

Driven end-to-end through process_incoming_message so the dispatch ordering is
exercised, following the test_workflow_orchestrator.py mocking style.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from bson import ObjectId

import app.services.workflow_service as workflow_service
from app.services.workflow_service import process_incoming_message
from app.core.constants import UserStates, LeadStatus


@pytest.fixture
def loyalty_mocks(monkeypatch, mock_db):
    mock_wa = MagicMock()
    mock_wa.send_message = AsyncMock()
    mock_wa.send_chat_state_typing = AsyncMock()
    monkeypatch.setattr(workflow_service, "whatsapp", mock_wa)

    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(
        return_value=UserStates.AWAITING_LOYALTY_CONFIRMATION
    )
    mock_state.set_state = AsyncMock()
    mock_state.clear_state = AsyncMock()
    mock_state.get_metadata = AsyncMock(return_value={})
    monkeypatch.setattr(workflow_service, "StateManager", mock_state)

    mock_lm = MagicMock()
    mock_lm.log_message = AsyncMock()
    monkeypatch.setattr(workflow_service, "lead_manager", mock_lm)

    monkeypatch.setattr(workflow_service, "has_consent", AsyncMock(return_value=True))

    return mock_wa, mock_state, mock_lm, mock_db


def _sent_text(mock_wa):
    return " ".join(str(c.args[1]) for c in mock_wa.send_message.call_args_list)


@pytest.mark.asyncio
async def test_loyalty_accept_assigns_lead_to_past_pro(loyalty_mocks):
    mock_wa, mock_state, _, db = loyalty_mocks
    past_pro_id = ObjectId()
    lead_id = ObjectId()
    await db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": "customer@c.us",
            "status": LeadStatus.NEW,
        }
    )
    mock_state.get_metadata.return_value = {"past_pro_id": str(past_pro_id)}

    await process_incoming_message("customer@c.us", "1")

    updated = await db.leads.find_one({"_id": lead_id})
    assert updated["pro_id"] == past_pro_id
    mock_state.set_state.assert_awaited_with("customer@c.us", UserStates.IDLE)
    assert "מעולה" in _sent_text(mock_wa)


@pytest.mark.asyncio
async def test_loyalty_decline_falls_back_to_matching(loyalty_mocks):
    mock_wa, mock_state, _, db = loyalty_mocks
    lead_id = ObjectId()
    await db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": "customer@c.us",
            "status": LeadStatus.NEW,
        }
    )
    mock_state.get_metadata.return_value = {"past_pro_id": str(ObjectId())}

    await process_incoming_message("customer@c.us", "2")

    updated = await db.leads.find_one({"_id": lead_id})
    assert "pro_id" not in updated  # lead not assigned to past pro
    mock_state.set_state.assert_awaited_with("customer@c.us", UserStates.IDLE)
    assert "הפנוי" in _sent_text(mock_wa)


@pytest.mark.asyncio
async def test_loyalty_unclear_reply_reprompts_and_keeps_state(loyalty_mocks):
    mock_wa, mock_state, _, db = loyalty_mocks
    mock_state.get_metadata.return_value = {"past_pro_id": str(ObjectId())}

    await process_incoming_message("customer@c.us", "אולי")

    assert "1 (כן)" in _sent_text(mock_wa)
    # State not advanced to IDLE
    for call in mock_state.set_state.call_args_list:
        assert call.args[1] != UserStates.IDLE


@pytest.mark.asyncio
async def test_loyalty_accept_without_active_lead_still_acks(loyalty_mocks):
    """Edge: reply 1 but no active lead / no past_pro_id → ack sent, no crash."""
    mock_wa, mock_state, _, db = loyalty_mocks
    await db.leads.delete_many({})
    mock_state.get_metadata.return_value = {}  # no past_pro_id

    await process_incoming_message("customer@c.us", "כן")

    mock_state.set_state.assert_awaited_with("customer@c.us", UserStates.IDLE)
    assert "מעולה" in _sent_text(mock_wa)
