"""Scheduled jobs in ``app/services/monitor_service.py`` that have no file of their
own. The reassignment, SLA-nudge and quality-watchdog jobs are covered in
tests/test_reassign_escalation.py, tests/test_approval_sla.py and
tests/test_whatsapp_quality.py.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

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


# ------------------------------------------------- SLA deflection: who it reaches


def _lapsed():
    return datetime.now(timezone.utc) - timedelta(
        seconds=WorkerConstants.PAUSE_TTL_SECONDS + 60
    )


@pytest_asyncio.fixture
async def sla_world(mock_db, monkeypatch):
    monkeypatch.setattr(monitor_service, "leads_collection", mock_db.leads)
    await mock_db.leads.delete_many({})
    whatsapp = MagicMock()
    whatsapp.send_message = AsyncMock()
    monkeypatch.setattr(monitor_service, "whatsapp", whatsapp)
    return mock_db.leads, whatsapp


async def _paused_lead(leads, status=LeadStatus.BOOKED, paused_at=None, **extra):
    return (
        await leads.insert_one(
            {
                "chat_id": CHAT_ID,
                "status": status,
                "is_paused": True,
                "paused_at": paused_at or _lapsed(),
                **extra,
            }
        )
    ).inserted_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", [LeadStatus.CONTACTED, LeadStatus.NEW, LeadStatus.BOOKED]
)
async def test_sla_deflection_fires_once_the_pause_state_has_expired(sla_world, status):
    """The state key carries the same TTL and is written before paused_at, so a
    lead that qualifies normally has no state left. That is a lapsed pause,
    not a resumed one: the customer is told and the lead marked."""
    leads, whatsapp = sla_world
    lead_id = await _paused_lead(leads, status=status)

    await monitor_service.check_sla_deflection()

    whatsapp.send_message.assert_awaited_once()
    assert whatsapp.send_message.await_args.args == (
        CHAT_ID,
        Messages.Customer.SLA_DEFLECTION_MESSAGE,
    )
    stored = await leads.find_one({"_id": lead_id})
    assert stored["sla_deflected"] is True
    assert stored["is_paused"] is False
    assert stored["sla_deflected_at"]


@pytest.mark.asyncio
async def test_sla_deflection_clears_only_the_flag_for_a_customer_in_another_flow(
    sla_world,
):
    leads, whatsapp = sla_world
    lead_id = await _paused_lead(leads)
    await StateManager.set_state(CHAT_ID, UserStates.AWAITING_ADDRESS, ttl=3600)

    await monitor_service.check_sla_deflection()

    whatsapp.send_message.assert_not_awaited()
    stored = await leads.find_one({"_id": lead_id})
    assert stored["is_paused"] is False
    assert "sla_deflected" not in stored
    assert await StateManager.get_state(CHAT_ID) == UserStates.AWAITING_ADDRESS


@pytest.mark.asyncio
async def test_sla_deflection_leaves_a_chat_whose_younger_pause_is_live(sla_world):
    leads, whatsapp = sla_world
    old_id = await _paused_lead(leads)
    young_id = await _paused_lead(leads, paused_at=datetime.now(timezone.utc))
    await StateManager.set_state(
        CHAT_ID, UserStates.PAUSED_FOR_HUMAN, ttl=WorkerConstants.PAUSE_TTL_SECONDS
    )

    await monitor_service.check_sla_deflection()

    whatsapp.send_message.assert_not_awaited()
    assert await StateManager.get_state(CHAT_ID) == UserStates.PAUSED_FOR_HUMAN
    for lead_id in (old_id, young_id):
        assert (await leads.find_one({"_id": lead_id}))["is_paused"] is True


@pytest.mark.asyncio
async def test_sla_deflection_sends_once_per_chat_however_many_leads_lapsed(
    sla_world,
):
    leads, whatsapp = sla_world
    ids = [
        await _paused_lead(leads, status=LeadStatus.CONTACTED),
        await _paused_lead(leads, paused_at=_lapsed() - timedelta(minutes=10)),
    ]

    await monitor_service.check_sla_deflection()

    whatsapp.send_message.assert_awaited_once()
    for lead_id in ids:
        assert (await leads.find_one({"_id": lead_id}))["sla_deflected"] is True


@pytest.mark.asyncio
async def test_two_concurrent_sla_runs_send_one_deflection(sla_world):
    """Two replicas on the same tick: the conditional claim lets one win."""
    import asyncio

    leads, whatsapp = sla_world
    await _paused_lead(leads)

    await asyncio.gather(
        monitor_service.check_sla_deflection(),
        monitor_service.check_sla_deflection(),
    )
    await monitor_service.check_sla_deflection()

    whatsapp.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_sla_deflection_skips_a_pause_refreshed_after_the_read(
    sla_world, monkeypatch
):
    """The customer wrote again between the job's read and its claim, moving
    paused_at forward: the claim no longer matches, so nothing is sent."""
    leads, whatsapp = sla_world
    lead_id = await _paused_lead(leads)
    real_get_state = StateManager.get_state

    async def get_state_after_a_refresh(chat_id):
        await leads.update_one(
            {"_id": lead_id}, {"$set": {"paused_at": datetime.now(timezone.utc)}}
        )
        return await real_get_state(chat_id)

    monkeypatch.setattr(
        monitor_service.StateManager, "get_state", get_state_after_a_refresh
    )

    await monitor_service.check_sla_deflection()

    whatsapp.send_message.assert_not_awaited()
    assert (await leads.find_one({"_id": lead_id}))["is_paused"] is True
