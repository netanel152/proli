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
from bson import ObjectId

from app.core.constants import LeadStatus, UserStates, WorkerConstants
from app.core.messages import Messages
from app.core.config import settings
from app.core.phone import to_chat_id, to_local_phone
from app.core.redis_client import get_redis_client
from app.services import monitor_service
from app.services.monitor_service import reassign_lead
from app.services.state_manager_service import StateManager


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
async def test_successful_reassignment_tells_customer_who_was_found(
    mock_db, monkeypatch, mock_whatsapp, mock_state_and_context
):
    """On a successful reassign the customer must be told the new pro's name —
    otherwise the thread goes silent after CUSTOMER_REASSIGNING until the new
    pro engages."""
    monkeypatch.setattr(monitor_service, "leads_collection", mock_db.leads)
    monkeypatch.setattr(monitor_service, "users_collection", mock_db.users)
    await mock_db.leads.delete_many({})
    new_pro = {
        "_id": ObjectId(),
        "business_name": "אבי אינסטלציה",
        "phone_number": "972559444143",
    }
    monkeypatch.setattr(
        "app.services.matching_service.determine_best_pro",
        AsyncMock(return_value=new_pro),
    )
    # Isolate the customer-notification assertion from the pro-offer send and the
    # DB write (whose collection wiring varies with suite order).
    monkeypatch.setattr(
        monitor_service, "notify_pro_new_lead", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        monitor_service, "set_lead_status", AsyncMock(return_value=True)
    )
    lead = await _insert_exhausted_lead(mock_db, reassignment_count=0)

    result = await reassign_lead(lead)

    assert result is True
    mock_whatsapp.send_message.assert_any_call(
        lead["chat_id"],
        Messages.Customer.AWAITING_APPROVAL_TRANSPARENT.format(
            pro_name="אבי אינסטלציה"
        ),
    )


@pytest.mark.asyncio
async def test_exhausted_reassignment_pages_admin(
    mock_db, monkeypatch, mock_whatsapp, mock_state_and_context, mock_matching
):
    """PRO-88: the admin is paged via Sentry, never over WhatsApp.

    The admin never messages the bot, so their Cloud API service window is
    permanently closed — this alert would have needed its own approved
    template to keep working.
    """
    monkeypatch.setattr(monitor_service, "leads_collection", mock_db.leads)
    pages = []
    monkeypatch.setattr(monitor_service, "page_operator", pages.append)
    await mock_db.leads.delete_many({})
    lead = await _insert_exhausted_lead(mock_db)

    await reassign_lead(lead)

    admin_chat_id = to_chat_id(settings.ADMIN_PHONE)
    admin_calls = [
        call
        for call in mock_whatsapp.send_message.await_args_list
        if call.args[0] == admin_chat_id
    ]
    assert admin_calls == [], "admin must no longer receive WhatsApp"

    assert len(pages) == 1
    page = pages[0]
    assert str(WorkerConstants.MAX_REASSIGNMENTS) in page
    assert lead["issue_type"] in page
    # Masked, not the full local number — this page is retained in Sentry.
    assert to_local_phone(lead["chat_id"]) not in page
    assert f"***{to_local_phone(lead['chat_id'])[-4:]}" in page


@pytest.mark.asyncio
async def test_exhausted_reassignment_survives_admin_alert_failure(
    mock_db, monkeypatch, mock_whatsapp, mock_state_and_context, mock_matching
):
    """Admin paging is best-effort — a failed page must not abort the
    escalation. This is the important resilience case.

    PRO-88 changed what "fails" means here: the admin leg is no longer a
    WhatsApp send, so the failure is injected into page_operator itself.
    """
    monkeypatch.setattr(monitor_service, "leads_collection", mock_db.leads)
    await mock_db.leads.delete_many({})
    lead = await _insert_exhausted_lead(mock_db)

    def exploding_page(_summary):
        raise RuntimeError("Sentry transport is down")

    monkeypatch.setattr(monitor_service, "page_operator", exploding_page)

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

    # Text-only menu rule (CLAUDE.md) — no interactive buttons anywhere in this flow.
    mock_whatsapp.send_interactive_buttons.assert_not_called()


# ---------------------------------------------------------------------------
# PRO-117 — notify_old_pro param and the no-usable-location escalation branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reassign_lead_default_notifies_old_pro_of_lost_lead(
    mock_db, monkeypatch, mock_whatsapp, mock_state_and_context
):
    """Default (notify_old_pro=True, the SOS Healer / PRO-56 offer path) must
    keep telling the old pro their lead was reassigned."""
    monkeypatch.setattr(monitor_service, "leads_collection", mock_db.leads)
    monkeypatch.setattr(monitor_service, "users_collection", mock_db.users)
    await mock_db.leads.delete_many({})
    await mock_db.users.delete_many({})

    old_pro_id = ObjectId()
    await mock_db.users.insert_one({"_id": old_pro_id, "phone_number": "972500000003"})
    new_pro = {
        "_id": ObjectId(),
        "business_name": "אבי",
        "phone_number": "972500000002",
    }
    monkeypatch.setattr(
        "app.services.matching_service.determine_best_pro",
        AsyncMock(return_value=new_pro),
    )
    monkeypatch.setattr(
        monitor_service, "notify_pro_new_lead", AsyncMock(return_value=True)
    )
    lead = await _insert_exhausted_lead(
        mock_db, reassignment_count=0, pro_id=old_pro_id
    )

    result = await reassign_lead(lead)

    assert result is True
    from app.core.phone import to_chat_id

    mock_whatsapp.send_message.assert_any_call(
        to_chat_id("972500000003"), Messages.SOS.PRO_LOST_LEAD
    )


@pytest.mark.asyncio
async def test_reassign_lead_notify_old_pro_false_skips_lost_lead_message(
    mock_db, monkeypatch, mock_whatsapp, mock_state_and_context
):
    """PRO-117 — a pro who explicitly rejected already got the reject
    acknowledgement; they must NOT also get PRO_LOST_LEAD ("הועברה עקב חוסר
    מענה"), which is the wrong message for an explicit action."""
    monkeypatch.setattr(monitor_service, "leads_collection", mock_db.leads)
    monkeypatch.setattr(monitor_service, "users_collection", mock_db.users)
    await mock_db.leads.delete_many({})
    await mock_db.users.delete_many({})

    old_pro_id = ObjectId()
    await mock_db.users.insert_one({"_id": old_pro_id, "phone_number": "972500000003"})
    new_pro = {
        "_id": ObjectId(),
        "business_name": "אבי",
        "phone_number": "972500000002",
    }
    monkeypatch.setattr(
        "app.services.matching_service.determine_best_pro",
        AsyncMock(return_value=new_pro),
    )
    monkeypatch.setattr(
        monitor_service, "notify_pro_new_lead", AsyncMock(return_value=True)
    )
    lead = await _insert_exhausted_lead(
        mock_db, reassignment_count=0, pro_id=old_pro_id
    )

    result = await reassign_lead(lead, notify_old_pro=False)

    assert result is True
    for call in mock_whatsapp.send_message.await_args_list:
        assert call.args[1] != Messages.SOS.PRO_LOST_LEAD


@pytest.mark.asyncio
async def test_reassign_lead_no_usable_location_notifies_customer(
    mock_db, monkeypatch, mock_whatsapp, mock_state_and_context, mock_matching
):
    """PRO-117 — this branch used to escalate silently (state cleared, no
    message at all). It must now fail-open like the other two escalation
    branches: tell the customer, then clear state and context."""
    monkeypatch.setattr(monitor_service, "leads_collection", mock_db.leads)
    await mock_db.leads.delete_many({})
    lead = await _insert_exhausted_lead(
        mock_db, reassignment_count=0, full_address=None
    )

    result = await reassign_lead(lead)

    assert result is False
    updated = await mock_db.leads.find_one({"_id": lead["_id"]})
    assert updated["status"] == LeadStatus.PENDING_ADMIN_REVIEW
    assert updated["escalation_reason"] == "no_usable_location"

    mock_whatsapp.send_message.assert_any_call(
        lead["chat_id"], Messages.Customer.PENDING_REVIEW
    )
    mock_matching.assert_not_awaited()

    state_mgr, context_mgr = mock_state_and_context
    state_mgr.clear_state.assert_awaited_once_with(lead["chat_id"])
    context_mgr.clear_context.assert_awaited_once_with(lead["chat_id"])


@pytest.mark.asyncio
async def test_reassign_lead_new_write_guard_skips_on_concurrent_status_change(
    mock_db, monkeypatch, mock_whatsapp
):
    """PRO-117 gap 6 — the success-path NEW write is itself guarded with
    ``expected_status=lead.get('status')`` now (a pro's reject and the
    Healer's tick can race in different processes). If the DB doc has already
    moved by the time this caller tries to write NEW, the guard must fail
    closed: no pro notified, no NEW write, no "found you someone" message —
    only the unconditional CUSTOMER_REASSIGNING notice that fires before the
    matching round."""
    monkeypatch.setattr(monitor_service, "leads_collection", mock_db.leads)
    await mock_db.leads.delete_many({})
    new_pro = {
        "_id": ObjectId(),
        "business_name": "אבי",
        "phone_number": "972500000002",
    }
    monkeypatch.setattr(
        "app.services.matching_service.determine_best_pro",
        AsyncMock(return_value=new_pro),
    )
    notify_pro = AsyncMock(return_value=True)
    monkeypatch.setattr(monitor_service, "notify_pro_new_lead", notify_pro)

    lead = await _insert_exhausted_lead(
        mock_db, status=LeadStatus.PENDING_ADMIN_REVIEW, reassignment_count=0
    )
    # Stale read: a concurrent caller already moved the DB doc, but this
    # caller's in-memory copy still says NEW.
    stale_lead = dict(lead)
    stale_lead["status"] = LeadStatus.NEW

    result = await reassign_lead(stale_lead)

    assert result is False
    notify_pro.assert_not_awaited()

    updated = await mock_db.leads.find_one({"_id": lead["_id"]})
    assert updated["status"] == LeadStatus.PENDING_ADMIN_REVIEW
    assert updated["pro_id"] == "old_pro"
    assert "reassignment_count" not in updated or updated["reassignment_count"] == 0

    mock_whatsapp.send_message.assert_any_call(
        lead["chat_id"], Messages.SOS.CUSTOMER_REASSIGNING
    )
    transparent_msg = Messages.Customer.AWAITING_APPROVAL_TRANSPARENT.format(
        pro_name=new_pro["business_name"]
    )
    for call in mock_whatsapp.send_message.await_args_list:
        assert call.args[1] != transparent_msg


@pytest.mark.asyncio
async def test_reassign_lead_no_usable_location_survives_notify_failure(
    mock_db, monkeypatch, mock_whatsapp, mock_state_and_context, mock_matching
):
    """The customer notify on this branch is best-effort -- a failed send
    must not stop the escalation or the state/context cleanup."""
    monkeypatch.setattr(monitor_service, "leads_collection", mock_db.leads)
    await mock_db.leads.delete_many({})
    mock_whatsapp.send_message.side_effect = RuntimeError("wa down")
    lead = await _insert_exhausted_lead(
        mock_db, reassignment_count=0, full_address=None
    )

    result = await reassign_lead(lead)

    assert result is False
    updated = await mock_db.leads.find_one({"_id": lead["_id"]})
    assert updated["status"] == LeadStatus.PENDING_ADMIN_REVIEW
    assert updated["escalation_reason"] == "no_usable_location"

    state_mgr, context_mgr = mock_state_and_context
    state_mgr.clear_state.assert_awaited_once_with(lead["chat_id"])
    context_mgr.clear_context.assert_awaited_once_with(lead["chat_id"])


# ---------------------------------------------------------------------------
# PRO-117 (re-review) — conditional re-arm on the success path: a CONTACTED
# lead the Healer swept was never finalized and keeps the old clear-state
# semantics; every other status (NEW from the PRO-56 "1" reply, REJECTED from
# a pro reject) re-arms the approval SLA instead. Real (fakeredis-backed)
# StateManager, not a mock, so both the value and the TTL are observable.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reassign_lead_contacted_lead_clears_state_instead_of_rearming(
    mock_db, monkeypatch, mock_whatsapp
):
    """A CONTACTED lead may still be mid-conversation (AWAITING_ADDRESS/MEDIA/
    TIME/CONSENT) -- re-arming AWAITING_PRO_APPROVAL would soft-hold every
    message the customer sends for PRO_APPROVAL_TTL_SECONDS. This branch keeps
    the old clear-state behaviour so the next message reaches the AI."""
    monkeypatch.setattr(monitor_service, "leads_collection", mock_db.leads)
    monkeypatch.setattr(monitor_service, "users_collection", mock_db.users)
    await mock_db.leads.delete_many({})
    new_pro = {
        "_id": ObjectId(),
        "business_name": "אבי",
        "phone_number": "972500000002",
    }
    monkeypatch.setattr(
        "app.services.matching_service.determine_best_pro",
        AsyncMock(return_value=new_pro),
    )
    monkeypatch.setattr(
        monitor_service, "notify_pro_new_lead", AsyncMock(return_value=True)
    )
    lead = await _insert_exhausted_lead(
        mock_db, status=LeadStatus.CONTACTED, reassignment_count=0
    )
    # Pre-set a mid-conversation state so a passing assertion proves it was
    # actually cleared, not just never written.
    await StateManager.set_state(lead["chat_id"], UserStates.AWAITING_ADDRESS)

    result = await reassign_lead(lead)

    assert result is True
    assert await StateManager.get_state(lead["chat_id"]) == UserStates.IDLE


@pytest.mark.asyncio
async def test_reassign_lead_non_contacted_lead_rearms_approval_sla(
    mock_db, monkeypatch, mock_whatsapp
):
    """NEW (PRO-56 "1" reply) and REJECTED (pro reject) leads are already in
    the approval funnel -- the success path must re-arm AWAITING_PRO_APPROVAL
    with PRO_APPROVAL_TTL_SECONDS so the nudge/reassign-offer stays live for
    the new pro, rather than clearing state."""
    monkeypatch.setattr(monitor_service, "leads_collection", mock_db.leads)
    monkeypatch.setattr(monitor_service, "users_collection", mock_db.users)
    await mock_db.leads.delete_many({})
    new_pro = {
        "_id": ObjectId(),
        "business_name": "אבי",
        "phone_number": "972500000002",
    }
    monkeypatch.setattr(
        "app.services.matching_service.determine_best_pro",
        AsyncMock(return_value=new_pro),
    )
    monkeypatch.setattr(
        monitor_service, "notify_pro_new_lead", AsyncMock(return_value=True)
    )
    lead = await _insert_exhausted_lead(
        mock_db, status=LeadStatus.NEW, reassignment_count=0
    )

    result = await reassign_lead(lead)

    assert result is True
    assert (
        await StateManager.get_state(lead["chat_id"])
        == UserStates.AWAITING_PRO_APPROVAL
    )

    redis = await get_redis_client()
    ttl = await redis.ttl(f"state:{lead['chat_id']}")
    assert 0 < ttl <= WorkerConstants.PRO_APPROVAL_TTL_SECONDS


# ---------------------------------------------------------------------------
# PRO-125 (code half) — a failed pro offer is an assignment failure, not a
# silent success. ``notify_pro_new_lead`` returning False (PRO-159 made the
# return honest: closed 24h window / breaker / raised send) must now make
# ``reassign_lead`` escalate to PENDING_ADMIN_REVIEW instead of reporting
# "נמצא לך איש מקצוע" over a pro who never saw the offer.
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_page_operator(monkeypatch):
    pages = []
    monkeypatch.setattr(monitor_service, "page_operator", pages.append)
    return pages


@pytest.mark.asyncio
async def test_reassign_lead_offer_send_failed_escalates_to_pending_review(
    mock_db, monkeypatch, mock_whatsapp, mock_state_and_context, mock_page_operator
):
    """PRO-125 — a genuine offer-send failure (closed window / breaker /
    raised send) must escalate the lead to PENDING_ADMIN_REVIEW with
    escalation_reason=pro_offer_send_failed rather than pretending the
    reassignment succeeded."""
    monkeypatch.setattr(monitor_service, "leads_collection", mock_db.leads)
    monkeypatch.setattr(monitor_service, "users_collection", mock_db.users)
    await mock_db.leads.delete_many({})
    new_pro = {
        "_id": ObjectId(),
        "business_name": "אבי",
        "phone_number": "972500000002",
    }
    monkeypatch.setattr(
        "app.services.matching_service.determine_best_pro",
        AsyncMock(return_value=new_pro),
    )
    monkeypatch.setattr(
        monitor_service, "notify_pro_new_lead", AsyncMock(return_value=False)
    )
    lead = await _insert_exhausted_lead(mock_db, reassignment_count=0)

    result = await reassign_lead(lead)

    assert result is False
    updated = await mock_db.leads.find_one({"_id": lead["_id"]})
    assert updated["status"] == LeadStatus.PENDING_ADMIN_REVIEW
    assert updated["escalation_reason"] == "pro_offer_send_failed"


@pytest.mark.asyncio
async def test_reassign_lead_offer_send_failed_notifies_customer_pending_review_only(
    mock_db, monkeypatch, mock_whatsapp, mock_state_and_context, mock_page_operator
):
    """The customer must get PENDING_REVIEW on this path, never the
    "we found you a pro" transparent message — that pro never received the
    offer."""
    monkeypatch.setattr(monitor_service, "leads_collection", mock_db.leads)
    monkeypatch.setattr(monitor_service, "users_collection", mock_db.users)
    await mock_db.leads.delete_many({})
    new_pro = {
        "_id": ObjectId(),
        "business_name": "אבי",
        "phone_number": "972500000002",
    }
    monkeypatch.setattr(
        "app.services.matching_service.determine_best_pro",
        AsyncMock(return_value=new_pro),
    )
    monkeypatch.setattr(
        monitor_service, "notify_pro_new_lead", AsyncMock(return_value=False)
    )
    lead = await _insert_exhausted_lead(mock_db, reassignment_count=0)

    result = await reassign_lead(lead)

    assert result is False
    mock_whatsapp.send_message.assert_any_call(
        lead["chat_id"], Messages.Customer.PENDING_REVIEW
    )
    transparent_msg = Messages.Customer.AWAITING_APPROVAL_TRANSPARENT.format(
        pro_name=new_pro["business_name"]
    )
    for call in mock_whatsapp.send_message.await_args_list:
        assert call.args[1] != transparent_msg


@pytest.mark.asyncio
async def test_reassign_lead_offer_send_failed_never_sends_pro_lost_lead(
    mock_db, monkeypatch, mock_whatsapp, mock_state_and_context, mock_page_operator
):
    """The old-pro PRO_LOST_LEAD leg must not fire on this path either — the
    reassignment itself never landed."""
    monkeypatch.setattr(monitor_service, "leads_collection", mock_db.leads)
    monkeypatch.setattr(monitor_service, "users_collection", mock_db.users)
    await mock_db.leads.delete_many({})
    await mock_db.users.delete_many({})
    old_pro_id = ObjectId()
    await mock_db.users.insert_one({"_id": old_pro_id, "phone_number": "972500000003"})
    new_pro = {
        "_id": ObjectId(),
        "business_name": "אבי",
        "phone_number": "972500000002",
    }
    monkeypatch.setattr(
        "app.services.matching_service.determine_best_pro",
        AsyncMock(return_value=new_pro),
    )
    monkeypatch.setattr(
        monitor_service, "notify_pro_new_lead", AsyncMock(return_value=False)
    )
    lead = await _insert_exhausted_lead(
        mock_db, reassignment_count=0, pro_id=old_pro_id
    )

    await reassign_lead(lead)

    for call in mock_whatsapp.send_message.await_args_list:
        assert call.args[1] != Messages.SOS.PRO_LOST_LEAD


@pytest.mark.asyncio
async def test_reassign_lead_offer_send_failed_pages_operator(
    mock_db, monkeypatch, mock_whatsapp, mock_state_and_context, mock_page_operator
):
    """An offer-send failure must page the operator — a human needs to
    manually reach the pro or reclaim the lead."""
    monkeypatch.setattr(monitor_service, "leads_collection", mock_db.leads)
    monkeypatch.setattr(monitor_service, "users_collection", mock_db.users)
    await mock_db.leads.delete_many({})
    new_pro = {
        "_id": ObjectId(),
        "business_name": "אבי",
        "phone_number": "972500000002",
    }
    monkeypatch.setattr(
        "app.services.matching_service.determine_best_pro",
        AsyncMock(return_value=new_pro),
    )
    monkeypatch.setattr(
        monitor_service, "notify_pro_new_lead", AsyncMock(return_value=False)
    )
    lead = await _insert_exhausted_lead(mock_db, reassignment_count=0)

    await reassign_lead(lead)

    assert len(mock_page_operator) == 1
    assert str(lead["_id"]) in mock_page_operator[0]


@pytest.mark.asyncio
async def test_reassign_lead_offer_send_failed_clears_state_and_context(
    mock_db, monkeypatch, mock_whatsapp, mock_state_and_context, mock_page_operator
):
    monkeypatch.setattr(monitor_service, "leads_collection", mock_db.leads)
    monkeypatch.setattr(monitor_service, "users_collection", mock_db.users)
    await mock_db.leads.delete_many({})
    new_pro = {
        "_id": ObjectId(),
        "business_name": "אבי",
        "phone_number": "972500000002",
    }
    monkeypatch.setattr(
        "app.services.matching_service.determine_best_pro",
        AsyncMock(return_value=new_pro),
    )
    monkeypatch.setattr(
        monitor_service, "notify_pro_new_lead", AsyncMock(return_value=False)
    )
    lead = await _insert_exhausted_lead(mock_db, reassignment_count=0)

    await reassign_lead(lead)

    state_mgr, context_mgr = mock_state_and_context
    state_mgr.clear_state.assert_awaited_once_with(lead["chat_id"])
    context_mgr.clear_context.assert_awaited_once_with(lead["chat_id"])


@pytest.mark.asyncio
async def test_reassign_lead_offer_send_failed_survives_customer_notify_failure(
    mock_db, monkeypatch, mock_whatsapp, mock_state_and_context, mock_page_operator
):
    """PENDING_REVIEW notify is fail-open — a failed send must not stop the
    escalation or the state/context cleanup."""
    monkeypatch.setattr(monitor_service, "leads_collection", mock_db.leads)
    monkeypatch.setattr(monitor_service, "users_collection", mock_db.users)
    await mock_db.leads.delete_many({})
    new_pro = {
        "_id": ObjectId(),
        "business_name": "אבי",
        "phone_number": "972500000002",
    }
    monkeypatch.setattr(
        "app.services.matching_service.determine_best_pro",
        AsyncMock(return_value=new_pro),
    )
    monkeypatch.setattr(
        monitor_service, "notify_pro_new_lead", AsyncMock(return_value=False)
    )
    mock_whatsapp.send_message.side_effect = RuntimeError("wa down")
    lead = await _insert_exhausted_lead(mock_db, reassignment_count=0)

    result = await reassign_lead(lead)

    assert result is False
    updated = await mock_db.leads.find_one({"_id": lead["_id"]})
    assert updated["status"] == LeadStatus.PENDING_ADMIN_REVIEW
    assert updated["escalation_reason"] == "pro_offer_send_failed"

    state_mgr, context_mgr = mock_state_and_context
    state_mgr.clear_state.assert_awaited_once_with(lead["chat_id"])
    context_mgr.clear_context.assert_awaited_once_with(lead["chat_id"])


@pytest.mark.asyncio
async def test_reassign_lead_offer_send_failed_escalation_guard_loses_returns_false_quietly(
    mock_db, monkeypatch, mock_whatsapp, mock_state_and_context, mock_page_operator
):
    """If a concurrent caller moves the lead off NEW between our claim write
    and the offer-failure escalation, the `expected_status=NEW` guard on the
    escalation write must lose gracefully: no PENDING_REVIEW message, no
    admin page, no state/context churn — just False, leaving the lead to
    whoever else is now handling it."""
    monkeypatch.setattr(monitor_service, "leads_collection", mock_db.leads)
    monkeypatch.setattr(monitor_service, "users_collection", mock_db.users)
    await mock_db.leads.delete_many({})
    new_pro = {
        "_id": ObjectId(),
        "business_name": "אבי",
        "phone_number": "972500000002",
    }
    monkeypatch.setattr(
        "app.services.matching_service.determine_best_pro",
        AsyncMock(return_value=new_pro),
    )
    monkeypatch.setattr(
        monitor_service, "notify_pro_new_lead", AsyncMock(return_value=False)
    )
    lead = await _insert_exhausted_lead(mock_db, reassignment_count=0)

    # First set_lead_status call (the NEW claim) behaves for real; the second
    # (the offer-failure escalation) simulates a concurrent writer having
    # already moved the lead off NEW by returning None, as the real
    # expected_status guard would.
    real_set_lead_status = monitor_service.set_lead_status
    calls = {"n": 0}

    async def flaky_set_lead_status(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return await real_set_lead_status(*args, **kwargs)
        return None

    monkeypatch.setattr(monitor_service, "set_lead_status", flaky_set_lead_status)

    result = await reassign_lead(lead)

    assert result is False
    assert calls["n"] == 2
    assert mock_page_operator == []
    for call in mock_whatsapp.send_message.await_args_list:
        assert call.args[1] != Messages.Customer.PENDING_REVIEW
    state_mgr, context_mgr = mock_state_and_context
    state_mgr.clear_state.assert_not_awaited()
    context_mgr.clear_context.assert_not_awaited()
