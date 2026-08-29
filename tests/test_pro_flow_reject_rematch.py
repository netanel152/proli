"""
PRO-117 -- pro-reject -> automatic rematch.

``_handle_reject`` (pro_flow.py) no longer dead-ends a rejected lead. It now:

  * Claims the rejection ATOMICALLY via ``set_lead_status(..., REJECTED,
    Actor.PRO, extra_set={"rejected_by": [...], "last_rejected_at": now},
    expected_status=NEW)``. A lost claim (concurrent writer / double-tap)
    returns ALREADY_RESPONDED. It no longer touches ``lead_manager`` at all
    for reject -- ``_handle_reject``'s signature is ``(pro, whatsapp)``.
  * Hands the claimed lead to ``monitor_service.reassign_lead(lead,
    notify_old_pro=False)`` -- the same helper the SOS Healer and the PRO-56
    approval-SLA offer use, excluding every pro in ``rejected_by`` (not just
    the current one, so a reject chain can't ping-pong A->B->A).
  * On a `reassign_lead` False return, re-reads the lead: still REJECTED ->
    ``_escalate_rejected_lead`` (admin review + admin page + customer
    PENDING_REVIEW + state/context clear); already NEW -> a concurrent winner
    got there first, still REJECT_SUCCESS. The raise-path also routes through
    ``_escalate_rejected_lead``.

Covers (numbered against the reviewer's gap list):
  1. Reject at MAX_REASSIGNMENTS, real DB writes.
  2. No-replacement escalation, real DB writes + full status_history.
  3. Double-tap דחה: second call after a successful rematch -> ALREADY_RESPONDED.
  4. rejected_by accumulates and is passed to determine_best_pro as
     excluded_pro_ids across two reject hops (A, then B).
  5. SLA re-arm: successful reject->rematch leaves the customer in
     AWAITING_PRO_APPROVAL (real, fakeredis-backed StateManager), not cleared.
  6. (monitor_service-level concurrency guard lives in
     tests/test_reassign_escalation.py, alongside the rest of reassign_lead's
     own unit coverage.)
  7. _escalate_rejected_lead: reassign_lead returns False but leaves the lead
     REJECTED -> escalation_reason, admin page, customer notified via the
     INJECTED whatsapp, state+context cleared, REJECT_SUCCESS_ESCALATED.
  8. notify_old_pro=False holds on the no-replacement escalation branch too.

Note: reassign_lead sends its own customer/pro messages through the
module-level ``monitor_service.whatsapp`` facade, not the ``whatsapp``
parameter threaded through ``handle_pro_text_command`` -- so assertions on
the reassignment's own messages must read ``mock_monitor_wa``, not
``mock_wa``. ``monitor_service`` is imported *locally* inside pro_flow's
reject functions now (not a module-level name), so patch targets must be
``app.services.monitor_service.<name>`` directly -- ``app.services.pro_flow.
monitor_service.<name>`` no longer resolves.
"""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock
from bson import ObjectId
from datetime import datetime, timezone

from app.core.constants import LeadStatus, Actor, UserStates, WorkerConstants
from app.core.messages import Messages
from app.core.redis_client import get_redis_client
from app.services.pro_flow import handle_pro_text_command
from app.services.state_manager_service import StateManager
from app.services import monitor_service

REJECTING_PRO_ID = ObjectId()
REJECTING_PRO_PHONE = "972500555000"
REJECTING_PRO_CHAT_ID = f"{REJECTING_PRO_PHONE}@c.us"

SECOND_PRO_ID = ObjectId()
SECOND_PRO_PHONE = "972500555111"
SECOND_PRO_CHAT_ID = f"{SECOND_PRO_PHONE}@c.us"


@pytest.fixture
def mock_wa():
    """The whatsapp instance injected into handle_pro_text_command / _handle_reject."""
    wa = MagicMock()
    wa.send_message = AsyncMock()
    return wa


@pytest.fixture
def mock_lm():
    lm = MagicMock()
    lm.update_lead_status = AsyncMock()
    return lm


@pytest.fixture
def mock_monitor_wa(monkeypatch):
    """reassign_lead sends through its own module-level facade, not the
    whatsapp instance injected into pro_flow -- must be patched separately."""
    wa = MagicMock()
    wa.send_message = AsyncMock()
    wa.send_location_link = AsyncMock()
    monkeypatch.setattr(monitor_service, "whatsapp", wa)
    return wa


@pytest.fixture
def isolate_new_pro_notification(monkeypatch):
    """Stub the pro-offer send so tests don't need a fully-shaped pro doc."""
    mock = AsyncMock(return_value=True)
    monkeypatch.setattr(monitor_service, "notify_pro_new_lead", mock)
    return mock


@pytest_asyncio.fixture
async def rejecting_pro(mock_db):
    doc = {
        "_id": REJECTING_PRO_ID,
        "phone_number": REJECTING_PRO_PHONE,
        "role": "professional",
        "business_name": "פלוני אלמוני",
        "is_active": True,
    }
    existing = await mock_db.users.find_one({"_id": REJECTING_PRO_ID})
    if not existing:
        await mock_db.users.insert_one(doc)
    return doc


@pytest_asyncio.fixture
async def second_pro(mock_db):
    """A real pro doc so the second hop of a reject chain can itself call
    handle_pro_text_command as a genuine professional."""
    doc = {
        "_id": SECOND_PRO_ID,
        "phone_number": SECOND_PRO_PHONE,
        "role": "professional",
        "business_name": "בית ספר שני",
        "is_active": True,
    }
    existing = await mock_db.users.find_one({"_id": SECOND_PRO_ID})
    if not existing:
        await mock_db.users.insert_one(doc)
    return doc


async def _insert_new_lead(mock_db, **overrides):
    doc = {
        "pro_id": REJECTING_PRO_ID,
        "status": LeadStatus.NEW,
        "chat_id": "972501234567@c.us",
        "issue_type": "leak",
        "full_address": "הרצל 1, תל אביב",
        "appointment_time": "10:00",
        "created_at": datetime.now(timezone.utc),
    }
    doc.update(overrides)
    res = await mock_db.leads.insert_one(doc)
    return await mock_db.leads.find_one({"_id": res.inserted_id})


# ---------------------------------------------------------------------------
# Replacement found — reassign succeeds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reject_with_replacement_reassigns_and_notifies_customer(
    mock_db,
    rejecting_pro,
    mock_wa,
    mock_lm,
    mock_monitor_wa,
    isolate_new_pro_notification,
    monkeypatch,
):
    lead = await _insert_new_lead(mock_db)
    new_pro = {
        "_id": ObjectId(),
        "business_name": "אבי אינסטלציה",
        "phone_number": "972559444143",
    }
    monkeypatch.setattr(
        "app.services.matching_service.determine_best_pro",
        AsyncMock(return_value=new_pro),
    )

    result = await handle_pro_text_command(
        REJECTING_PRO_CHAT_ID, "דחה", mock_wa, mock_lm
    )

    assert result == Messages.Pro.REJECT_SUCCESS
    mock_lm.update_lead_status.assert_not_called()

    updated = await mock_db.leads.find_one({"_id": lead["_id"]})
    assert updated["status"] == LeadStatus.NEW
    assert updated["pro_id"] == new_pro["_id"]
    assert updated["reassignment_count"] == 1
    assert updated["rejected_by"] == [REJECTING_PRO_ID]

    # status_history: REJECTED(pro) then NEW(system)
    history = updated["status_history"]
    assert [h["status"] for h in history] == [LeadStatus.REJECTED, LeadStatus.NEW]
    assert [h["by"] for h in history] == [Actor.PRO, Actor.SYSTEM]

    # customer told a new pro was found
    mock_monitor_wa.send_message.assert_any_call(
        lead["chat_id"], Messages.SOS.CUSTOMER_REASSIGNING
    )
    mock_monitor_wa.send_message.assert_any_call(
        lead["chat_id"],
        Messages.Customer.AWAITING_APPROVAL_TRANSPARENT.format(
            pro_name=new_pro["business_name"]
        ),
    )

    # the rejecting pro must never receive PRO_LOST_LEAD -- that copy is for
    # pros who went silent, not ones who explicitly rejected.
    for call in mock_monitor_wa.send_message.await_args_list:
        assert call.args != (REJECTING_PRO_CHAT_ID, Messages.SOS.PRO_LOST_LEAD)


@pytest.mark.asyncio
async def test_reject_with_replacement_does_not_clear_customer_context(
    mock_db,
    rejecting_pro,
    mock_wa,
    mock_lm,
    mock_monitor_wa,
    isolate_new_pro_notification,
    monkeypatch,
):
    """Conversation continues with the new pro, so the customer's context
    must survive the reject-and-rematch."""
    lead = await _insert_new_lead(mock_db, chat_id="972507654321@c.us")
    new_pro = {
        "_id": ObjectId(),
        "business_name": "אבי אינסטלציה",
        "phone_number": "972559444143",
    }
    monkeypatch.setattr(
        "app.services.matching_service.determine_best_pro",
        AsyncMock(return_value=new_pro),
    )
    monkeypatch.setattr(monitor_service.ContextManager, "clear_context", AsyncMock())

    result = await handle_pro_text_command(
        REJECTING_PRO_CHAT_ID, "דחה", mock_wa, mock_lm
    )

    assert result == Messages.Pro.REJECT_SUCCESS
    monitor_service.ContextManager.clear_context.assert_not_awaited()

    import app.services.pro_flow as pro_flow_mod

    pro_flow_mod.ContextManager.clear_context.assert_not_called()


@pytest.mark.asyncio
async def test_reject_with_replacement_rearms_approval_sla(
    mock_db,
    rejecting_pro,
    mock_wa,
    mock_lm,
    mock_monitor_wa,
    isolate_new_pro_notification,
    monkeypatch,
):
    """Gap 5 -- a successful reassignment must re-arm the PRO-56 approval SLA
    (AWAITING_PRO_APPROVAL, TTL'd) rather than clear state -- the old
    clear_state behaviour silently disarmed the nudge/reassign-offer for
    every reassigned lead."""
    chat_id = "972500009999@c.us"
    lead = await _insert_new_lead(mock_db, chat_id=chat_id)
    new_pro = {
        "_id": ObjectId(),
        "business_name": "אבי אינסטלציה",
        "phone_number": "972559444143",
    }
    monkeypatch.setattr(
        "app.services.matching_service.determine_best_pro",
        AsyncMock(return_value=new_pro),
    )

    result = await handle_pro_text_command(
        REJECTING_PRO_CHAT_ID, "דחה", mock_wa, mock_lm
    )

    assert result == Messages.Pro.REJECT_SUCCESS
    assert await StateManager.get_state(chat_id) == UserStates.AWAITING_PRO_APPROVAL

    redis = await get_redis_client()
    ttl = await redis.ttl(f"state:{chat_id}")
    assert 0 < ttl <= WorkerConstants.PRO_APPROVAL_TTL_SECONDS


# ---------------------------------------------------------------------------
# No replacement -> escalate (gap 2: real writes + full status_history;
# gap 8: notify_old_pro=False holds here too)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reject_no_replacement_escalates_with_real_status_history(
    mock_db, rejecting_pro, mock_wa, mock_lm, mock_monitor_wa, monkeypatch
):
    lead = await _insert_new_lead(mock_db, chat_id="972509998888@c.us")
    monkeypatch.setattr(
        "app.services.matching_service.determine_best_pro",
        AsyncMock(return_value=None),
    )

    result = await handle_pro_text_command(
        REJECTING_PRO_CHAT_ID, "דחה", mock_wa, mock_lm
    )

    assert result == Messages.Pro.REJECT_SUCCESS_ESCALATED
    mock_lm.update_lead_status.assert_not_called()

    updated = await mock_db.leads.find_one({"_id": lead["_id"]})
    assert updated["status"] == LeadStatus.PENDING_ADMIN_REVIEW

    # _handle_reject's own claim writes REJECTED for real now (no lead_manager
    # mock in the way) -- the full lifecycle must be on the record.
    history = updated["status_history"]
    assert [h["status"] for h in history] == [
        LeadStatus.REJECTED,
        LeadStatus.PENDING_ADMIN_REVIEW,
    ]
    assert [h["by"] for h in history] == [Actor.PRO, Actor.SYSTEM]

    mock_monitor_wa.send_message.assert_any_call(
        lead["chat_id"], Messages.Customer.PENDING_REVIEW
    )

    # Gap 8 — notify_old_pro=False holds on this branch too (trivially true
    # since the "notify old pro" step only runs on the success path, but
    # asserted explicitly as a regression guard).
    for call in mock_monitor_wa.send_message.await_args_list:
        assert call.args != (REJECTING_PRO_CHAT_ID, Messages.SOS.PRO_LOST_LEAD)


# ---------------------------------------------------------------------------
# Gap 1: reject at MAX_REASSIGNMENTS, real DB writes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reject_at_max_reassignments_escalates_with_real_writes(
    mock_db, rejecting_pro, mock_wa, mock_lm, mock_monitor_wa
):
    lead = await _insert_new_lead(
        mock_db,
        chat_id="972501110000@c.us",
        reassignment_count=WorkerConstants.MAX_REASSIGNMENTS,
    )

    result = await handle_pro_text_command(
        REJECTING_PRO_CHAT_ID, "דחה", mock_wa, mock_lm
    )

    assert result == Messages.Pro.REJECT_SUCCESS_ESCALATED

    updated = await mock_db.leads.find_one({"_id": lead["_id"]})
    assert updated["status"] == LeadStatus.PENDING_ADMIN_REVIEW
    assert updated["escalation_reason"] == "max_reassignments_exhausted"

    history = updated["status_history"]
    assert [h["status"] for h in history][-2:] == [
        LeadStatus.REJECTED,
        LeadStatus.PENDING_ADMIN_REVIEW,
    ]
    assert [h["by"] for h in history][-2:] == [Actor.PRO, Actor.SYSTEM]

    mock_monitor_wa.send_message.assert_any_call(
        lead["chat_id"], Messages.SOS.MAX_REASSIGNMENTS_REACHED
    )


# ---------------------------------------------------------------------------
# Gap 3: double-tap דחה — sequential, via rejected_by/last_rejected_at
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_double_tap_reject_after_successful_rematch_is_already_responded(
    mock_db,
    rejecting_pro,
    mock_wa,
    mock_lm,
    mock_monitor_wa,
    isolate_new_pro_notification,
    monkeypatch,
):
    lead = await _insert_new_lead(mock_db, chat_id="972501119999@c.us")
    new_pro = {
        "_id": ObjectId(),
        "business_name": "אבי אינסטלציה",
        "phone_number": "972559444143",
    }
    monkeypatch.setattr(
        "app.services.matching_service.determine_best_pro",
        AsyncMock(return_value=new_pro),
    )

    first = await handle_pro_text_command(
        REJECTING_PRO_CHAT_ID, "דחה", mock_wa, mock_lm
    )
    assert first == Messages.Pro.REJECT_SUCCESS

    # The lead is now NEW under new_pro — REJECTING_PRO_ID no longer has a
    # matching NEW lead, so the fat-finger guard (rejected_by/last_rejected_at)
    # is what must catch the double-tap.
    second = await handle_pro_text_command(
        REJECTING_PRO_CHAT_ID, "דחה", mock_wa, mock_lm
    )
    assert second == Messages.Pro.ALREADY_RESPONDED

    # The double-tap must not have touched the lead again.
    updated = await mock_db.leads.find_one({"_id": lead["_id"]})
    assert updated["pro_id"] == new_pro["_id"]
    assert updated["reassignment_count"] == 1


# ---------------------------------------------------------------------------
# Gap 4: rejected_by exclusion accumulates across reject hops
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rejected_by_excludes_every_pro_across_two_reject_hops(
    mock_db,
    rejecting_pro,
    second_pro,
    mock_wa,
    mock_lm,
    mock_monitor_wa,
    isolate_new_pro_notification,
    monkeypatch,
):
    lead = await _insert_new_lead(mock_db, chat_id="972501113333@c.us")
    third_pro = {
        "_id": ObjectId(),
        "business_name": "פרו שלישי",
        "phone_number": "972559999000",
    }
    determine_best_pro = AsyncMock(
        side_effect=[
            {
                "_id": SECOND_PRO_ID,
                "business_name": second_pro["business_name"],
                "phone_number": SECOND_PRO_PHONE,
            },
            third_pro,
        ]
    )
    monkeypatch.setattr(
        "app.services.matching_service.determine_best_pro", determine_best_pro
    )

    # Hop 1: pro A (REJECTING_PRO_ID) rejects -> lead goes to pro B.
    first = await handle_pro_text_command(
        REJECTING_PRO_CHAT_ID, "דחה", mock_wa, mock_lm
    )
    assert first == Messages.Pro.REJECT_SUCCESS

    first_call_kwargs = determine_best_pro.call_args_list[0].kwargs
    assert first_call_kwargs["excluded_pro_ids"] == [REJECTING_PRO_ID]

    updated = await mock_db.leads.find_one({"_id": lead["_id"]})
    assert updated["pro_id"] == SECOND_PRO_ID

    # Hop 2: pro B rejects too -> lead goes to a third pro, and the exclusion
    # list must now contain BOTH A and B.
    second = await handle_pro_text_command(SECOND_PRO_CHAT_ID, "דחה", mock_wa, mock_lm)
    assert second == Messages.Pro.REJECT_SUCCESS

    second_call_kwargs = determine_best_pro.call_args_list[1].kwargs
    assert set(second_call_kwargs["excluded_pro_ids"]) == {
        REJECTING_PRO_ID,
        SECOND_PRO_ID,
    }

    final = await mock_db.leads.find_one({"_id": lead["_id"]})
    assert final["pro_id"] == third_pro["_id"]
    assert set(final["rejected_by"]) == {REJECTING_PRO_ID, SECOND_PRO_ID}


# ---------------------------------------------------------------------------
# reassign_lead raises -> pro_flow's own fallback (_escalate_rejected_lead
# via the exception path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reject_rematch_raises_escalates_and_notifies_via_injected_whatsapp(
    mock_db, rejecting_pro, mock_wa, mock_lm, monkeypatch
):
    lead = await _insert_new_lead(mock_db, chat_id="972501112222@c.us")
    monkeypatch.setattr(
        "app.services.monitor_service.reassign_lead",
        AsyncMock(side_effect=RuntimeError("boom")),
    )

    result = await handle_pro_text_command(
        REJECTING_PRO_CHAT_ID, "דחה", mock_wa, mock_lm
    )

    assert result == Messages.Pro.REJECT_SUCCESS_ESCALATED

    updated = await mock_db.leads.find_one({"_id": lead["_id"]})
    assert updated["status"] == LeadStatus.PENDING_ADMIN_REVIEW
    assert updated["escalation_reason"] == "reject_rematch_failed"

    # Notified via the whatsapp instance INJECTED into _handle_reject, not
    # monitor_service's -- reassign_lead never ran, so it never had the
    # chance to send anything through its own facade.
    mock_wa.send_message.assert_any_call(
        lead["chat_id"], Messages.Customer.PENDING_REVIEW
    )

    import app.services.pro_flow as pro_flow_mod

    pro_flow_mod.ContextManager.clear_context.assert_called_with(lead["chat_id"])
    assert await StateManager.get_state(lead["chat_id"]) == UserStates.IDLE


@pytest.mark.asyncio
async def test_reject_rematch_raise_survives_customer_notify_failure(
    mock_db, rejecting_pro, mock_lm, monkeypatch
):
    """The customer notify in the fallback branch is best-effort -- a failed
    send must not stop the lead from landing in PENDING_ADMIN_REVIEW nor
    propagate out of _handle_reject."""
    lead = await _insert_new_lead(mock_db, chat_id="972503334444@c.us")
    monkeypatch.setattr(
        "app.services.monitor_service.reassign_lead",
        AsyncMock(side_effect=RuntimeError("boom")),
    )
    exploding_wa = MagicMock()
    exploding_wa.send_message = AsyncMock(side_effect=RuntimeError("wa down"))

    result = await handle_pro_text_command(
        REJECTING_PRO_CHAT_ID, "דחה", exploding_wa, mock_lm
    )

    assert result == Messages.Pro.REJECT_SUCCESS_ESCALATED
    updated = await mock_db.leads.find_one({"_id": lead["_id"]})
    assert updated["status"] == LeadStatus.PENDING_ADMIN_REVIEW
    assert updated["escalation_reason"] == "reject_rematch_failed"


# ---------------------------------------------------------------------------
# Gap 7: _escalate_rejected_lead via reassign_lead returning False while the
# lead stays REJECTED (its own guards no-op without an exception)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reassign_returns_false_leaves_rejected_escalates_and_pages_admin(
    mock_db, rejecting_pro, mock_wa, mock_lm, monkeypatch
):
    lead = await _insert_new_lead(mock_db, chat_id="972501115555@c.us")

    async def fake_reassign_lead(lead_arg, notify_old_pro=True):
        # No-op: doesn't touch the DB, doesn't notify anyone — mirrors a
        # reassign_lead guard bailing out (e.g. an idempotency check) without
        # ever moving the lead off REJECTED.
        return False

    monkeypatch.setattr(
        "app.services.monitor_service.reassign_lead", fake_reassign_lead
    )
    alert_admin = AsyncMock()
    monkeypatch.setattr(
        "app.services.monitor_service._alert_admin_lead_escalated", alert_admin
    )

    result = await handle_pro_text_command(
        REJECTING_PRO_CHAT_ID, "דחה", mock_wa, mock_lm
    )

    assert result == Messages.Pro.REJECT_SUCCESS_ESCALATED

    updated = await mock_db.leads.find_one({"_id": lead["_id"]})
    assert updated["status"] == LeadStatus.PENDING_ADMIN_REVIEW
    assert updated["escalation_reason"] == "reject_rematch_failed"

    alert_admin.assert_awaited_once()
    paged_lead = alert_admin.await_args.args[0]
    assert paged_lead["_id"] == lead["_id"]

    mock_wa.send_message.assert_any_call(
        lead["chat_id"], Messages.Customer.PENDING_REVIEW
    )

    import app.services.pro_flow as pro_flow_mod

    pro_flow_mod.ContextManager.clear_context.assert_called_with(lead["chat_id"])
    assert await StateManager.get_state(lead["chat_id"]) == UserStates.IDLE
