"""
PRO-56 — pro-approval SLA coverage.

check_pro_approval_sla (monitor_service.py):
  * T+APPROVAL_NUDGE_MINUTES (10)         -> nudge the silent pro once.
  * T+APPROVAL_REASSIGN_OFFER_MINUTES(25) -> offer the customer a reassignment once.
  * Emergency leads use half the thresholds (//2 -> 5 / 12).
  * Idempotent via the approval_nudged / reassign_offered boolean flags.
  * Only acts while the customer's Redis state is still AWAITING_PRO_APPROVAL.

Customer 1/2 reply handling (workflow_service.py, AWAITING_PRO_APPROVAL branch):
  * "1" on an offered (reassign_offered=True) lead -> monitor_service.reassign_lead(lead).
  * "2" on an offered lead -> reassign_offered cleared, pro_notified_at bumped,
    REASSIGN_WAIT_ACK sent to the customer.
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, AsyncMock, MagicMock

from app.core.constants import LeadStatus, UserStates, WorkerConstants
from app.core.messages import Messages
from app.services.monitor_service import check_pro_approval_sla


# ---------------------------------------------------------------------------
# check_pro_approval_sla
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_whatsapp():
    with patch("app.services.monitor_service.whatsapp") as mock:
        mock.send_message = AsyncMock()
        yield mock


@pytest.fixture
def mock_state_awaiting_approval(monkeypatch):
    """Customer is parked in AWAITING_PRO_APPROVAL — the SLA job should act."""
    mock_get_state = AsyncMock(return_value=UserStates.AWAITING_PRO_APPROVAL)
    monkeypatch.setattr(
        "app.services.monitor_service.StateManager.get_state", mock_get_state
    )
    return mock_get_state


@pytest.fixture(autouse=True)
def _force_business_hours(monkeypatch):
    """PRO-73 gates the customer offer to business hours. Pin it True so these SLA
    tests are deterministic regardless of wall-clock time; the outside-hours path
    has its own dedicated test."""
    monkeypatch.setattr(
        "app.services.monitor_service.within_business_hours", lambda *a, **k: True
    )


async def _seed_lead(mock_db, minutes_ago, is_emergency=False, pro_id="pro_123"):
    """Insert an assigned NEW lead whose pro was notified `minutes_ago` ago."""
    await mock_db.users.insert_one(
        {"_id": pro_id, "phone_number": "972500000999", "role": "professional"}
    )
    notified_at = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    result = await mock_db.leads.insert_one(
        {
            "chat_id": "customer_sla@c.us",
            "status": LeadStatus.NEW,
            "pro_id": pro_id,
            "pro_notified_at": notified_at,
            "approval_nudged": False,
            "reassign_offered": False,
            "is_emergency": is_emergency,
        }
    )
    return result.inserted_id


# PRODUCTION BUG (found while writing this coverage): `check_pro_approval_sla`
# computes `waited_min = (now - notified_at).total_seconds() / 60` where `now`
# is timezone-aware (`datetime.now(timezone.utc)`) but `notified_at` is the raw
# value read back from Mongo — Motor/mongomock return *naive* UTC datetimes by
# default (no `tz_aware=True` on the client; see app/core/database.py). Every
# other place in this codebase that does this subtraction first normalizes with
# `if x.tzinfo is None: x = x.replace(tzinfo=timezone.utc)` (see
# workflow_service.py:918-919, matching_service.py:225, pro_flow.py:762,
# admin_flow.py:68, scheduling_service.py:140) — monitor_service.py's new
# `check_pro_approval_sla` (PRO-56) is missing that guard for `notified_at`.
# The subtraction raises `TypeError: can't subtract offset-naive and
# offset-aware datetimes`, which is caught by the function's own per-lead
# try/except and only `logger.error`'d — so against a real (non-tz-aware)
# MongoDB the nudge/reassign-offer feature silently never fires for ANY lead.
# NOTE: these tests originally caught a real bug — check_pro_approval_sla
# subtracted an aware `now` from the tz-naive `pro_notified_at` that Mongo
# returns, raising a TypeError swallowed by the per-lead try/except, so the
# feature would never have fired in production. Fixed by normalizing
# `notified_at` to UTC before the subtraction.


@pytest.mark.asyncio
async def test_nudge_fires_at_t_plus_10_non_emergency(
    mock_db, monkeypatch, mock_whatsapp, mock_state_awaiting_approval
):
    monkeypatch.setattr("app.services.monitor_service.leads_collection", mock_db.leads)
    monkeypatch.setattr("app.services.monitor_service.users_collection", mock_db.users)
    await mock_db.leads.delete_many({})
    await mock_db.users.delete_many({})

    lead_id = await _seed_lead(mock_db, minutes_ago=12)

    await check_pro_approval_sla()

    # Pro gets the nudge; the customer gets nothing (offer threshold not reached).
    mock_whatsapp.send_message.assert_called_once_with(
        "972500000999@c.us",
        Messages.Pro.APPROVAL_NUDGE.format(
            minutes=WorkerConstants.APPROVAL_NUDGE_MINUTES
        ),
    )

    updated = await mock_db.leads.find_one({"_id": lead_id})
    assert updated["approval_nudged"] is True
    assert updated["reassign_offered"] is False


@pytest.mark.asyncio
async def test_reassign_offer_fires_at_t_plus_25_non_emergency(
    mock_db, monkeypatch, mock_whatsapp, mock_state_awaiting_approval
):
    monkeypatch.setattr("app.services.monitor_service.leads_collection", mock_db.leads)
    monkeypatch.setattr("app.services.monitor_service.users_collection", mock_db.users)
    await mock_db.leads.delete_many({})
    await mock_db.users.delete_many({})

    lead_id = await _seed_lead(mock_db, minutes_ago=26)

    await check_pro_approval_sla()

    # Customer gets the reassignment offer; the pro is never nudged for this tick
    # (the offer branch `continue`s before the nudge check runs).
    mock_whatsapp.send_message.assert_called_once_with(
        "customer_sla@c.us", Messages.Customer.REASSIGN_OFFER
    )

    updated = await mock_db.leads.find_one({"_id": lead_id})
    assert updated["reassign_offered"] is True
    assert updated["approval_nudged"] is False


@pytest.mark.asyncio
async def test_offer_skipped_outside_business_hours(
    mock_db, monkeypatch, mock_whatsapp, mock_state_awaiting_approval
):
    """PRO-73: outside business hours the customer offer is NOT sent —
    reassign_offered stays False so it fires on the next in-hours tick."""
    monkeypatch.setattr(
        "app.services.monitor_service.within_business_hours", lambda *a, **k: False
    )
    monkeypatch.setattr("app.services.monitor_service.leads_collection", mock_db.leads)
    monkeypatch.setattr("app.services.monitor_service.users_collection", mock_db.users)
    await mock_db.leads.delete_many({})
    await mock_db.users.delete_many({})

    lead_id = await _seed_lead(mock_db, minutes_ago=26)
    # Pre-mark as nudged so the (ungated) nudge branch is a no-op, isolating the offer gate.
    await mock_db.leads.update_one(
        {"_id": lead_id}, {"$set": {"approval_nudged": True}}
    )

    await check_pro_approval_sla()

    for call in mock_whatsapp.send_message.call_args_list:
        assert Messages.Customer.REASSIGN_OFFER not in call.args
    updated = await mock_db.leads.find_one({"_id": lead_id})
    assert updated["reassign_offered"] is False


@pytest.mark.asyncio
async def test_nothing_fires_before_nudge_threshold(
    mock_db, monkeypatch, mock_whatsapp, mock_state_awaiting_approval
):
    """Legitimate no-op case: waited (3m) is below both thresholds.

    Also asserts logger.error was never hit — without this guard the assertion
    below would trivially pass even if check_pro_approval_sla crashed on every
    lead (the original tz-naive-datetime bug), since a swallowed exception also
    results in "no message sent".
    """
    monkeypatch.setattr("app.services.monitor_service.leads_collection", mock_db.leads)
    monkeypatch.setattr("app.services.monitor_service.users_collection", mock_db.users)
    await mock_db.leads.delete_many({})
    await mock_db.users.delete_many({})

    lead_id = await _seed_lead(mock_db, minutes_ago=3)

    with patch("app.services.monitor_service.logger") as mock_logger:
        await check_pro_approval_sla()
        mock_logger.error.assert_not_called()

    mock_whatsapp.send_message.assert_not_called()

    updated = await mock_db.leads.find_one({"_id": lead_id})
    assert updated["approval_nudged"] is False
    assert updated["reassign_offered"] is False


@pytest.mark.asyncio
async def test_nudge_is_idempotent_across_runs(
    mock_db, monkeypatch, mock_whatsapp, mock_state_awaiting_approval
):
    """Running the SLA sweep twice must not double-nudge the same pro."""
    monkeypatch.setattr("app.services.monitor_service.leads_collection", mock_db.leads)
    monkeypatch.setattr("app.services.monitor_service.users_collection", mock_db.users)
    await mock_db.leads.delete_many({})
    await mock_db.users.delete_many({})

    await _seed_lead(mock_db, minutes_ago=12)

    await check_pro_approval_sla()
    await check_pro_approval_sla()

    mock_whatsapp.send_message.assert_called_once_with(
        "972500000999@c.us",
        Messages.Pro.APPROVAL_NUDGE.format(
            minutes=WorkerConstants.APPROVAL_NUDGE_MINUTES
        ),
    )


@pytest.mark.asyncio
async def test_emergency_lead_uses_halved_thresholds(
    mock_db, monkeypatch, mock_whatsapp, mock_state_awaiting_approval
):
    monkeypatch.setattr("app.services.monitor_service.leads_collection", mock_db.leads)
    monkeypatch.setattr("app.services.monitor_service.users_collection", mock_db.users)
    await mock_db.leads.delete_many({})
    await mock_db.users.delete_many({})

    # 6 minutes would NOT trip the normal 10-min nudge threshold, but emergency
    # leads halve it to 5, so it should fire here.
    lead_id = await _seed_lead(mock_db, minutes_ago=6, is_emergency=True)

    await check_pro_approval_sla()

    halved_nudge = WorkerConstants.APPROVAL_NUDGE_MINUTES // 2
    mock_whatsapp.send_message.assert_called_once_with(
        "972500000999@c.us",
        Messages.Pro.APPROVAL_NUDGE.format(minutes=halved_nudge),
    )

    updated = await mock_db.leads.find_one({"_id": lead_id})
    assert updated["approval_nudged"] is True


@pytest.mark.asyncio
async def test_customer_not_in_awaiting_approval_state_is_skipped(
    mock_db, monkeypatch, mock_whatsapp
):
    """If the customer already left AWAITING_PRO_APPROVAL, the SLA job is a no-op."""
    monkeypatch.setattr("app.services.monitor_service.leads_collection", mock_db.leads)
    monkeypatch.setattr("app.services.monitor_service.users_collection", mock_db.users)
    monkeypatch.setattr(
        "app.services.monitor_service.StateManager.get_state",
        AsyncMock(return_value=UserStates.IDLE),
    )
    await mock_db.leads.delete_many({})
    await mock_db.users.delete_many({})

    lead_id = await _seed_lead(mock_db, minutes_ago=30)

    await check_pro_approval_sla()

    mock_whatsapp.send_message.assert_not_called()

    updated = await mock_db.leads.find_one({"_id": lead_id})
    assert updated["approval_nudged"] is False
    assert updated["reassign_offered"] is False


# ---------------------------------------------------------------------------
# Customer 1/2 reply handling in workflow_service (AWAITING_PRO_APPROVAL branch)
# ---------------------------------------------------------------------------

import app.services.workflow_service
from app.services.workflow_service import process_incoming_message


@pytest.fixture
def wf_awaiting_approval_mocks(monkeypatch):
    """Minimal workflow mocks with the customer parked in AWAITING_PRO_APPROVAL."""
    mock_wa = MagicMock()
    mock_wa.send_message = AsyncMock()
    mock_wa.send_chat_state_typing = AsyncMock()
    monkeypatch.setattr(app.services.workflow_service, "whatsapp", mock_wa)

    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=UserStates.AWAITING_PRO_APPROVAL)
    mock_state.set_state = AsyncMock()
    mock_state.clear_state = AsyncMock()
    mock_state.get_metadata = AsyncMock(return_value={})
    mock_state.set_metadata = AsyncMock()
    monkeypatch.setattr(app.services.workflow_service, "StateManager", mock_state)

    monkeypatch.setattr(
        app.services.workflow_service, "has_consent", AsyncMock(return_value=True)
    )

    return mock_wa, mock_state


@pytest.mark.asyncio
async def test_customer_reply_1_on_offered_lead_triggers_reassign(
    mock_db, wf_awaiting_approval_mocks, monkeypatch
):
    mock_wa, _ = wf_awaiting_approval_mocks
    chat_id = "972501234567@c.us"
    await mock_db.leads.delete_many({})
    lead_result = await mock_db.leads.insert_one(
        {
            "chat_id": chat_id,
            "status": LeadStatus.NEW,
            "reassign_offered": True,
            "created_at": datetime.now(timezone.utc),
        }
    )

    mock_reassign = AsyncMock()
    monkeypatch.setattr("app.services.monitor_service.reassign_lead", mock_reassign)

    await process_incoming_message(chat_id, "1")

    mock_reassign.assert_awaited_once()
    (called_lead,), _ = mock_reassign.await_args
    assert called_lead["_id"] == lead_result.inserted_id

    # Green API constraint — no interactive buttons anywhere in this flow.
    mock_wa.send_interactive_buttons.assert_not_called()


@pytest.mark.asyncio
async def test_customer_reply_2_on_offered_lead_keeps_waiting(
    mock_db, wf_awaiting_approval_mocks
):
    mock_wa, _ = wf_awaiting_approval_mocks
    chat_id = "972501234568@c.us"
    await mock_db.leads.delete_many({})
    lead_result = await mock_db.leads.insert_one(
        {
            "chat_id": chat_id,
            "status": LeadStatus.NEW,
            "reassign_offered": True,
            "pro_notified_at": datetime.now(timezone.utc) - timedelta(minutes=26),
            "created_at": datetime.now(timezone.utc),
        }
    )

    await process_incoming_message(chat_id, "2")

    mock_wa.send_message.assert_called_once_with(
        chat_id, Messages.Customer.REASSIGN_WAIT_ACK
    )
    mock_wa.send_interactive_buttons.assert_not_called()

    updated = await mock_db.leads.find_one({"_id": lead_result.inserted_id})
    assert updated["reassign_offered"] is False
    assert updated["pro_notified_at"] is not None
    # Timer restarted — should now read as "just notified", not the old 26-min
    # mark. Mongo/mongomock hand back a naive UTC datetime on read even though
    # an aware one was written, so normalize before subtracting (the same guard
    # check_pro_approval_sla applies in production).
    stored_notified_at = updated["pro_notified_at"]
    if stored_notified_at.tzinfo is None:
        stored_notified_at = stored_notified_at.replace(tzinfo=timezone.utc)
    waited = datetime.now(timezone.utc) - stored_notified_at
    assert waited < timedelta(minutes=1)


@pytest.mark.asyncio
async def test_reassign_lead_rearms_sla_fields(mock_db, monkeypatch):
    """A successful reassignment (e.g. customer chose '1') must reset the PRO-56
    SLA clock + flags for the new pro, so the nudge/offer re-arm against them."""
    from app.services import monitor_service

    monkeypatch.setattr(monitor_service, "leads_collection", mock_db.leads)
    monkeypatch.setattr(monitor_service, "users_collection", mock_db.users)
    monkeypatch.setattr(
        "app.services.matching_service.determine_best_pro",
        AsyncMock(return_value={"_id": "new_pro", "phone_number": "972500000002"}),
    )
    monkeypatch.setattr(monitor_service.StateManager, "clear_state", AsyncMock())
    monkeypatch.setattr(
        monitor_service,
        "whatsapp",
        MagicMock(
            send_message=AsyncMock(),
            send_file_by_url=AsyncMock(),
            send_location_link=AsyncMock(),
        ),
    )

    await mock_db.users.insert_one(
        {"_id": "old_pro", "phone_number": "972500000001", "role": "professional"}
    )
    old_notified = datetime.now(timezone.utc) - timedelta(minutes=30)
    res = await mock_db.leads.insert_one(
        {
            "chat_id": "cust_reassign@c.us",
            "status": LeadStatus.NEW,
            "pro_id": "old_pro",
            "full_address": "הרצל 1, תל אביב",
            "issue_type": "leak",
            "pro_notified_at": old_notified,
            "approval_nudged": True,
            "reassign_offered": True,
            "reassignment_count": 0,
        }
    )
    lead = await mock_db.leads.find_one({"_id": res.inserted_id})

    assert await monitor_service.reassign_lead(lead) is True

    updated = await mock_db.leads.find_one({"_id": res.inserted_id})
    assert updated["pro_id"] == "new_pro"
    assert updated["approval_nudged"] is False
    assert updated["reassign_offered"] is False
    ts = updated["pro_notified_at"]
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    assert ts > old_notified  # fresh SLA clock
