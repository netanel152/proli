"""PRO-208: an admin offer that never reached the pro stays with the operator.

The panel tells the operator to phone the pro when an offer is not sent, or is
"sent" by a muted provider. The SOS healer did not know: 60 minutes later it
moved the lead to another pro and told the first one they had lost a job they
never saw.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId

import app.services.admin_flow as admin_flow
from app.core.constants import LeadStatus
from app.core.messages import Messages
from app.services import monitor_service


def _whatsapp(transmits=True):
    wa = MagicMock()
    wa.send_message = AsyncMock()
    wa.provider = SimpleNamespace(transmits=transmits)
    return wa


async def _waiting_lead(db):
    lead_id = ObjectId()
    await db.leads.insert_one(
        {
            "_id": lead_id,
            "status": LeadStatus.PENDING_ADMIN_REVIEW,
            "chat_id": "972500000555@c.us",
            "full_address": "הרצל 1, תל אביב",
            "issue_type": "נזילה",
        }
    )
    return lead_id


@pytest.fixture
def dana(monkeypatch, mock_db):
    monkeypatch.setattr(admin_flow, "leads_collection", mock_db.leads)
    monkeypatch.setattr(admin_flow, "users_collection", mock_db.users)
    pro = {"_id": ObjectId(), "business_name": "Dana", "phone_number": "972500000556"}
    return pro


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sent, transmits, undelivered",
    [(False, True, True), (True, False, True), (True, True, False)],
    ids=["not-sent", "muted", "delivered"],
)
async def test_an_undelivered_admin_offer_is_marked_on_the_lead(
    mock_db, monkeypatch, dana, sent, transmits, undelivered
):
    monkeypatch.setattr(admin_flow, "notify_pro_new_lead", AsyncMock(return_value=sent))
    lead_id = await _waiting_lead(mock_db)

    await admin_flow.assign_lead_to_pro(lead_id, dana, _whatsapp(transmits))

    lead = await mock_db.leads.find_one({"_id": lead_id})
    assert ("admin_offer_undelivered_at" in lead) is undelivered


@pytest.mark.asyncio
async def test_a_delivered_reoffer_clears_the_mark(mock_db, monkeypatch, dana):
    lead_id = await _waiting_lead(mock_db)
    monkeypatch.setattr(
        admin_flow, "notify_pro_new_lead", AsyncMock(return_value=False)
    )
    await admin_flow.assign_lead_to_pro(lead_id, dana, _whatsapp())
    await mock_db.leads.update_one(
        {"_id": lead_id}, {"$set": {"status": LeadStatus.PENDING_ADMIN_REVIEW}}
    )

    monkeypatch.setattr(admin_flow, "notify_pro_new_lead", AsyncMock(return_value=True))
    await admin_flow.assign_lead_to_pro(lead_id, dana, _whatsapp())

    assert "admin_offer_undelivered_at" not in await mock_db.leads.find_one(
        {"_id": lead_id}
    )


@pytest.mark.asyncio
async def test_the_real_healer_leaves_an_undelivered_admin_offer_alone(
    mock_db, monkeypatch, dana
):
    monkeypatch.setattr(
        admin_flow, "notify_pro_new_lead", AsyncMock(return_value=False)
    )
    lead_id = await _waiting_lead(mock_db)
    await admin_flow.assign_lead_to_pro(lead_id, dana, _whatsapp())
    # 61 minutes later, by the healer's clock.
    await mock_db.leads.update_one(
        {"_id": lead_id},
        {"$set": {"created_at": datetime.now(timezone.utc) - timedelta(minutes=61)}},
    )
    healer_wa = _whatsapp()
    monkeypatch.setattr(monitor_service, "whatsapp", healer_wa)
    rematch = AsyncMock(return_value={"_id": ObjectId(), "business_name": "Bob"})
    monkeypatch.setattr("app.services.matching_service.determine_best_pro", rematch)

    await monitor_service.check_and_reassign_stale_leads()

    lead = await mock_db.leads.find_one({"_id": lead_id})
    assert (lead["status"], lead["pro_id"]) == (LeadStatus.NEW, dana["_id"])
    assert lead.get("reassignment_count", 0) == 0
    rematch.assert_not_awaited()
    healer_wa.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_reassignment_never_tells_that_pro_they_lost_it(
    mock_db, monkeypatch, dana
):
    """Another path (the customer's "1", a reject) can still move such a lead;
    the pro who never got the offer is not told they lost it."""
    await mock_db.users.insert_one(dict(dana))
    lead_id = ObjectId()
    await mock_db.leads.insert_one(
        {
            "_id": lead_id,
            "status": LeadStatus.NEW,
            "pro_id": dana["_id"],
            "chat_id": "972500000555@c.us",
            "full_address": "הרצל 1, תל אביב",
            "issue_type": "נזילה",
            "admin_offer_undelivered_at": datetime.now(timezone.utc),
        }
    )
    wa = _whatsapp()
    monkeypatch.setattr(monitor_service, "whatsapp", wa)
    monkeypatch.setattr(monitor_service, "users_collection", mock_db.users)
    monkeypatch.setattr(monitor_service.StateManager, "set_state", AsyncMock())
    monkeypatch.setattr(
        monitor_service, "notify_pro_new_lead", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        "app.services.matching_service.determine_best_pro",
        AsyncMock(return_value={"_id": ObjectId(), "business_name": "Bob"}),
    )

    moved = await monitor_service.reassign_lead(
        await mock_db.leads.find_one({"_id": lead_id})
    )

    assert moved is True
    texts = [c.args[1] for c in wa.send_message.call_args_list]
    assert Messages.SOS.PRO_LOST_LEAD not in texts
    lead = await mock_db.leads.find_one({"_id": lead_id})
    assert "admin_offer_undelivered_at" not in lead
