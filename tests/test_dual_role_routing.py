"""
Tests for PRO-69 PR-1: dual-role routing for a professional who is also a
customer (their own request, served by a different pro).

Covers:
  - FM-1: explicit "לקוח" mode switch (from PRO_MODE and from IDLE, and the
    non-pro no-op case).
  - FM-3 + TTL edge: `_get_active_customer_lead` status matrix, the
    post-dispatch auto-return suppression/firing, and the IDLE IDLE-detect
    Redis-TTL-expiry edge case.
  - FM-4: context-aware Safety Bypass — ambiguous keywords defer to an open
    customer-side lead, pro-only keywords bypass unconditionally.
  - AMBIGUOUS_PRO_KEYWORDS / PRO_BUSINESS_KEYWORDS invariants.
  - Customer status query ("סטטוס" / "?") still reachable from CUSTOMER_MODE.

FM-2 (re-prompt) is already covered in test_workflow_orchestrator.py.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from bson import ObjectId
from app.core.constants import UserStates, LeadStatus
from app.core.messages import Messages
from app.services.workflow_service import (
    process_incoming_message,
    _get_active_customer_lead,
    PRO_BUSINESS_KEYWORDS,
    AMBIGUOUS_PRO_KEYWORDS,
    PRO_ONLY_KEYWORDS,
)
from app.services.ai_engine_service import AIResponse, ExtractedData
import app.services.workflow_service


@pytest.fixture
def wf_mocks(monkeypatch, mock_db):
    """Common mocks for workflow orchestrator tests (mirrors test_workflow_orchestrator.py)."""
    mock_wa = MagicMock()
    mock_wa.send_message = AsyncMock()
    mock_wa.send_location_link = AsyncMock()
    mock_wa.send_chat_state_typing = AsyncMock()
    monkeypatch.setattr(app.services.workflow_service, "whatsapp", mock_wa)

    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=UserStates.IDLE)
    mock_state.set_state = AsyncMock()
    mock_state.clear_state = AsyncMock()
    mock_state.get_metadata = AsyncMock(return_value={})
    mock_state.set_metadata = AsyncMock()
    monkeypatch.setattr(app.services.workflow_service, "StateManager", mock_state)

    mock_ctx = MagicMock()
    mock_ctx.clear_context = AsyncMock()
    monkeypatch.setattr(app.services.workflow_service, "ContextManager", mock_ctx)

    monkeypatch.setattr(
        app.services.workflow_service, "has_consent", AsyncMock(return_value=True)
    )

    mock_ai = MagicMock()
    mock_ai.analyze_conversation = AsyncMock(
        return_value=AIResponse(
            reply_to_user="AI response",
            extracted_data=ExtractedData(
                city=None, issue=None, full_address=None, appointment_time=None
            ),
            transcription=None,
            is_deal=False,
        )
    )
    mock_ai.detect_service_intent = AsyncMock(return_value=False)
    monkeypatch.setattr(app.services.workflow_service, "ai", mock_ai)

    mock_lm = MagicMock()
    mock_lm.log_message = AsyncMock()
    mock_lm.get_chat_history = AsyncMock(return_value=[])
    mock_lm.create_lead_from_dict = AsyncMock(
        return_value={
            "_id": ObjectId(),
            "full_address": "Test",
            "issue_type": "Leak",
            "appointment_time": "10:00",
            "chat_id": "user@c.us",
        }
    )
    monkeypatch.setattr(app.services.workflow_service, "lead_manager", mock_lm)

    return mock_wa, mock_state, mock_ctx, mock_ai, mock_lm


# --- FM-1: Explicit "לקוח" switch ---


@pytest.mark.asyncio
async def test_customer_mode_command_from_pro_mode_switches(wf_mocks, mock_db):
    """Pro in PRO_MODE sends 'לקוח' -> CUSTOMER_MODE, context cleared, confirmation sent, no AI/dashboard."""
    mock_wa, mock_state, mock_ctx, mock_ai, _ = wf_mocks
    mock_state.get_state = AsyncMock(return_value=UserStates.PRO_MODE)

    pro_phone = "972501000001"
    chat_id = f"{pro_phone}@c.us"
    await mock_db.users.insert_one(
        {"phone_number": pro_phone, "role": "professional", "is_active": True}
    )

    await process_incoming_message(chat_id, "לקוח")

    mock_state.set_state.assert_called_once_with(chat_id, UserStates.CUSTOMER_MODE)
    mock_ctx.clear_context.assert_called_once_with(chat_id)
    mock_wa.send_message.assert_called_once_with(
        chat_id, Messages.Pro.SWITCHED_TO_CUSTOMER
    )
    mock_ai.analyze_conversation.assert_not_called()
    # Green API has no interactive buttons. Asserting send_interactive_buttons was
    # not called on mock_wa would pass vacuously (a bare MagicMock auto-creates the
    # attribute), so assert against the real client instead — the helper was removed
    # in April 2026 and must stay gone.
    from app.services.whatsapp_client_service import WhatsAppClient

    assert not hasattr(WhatsAppClient, "send_interactive_buttons")


@pytest.mark.asyncio
async def test_customer_mode_command_from_idle_bypasses_pro_autodetect(
    wf_mocks, mock_db, monkeypatch
):
    """Pro in IDLE sends 'אני לקוח' -> CUSTOMER_MODE, never routed through PRO_MODE
    auto-detect (which would otherwise fire on IDLE)."""
    mock_wa, mock_state, mock_ctx, mock_ai, _ = wf_mocks
    mock_state.get_state = AsyncMock(return_value=UserStates.IDLE)

    mock_pro_cmd = AsyncMock(return_value="")
    monkeypatch.setattr(app.services.workflow_service, "_handle_pro_cmd", mock_pro_cmd)

    pro_phone = "972501000002"
    chat_id = f"{pro_phone}@c.us"
    await mock_db.users.insert_one(
        {"phone_number": pro_phone, "role": "professional", "is_active": True}
    )

    await process_incoming_message(chat_id, "אני לקוח")

    mock_state.set_state.assert_called_once_with(chat_id, UserStates.CUSTOMER_MODE)
    for call in mock_state.set_state.call_args_list:
        assert call.args[1] != UserStates.PRO_MODE
    mock_pro_cmd.assert_not_called()
    mock_ai.analyze_conversation.assert_not_called()
    mock_wa.send_message.assert_called_once_with(
        chat_id, Messages.Pro.SWITCHED_TO_CUSTOMER
    )


@pytest.mark.asyncio
async def test_customer_mode_command_non_pro_falls_through(wf_mocks, mock_db):
    """A non-pro sending 'לקוח' is unaffected — falls through to normal customer routing."""
    mock_wa, mock_state, mock_ctx, mock_ai, _ = wf_mocks
    mock_state.get_state = AsyncMock(return_value=UserStates.IDLE)

    chat_id = "972501000003@c.us"  # no matching user in DB

    await process_incoming_message(chat_id, "לקוח")

    for call in mock_wa.send_message.call_args_list:
        assert call.args[1] != Messages.Pro.SWITCHED_TO_CUSTOMER
    mock_ai.analyze_conversation.assert_called_once()


# --- FM-3: _get_active_customer_lead status matrix ---


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "idx,status,extra,expected",
    [
        (i, status, extra, expected)
        for i, (status, extra, expected) in enumerate(
            [
                (LeadStatus.CONTACTED, {}, True),
                (LeadStatus.NEW, {}, True),
                (LeadStatus.BOOKED, {}, True),
                (LeadStatus.COMPLETED, {"waiting_for_rating": True}, True),
                (LeadStatus.COMPLETED, {"waiting_for_rating": False}, False),
                (LeadStatus.COMPLETED, {}, False),
                (LeadStatus.CANCELLED, {}, False),
                (LeadStatus.REJECTED, {}, False),
                (LeadStatus.CLOSED, {}, False),
                (LeadStatus.PENDING_ADMIN_REVIEW, {}, False),
            ]
        )
    ],
)
async def test_get_active_customer_lead_status_matrix(
    mock_db, idx, status, extra, expected
):
    """Direct unit test of the sticky-lifecycle status matrix, including the
    COMPLETED + waiting_for_rating boundary."""
    chat_id = f"9725060{idx:05d}@c.us"
    await mock_db.leads.insert_one(
        {"chat_id": chat_id, "status": status, "created_at": "2026-01-01", **extra}
    )

    result = await _get_active_customer_lead(chat_id)

    if expected:
        assert result is not None
        assert result["status"] == status
    else:
        assert result is None


# --- FM-3: post-dispatch auto-return (end of _finalize_deal) ---


def _deal_ai_responses():
    dispatcher_resp = AIResponse(
        reply_to_user="מצאתי לך בעל מקצוע",
        extracted_data=ExtractedData(
            city="Tel Aviv", issue="Leak", full_address=None, appointment_time=None
        ),
        transcription=None,
        is_deal=False,
    )
    pro_resp = AIResponse(
        reply_to_user="[DEAL: 10:00 | Herzl 10 | Leak]",
        extracted_data=ExtractedData(
            city="Tel Aviv",
            issue="Leak",
            street="Herzl",
            street_number="10",
            floor="1",
            apartment="2",
            appointment_time="10:00",
        ),
        transcription=None,
        is_deal=True,
    )
    return dispatcher_resp, pro_resp


@pytest.mark.asyncio
async def test_auto_return_suppressed_while_own_booked_lead_exists(
    wf_mocks, monkeypatch, mock_db
):
    """A pro-as-customer with an existing BOOKED lead of their own finalizes a
    *second*, separate deal — they must stay in CUSTOMER_MODE (not snapped back
    to PRO_MODE), and AUTO_RETURNED_TO_PRO must not be sent."""
    mock_wa, mock_state, mock_ctx, mock_ai, mock_lm = wf_mocks
    mock_state.get_state = AsyncMock(return_value=UserStates.CUSTOMER_MODE)

    pro_phone = "972505000001"
    chat_id = f"{pro_phone}@c.us"
    await mock_db.users.insert_one(
        {
            "phone_number": pro_phone,
            "role": "professional",
            "is_active": True,
            "business_name": "Self Pro",
        }
    )
    # Their own, already-booked job with a different professional.
    await mock_db.leads.insert_one(
        {"chat_id": chat_id, "status": LeadStatus.BOOKED, "created_at": "2026-01-01"}
    )

    other_pro_doc = {
        "_id": ObjectId(),
        "business_name": "Other Pro",
        "phone_number": "972500000099",
        "service_areas": ["Tel Aviv"],
        "is_active": True,
    }
    dispatcher_resp, pro_resp = _deal_ai_responses()
    mock_ai.analyze_conversation.side_effect = [dispatcher_resp, pro_resp]
    monkeypatch.setattr(
        app.services.workflow_service,
        "determine_best_pro",
        AsyncMock(return_value=other_pro_doc),
    )
    mock_lm.create_lead_from_dict.return_value = {
        "_id": ObjectId(),
        "full_address": "Herzl 10",
        "issue_type": "Leak",
        "appointment_time": "10:00",
        "chat_id": chat_id,
    }

    await process_incoming_message(chat_id, "יש לי נזילה נוספת ברחוב הרצל 10")

    for call in mock_state.set_state.call_args_list:
        assert call.args[1] != UserStates.PRO_MODE
    for call in mock_wa.send_message.call_args_list:
        assert call.args[1] != Messages.Pro.AUTO_RETURNED_TO_PRO


@pytest.mark.asyncio
async def test_no_auto_return_at_dispatch_even_with_terminal_prior_lead(
    wf_mocks, monkeypatch, mock_db
):
    """PRO-69 FM-3: dispatch never returns a pro-as-customer to PRO_MODE.

    The lead just dispatched is itself an open customer-side request, so the old
    auto-return could only ever fire at the wrong moment. Only a pro keyword or the
    IDLE auto-detect (once the lead closes) brings them back — a stale terminal lead
    from a previous request must not resurrect the old behavior."""
    mock_wa, mock_state, mock_ctx, mock_ai, mock_lm = wf_mocks
    mock_state.get_state = AsyncMock(return_value=UserStates.CUSTOMER_MODE)

    pro_phone = "972505000002"
    chat_id = f"{pro_phone}@c.us"
    await mock_db.users.insert_one(
        {
            "phone_number": pro_phone,
            "role": "professional",
            "is_active": True,
            "business_name": "Self Pro",
        }
    )
    # Their prior request is fully closed — no active lead of their own.
    await mock_db.leads.insert_one(
        {"chat_id": chat_id, "status": LeadStatus.CANCELLED, "created_at": "2026-01-01"}
    )

    other_pro_doc = {
        "_id": ObjectId(),
        "business_name": "Other Pro",
        "phone_number": "972500000098",
        "service_areas": ["Tel Aviv"],
        "is_active": True,
    }
    dispatcher_resp, pro_resp = _deal_ai_responses()
    mock_ai.analyze_conversation.side_effect = [dispatcher_resp, pro_resp]
    monkeypatch.setattr(
        app.services.workflow_service,
        "determine_best_pro",
        AsyncMock(return_value=other_pro_doc),
    )
    mock_lm.create_lead_from_dict.return_value = {
        "_id": ObjectId(),
        "full_address": "Herzl 10",
        "issue_type": "Leak",
        "appointment_time": "10:00",
        "chat_id": chat_id,
    }

    await process_incoming_message(chat_id, "יש לי נזילה ברחוב הרצל 10")

    with pytest.raises(AssertionError):
        mock_state.set_state.assert_any_call(chat_id, UserStates.PRO_MODE)
    for call in mock_wa.send_message.call_args_list:
        assert call.args[1] != Messages.Pro.AUTO_RETURNED_TO_PRO


# --- FM-3: IDLE auto-detect Redis-TTL-expiry edge ---


@pytest.mark.asyncio
async def test_idle_pro_with_open_own_lead_restores_customer_mode(
    wf_mocks, mock_db, monkeypatch
):
    """A pro whose CUSTOMER_MODE Redis key expired (state=IDLE) but whose own
    lead is still open (BOOKED) must be restored to CUSTOMER_MODE, not PRO_MODE,
    and the pro dashboard must not fire."""
    mock_wa, mock_state, mock_ctx, mock_ai, _ = wf_mocks
    mock_state.get_state = AsyncMock(return_value=UserStates.IDLE)

    mock_pro_cmd = AsyncMock(return_value="")
    monkeypatch.setattr(app.services.workflow_service, "_handle_pro_cmd", mock_pro_cmd)

    pro_phone = "972507000001"
    chat_id = f"{pro_phone}@c.us"
    await mock_db.users.insert_one(
        {
            "phone_number": pro_phone,
            "role": "professional",
            "is_active": True,
            "business_name": "Test Pro",
        }
    )
    await mock_db.leads.insert_one(
        {"chat_id": chat_id, "status": LeadStatus.BOOKED, "created_at": "2026-01-01"}
    )

    await process_incoming_message(chat_id, "שלום")

    mock_state.set_state.assert_any_call(chat_id, UserStates.CUSTOMER_MODE)
    for call in mock_state.set_state.call_args_list:
        assert call.args[1] != UserStates.PRO_MODE
    mock_pro_cmd.assert_not_called()


@pytest.mark.asyncio
async def test_idle_pro_without_open_lead_still_auto_detects_pro_mode(
    wf_mocks, mock_db, monkeypatch
):
    """Existing behavior preserved: a pro in IDLE with no open lead of their
    own is still auto-detected into PRO_MODE."""
    mock_wa, mock_state, mock_ctx, mock_ai, mock_lm = wf_mocks
    mock_state.get_state = AsyncMock(return_value=UserStates.IDLE)

    mock_pro_cmd = AsyncMock(return_value="")
    monkeypatch.setattr(app.services.workflow_service, "_handle_pro_cmd", mock_pro_cmd)

    pro_phone = "972507000002"
    chat_id = f"{pro_phone}@c.us"
    await mock_db.users.insert_one(
        {
            "phone_number": pro_phone,
            "role": "professional",
            "is_active": True,
            "business_name": "Test Pro",
        }
    )

    await process_incoming_message(chat_id, "שלום")

    mock_state.set_state.assert_any_call(chat_id, UserStates.PRO_MODE)
    mock_pro_cmd.assert_called_once()


# --- FM-4: context-aware Safety Bypass ---


@pytest.mark.asyncio
async def test_ambiguous_keyword_deferred_when_rating_prompt_open(wf_mocks, mock_db):
    """Pro in CUSTOMER_MODE who owes a rating sends '1' -> must NOT be snapped
    back to PRO_MODE; the digit is their rating, not a job approval."""
    mock_wa, mock_state, mock_ctx, mock_ai, _ = wf_mocks
    mock_state.get_state = AsyncMock(return_value=UserStates.CUSTOMER_MODE)

    pro_phone = "972508000001"
    chat_id = f"{pro_phone}@c.us"
    await mock_db.users.insert_one(
        {"phone_number": pro_phone, "role": "professional", "is_active": True}
    )
    await mock_db.leads.insert_one(
        {
            "chat_id": chat_id,
            "status": LeadStatus.COMPLETED,
            "waiting_for_rating": True,
            "created_at": "2026-01-01",
        }
    )

    await process_incoming_message(chat_id, "1")

    for call in mock_state.set_state.call_args_list:
        assert call.args[1] != UserStates.PRO_MODE


@pytest.mark.asyncio
async def test_ambiguous_keyword_deferred_mid_reschedule(wf_mocks, mock_db):
    """Pro picking a reschedule slot sends '2' -> stays on the customer side."""
    mock_wa, mock_state, mock_ctx, mock_ai, _ = wf_mocks
    mock_state.get_state = AsyncMock(return_value=UserStates.AWAITING_RESCHEDULE_TIME)

    pro_phone = "972508000004"
    chat_id = f"{pro_phone}@c.us"
    await mock_db.users.insert_one(
        {"phone_number": pro_phone, "role": "professional", "is_active": True}
    )

    await process_incoming_message(chat_id, "2")

    for call in mock_state.set_state.call_args_list:
        assert call.args[1] != UserStates.PRO_MODE


@pytest.mark.asyncio
async def test_ambiguous_keyword_bypasses_when_only_a_booked_lead_exists(
    wf_mocks, mock_db, monkeypatch
):
    """Regression (PRO-69 review, blocker #2): an open BOOKED lead of the pro's own
    can sit for days. It must NOT block 'אשר' — otherwise the pro cannot answer
    incoming job offers for that entire window and the healer reassigns them away.
    Only a customer prompt that is genuinely open defers the bypass."""
    mock_wa, mock_state, mock_ctx, mock_ai, _ = wf_mocks
    mock_state.get_state = AsyncMock(return_value=UserStates.CUSTOMER_MODE)

    mock_pro_cmd = AsyncMock(return_value="")
    monkeypatch.setattr(app.services.workflow_service, "_handle_pro_cmd", mock_pro_cmd)

    pro_phone = "972508000005"
    chat_id = f"{pro_phone}@c.us"
    await mock_db.users.insert_one(
        {"phone_number": pro_phone, "role": "professional", "is_active": True}
    )
    await mock_db.leads.insert_one(
        {"chat_id": chat_id, "status": LeadStatus.BOOKED, "created_at": "2026-01-01"}
    )

    await process_incoming_message(chat_id, "אשר")

    mock_state.set_state.assert_any_call(chat_id, UserStates.PRO_MODE)
    mock_pro_cmd.assert_called_once()


@pytest.mark.asyncio
async def test_pro_only_keyword_bypasses_unconditionally_despite_open_lead(
    wf_mocks, mock_db, monkeypatch
):
    """Pro in CUSTOMER_MODE with an open lead of their own sends 'סיימתי'
    (a pro-only keyword, not ambiguous) -> IS snapped back to PRO_MODE and
    routed to pro_flow, even with the open lead."""
    mock_wa, mock_state, mock_ctx, mock_ai, _ = wf_mocks
    mock_state.get_state = AsyncMock(return_value=UserStates.CUSTOMER_MODE)

    mock_pro_cmd = AsyncMock(return_value="")
    monkeypatch.setattr(app.services.workflow_service, "_handle_pro_cmd", mock_pro_cmd)

    pro_phone = "972508000002"
    chat_id = f"{pro_phone}@c.us"
    await mock_db.users.insert_one(
        {"phone_number": pro_phone, "role": "professional", "is_active": True}
    )
    await mock_db.leads.insert_one(
        {"chat_id": chat_id, "status": LeadStatus.NEW, "created_at": "2026-01-01"}
    )

    await process_incoming_message(chat_id, "סיימתי")

    mock_state.set_state.assert_any_call(chat_id, UserStates.PRO_MODE)
    mock_pro_cmd.assert_called_once()


@pytest.mark.asyncio
async def test_pro_escapes_soft_hold_while_awaiting_own_approval(
    wf_mocks, mock_db, monkeypatch
):
    """Regression (PRO-69 review, blocker #1): after ordering service for themselves
    a pro parks in AWAITING_PRO_APPROVAL for up to an hour. Suppressing the old
    auto-return means the soft hold now applies to them, so a pro-only keyword must
    still escape it — otherwise 'סיימתי' answers 'still waiting' for the full TTL."""
    mock_wa, mock_state, mock_ctx, mock_ai, _ = wf_mocks
    mock_state.get_state = AsyncMock(return_value=UserStates.AWAITING_PRO_APPROVAL)

    mock_pro_cmd = AsyncMock(return_value="")
    monkeypatch.setattr(app.services.workflow_service, "_handle_pro_cmd", mock_pro_cmd)

    pro_phone = "972508000003"
    chat_id = f"{pro_phone}@c.us"
    await mock_db.users.insert_one(
        {"phone_number": pro_phone, "role": "professional", "is_active": True}
    )

    await process_incoming_message(chat_id, "סיימתי")

    mock_pro_cmd.assert_called_once()
    for call in mock_wa.send_message.call_args_list:
        assert call.args[1] != Messages.Customer.STILL_WAITING


@pytest.mark.asyncio
async def test_customer_still_held_by_soft_hold(wf_mocks, mock_db):
    """The soft-hold escape is pro-only: a plain customer awaiting approval still
    gets STILL_WAITING, and so does a pro sending a non-escape word."""
    mock_wa, mock_state, mock_ctx, mock_ai, _ = wf_mocks
    mock_state.get_state = AsyncMock(return_value=UserStates.AWAITING_PRO_APPROVAL)

    await process_incoming_message("972509000001@c.us", "מתי הוא מגיע?")

    mock_wa.send_message.assert_called_once_with(
        "972509000001@c.us", Messages.Customer.STILL_WAITING
    )


# --- Keyword-set invariants ---


def test_ambiguous_keywords_bare_digits_are_all_included():
    """Every bare digit in PRO_BUSINESS_KEYWORDS (menu picks: approve/reject/etc.
    by number) must be present in AMBIGUOUS_PRO_KEYWORDS."""
    bare_digits = {kw for kw in PRO_BUSINESS_KEYWORDS if kw.isdigit()}
    assert bare_digits.issubset(AMBIGUOUS_PRO_KEYWORDS)


def test_ambiguous_and_pro_only_partition_the_bypass_set():
    """AMBIGUOUS_PRO_KEYWORDS and PRO_ONLY_KEYWORDS must exactly partition
    PRO_BUSINESS_KEYWORDS — a keyword that falls in neither would silently lose its
    bypass, and one in both would get contradictory routing. Entries that aren't in
    the bypass set at all (e.g. ביטול/פרטים, whose command lists are not part of the
    union) are dead weight: the `in PRO_BUSINESS_KEYWORDS` gate never reaches them."""
    assert AMBIGUOUS_PRO_KEYWORDS <= PRO_BUSINESS_KEYWORDS
    assert AMBIGUOUS_PRO_KEYWORDS | PRO_ONLY_KEYWORDS == PRO_BUSINESS_KEYWORDS
    assert not (AMBIGUOUS_PRO_KEYWORDS & PRO_ONLY_KEYWORDS)


def test_pro_only_keywords_are_unambiguous():
    """The escape hatches a pro relies on (finish, search, availability) must never
    be classified ambiguous — they gate the soft-hold escape in PRO-69."""
    for kw in ("סיימתי", "חפש", "מצא", "זמין", "הפסקה"):
        if kw in PRO_BUSINESS_KEYWORDS:
            assert kw in PRO_ONLY_KEYWORDS


# --- Status query still reachable from CUSTOMER_MODE ---


@pytest.mark.asyncio
async def test_status_word_reaches_customer_handler_in_customer_mode(
    wf_mocks, monkeypatch
):
    """A pro in CUSTOMER_MODE sending 'סטטוס' must still reach the customer
    status handler (not the pro dashboard)."""
    mock_wa, mock_state, mock_ctx, mock_ai, _ = wf_mocks
    mock_state.get_state = AsyncMock(return_value=UserStates.CUSTOMER_MODE)

    mock_status = AsyncMock(return_value="STATUS_RESPONSE")
    monkeypatch.setattr(
        app.services.workflow_service, "_handle_status_query", mock_status
    )

    chat_id = "972509000001@c.us"
    await process_incoming_message(chat_id, "סטטוס")

    mock_status.assert_called_once_with(chat_id)
    mock_wa.send_message.assert_called_once_with(chat_id, "STATUS_RESPONSE")


@pytest.mark.asyncio
async def test_question_mark_reaches_customer_handler_in_customer_mode(
    wf_mocks, monkeypatch
):
    """A pro in CUSTOMER_MODE sending '?' must still reach the customer
    status handler."""
    mock_wa, mock_state, mock_ctx, mock_ai, _ = wf_mocks
    mock_state.get_state = AsyncMock(return_value=UserStates.CUSTOMER_MODE)

    mock_status = AsyncMock(return_value="STATUS_RESPONSE")
    monkeypatch.setattr(
        app.services.workflow_service, "_handle_status_query", mock_status
    )

    chat_id = "972509000002@c.us"
    await process_incoming_message(chat_id, "?")

    mock_status.assert_called_once_with(chat_id)
    mock_wa.send_message.assert_called_once_with(chat_id, "STATUS_RESPONSE")
