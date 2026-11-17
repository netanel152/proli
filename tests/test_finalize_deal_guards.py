"""PRO-271: `_finalize_deal` must not overwrite a lead that changed during the turn.

The turn reads the lead, spends seconds in the AI call, then finalizes. A pro
approving, the healer reassigning, or the fast path reaching an already-offered
NEW lead must not end with the lead written back to NEW under the turn's pro,
re-offered, and its approval clock restarted.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId

import app.services.workflow_service as wf
from app.core.constants import LeadStatus, UserStates
from app.services.ai_engine_service import AIResponse, ExtractedData

CUSTOMER = "972501112222@c.us"
PRO_A_PHONE = "972500000001"
PRO_B_PHONE = "972500000002"


@pytest.fixture
def mocks(monkeypatch, mock_db):
    wa = MagicMock()
    wa.send_message = AsyncMock()
    wa.send_location_link = AsyncMock()
    wa.indicate_typing = AsyncMock()
    monkeypatch.setattr(wf, "whatsapp", wa)

    state = MagicMock()
    state.get_state = AsyncMock(return_value=UserStates.IDLE)
    state.set_state = AsyncMock()
    state.clear_state = AsyncMock()
    state.get_metadata = AsyncMock(return_value={})
    state.set_metadata = AsyncMock()
    monkeypatch.setattr(wf, "StateManager", state)

    ctx = MagicMock()
    ctx.clear_context = AsyncMock()
    monkeypatch.setattr(wf, "ContextManager", ctx)
    monkeypatch.setattr(wf, "has_consent", AsyncMock(return_value=True))

    ai = MagicMock()
    ai.detect_service_intent = AsyncMock(return_value=False)
    monkeypatch.setattr(wf, "ai", ai)

    lm = MagicMock()
    lm.log_message = AsyncMock()
    lm.get_chat_history = AsyncMock(return_value=[])
    monkeypatch.setattr(wf, "lead_manager", lm)
    return wa, state, ai, mock_db


def _closing_reply() -> AIResponse:
    return AIResponse(
        reply_to_user="מעולה, רשמתי. הפרטים עוברים לאישור.",
        extracted_data=ExtractedData(
            city="תל אביב",
            issue="נזילה",
            street="הרצל",
            street_number="10",
            floor="2",
            apartment="4",
            appointment_time="מחר 10:00",
        ),
        transcription=None,
        is_deal=True,
    )


async def _cast(db, *, pro_a_active=True):
    pro_a = {
        "_id": ObjectId(),
        "business_name": "Pro A",
        "phone_number": PRO_A_PHONE,
        "is_active": pro_a_active,
    }
    pro_b = {
        "_id": ObjectId(),
        "business_name": "Pro B",
        "phone_number": PRO_B_PHONE,
        "is_active": True,
    }
    await db.users.insert_many([pro_a, pro_b])
    return pro_a, pro_b


async def _lead(db, status, pro_id, **extra):
    doc = {
        "_id": ObjectId(),
        "chat_id": CUSTOMER,
        "status": status,
        "pro_id": pro_id,
        "issue_type": "נזילה",
        "created_at": datetime.now(timezone.utc),
        **extra,
    }
    await db.leads.insert_one(doc)
    return doc["_id"]


def _sent_to(wa, phone):
    return [c for c in wa.send_message.call_args_list if c.args[0] == f"{phone}@c.us"]


async def _finalize(pro, lead_id):
    await wf._finalize_deal(
        CUSTOMER, pro, _closing_reply(), "תל אביב", "נזילה", None, lead_id
    )


@pytest.mark.asyncio
async def test_a_contacted_lead_is_finalized(mocks):
    """The normal first match still goes through."""
    wa, state, _, db = mocks
    pro_a, _ = await _cast(db)
    lead_id = await _lead(db, LeadStatus.CONTACTED, pro_a["_id"])

    await _finalize(pro_a, lead_id)

    lead = await db.leads.find_one({"_id": lead_id})
    assert lead["status"] == LeadStatus.NEW
    assert lead["pro_id"] == pro_a["_id"]
    assert _sent_to(wa, PRO_A_PHONE), "the pro gets the offer"


@pytest.mark.asyncio
async def test_a_lead_booked_during_the_turn_stays_booked(mocks):
    wa, state, _, db = mocks
    pro_a, _ = await _cast(db)
    lead_id = await _lead(
        db, LeadStatus.BOOKED, pro_a["_id"], booked_slot_id=ObjectId()
    )

    await _finalize(pro_a, lead_id)

    lead = await db.leads.find_one({"_id": lead_id})
    assert lead["status"] == LeadStatus.BOOKED
    assert _sent_to(wa, PRO_A_PHONE) == [], "no second offer for a booked job"
    assert wa.send_message.call_args_list == []
    state.set_state.assert_not_called()


@pytest.mark.asyncio
async def test_a_lead_reassigned_during_the_turn_keeps_its_new_pro(mocks):
    wa, state, _, db = mocks
    pro_a, pro_b = await _cast(db)
    lead_id = await _lead(db, LeadStatus.NEW, pro_b["_id"], approval_nudged=True)

    await _finalize(pro_a, lead_id)

    lead = await db.leads.find_one({"_id": lead_id})
    assert lead["pro_id"] == pro_b["_id"]
    assert lead["approval_nudged"] is True, "B's approval clock is untouched"
    assert _sent_to(wa, PRO_A_PHONE) == []
    state.set_state.assert_not_called()


@pytest.mark.asyncio
async def test_a_new_lead_already_offered_to_this_pro_is_not_reoffered(mocks):
    wa, state, _, db = mocks
    pro_a, _ = await _cast(db)
    offered_at = datetime(2026, 11, 17, 8, 0, tzinfo=timezone.utc)
    lead_id = await _lead(
        db,
        LeadStatus.NEW,
        pro_a["_id"],
        pro_notified_at=offered_at,
        approval_nudged=True,
        reassign_offered=True,
    )

    await _finalize(pro_a, lead_id)

    lead = await db.leads.find_one({"_id": lead_id})
    assert lead["approval_nudged"] is True
    assert lead["reassign_offered"] is True
    assert lead["pro_notified_at"].replace(tzinfo=timezone.utc) == offered_at
    assert _sent_to(wa, PRO_A_PHONE) == []


@pytest.mark.asyncio
async def test_an_orphaned_offer_is_rematched(mocks):
    """A NEW lead whose pro was deactivated still goes to the matched pro."""
    wa, _, _, db = mocks
    pro_a, pro_b = await _cast(db, pro_a_active=False)
    lead_id = await _lead(db, LeadStatus.NEW, pro_a["_id"])

    await _finalize(pro_b, lead_id)

    lead = await db.leads.find_one({"_id": lead_id})
    assert lead["pro_id"] == pro_b["_id"]
    assert _sent_to(wa, PRO_B_PHONE)


@pytest.mark.asyncio
async def test_a_change_between_the_read_and_the_write_loses_the_race(
    mocks, monkeypatch
):
    """The write re-checks atomically: a lead claimed after `_deal_claim` read it
    is not written, and nothing that follows the write runs."""
    wa, state, _, db = mocks
    pro_a, _ = await _cast(db)
    lead_id = await _lead(db, LeadStatus.CONTACTED, pro_a["_id"])

    real_claim = wf._deal_claim

    async def claim_then_lose(lead, best_pro):
        claim = await real_claim(lead, best_pro)
        await db.leads.update_one(
            {"_id": lead_id}, {"$set": {"status": LeadStatus.PENDING_ADMIN_REVIEW}}
        )
        return claim

    monkeypatch.setattr(wf, "_deal_claim", claim_then_lose)

    await wf._finalize_deal(
        CUSTOMER,
        pro_a,
        _closing_reply(),
        "תל אביב",
        "נזילה",
        None,
        lead_id,
        media_url="https://example.invalid/photo.jpg",
    )

    lead = await db.leads.find_one({"_id": lead_id})
    assert lead["status"] == LeadStatus.PENDING_ADMIN_REVIEW
    assert "media_urls" not in lead, "a lost race must not touch the lead"
    assert wa.send_message.call_args_list == []
    state.set_state.assert_not_called()


@pytest.mark.asyncio
async def test_a_pro_approving_during_the_ai_call_leaves_the_lead_booked(mocks):
    """End to end on the assigned-pro fast path: the lead is read as NEW, the
    pro approves while the model is answering, and the model closes the deal."""
    wa, _, ai, db = mocks
    pro_a, _ = await _cast(db)
    lead_id = await _lead(db, LeadStatus.NEW, pro_a["_id"])

    async def approve_while_answering(*args, **kwargs):
        await db.leads.update_one(
            {"_id": lead_id}, {"$set": {"status": LeadStatus.BOOKED}}
        )
        return _closing_reply()

    ai.analyze_conversation = AsyncMock(side_effect=approve_while_answering)

    await wf.process_incoming_message(CUSTOMER, "מחר בעשר מתאים")

    lead = await db.leads.find_one({"_id": lead_id})
    assert lead["status"] == LeadStatus.BOOKED
    assert _sent_to(wa, PRO_A_PHONE) == [], "the pro is not offered a job they took"


@pytest.mark.asyncio
async def test_the_fast_path_does_not_refinalize_a_new_emergency_lead(mocks):
    """A NEW emergency lead used to be re-offered on every customer turn."""
    wa, _, ai, db = mocks
    pro_a, _ = await _cast(db)
    lead_id = await _lead(
        db, LeadStatus.NEW, pro_a["_id"], is_emergency=True, approval_nudged=True
    )
    reply = _closing_reply()
    reply.is_deal = False
    reply.reply_to_user = "איזו קומה?"
    ai.analyze_conversation = AsyncMock(return_value=reply)

    await wf.process_incoming_message(CUSTOMER, "עדיין מחכה")

    lead = await db.leads.find_one({"_id": lead_id})
    assert lead["approval_nudged"] is True
    assert _sent_to(wa, PRO_A_PHONE) == []
    # The reply is not withheld: nothing is dispatched on this turn.
    wa.send_message.assert_any_call(CUSTOMER, "איזו קומה?")
