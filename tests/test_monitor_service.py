"""Scheduled jobs in ``app/services/monitor_service.py`` that have no file of their
own. The reassignment, SLA-nudge and quality-watchdog jobs are covered in
tests/test_reassign_escalation.py, tests/test_approval_sla.py and
tests/test_whatsapp_quality.py.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.constants import LeadStatus, UserStates, WorkerConstants
from app.core.messages import Messages
from app.services import monitor_service
from app.services.state_manager_service import StateManager

CHAT_ID = "972500000077@c.us"


@pytest.mark.asyncio
async def test_sla_deflection_is_sent_untemplated_so_a_closed_window_is_no_page(
    mock_db, monkeypatch
):
    """PRO-232: the deflection is a reply to a customer who stopped writing, with
    no template by design. Marked so the facade drops it quietly instead of
    paging the operator for a missing template."""
    monkeypatch.setattr(monitor_service, "leads_collection", mock_db.leads)
    whatsapp = MagicMock()
    whatsapp.send_message = AsyncMock()
    monkeypatch.setattr(monitor_service, "whatsapp", whatsapp)
    await mock_db.leads.delete_many({})
    lead_id = (
        await mock_db.leads.insert_one(
            {
                "chat_id": CHAT_ID,
                "status": LeadStatus.BOOKED,
                "is_paused": True,
                "paused_at": datetime.now(timezone.utc)
                - timedelta(seconds=WorkerConstants.PAUSE_TTL_SECONDS + 60),
            }
        )
    ).inserted_id
    await StateManager.set_state(
        CHAT_ID,
        UserStates.PAUSED_FOR_HUMAN,
        ttl=WorkerConstants.PAUSE_TTL_SECONDS,
    )

    await monitor_service.check_sla_deflection()

    whatsapp.send_message.assert_awaited_once()
    call = whatsapp.send_message.await_args
    assert call.args == (CHAT_ID, Messages.Customer.SLA_DEFLECTION_MESSAGE)
    assert call.kwargs["untemplated"]
    assert "template_key" not in call.kwargs
    assert (await mock_db.leads.find_one({"_id": lead_id}))["sla_deflected"] is True
