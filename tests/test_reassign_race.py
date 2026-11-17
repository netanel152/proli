"""PRO-281: two reassignments from the same stale read — only one wins.

A NEW lead is NEW before and after a NEW→NEW reassignment, so a guard on status
alone let a second caller holding a stale copy (the SOS healer's batch) reassign
the lead again: two pros offered, the customer told two names, a count lost.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId

from app.core.constants import LeadStatus
from app.services import monitor_service


@pytest.fixture
def world(monkeypatch, mock_db):
    wa = MagicMock()
    wa.send_message = AsyncMock()
    monkeypatch.setattr(monitor_service, "whatsapp", wa)
    monkeypatch.setattr(monitor_service, "users_collection", mock_db.users)
    monkeypatch.setattr(monitor_service.StateManager, "set_state", AsyncMock())
    monkeypatch.setattr(monitor_service.StateManager, "clear_state", AsyncMock())
    monkeypatch.setattr(monitor_service.ContextManager, "clear_context", AsyncMock())

    offered = []

    async def notify(lead, pro, whatsapp):
        offered.append(pro["_id"])
        return True

    monkeypatch.setattr(monitor_service, "notify_pro_new_lead", notify)

    pros = iter([ObjectId(), ObjectId(), ObjectId()])

    async def best_pro(**kwargs):
        return {"_id": next(pros), "business_name": "Pro", "phone_number": "9725"}

    monkeypatch.setattr(
        "app.services.matching_service.determine_best_pro",
        AsyncMock(side_effect=best_pro),
    )
    return wa, offered, mock_db


async def _stale_new_lead(db, minutes_old=90):
    doc = {
        "chat_id": "972500000123@c.us",
        "status": LeadStatus.NEW,
        "pro_id": ObjectId(),
        "full_address": "הרצל 1, תל אביב",
        "issue_type": "נזילה",
        "created_at": datetime.now(timezone.utc) - timedelta(minutes=minutes_old),
    }
    await db.leads.insert_one(doc)
    return doc


@pytest.mark.asyncio
async def test_two_reassignments_from_one_stale_read_offer_one_pro(world):
    wa, offered, db = world
    stale = await _stale_new_lead(db)

    first = await monitor_service.reassign_lead(dict(stale))
    second = await monitor_service.reassign_lead(dict(stale))

    assert (first, second) == (True, False)
    assert len(offered) == 1, "only one pro is offered the lead"
    lead = await db.leads.find_one({"_id": stale["_id"]})
    assert lead["pro_id"] == offered[0]
    assert lead["reassignment_count"] == 1
    assert lead["reassigned_from"] == stale["pro_id"]
    # The loser told nobody anything: one "finding another pro", one name.
    assert len(wa.send_message.call_args_list) == len(
        [c for c in wa.send_message.call_args_list if c.args[0] == stale["chat_id"]]
    )
    assert len(wa.send_message.call_args_list) == 2


@pytest.mark.asyncio
async def test_the_healer_skips_a_lead_refreshed_since_its_read(world, monkeypatch):
    """The healer's batch read holds a lead that the customer's "1" reassigns
    before the healer reaches it: the fresh lead is not stale any more."""
    wa, offered, db = world
    stale = await _stale_new_lead(db)

    real_reassign = monitor_service.reassign_lead
    calls = []

    async def customer_first(lead, **kwargs):
        if not calls:
            calls.append("customer")
            await real_reassign(dict(lead))  # the customer's "1"
        calls.append("healer")
        return await real_reassign(lead, **kwargs)

    monkeypatch.setattr(monitor_service, "reassign_lead", customer_first)

    await monitor_service.check_and_reassign_stale_leads()

    assert calls == ["customer", "healer"]
    assert len(offered) == 1
    lead = await db.leads.find_one({"_id": stale["_id"]})
    assert lead["reassignment_count"] == 1
