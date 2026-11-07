"""PRO-228: a new lead must never carry address parts from an earlier conversation.

Staging, 2026-11-07: the customer gave "בן גוריון 46 רמת גן דירה 10 קומה 2" on
lead A, which the operator then cancelled in the admin panel. The customer's next
request, "יש לי נזילה מתחת לכיור בתל אביב", accepted the loyalty offer, was asked
only for "עיר", answered "תל אביב", and was BOOKED at "בן גוריון 46, תל אביב" with
A's floor and apartment: an address nobody gave.

These run the real LeadManager / ContextManager / StateManager on mongomock and
fakeredis. Only WhatsApp and the model are faked, and the fake model behaves like
the real one in the way that matters: it extracts an address part only from text
it was actually shown, history included.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import mongomock
import pytest
from bson import ObjectId

import app.services.workflow_service as workflow_service
from admin_panel.core.lead_queries import apply_lead_changes
from app.core.constants import LeadStatus, UserStates
from app.core.messages import Messages
from app.services.ai_engine_service import AIResponse, ExtractedData
from app.services.state_manager_service import StateManager

OLD_ADDRESS = "בן גוריון 46 רמת גן דירה 10 קומה 2"


def _fake_model_reply(history, user_text, **_):
    """Extract only what the model can see: the history it was handed plus this turn."""
    seen = " ".join(p for m in history or [] for p in m.get("parts", []))
    seen += " " + (user_text or "")
    data = {"issue": "נזילה מתחת לכיור" if "נזילה" in seen else None}
    if "תל אביב" in (user_text or ""):
        data["city"] = "תל אביב"
    elif "רמת גן" in seen:
        data["city"] = "רמת גן"
    if "בן גוריון 46" in seen:
        data.update(street="בן גוריון", street_number="46", floor="2", apartment="10")
    if "הרצל 5" in seen:
        data.update(street="הרצל", street_number="5")
    if "קומה 3 דירה 7" in seen:
        data.update(floor="3", apartment="7")
    return AIResponse(
        reply_to_user="קיבלתי",
        extracted_data=ExtractedData(full_address=None, appointment_time=None, **data),
        transcription=None,
        is_deal=False,
    )


@pytest.fixture
def turn(monkeypatch):
    mock_wa = MagicMock()
    mock_wa.send_message = AsyncMock(return_value=True)
    mock_wa.send_location_link = AsyncMock()
    mock_wa.send_chat_state_typing = AsyncMock()
    monkeypatch.setattr(workflow_service, "whatsapp", mock_wa)
    monkeypatch.setattr(workflow_service, "has_consent", AsyncMock(return_value=True))
    mock_ai = MagicMock()
    mock_ai.analyze_conversation = AsyncMock(side_effect=_fake_model_reply)
    mock_ai.detect_service_intent = AsyncMock(return_value=True)
    monkeypatch.setattr(workflow_service, "ai", mock_ai)
    monkeypatch.setattr(
        workflow_service, "determine_best_pro", AsyncMock(return_value=None)
    )

    async def send(chat_id, text):
        mock_wa.send_message.reset_mock()
        await workflow_service.process_incoming_message(chat_id, text)
        return " ".join(str(c.args[1]) for c in mock_wa.send_message.call_args_list)

    return send


async def _returning_customer(db):
    """A customer with a completed plumbing job behind them (the loyalty offer's source)."""
    chat_id = f"9725{ObjectId()}"[:12] + "@c.us"
    pro_id = ObjectId()
    await db.users.insert_one(
        {
            "_id": pro_id,
            "is_active": True,
            "role": "professional",
            "business_name": "יוסי אינסטלציה",
            "phone_number": "972500001111",
            "type": "plumber",
            "categories": ["plumber"],
        }
    )
    long_ago = datetime.now(timezone.utc) - timedelta(days=30)
    await db.leads.insert_one(
        {
            "chat_id": chat_id,
            "status": LeadStatus.COMPLETED,
            "pro_id": pro_id,
            "created_at": long_ago,
            "completed_at": long_ago + timedelta(hours=2),
        }
    )
    return chat_id, pro_id


async def _lead_a_cancelled_in_panel(db, chat_id):
    """Lead A: the old address is in the chat log, then the panel cancels it."""
    lead_a = ObjectId()
    started = datetime.now(timezone.utc) - timedelta(hours=1)
    await db.leads.insert_one(
        {
            "_id": lead_a,
            "chat_id": chat_id,
            "status": LeadStatus.CONTACTED,
            "created_at": started,
            "updated_at": started,
            "city": "רמת גן",
            "issue_type": "נזילה",
        }
    )
    lm = workflow_service.lead_manager
    await lm.log_message(chat_id, "user", "יש לי נזילה ברמת גן")
    await lm.log_message(chat_id, "model", "מה הכתובת?")
    await lm.log_message(chat_id, "user", OLD_ADDRESS)
    await StateManager.set_state(chat_id, UserStates.AWAITING_ADDRESS)
    # The panel's own write path, on the same store the worker reads.
    sync_leads = db.leads._AsyncMongoMockCollection__collection
    cleared = []
    apply_lead_changes(
        str(lead_a),
        {"status": LeadStatus.CANCELLED},
        leads_collection=sync_leads,
        end_conversation=cleared.append,
    )
    return lead_a, cleared


@pytest.mark.asyncio
async def test_new_lead_does_not_inherit_a_panel_cancelled_leads_address(turn, mock_db):
    chat_id, _ = await _returning_customer(mock_db)
    _, cleared = await _lead_a_cancelled_in_panel(mock_db, chat_id)
    assert cleared == [chat_id]  # the panel asked for the conversation to end

    sent = await turn(chat_id, "יש לי נזילה מתחת לכיור בתל אביב")
    assert Messages.Customer.LOYALTY_OFFER.split("\n")[0] in sent
    lead_b = await mock_db.leads.find_one(
        {"chat_id": chat_id, "status": LeadStatus.CONTACTED}
    )
    assert lead_b["city"] == "תל אביב"  # the opening message named it
    assert not any(
        lead_b.get(f) for f in ("street", "street_number", "floor", "apartment")
    )

    sent = await turn(chat_id, "1")
    for label in ("רחוב", "מספר בית", "קומה", "מספר דירה"):
        assert label in sent
    assert "עיר" not in sent

    await turn(chat_id, "תל אביב")
    lead_b = await mock_db.leads.find_one({"_id": lead_b["_id"]})
    assert lead_b["status"] == LeadStatus.CONTACTED  # not dispatched
    assert "בן גוריון" not in (lead_b.get("full_address") or "")
    assert not lead_b.get("street")


@pytest.mark.asyncio
async def test_parts_given_in_another_city_are_dropped_when_the_city_changes(
    turn, mock_db
):
    """Same conversation, the customer corrects the city: the street they gave for
    Ramat Gan must not be joined to Tel Aviv. (No issue word, so the lead stays in
    intake instead of reaching matching.)"""
    chat_id = f"{ObjectId()}@c.us"
    await turn(chat_id, "יש לי בעיה ברמת גן, " + OLD_ADDRESS)
    lead = await mock_db.leads.find_one({"chat_id": chat_id})
    assert lead["street"] == "בן גוריון" and lead["address_city"] == "רמת גן"

    await turn(chat_id, "סליחה, זה בכלל בתל אביב")
    lead = await mock_db.leads.find_one({"_id": lead["_id"]})
    assert lead["city"] == "תל אביב"
    assert not any(
        lead.get(f) for f in ("street", "street_number", "floor", "apartment")
    )


@pytest.mark.asyncio
async def test_same_city_follow_up_keeps_parts_from_earlier_in_this_lead(turn, mock_db):
    """PRO-119 must not regress: parts given earlier in the SAME conversation stay."""
    chat_id = f"{ObjectId()}@c.us"
    await turn(chat_id, "יש לי בעיה בתל אביב ברחוב הרצל 5")
    await turn(chat_id, "קומה 3 דירה 7, תל אביב")
    lead = await mock_db.leads.find_one({"chat_id": chat_id})
    assert (lead["street"], lead["street_number"]) == ("הרצל", "5")
    assert (lead["floor"], lead["apartment"]) == ("3", "7")
    assert lead["city"] == "תל אביב"


@pytest.mark.asyncio
async def test_chat_history_cutoff_is_the_latest_terminal_lead_not_a_completed_one(
    mock_db,
):
    """The Mongo fallback used to sort by `completed_at` first, so any COMPLETED
    lead won over a later CANCELLED one and the cancelled lead's chat leaked."""
    chat_id, _ = await _returning_customer(mock_db)
    await _lead_a_cancelled_in_panel(mock_db, chat_id)
    await workflow_service.ContextManager.clear_context(chat_id)
    history = await workflow_service.lead_manager.get_chat_history(chat_id)
    assert OLD_ADDRESS not in " ".join(p for m in history for p in m["parts"])


def test_panel_cancel_ends_the_conversation_only_when_nothing_else_is_open():
    leads = mongomock.MongoClient().db.leads
    chat_id = "972500002222@c.us"
    a = leads.insert_one(
        {"chat_id": chat_id, "status": LeadStatus.CONTACTED}
    ).inserted_id
    b = leads.insert_one({"chat_id": chat_id, "status": LeadStatus.BOOKED}).inserted_id
    cleared = []
    apply_lead_changes(
        str(a),
        {"status": LeadStatus.CANCELLED},
        leads_collection=leads,
        end_conversation=cleared.append,
    )
    assert cleared == []  # lead b is still open; its conversation stays
    apply_lead_changes(
        str(b),
        {"status": LeadStatus.CANCELLED},
        leads_collection=leads,
        end_conversation=cleared.append,
    )
    assert cleared == [chat_id]
    apply_lead_changes(
        str(b),
        {"status": LeadStatus.NEW},
        leads_collection=leads,
        end_conversation=cleared.append,
    )
    assert cleared == [chat_id]  # reopening is not an end


@pytest.mark.asyncio
async def test_address_gate_does_not_join_a_new_city_to_stored_parts(turn, mock_db):
    """The staging shape at the gate itself: four parts stored for Ramat Gan, the
    customer answers "תל אביב". The parts go and are asked for again."""
    chat_id = f"{ObjectId()}@c.us"
    lead_id = ObjectId()
    await mock_db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": chat_id,
            "status": LeadStatus.CONTACTED,
            "issue_type": "נזילה",
            "address_city": "רמת גן",
            "street": "בן גוריון",
            "street_number": "46",
            "floor": "2",
            "apartment": "10",
        }
    )
    await StateManager.set_state(chat_id, UserStates.AWAITING_ADDRESS)

    sent = await turn(chat_id, "תל אביב")

    lead = await mock_db.leads.find_one({"_id": lead_id})
    assert lead["city"] == "תל אביב"
    assert not any(
        lead.get(f) for f in ("street", "street_number", "floor", "apartment")
    )
    assert not lead.get("full_address")
    assert "רחוב" in sent and "מספר דירה" in sent
