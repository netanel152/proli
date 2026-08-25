"""
Tests for the AWAITING_LOYALTY_CONFIRMATION branch in workflow_service (PRO-119).

Returning customers are offered their previous professional. Before PRO-119 the
handler only accepted the literal "1"/"כן"/"2"/"לא", re-prompted forever on
anything else with no fall-through, inherited state_manager_service's 4h
default TTL (instead of a bounded one), and the accept path acked "אני בודק
מולו ומעדכן" without ever actually contacting the pro.

PRO-119 fixes all four: natural-language yes/no via whole-token keyword
matching, a bounded LOYALTY_CONFIRM_TTL_SECONDS TTL on both the initial offer
and the one-time re-prompt, a second unclear reply releases the customer to
normal routing instead of trapping them, and accepting really does dispatch
the lead to the pro (or explains what's still missing) via the new
``_accept_loyalty_offer`` helper.

Post-review revisions this file tracks:
* Dispatchability is judged ONLY on the five persisted address parts (plus a
  narrow is_emergency+city bypass) — never on `full_address`, which an intake
  lead carries as a bare city (`full_address=extracted_city`). Treating that as
  "complete" would have dispatched a pro to a city with no street.
* The incomplete-address branch lands the customer in AWAITING_ADDRESS, not
  IDLE — the ack it sends *is* that state's own missing-parts question.
* Decline (explicit, or accept-that-can't-be-honoured) now clears state via
  `StateManager.clear_state`, not `set_state(IDLE)` — metadata goes with it.
* The dispatch write is guarded with `expected_status` (a status race loses
  silently, falling back to decline) and normalizes `full_address` (composed
  from parts) and `appointment_time` (English sentinel -> TIME_ASAP) on the
  lead the pro's offer is built from.

Driven end-to-end through process_incoming_message so the dispatch ordering is
exercised, following the wf_mocks style in test_workflow_orchestrator.py.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from bson import ObjectId
from types import SimpleNamespace

import app.services.workflow_service as workflow_service
from app.services.workflow_service import process_incoming_message
from app.services.ai_engine_service import AIResponse, ExtractedData
from app.services.lead_manager_service import is_address_complete, compose_full_address
from app.core.constants import UserStates, LeadStatus, WorkerConstants, Actor, Defaults
from app.core.messages import Messages
from app.core.phone import to_chat_id


@pytest.fixture
def loyalty_mocks(monkeypatch, mock_db):
    mock_wa = MagicMock()
    mock_wa.send_message = AsyncMock()
    mock_wa.send_location_link = AsyncMock()
    mock_wa.send_chat_state_typing = AsyncMock()
    monkeypatch.setattr(workflow_service, "whatsapp", mock_wa)

    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(
        return_value=UserStates.AWAITING_LOYALTY_CONFIRMATION
    )
    mock_state.set_state = AsyncMock()
    mock_state.clear_state = AsyncMock()
    mock_state.get_metadata = AsyncMock(return_value={})
    mock_state.set_metadata = AsyncMock()
    monkeypatch.setattr(workflow_service, "StateManager", mock_state)

    mock_ctx = MagicMock()
    mock_ctx.clear_context = AsyncMock()
    monkeypatch.setattr(workflow_service, "ContextManager", mock_ctx)

    mock_lm = MagicMock()
    mock_lm.log_message = AsyncMock()
    mock_lm.get_chat_history = AsyncMock(return_value=[])
    monkeypatch.setattr(workflow_service, "lead_manager", mock_lm)

    monkeypatch.setattr(workflow_service, "has_consent", AsyncMock(return_value=True))

    # Default: neutral AI response, in case an unclear reply falls through to
    # the normal dispatcher (PRO-119's second-miss behavior).
    mock_ai = MagicMock()
    mock_ai.analyze_conversation = AsyncMock(
        return_value=AIResponse(
            reply_to_user="Mock AI Response",
            extracted_data=ExtractedData(
                city=None, issue=None, full_address=None, appointment_time=None
            ),
            transcription=None,
            is_deal=False,
        )
    )
    mock_ai.detect_service_intent = AsyncMock(return_value=False)
    monkeypatch.setattr(workflow_service, "ai", mock_ai)

    return mock_wa, mock_state, mock_ctx, mock_lm, mock_ai, mock_db


def _sent_text(mock_wa):
    return " ".join(str(c.args[1]) for c in mock_wa.send_message.call_args_list)


def _chat_id():
    """A fresh, unique chat id — mock_db is module-scoped so tests must not
    collide on the same chat_id/lead documents."""
    return f"{ObjectId()}@c.us"


async def _insert_pro(
    db, is_active=True, business_name="דני החשמלאי", phone="972500007777"
):
    pro_id = ObjectId()
    await db.users.insert_one(
        {
            "_id": pro_id,
            "is_active": is_active,
            "business_name": business_name,
            "phone_number": phone,
        }
    )
    return pro_id


async def _insert_active_lead(db, chat_id, status=LeadStatus.CONTACTED, **fields):
    lead_id = ObjectId()
    doc = {"_id": lead_id, "chat_id": chat_id, "status": status}
    doc.update(fields)
    await db.leads.insert_one(doc)
    return lead_id


def _pro_calls(mock_wa, pro_chat_id):
    return [c for c in mock_wa.send_message.call_args_list if c.args[0] == pro_chat_id]


# --- 1. Entering the loyalty state uses the bounded TTL ---------------------


@pytest.mark.asyncio
async def test_loyalty_offer_triggered_with_bounded_ttl(loyalty_mocks, mock_db):
    """The initial "want your previous pro?" offer (triggered mid-intake for a
    returning customer with a COMPLETED lead under an active past pro) must set
    AWAITING_LOYALTY_CONFIRMATION with LOYALTY_CONFIRM_TTL_SECONDS — not the 4h
    state_manager_service default that trapped a customer for hours pre-PRO-119.
    """
    mock_wa, mock_state, mock_ctx, mock_lm, mock_ai, db = loyalty_mocks
    mock_state.get_state.return_value = UserStates.IDLE
    chat_id = _chat_id()

    past_pro_id = await _insert_pro(db, business_name="יוסי הצנרת")
    await db.leads.insert_one(
        {
            "_id": ObjectId(),
            "chat_id": chat_id,
            "status": LeadStatus.COMPLETED,
            "pro_id": past_pro_id,
            "created_at": "2026-01-01",
        }
    )
    current_lead_id = await _insert_active_lead(
        db,
        chat_id,
        status=LeadStatus.CONTACTED,
        issue_type="נזילה",
        created_at="2026-08-20",
    )

    mock_ai.analyze_conversation.return_value = AIResponse(
        reply_to_user="בסדר, אני בודק",
        extracted_data=ExtractedData(
            city="תל אביב", issue="נזילה", full_address=None, appointment_time=None
        ),
        transcription=None,
        is_deal=False,
    )

    await process_incoming_message(chat_id, "עוד נזילה באותו מקום")

    mock_state.set_metadata.assert_awaited_with(
        chat_id, {"past_pro_id": str(past_pro_id)}
    )
    mock_state.set_state.assert_awaited_with(
        chat_id,
        UserStates.AWAITING_LOYALTY_CONFIRMATION,
        ttl=WorkerConstants.LOYALTY_CONFIRM_TTL_SECONDS,
    )
    mock_wa.send_message.assert_any_call(
        chat_id, Messages.Customer.LOYALTY_OFFER.format(pro_name="יוסי הצנרת")
    )
    updated = await db.leads.find_one({"_id": current_lead_id})
    assert updated["loyalty_offered"] is True


# --- 2 & 3. Natural-language accept / decline --------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ["כן בבקשה", "בטח", "yes"])
async def test_loyalty_natural_language_accept_takes_accept_path(loyalty_mocks, reply):
    """ "כן בבקשה" / "בטח" / "yes" must all be recognized as acceptance, not
    trigger the re-prompt (the old handler only accepted literal "1"/"כן").
    The lead here has no address parts, so acceptance lands it in
    AWAITING_ADDRESS (the address gate's own state), not IDLE."""
    mock_wa, mock_state, mock_ctx, mock_lm, mock_ai, db = loyalty_mocks
    chat_id = _chat_id()
    past_pro_id = await _insert_pro(db)
    # No address parts at all -> the NEED_DETAILS branch, kept simple.
    await _insert_active_lead(db, chat_id, status=LeadStatus.CONTACTED)
    mock_state.get_metadata.return_value = {"past_pro_id": str(past_pro_id)}

    await process_incoming_message(chat_id, reply)

    sent = _sent_text(mock_wa)
    assert Messages.Customer.LOYALTY_REPROMPT not in sent
    assert Messages.Customer.LOYALTY_DECLINED not in sent
    mock_state.set_state.assert_awaited_with(chat_id, UserStates.AWAITING_ADDRESS)
    mock_state.clear_state.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ["לא תודה", "no"])
async def test_loyalty_natural_language_decline_sends_declined_message(
    loyalty_mocks, reply
):
    """A decline — explicit or natural-language — clears state (not
    set_state(IDLE)) so the transient loyalty metadata goes with it."""
    mock_wa, mock_state, mock_ctx, mock_lm, mock_ai, db = loyalty_mocks
    chat_id = _chat_id()
    past_pro_id = await _insert_pro(db)
    lead_id = await _insert_active_lead(db, chat_id, status=LeadStatus.CONTACTED)
    mock_state.get_metadata.return_value = {"past_pro_id": str(past_pro_id)}

    await process_incoming_message(chat_id, reply)

    mock_wa.send_message.assert_awaited_with(
        chat_id, Messages.Customer.LOYALTY_DECLINED
    )
    mock_state.clear_state.assert_awaited_with(chat_id)
    mock_state.set_state.assert_not_awaited()
    updated = await db.leads.find_one({"_id": lead_id})
    assert "pro_id" not in updated


# --- 4. Ambiguous reply matching both sides re-prompts, not accepts ---------


@pytest.mark.asyncio
async def test_loyalty_ambiguous_reply_matching_both_sides_reprompts(loyalty_mocks):
    """A reply matching BOTH an affirmative and a negative whole-token keyword
    (e.g. "כן אבל בעצם לא") is genuinely ambiguous and must hit the same
    re-prompt-once path as a reply matching neither — never a silent decline.
    Guaranteed by the `says_no and not says_yes` guard in
    workflow_service._process_incoming_message_inner (the mirror of the
    `says_yes and not says_no` accept guard)."""
    mock_wa, mock_state, mock_ctx, mock_lm, mock_ai, db = loyalty_mocks
    chat_id = _chat_id()
    past_pro_id = await _insert_pro(db)
    lead_id = await _insert_active_lead(db, chat_id, status=LeadStatus.CONTACTED)
    mock_state.get_metadata.return_value = {"past_pro_id": str(past_pro_id)}

    # Contains both an affirmative and a negative whole-token keyword.
    await process_incoming_message(chat_id, "כן אבל בעצם לא")

    mock_wa.send_message.assert_awaited_with(
        chat_id, Messages.Customer.LOYALTY_REPROMPT
    )
    mock_state.clear_state.assert_not_awaited()
    for call in mock_state.set_state.call_args_list:
        assert call.args[1] != UserStates.IDLE
    updated = await db.leads.find_one({"_id": lead_id})
    assert "pro_id" not in updated


# --- 5. Accept with a COMPLETE address really dispatches the lead ----------


@pytest.mark.asyncio
async def test_loyalty_accept_complete_address_dispatches_to_pro(loyalty_mocks):
    mock_wa, mock_state, mock_ctx, mock_lm, mock_ai, db = loyalty_mocks
    chat_id = _chat_id()
    past_pro_id = await _insert_pro(
        db, business_name="דני החשמלאי", phone="972500007777"
    )
    lead_id = await _insert_active_lead(
        db,
        chat_id,
        status=LeadStatus.CONTACTED,
        street="הרצל",
        street_number="10",
        city="תל אביב",
        floor="2",
        apartment="5",
    )
    mock_state.get_metadata.return_value = {"past_pro_id": str(past_pro_id)}

    await process_incoming_message(chat_id, "כן בבקשה")

    updated = await db.leads.find_one({"_id": lead_id})
    assert updated["status"] == LeadStatus.NEW
    assert updated["pro_id"] == past_pro_id
    assert updated["pro_notified_at"] is not None
    assert updated["approval_nudged"] is False
    assert updated["reassign_offered"] is False
    assert updated["status_history"][-1]["status"] == LeadStatus.NEW
    assert updated["status_history"][-1]["by"] == Actor.CUSTOMER

    mock_state.set_state.assert_awaited_with(
        chat_id,
        UserStates.AWAITING_PRO_APPROVAL,
        ttl=WorkerConstants.PRO_APPROVAL_TTL_SECONDS,
    )
    mock_wa.send_message.assert_any_call(
        chat_id,
        Messages.Customer.LOYALTY_ACCEPTED_NOTIFYING.format(pro_name="דני החשמלאי"),
    )
    pro_chat_id = to_chat_id("972500007777")
    assert len(_pro_calls(mock_wa, pro_chat_id)) >= 1


# --- 6. Accept with an INCOMPLETE address saves the preference and hands off
# --- to the address gate (AWAITING_ADDRESS), never dispatches ---------------


@pytest.mark.asyncio
async def test_loyalty_accept_incomplete_address_saves_preference_no_dispatch(
    loyalty_mocks,
):
    mock_wa, mock_state, mock_ctx, mock_lm, mock_ai, db = loyalty_mocks
    chat_id = _chat_id()
    past_pro_id = await _insert_pro(
        db, business_name="דני החשמלאי", phone="972500007777"
    )
    # Missing floor + apartment.
    lead_id = await _insert_active_lead(
        db,
        chat_id,
        status=LeadStatus.CONTACTED,
        street="הרצל",
        street_number="10",
        city="תל אביב",
    )
    mock_state.get_metadata.return_value = {"past_pro_id": str(past_pro_id)}

    expected_ok, expected_reason = is_address_complete(
        SimpleNamespace(
            street="הרצל",
            street_number="10",
            city="תל אביב",
            floor=None,
            apartment=None,
        )
    )
    assert expected_ok is False  # sanity: the fixture really is incomplete

    await process_incoming_message(chat_id, "1")

    updated = await db.leads.find_one({"_id": lead_id})
    assert updated["pro_id"] == past_pro_id
    assert updated["status"] == LeadStatus.CONTACTED  # NOT advanced to NEW

    pro_chat_id = to_chat_id("972500007777")
    assert _pro_calls(mock_wa, pro_chat_id) == []  # pro never contacted

    # AWAITING_ADDRESS, not IDLE: the ack IS that state's own missing-parts
    # question, and that state owns the re-extract/merge/compose answer path.
    mock_state.set_state.assert_awaited_with(chat_id, UserStates.AWAITING_ADDRESS)
    mock_state.clear_state.assert_not_awaited()
    mock_wa.send_message.assert_awaited_with(
        chat_id,
        Messages.Customer.LOYALTY_ACCEPTED_NEED_DETAILS.format(
            pro_name="דני החשמלאי", missing=expected_reason
        ),
    )


@pytest.mark.asyncio
async def test_loyalty_accept_bare_city_full_address_still_needs_details(
    loyalty_mocks,
):
    """Regression pin: a lead's `full_address` is a bare city at intake
    (`full_address=extracted_city`) — an earlier revision of this handler
    treated ANY `full_address` as proof of a complete address, which would
    have dispatched a pro to a city with no street. Dispatchability is judged
    only on the five persisted parts (plus the emergency bypass), so this
    must still take the NEED_DETAILS path: no dispatch, state AWAITING_ADDRESS."""
    mock_wa, mock_state, mock_ctx, mock_lm, mock_ai, db = loyalty_mocks
    chat_id = _chat_id()
    past_pro_id = await _insert_pro(
        db, business_name="דני החשמלאי", phone="972500007777"
    )
    lead_id = await _insert_active_lead(
        db,
        chat_id,
        status=LeadStatus.CONTACTED,
        full_address="תל אביב",  # bare city, no street/number/floor/apartment
    )
    mock_state.get_metadata.return_value = {"past_pro_id": str(past_pro_id)}

    await process_incoming_message(chat_id, "1")

    updated = await db.leads.find_one({"_id": lead_id})
    assert updated["status"] == LeadStatus.CONTACTED  # NOT advanced to NEW
    assert updated["pro_id"] == past_pro_id  # preference still saved

    pro_chat_id = to_chat_id("972500007777")
    assert _pro_calls(mock_wa, pro_chat_id) == []  # pro NOT notified

    mock_state.set_state.assert_awaited_with(chat_id, UserStates.AWAITING_ADDRESS)


# --- Emergency bypass: city-only is enough, mirroring _finalize_deal -------


@pytest.mark.asyncio
async def test_loyalty_accept_emergency_bypass_dispatches_on_city_alone(
    loyalty_mocks,
):
    mock_wa, mock_state, mock_ctx, mock_lm, mock_ai, db = loyalty_mocks
    chat_id = _chat_id()
    past_pro_id = await _insert_pro(
        db, business_name="דני החשמלאי", phone="972500007777"
    )
    lead_id = await _insert_active_lead(
        db,
        chat_id,
        status=LeadStatus.CONTACTED,
        is_emergency=True,
        city="תל אביב",
        full_address="תל אביב",
    )
    mock_state.get_metadata.return_value = {"past_pro_id": str(past_pro_id)}

    await process_incoming_message(chat_id, "1")

    updated = await db.leads.find_one({"_id": lead_id})
    assert updated["status"] == LeadStatus.NEW
    assert updated["pro_id"] == past_pro_id
    # No street parts to compose from -> full_address is left exactly as
    # stored, not recomposed/blanked.
    assert updated["full_address"] == "תל אביב"

    pro_chat_id = to_chat_id("972500007777")
    assert len(_pro_calls(mock_wa, pro_chat_id)) >= 1
    mock_state.set_state.assert_awaited_with(
        chat_id,
        UserStates.AWAITING_PRO_APPROVAL,
        ttl=WorkerConstants.PRO_APPROVAL_TTL_SECONDS,
    )


@pytest.mark.asyncio
async def test_loyalty_accept_emergency_bypass_writes_city_as_full_address_when_absent(
    loyalty_mocks,
):
    """W1: when there is no composable street+number pair AND no stored
    `full_address` at all, the emergency bypass must still dispatch — and the
    lead's `full_address` is written as the known `city` so the pro's offer
    never prints the "לא ידוע" (unknown) fallback for an address we do know."""
    mock_wa, mock_state, mock_ctx, mock_lm, mock_ai, db = loyalty_mocks
    chat_id = _chat_id()
    past_pro_id = await _insert_pro(
        db, business_name="דני החשמלאי", phone="972500007777"
    )
    lead_id = await _insert_active_lead(
        db,
        chat_id,
        status=LeadStatus.CONTACTED,
        is_emergency=True,
        city="תל אביב",
        # No full_address key at all.
    )
    mock_state.get_metadata.return_value = {"past_pro_id": str(past_pro_id)}

    await process_incoming_message(chat_id, "1")

    updated = await db.leads.find_one({"_id": lead_id})
    assert updated["status"] == LeadStatus.NEW
    assert updated["pro_id"] == past_pro_id
    assert updated["full_address"] == "תל אביב"

    pro_chat_id = to_chat_id("972500007777")
    assert len(_pro_calls(mock_wa, pro_chat_id)) >= 1
    mock_state.set_state.assert_awaited_with(
        chat_id,
        UserStates.AWAITING_PRO_APPROVAL,
        ttl=WorkerConstants.PRO_APPROVAL_TTL_SECONDS,
    )


# --- Field normalization on dispatch: no raw English sentinels reach the pro


@pytest.mark.asyncio
async def test_loyalty_accept_dispatch_normalizes_address_and_time(loyalty_mocks):
    mock_wa, mock_state, mock_ctx, mock_lm, mock_ai, db = loyalty_mocks
    chat_id = _chat_id()
    past_pro_id = await _insert_pro(
        db, business_name="דני החשמלאי", phone="972500007777"
    )
    lead_id = await _insert_active_lead(
        db,
        chat_id,
        status=LeadStatus.CONTACTED,
        street="הרצל",
        street_number="10",
        city="תל אביב",
        floor="2",
        apartment="5",
        full_address="תל אביב",  # the stale bare-city value from intake
        appointment_time=Defaults.PENDING_TIME,  # the English sentinel
    )
    mock_state.get_metadata.return_value = {"past_pro_id": str(past_pro_id)}

    await process_incoming_message(chat_id, "1")

    updated = await db.leads.find_one({"_id": lead_id})
    assert updated["status"] == LeadStatus.NEW
    expected_full_address = compose_full_address(
        SimpleNamespace(street="הרצל", street_number="10", city="תל אביב")
    )
    assert updated["full_address"] == expected_full_address
    assert updated["appointment_time"] == Messages.Fallbacks.TIME_ASAP
    assert updated["appointment_time"] != Defaults.PENDING_TIME
    assert updated["appointment_time"] != Defaults.ASAP_TIME


# --- Status race: the lead moved between read and write --------------------


@pytest.mark.asyncio
async def test_loyalty_accept_status_race_lost_sends_already_updated(
    loyalty_mocks, monkeypatch
):
    """`set_lead_status` is called with `expected_status=CONTACTED` guarding
    the read-then-write gap. If it returns None (a concurrent transition won
    the race — e.g. a monitor escalation), `_accept_loyalty_offer` returns
    None specifically (not False): the caller must not notify anyone, and
    must NOT answer with the generic decline copy either — "I'll go find you
    someone" is a promise the race winner already invalidated. It answers with
    the neutral LOYALTY_ALREADY_UPDATED instead, via `StateManager.clear_state`."""
    mock_wa, mock_state, mock_ctx, mock_lm, mock_ai, db = loyalty_mocks
    chat_id = _chat_id()
    past_pro_id = await _insert_pro(
        db, business_name="דני החשמלאי", phone="972500007777"
    )
    lead_id = await _insert_active_lead(
        db,
        chat_id,
        status=LeadStatus.CONTACTED,
        street="הרצל",
        street_number="10",
        city="תל אביב",
        floor="2",
        apartment="5",
    )
    mock_state.get_metadata.return_value = {"past_pro_id": str(past_pro_id)}

    monkeypatch.setattr(
        workflow_service, "set_lead_status", AsyncMock(return_value=None)
    )

    await process_incoming_message(chat_id, "1")

    mock_wa.send_message.assert_awaited_with(
        chat_id, Messages.Customer.LOYALTY_ALREADY_UPDATED
    )
    sent = _sent_text(mock_wa)
    assert Messages.Customer.LOYALTY_DECLINED not in sent
    mock_state.clear_state.assert_awaited_with(chat_id)
    pro_chat_id = to_chat_id("972500007777")
    assert _pro_calls(mock_wa, pro_chat_id) == []  # no notification sent

    # The lead itself was never actually mutated by the (mocked-out) write.
    unchanged = await db.leads.find_one({"_id": lead_id})
    assert unchanged["status"] == LeadStatus.CONTACTED


# --- 7. Accept when the past pro has since been deactivated ----------------


@pytest.mark.asyncio
async def test_loyalty_accept_deactivated_pro_falls_back_to_decline(loyalty_mocks):
    mock_wa, mock_state, mock_ctx, mock_lm, mock_ai, db = loyalty_mocks
    chat_id = _chat_id()
    past_pro_id = await _insert_pro(db, is_active=False, phone="972500007777")
    lead_id = await _insert_active_lead(
        db,
        chat_id,
        status=LeadStatus.CONTACTED,
        street="הרצל",
        street_number="10",
        city="תל אביב",
        floor="2",
        apartment="5",
    )
    mock_state.get_metadata.return_value = {"past_pro_id": str(past_pro_id)}

    await process_incoming_message(chat_id, "1")

    mock_wa.send_message.assert_awaited_with(
        chat_id, Messages.Customer.LOYALTY_DECLINED
    )
    mock_state.clear_state.assert_awaited_with(chat_id)
    updated = await db.leads.find_one({"_id": lead_id})
    assert "pro_id" not in updated
    assert updated["status"] == LeadStatus.CONTACTED
    pro_chat_id = to_chat_id("972500007777")
    assert _pro_calls(mock_wa, pro_chat_id) == []


# --- 8. Accept with a corrupt past_pro_id in metadata -----------------------


@pytest.mark.asyncio
async def test_loyalty_accept_corrupt_past_pro_id_falls_back_to_decline_no_crash(
    loyalty_mocks,
):
    mock_wa, mock_state, mock_ctx, mock_lm, mock_ai, db = loyalty_mocks
    chat_id = _chat_id()
    lead_id = await _insert_active_lead(db, chat_id, status=LeadStatus.CONTACTED)
    mock_state.get_metadata.return_value = {"past_pro_id": "not-an-oid"}

    await process_incoming_message(chat_id, "כן")

    mock_wa.send_message.assert_awaited_with(
        chat_id, Messages.Customer.LOYALTY_DECLINED
    )
    mock_state.clear_state.assert_awaited_with(chat_id)
    updated = await db.leads.find_one({"_id": lead_id})
    assert "pro_id" not in updated


# --- Restored: accept with no active lead at all (dropped from origin/dev) -


@pytest.mark.asyncio
async def test_loyalty_accept_without_active_lead_still_acks_no_crash(loyalty_mocks):
    """Edge: reply '1'/accept but there is no live lead to attach a pro to
    (and no past_pro_id either) — `_accept_loyalty_offer`'s
    `if not past_pro_id or not active_lead: return False` guard must send the
    honest decline copy rather than crashing or promising a check against
    nobody."""
    mock_wa, mock_state, mock_ctx, mock_lm, mock_ai, db = loyalty_mocks
    chat_id = _chat_id()
    await db.leads.delete_many({"chat_id": chat_id})
    mock_state.get_metadata.return_value = {}  # no past_pro_id

    await process_incoming_message(chat_id, "כן")

    mock_wa.send_message.assert_awaited_with(
        chat_id, Messages.Customer.LOYALTY_DECLINED
    )
    mock_state.clear_state.assert_awaited_with(chat_id)


# --- Accept clears the reprompt flag, not just sets it ----------------------


@pytest.mark.asyncio
async def test_loyalty_accept_clears_the_reprompt_flag(loyalty_mocks):
    mock_wa, mock_state, mock_ctx, mock_lm, mock_ai, db = loyalty_mocks
    chat_id = _chat_id()
    past_pro_id = await _insert_pro(db)
    await _insert_active_lead(db, chat_id, status=LeadStatus.CONTACTED)
    mock_state.get_metadata.return_value = {
        "past_pro_id": str(past_pro_id),
        "loyalty_reprompted": True,
    }

    await process_incoming_message(chat_id, "1")

    mock_state.set_metadata.assert_awaited_with(
        chat_id, {"past_pro_id": str(past_pro_id)}
    )


# --- 9. Two consecutive unclear replies release the customer to normal
# --- routing instead of trapping them ---------------------------------------


@pytest.mark.asyncio
async def test_loyalty_two_unclear_replies_reprompts_then_falls_through(
    loyalty_mocks,
):
    mock_wa, mock_state, mock_ctx, mock_lm, mock_ai, db = loyalty_mocks
    chat_id = _chat_id()
    past_pro_id = await _insert_pro(db)
    # issue_type set: a real production CONTACTED lead always has one (set by
    # create_lead_from_dict at creation) — a bare chat_id+status-only lead is
    # not a shape intake ever produces, and turn 2's fall-through reads
    # `current_lead_id`, which is only ever bound inside the sticky-persistence
    # gate when *some* fact (city/issue/media/emergency) is present.
    await _insert_active_lead(
        db, chat_id, status=LeadStatus.CONTACTED, issue_type="נזילה"
    )
    # A plain mutable dict: workflow_service mutates it in place
    # (meta["loyalty_reprompted"] = True) and AsyncMock(return_value=...)
    # hands back the SAME object on every await, so the mutation from turn 1
    # is visible on turn 2 without needing a side_effect list.
    shared_meta = {"past_pro_id": str(past_pro_id)}
    mock_state.get_metadata.return_value = shared_meta

    # Real StateManager.clear_state deletes the Redis key, so the very next
    # get_state naturally returns IDLE. The mock doesn't track real state, so
    # wire that up explicitly — this is what makes the second turn actually
    # exercise the same downstream (post-clear) dispatch path production does,
    # instead of vacuously re-reading the same mocked-fixed state.
    def _clear_flips_to_idle(cid):
        mock_state.get_state.return_value = UserStates.IDLE

    mock_state.clear_state.side_effect = _clear_flips_to_idle

    # Turn 1: unclear reply -> re-prompt once, TTL applied, state kept.
    await process_incoming_message(chat_id, "אולי בהמשך")

    mock_wa.send_message.assert_awaited_with(
        chat_id, Messages.Customer.LOYALTY_REPROMPT
    )
    mock_state.set_state.assert_awaited_with(
        chat_id,
        UserStates.AWAITING_LOYALTY_CONFIRMATION,
        ttl=WorkerConstants.LOYALTY_CONFIRM_TTL_SECONDS,
    )
    assert shared_meta.get("loyalty_reprompted") is True
    mock_state.clear_state.assert_not_awaited()

    mock_wa.send_message.reset_mock()
    mock_state.set_state.reset_mock()

    # Turn 2: still unclear -> released to normal routing, not trapped again.
    await process_incoming_message(chat_id, "צריך לבדוק את זה")

    mock_state.clear_state.assert_awaited_with(chat_id)
    assert mock_state.get_state.return_value == UserStates.IDLE
    sent_texts = [c.args[1] for c in mock_wa.send_message.call_args_list]
    assert Messages.Customer.LOYALTY_REPROMPT not in sent_texts
    # Reached the normal dispatcher — the AI got a turn instead of the
    # customer being stuck re-reading the loyalty menu forever.
    mock_ai.analyze_conversation.assert_awaited()


# --- 10. A notify failure on the complete-address path is fail-open --------


@pytest.mark.asyncio
async def test_loyalty_accept_notify_failure_still_parks_customer_in_approval(
    loyalty_mocks,
):
    """notify_pro_new_lead fails open (PRO-56 SLA recovers it): the lead is
    still advanced to NEW with the approval fields armed, and the customer
    still gets the ack and lands in AWAITING_PRO_APPROVAL, even though the
    actual send to the pro raised."""
    mock_wa, mock_state, mock_ctx, mock_lm, mock_ai, db = loyalty_mocks
    chat_id = _chat_id()
    past_pro_id = await _insert_pro(
        db, business_name="דני החשמלאי", phone="972500007777"
    )
    lead_id = await _insert_active_lead(
        db,
        chat_id,
        status=LeadStatus.CONTACTED,
        street="הרצל",
        street_number="10",
        city="תל אביב",
        floor="2",
        apartment="5",
    )
    mock_state.get_metadata.return_value = {"past_pro_id": str(past_pro_id)}

    pro_chat_id = to_chat_id("972500007777")

    async def _send_side_effect(to, *args, **kwargs):
        if to == pro_chat_id:
            raise Exception("simulated send failure")
        return None

    mock_wa.send_message = AsyncMock(side_effect=_send_side_effect)

    await process_incoming_message(chat_id, "1")

    updated = await db.leads.find_one({"_id": lead_id})
    assert updated["status"] == LeadStatus.NEW
    assert updated["pro_id"] == past_pro_id
    assert updated["pro_notified_at"] is not None
    assert updated["approval_nudged"] is False
    assert updated["reassign_offered"] is False

    # The attempted send to the pro really happened (and raised) — this is
    # not a silent skip.
    assert len(_pro_calls(mock_wa, pro_chat_id)) >= 1

    mock_state.set_state.assert_awaited_with(
        chat_id,
        UserStates.AWAITING_PRO_APPROVAL,
        ttl=WorkerConstants.PRO_APPROVAL_TTL_SECONDS,
    )
    customer_calls = [
        c for c in mock_wa.send_message.call_args_list if c.args[0] == chat_id
    ]
    assert any(
        c.args[1]
        == Messages.Customer.LOYALTY_ACCEPTED_NOTIFYING.format(pro_name="דני החשמלאי")
        for c in customer_calls
    )


# --- S1: address parts extracted mid-intake are persisted immediately ------


@pytest.mark.asyncio
async def test_intake_persists_address_parts_extracted_this_turn(
    loyalty_mocks, monkeypatch
):
    """PRO-119 S1: any street/street_number/floor/apartment this turn's AI
    extraction produced is written onto the active lead right away — not just
    city/issue/name, which is all the older sticky gate kept. Without this a
    customer who front-loaded their whole address had the parts dropped and
    was asked for them again, including immediately after accepting a loyalty
    offer (which can only dispatch once the parts are actually on the lead)."""
    mock_wa, mock_state, mock_ctx, mock_lm, mock_ai, db = loyalty_mocks
    mock_state.get_state.return_value = UserStates.IDLE
    chat_id = _chat_id()
    lead_id = await _insert_active_lead(
        db, chat_id, status=LeadStatus.CONTACTED, city="תל אביב", issue_type="נזילה"
    )
    # No pro available -> the turn ends on PENDING_ADMIN_REVIEW rather than a
    # real $geoNear aggregation, which mongomock does not support.
    monkeypatch.setattr(
        workflow_service, "determine_best_pro", AsyncMock(return_value=None)
    )
    mock_ai.analyze_conversation.return_value = AIResponse(
        reply_to_user="קיבלתי, רגע בודק",
        extracted_data=ExtractedData(
            city="תל אביב",
            issue="נזילה",
            full_address=None,
            appointment_time=None,
            street="הרצל",
            street_number="10",
            floor="2",
            apartment="5",
        ),
        transcription=None,
        is_deal=False,
    )

    await process_incoming_message(
        chat_id, "נזילה ברחוב הרצל 10 קומה 2 דירה 5 בתל אביב"
    )

    updated = await db.leads.find_one({"_id": lead_id})
    assert updated["street"] == "הרצל"
    assert updated["street_number"] == "10"
    assert updated["floor"] == "2"
    assert updated["apartment"] == "5"
