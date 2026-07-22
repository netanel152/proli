"""
PRO-63 — UX max-reassignments dead end: escalate instead of close.

``reassign_lead`` (monitor_service.py): when a lead's ``reassignment_count``
reaches ``WorkerConstants.MAX_REASSIGNMENTS`` it must hand the lead to a human
(``PENDING_ADMIN_REVIEW`` + ``escalation_reason=max_reassignments_exhausted``)
instead of the old behaviour of closing it (``LeadStatus.CLOSED`` +
``closed_reason=max_reassignments``). It must also notify the customer,
best-effort-page the admin via the new ``_alert_admin_lead_escalated`` helper,
and release the customer's FSM state + context. Below the threshold, normal
reassignment is unaffected.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from app.core.constants import LeadStatus, WorkerConstants
from app.core.messages import Messages
from app.core.config import settings
from app.core.phone import to_chat_id, to_local_phone
from app.services import monitor_service
from app.services.monitor_service import reassign_lead


@pytest.fixture
def mock_whatsapp(monkeypatch):
    mock = MagicMock()
    mock.send_message = AsyncMock()
    mock.send_file_by_url = AsyncMock()
    mock.send_location_link = AsyncMock()
    monkeypatch.setattr(monitor_service, "whatsapp", mock)
    return mock


@pytest.fixture
def mock_state_and_context(monkeypatch):
    monkeypatch.setattr(monitor_service.StateManager, "clear_state", AsyncMock())
    monkeypatch.setattr(monitor_service.ContextManager, "clear_context", AsyncMock())
    return monitor_service.StateManager, monitor_service.ContextManager


@pytest.fixture
def mock_matching(monkeypatch):
    """Stub the pro-matching call — irrelevant to the exhaustion branch, which
    runs regardless of its result, but it executes before the check so it must
    be deterministic (no real geo query against mongomock)."""
    mock = AsyncMock(return_value=None)
    monkeypatch.setattr("app.services.matching_service.determine_best_pro", mock)
    return mock


async def _insert_exhausted_lead(mock_db, **overrides):
    doc = {
        "chat_id": "972500000099@c.us",
        "status": LeadStatus.NEW,
        "pro_id": "old_pro",
        "full_address": "הרצל 1, תל אביב",
        "issue_type": "leak",
        "reassignment_count": WorkerConstants.MAX_REASSIGNMENTS,
    }
    doc.update(overrides)
    res = await mock_db.leads.insert_one(doc)
    return await mock_db.leads.find_one({"_id": res.inserted_id})


@pytest.mark.asyncio
async def test_exhausted_reassignment_escalates_not_closes(
    mock_db, monkeypatch, mock_whatsapp, mock_state_and_context, mock_matching
):
    """The regression PRO-63 exists to prevent: at MAX_REASSIGNMENTS the lead
    must become PENDING_ADMIN_REVIEW, never CLOSED."""
    monkeypatch.setattr(monitor_service, "leads_collection", mock_db.leads)
    await mock_db.leads.delete_many({})
    lead = await _insert_exhausted_lead(mock_db)

    result = await reassign_lead(lead)

    assert result is False
    updated = await mock_db.leads.find_one({"_id": lead["_id"]})
    assert updated["status"] == LeadStatus.PENDING_ADMIN_REVIEW
    assert updated["escalation_reason"] == "max_reassignments_exhausted"
    assert updated["status"] != LeadStatus.CLOSED
    assert "closed_reason" not in updated


@pytest.mark.asyncio
async def test_exhausted_reassignment_notifies_customer(
    mock_db, monkeypatch, mock_whatsapp, mock_state_and_context, mock_matching
):
    monkeypatch.setattr(monitor_service, "leads_collection", mock_db.leads)
    await mock_db.leads.delete_many({})
    lead = await _insert_exhausted_lead(mock_db)

    await reassign_lead(lead)

    mock_whatsapp.send_message.assert_any_call(
        lead["chat_id"], Messages.SOS.MAX_REASSIGNMENTS_REACHED
    )


@pytest.mark.asyncio
async def test_exhausted_reassignment_alerts_admin(
    mock_db, monkeypatch, mock_whatsapp, mock_state_and_context, mock_matching
):
    monkeypatch.setattr(monitor_service, "leads_collection", mock_db.leads)
    await mock_db.leads.delete_many({})
    lead = await _insert_exhausted_lead(mock_db)

    await reassign_lead(lead)

    admin_chat_id = to_chat_id(settings.ADMIN_PHONE)
    admin_calls = [
        call
        for call in mock_whatsapp.send_message.await_args_list
        if call.args[0] == admin_chat_id
    ]
    assert len(admin_calls) == 1
    admin_message = admin_calls[0].args[1]
    assert str(WorkerConstants.MAX_REASSIGNMENTS) in admin_message
    assert lead["issue_type"] in admin_message
    assert to_local_phone(lead["chat_id"]) in admin_message


@pytest.mark.asyncio
async def test_exhausted_reassignment_survives_admin_alert_failure(
    mock_db, monkeypatch, mock_whatsapp, mock_state_and_context, mock_matching
):
    """Admin paging is best-effort — a failed send must not abort the
    escalation. This is the important resilience case."""
    monkeypatch.setattr(monitor_service, "leads_collection", mock_db.leads)
    await mock_db.leads.delete_many({})
    lead = await _insert_exhausted_lead(mock_db)

    admin_chat_id = to_chat_id(settings.ADMIN_PHONE)

    async def flaky_send(chat_id, message):
        if chat_id == admin_chat_id:
            raise RuntimeError("Green API is down")
        return None

    mock_whatsapp.send_message = AsyncMock(side_effect=flaky_send)

    result = await reassign_lead(lead)

    assert result is False
    updated = await mock_db.leads.find_one({"_id": lead["_id"]})
    assert updated["status"] == LeadStatus.PENDING_ADMIN_REVIEW
    assert updated["escalation_reason"] == "max_reassignments_exhausted"


@pytest.mark.asyncio
async def test_exhausted_reassignment_clears_state_and_context(
    mock_db, monkeypatch, mock_whatsapp, mock_state_and_context, mock_matching
):
    monkeypatch.setattr(monitor_service, "leads_collection", mock_db.leads)
    await mock_db.leads.delete_many({})
    lead = await _insert_exhausted_lead(mock_db)

    await reassign_lead(lead)

    state_mgr, context_mgr = mock_state_and_context
    state_mgr.clear_state.assert_awaited_once_with(lead["chat_id"])
    context_mgr.clear_context.assert_awaited_once_with(lead["chat_id"])


@pytest.mark.asyncio
async def test_exhausted_reassignment_never_sends_reassigning_message(
    mock_db, monkeypatch, mock_whatsapp, mock_state_and_context, mock_matching
):
    """Fix 1 (#1) — the exhaustion check now runs BEFORE the customer is told
    'looking for someone else'. Sending CUSTOMER_REASSIGNING and then
    immediately MAX_REASSIGNMENTS_REACHED is exactly the whiplash PRO-63
    exists to remove."""
    monkeypatch.setattr(monitor_service, "leads_collection", mock_db.leads)
    await mock_db.leads.delete_many({})
    lead = await _insert_exhausted_lead(mock_db)

    await reassign_lead(lead)

    for call in mock_whatsapp.send_message.await_args_list:
        assert call.args[1] != Messages.SOS.CUSTOMER_REASSIGNING
    mock_whatsapp.send_message.assert_any_call(
        lead["chat_id"], Messages.SOS.MAX_REASSIGNMENTS_REACHED
    )


@pytest.mark.asyncio
async def test_exhausted_reassignment_never_calls_matching(
    mock_db, monkeypatch, mock_whatsapp, mock_state_and_context, mock_matching
):
    """Fix 1 (#2) — the geo-matching round is discarded on the exhaustion
    path, so it must never be awaited at all."""
    monkeypatch.setattr(monitor_service, "leads_collection", mock_db.leads)
    await mock_db.leads.delete_many({})
    lead = await _insert_exhausted_lead(mock_db)

    await reassign_lead(lead)

    mock_matching.assert_not_awaited()


@pytest.mark.asyncio
async def test_exhausted_reassignment_precedes_available_pro(
    mock_db, monkeypatch, mock_whatsapp, mock_state_and_context
):
    """Fix 1 (#3) — even when a replacement pro genuinely exists, a lead at
    MAX_REASSIGNMENTS still escalates instead of being reassigned, and its
    pro_id is left untouched."""
    monkeypatch.setattr(monitor_service, "leads_collection", mock_db.leads)
    monkeypatch.setattr(monitor_service, "users_collection", mock_db.users)
    mock_pro_call = AsyncMock(
        return_value={"_id": "new_pro", "phone_number": "972500000002"}
    )
    monkeypatch.setattr(
        "app.services.matching_service.determine_best_pro", mock_pro_call
    )
    await mock_db.leads.delete_many({})
    lead = await _insert_exhausted_lead(mock_db)

    result = await reassign_lead(lead)

    assert result is False
    mock_pro_call.assert_not_awaited()
    updated = await mock_db.leads.find_one({"_id": lead["_id"]})
    assert updated["status"] == LeadStatus.PENDING_ADMIN_REVIEW
    assert updated["pro_id"] == "old_pro"


@pytest.mark.asyncio
async def test_exhausted_reassignment_idempotent_when_already_escalated(
    mock_db, monkeypatch, mock_whatsapp, mock_state_and_context, mock_matching
):
    """Fix 1 (#4) — the loop guard. A lead already escalated for this reason
    (a human has since possibly re-assigned it, but reassignment_count still
    sits at MAX) must not be re-escalated on a subsequent Healer tick: no
    customer message, no admin alert, no status write."""
    monkeypatch.setattr(monitor_service, "leads_collection", mock_db.leads)
    await mock_db.leads.delete_many({})
    lead = await _insert_exhausted_lead(
        mock_db,
        status=LeadStatus.PENDING_ADMIN_REVIEW,
        escalation_reason="max_reassignments_exhausted",
    )

    result = await reassign_lead(lead)

    assert result is False
    mock_whatsapp.send_message.assert_not_called()
    updated = await mock_db.leads.find_one({"_id": lead["_id"]})
    # No write happened at all — the doc is byte-for-byte what we inserted
    # (status_history is only pushed by set_lead_status, which must not run).
    assert "status_history" not in updated
    assert updated["status"] == LeadStatus.PENDING_ADMIN_REVIEW


@pytest.mark.asyncio
async def test_exhausted_reassignment_concurrent_status_change_skips_side_effects(
    mock_db, monkeypatch, mock_whatsapp, mock_state_and_context, mock_matching
):
    """Fix 1 (#5) — the escalation write is guarded with
    ``expected_status=lead.get("status")``. If a concurrent caller already
    moved the lead (simulated here by handing reassign_lead a stale in-memory
    copy whose status disagrees with the DB), set_lead_status returns None and
    reassign_lead must back off silently — no customer message, no admin
    alert, no double status write."""
    monkeypatch.setattr(monitor_service, "leads_collection", mock_db.leads)
    await mock_db.leads.delete_many({})
    lead = await _insert_exhausted_lead(mock_db, status=LeadStatus.PENDING_ADMIN_REVIEW)
    # Stale read: a concurrent caller already moved the DB doc to
    # PENDING_ADMIN_REVIEW, but this caller's in-memory copy still says NEW.
    stale_lead = dict(lead)
    stale_lead["status"] = LeadStatus.NEW

    result = await reassign_lead(stale_lead)

    assert result is False
    mock_whatsapp.send_message.assert_not_called()
    updated = await mock_db.leads.find_one({"_id": lead["_id"]})
    assert updated["status"] == LeadStatus.PENDING_ADMIN_REVIEW
    assert "escalation_reason" not in updated


@pytest.mark.asyncio
async def test_below_max_reassignments_attempts_normal_reassignment(
    mock_db, monkeypatch, mock_whatsapp, mock_state_and_context
):
    """A lead below the threshold must go through the normal reassignment
    path — not the escalation branch — even though it shares the same
    function."""
    monkeypatch.setattr(monitor_service, "leads_collection", mock_db.leads)
    monkeypatch.setattr(monitor_service, "users_collection", mock_db.users)
    monkeypatch.setattr(
        "app.services.matching_service.determine_best_pro",
        AsyncMock(return_value={"_id": "new_pro", "phone_number": "972500000002"}),
    )
    await mock_db.leads.delete_many({})
    await mock_db.users.delete_many({})
    lead = await _insert_exhausted_lead(
        mock_db, reassignment_count=WorkerConstants.MAX_REASSIGNMENTS - 1
    )

    result = await reassign_lead(lead)

    assert result is True
    updated = await mock_db.leads.find_one({"_id": lead["_id"]})
    assert updated["status"] == LeadStatus.NEW
    assert updated["status"] != LeadStatus.PENDING_ADMIN_REVIEW
    assert "escalation_reason" not in updated
    assert updated["pro_id"] == "new_pro"
    assert updated["reassignment_count"] == WorkerConstants.MAX_REASSIGNMENTS

    # Green API constraint — no interactive buttons anywhere in this flow.
    mock_whatsapp.send_interactive_buttons.assert_not_called()
