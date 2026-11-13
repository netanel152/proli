"""What a template offer could not carry (``offer_withheld_details``) is stored
when the template goes out and must not outlive the pro it was meant for.

The storing rule itself, and the approval that delivers it, are covered in
tests/test_business_initiated_send.py. These are the two ends around them: the
initial offer that writes the field, and every write that hands the lead to
another owner.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from bson import ObjectId

import app.services.workflow_service
from app.core.constants import LeadStatus
from app.core.messages import Messages
from app.services import admin_flow, monitor_service, pro_flow
from app.services.ai_engine_service import AIResponse, ExtractedData
from app.services.workflow_service import process_incoming_message

CHAT_ID = "972501234567@c.us"
PRO_PHONE = "972500000001"
PRO_CHAT = f"{PRO_PHONE}@c.us"
CITY = "תל אביב"
TRANSCRIPT = "יש נזילה מתחת לכיור"
MEDIA_LINK = "https://example.com/a.jpg"
STALE_LINK = "https://example.com/earlier-offer.jpg"
STALE_DETAILS = f"\n\n{Messages.Pro.MEDIA_ATTACHED_HEADER}\n1. {STALE_LINK}"
TEMPLATE_SENT = {"idMessage": "x", "sent_as_template": "lead_offer"}
FREE_FORM_SENT = {"idMessage": "x"}


@pytest_asyncio.fixture
async def clean_db(mock_db):
    # mock_db is module-scoped, and these tests look people up by phone number.
    await mock_db.users.delete_many({})
    await mock_db.leads.delete_many({})
    return mock_db


@pytest_asyncio.fixture
async def wf(monkeypatch, clean_db):
    whatsapp = MagicMock()
    whatsapp.send_message = AsyncMock()
    whatsapp.send_chat_state_typing = AsyncMock()
    monkeypatch.setattr(app.services.workflow_service, "whatsapp", whatsapp)
    monkeypatch.setattr(
        app.services.workflow_service, "has_consent", AsyncMock(return_value=True)
    )
    return whatsapp


async def _pro(mock_db, **extra):
    return (
        await mock_db.users.insert_one(
            {
                "phone_number": PRO_PHONE,
                "role": "professional",
                "is_active": True,
                "business_name": "שירותי דוגמה",
                "service_areas": [CITY],
                "location": {"type": "Point", "coordinates": [34.7818, 32.0853]},
                **extra,
            }
        )
    ).inserted_id


async def _lead(mock_db, **fields):
    defaults = {
        "chat_id": CHAT_ID,
        "status": LeadStatus.NEW,
        "created_at": datetime.now(timezone.utc),
        "full_address": f"הרצל 1, {CITY}",
        "city": CITY,
        "issue_type": "נזילה",
    }
    return (await mock_db.leads.insert_one({**defaults, **fields})).inserted_id


def _replies(whatsapp, chat_id):
    return [
        c.args[1] for c in whatsapp.send_message.call_args_list if c.args[0] == chat_id
    ]


def _offer_whatsapp(sent):
    whatsapp = AsyncMock()
    whatsapp.send_message.return_value = sent
    return whatsapp


# ---------------------------------------------------------------------------
# The initial offer: _finalize_deal is what writes the field
# ---------------------------------------------------------------------------


def _agreed_deal():
    return AIResponse(
        reply_to_user="תודה",
        transcription=TRANSCRIPT,
        is_deal=True,
        extracted_data=ExtractedData(
            city=CITY,
            issue="נזילה",
            street="הרצל",
            street_number="10",
            floor="2",
            apartment="5",
            appointment_time="מחר",
        ),
    )


async def _finalize(wf, mock_db, sent):
    """Run the deal for a CONTACTED lead that carries one media link, with the
    pro's offer send answering ``sent``. Returns the offer call and the lead."""
    pro_id = await _pro(mock_db)
    lead_id = await _lead(mock_db, status=LeadStatus.CONTACTED, media_urls=[MEDIA_LINK])
    wf.send_message.return_value = sent
    pro = await mock_db.users.find_one({"_id": pro_id})

    await app.services.workflow_service._finalize_deal(
        CHAT_ID, pro, _agreed_deal(), CITY, "נזילה", TRANSCRIPT, lead_id
    )

    offer = next(c for c in wf.send_message.call_args_list if c.args[0] == PRO_CHAT)
    return offer, await mock_db.leads.find_one({"_id": lead_id})


@pytest.mark.asyncio
async def test_a_template_offer_keeps_the_transcript_and_media_for_the_approval(
    wf, clean_db
):
    offer, lead = await _finalize(wf, clean_db, TEMPLATE_SENT)

    transcript = Messages.Pro.NEW_LEAD_TRANSCRIPTION.format(transcription=TRANSCRIPT)
    assert transcript in lead["offer_withheld_details"]
    assert MEDIA_LINK in lead["offer_withheld_details"]
    # The free-form text still carries both, for a recipient whose window is open.
    assert offer.kwargs["template_key"] == "lead_offer"
    assert transcript in offer.args[1]
    assert MEDIA_LINK in offer.args[1]


@pytest.mark.asyncio
async def test_a_free_form_offer_stores_nothing_because_it_carried_everything(
    wf, clean_db
):
    offer, lead = await _finalize(wf, clean_db, FREE_FORM_SENT)

    assert "offer_withheld_details" not in lead
    assert MEDIA_LINK in offer.args[1]


# ---------------------------------------------------------------------------
# A new owner starts without the previous offer's details
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reassigning_a_lead_drops_the_previous_offers_withheld_details(
    clean_db, monkeypatch
):
    old_pro_id = await _pro(clean_db, phone_number="972500000009")
    lead_id = await _lead(
        clean_db, pro_id=old_pro_id, offer_withheld_details=STALE_DETAILS
    )
    lead = await clean_db.leads.find_one({"_id": lead_id})
    new_pro = {
        "_id": ObjectId(),
        "business_name": "אבי אינסטלציה",
        "phone_number": PRO_PHONE,
    }
    monkeypatch.setattr(monitor_service, "whatsapp", _offer_whatsapp(FREE_FORM_SENT))
    monkeypatch.setattr(
        monitor_service.matching_service,
        "determine_best_pro",
        AsyncMock(return_value=new_pro),
    )

    reassigned = await monitor_service.reassign_lead(lead, notify_old_pro=False)

    assert reassigned is True
    stored = await clean_db.leads.find_one({"_id": lead_id})
    assert stored["pro_id"] == new_pro["_id"]
    assert "offer_withheld_details" not in stored


@pytest.mark.asyncio
async def test_an_admin_assignment_drops_the_previous_offers_withheld_details(
    clean_db, monkeypatch
):
    monkeypatch.setattr(admin_flow, "leads_collection", clean_db.leads)
    lead_id = await _lead(
        clean_db,
        status=LeadStatus.PENDING_ADMIN_REVIEW,
        offer_withheld_details=STALE_DETAILS,
    )
    pro = {"_id": ObjectId(), "business_name": "דנה", "phone_number": PRO_PHONE}

    offer_sent, _ = await admin_flow.assign_lead_to_pro(
        lead_id, pro, _offer_whatsapp(FREE_FORM_SENT)
    )

    assert offer_sent is True
    stored = await clean_db.leads.find_one({"_id": lead_id})
    assert stored["pro_id"] == pro["_id"]
    assert "offer_withheld_details" not in stored


@pytest.mark.asyncio
async def test_a_pro_claiming_a_stuck_lead_with_find_drops_the_earlier_details(
    wf, clean_db
):
    pro_id = await _pro(clean_db)
    lead_id = await _lead(
        clean_db,
        status=LeadStatus.PENDING_ADMIN_REVIEW,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=75),
        offer_withheld_details=STALE_DETAILS,
    )
    pro = await clean_db.users.find_one({"_id": pro_id})

    await pro_flow._handle_search(pro, PRO_CHAT, wf)

    stored = await clean_db.leads.find_one({"_id": lead_id})
    assert (stored["status"], stored["pro_id"]) == (LeadStatus.NEW, pro_id)
    assert "offer_withheld_details" not in stored


@pytest.mark.asyncio
async def test_approving_a_claimed_stuck_lead_does_not_leak_the_earlier_offers_media(
    wf, clean_db
):
    await _pro(clean_db)
    await _lead(
        clean_db,
        status=LeadStatus.PENDING_ADMIN_REVIEW,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=75),
        offer_withheld_details=STALE_DETAILS,
    )

    await process_incoming_message(PRO_CHAT, "מצא")
    await process_incoming_message(PRO_CHAT, "אשר")

    approvals = [r for r in _replies(wf, PRO_CHAT) if Messages.Pro.APPROVE_SUCCESS in r]
    assert len(approvals) == 1
    # The contact card did go out, so the missing link is not a missing reply.
    assert approvals[0].endswith(Messages.Pro.CONTACT_CARD_CLOSING)
    assert STALE_LINK not in approvals[0]
