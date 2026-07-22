"""
Tests for admin_flow.py: the admin routing wizard.

Covers the `ניהול` keyword entry and the three wizard states
(ADMIN_SELECTING_LEAD → ADMIN_SELECTING_ACTION → ADMIN_SELECTING_PRO),
self-assign vs. show-pros branches, and the cancel / invalid-input edges.

admin_flow does `from app.core.database import leads_collection, users_collection`,
so conftest's autouse patching does NOT reach it — each test monkeypatches the
module-level collections directly (mirrors test_sos_monitor.py).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone
from bson import ObjectId

import app.services.admin_flow as admin_flow
import app.services.matching_service as matching_service
from app.core.config import settings
from app.core.constants import LeadStatus, UserStates, WorkerConstants


@pytest.fixture
def patch_admin_collections(monkeypatch, mock_db):
    """Point admin_flow at the in-memory mongomock collections."""
    monkeypatch.setattr(admin_flow, "leads_collection", mock_db.leads)
    monkeypatch.setattr(admin_flow, "users_collection", mock_db.users)
    return mock_db


@pytest.fixture
def mock_whatsapp():
    wa = MagicMock()
    wa.send_message = AsyncMock()
    return wa


@pytest.fixture
def mock_state():
    sm = MagicMock()
    sm.set_state = AsyncMock()
    sm.clear_state = AsyncMock()
    sm.set_metadata = AsyncMock()
    sm.get_metadata = AsyncMock(return_value={})
    return sm


def _sent_text(mock_whatsapp):
    """Concatenate every message body sent, for substring assertions."""
    return " ".join(str(c.args[1]) for c in mock_whatsapp.send_message.call_args_list)


# --- ניהול entry -----------------------------------------------------------


@pytest.mark.asyncio
async def test_nihul_with_no_stuck_leads_clears_state(
    patch_admin_collections, mock_state, mock_whatsapp
):
    await patch_admin_collections.leads.delete_many({})

    await admin_flow.handle_admin_message(
        "admin@c.us", "ניהול", UserStates.IDLE, mock_state, None, mock_whatsapp, None
    )

    mock_state.clear_state.assert_awaited_once_with("admin@c.us")
    assert "אין לידים תקועים" in _sent_text(mock_whatsapp)
    mock_state.set_state.assert_not_called()


@pytest.mark.asyncio
async def test_nihul_lists_pending_leads_and_enters_lead_selection(
    patch_admin_collections, mock_state, mock_whatsapp
):
    db = patch_admin_collections
    await db.leads.delete_many({})
    await db.leads.insert_one(
        {
            "_id": ObjectId(),
            "status": LeadStatus.PENDING_ADMIN_REVIEW,
            "city": "תל אביב",
            "issue_type": "נזילה",
            "created_at": datetime.now(timezone.utc),
        }
    )

    await admin_flow.handle_admin_message(
        "admin@c.us", "ניהול", UserStates.IDLE, mock_state, None, mock_whatsapp, None
    )

    # Entered lead-selection state with a leads map in metadata
    mock_state.set_state.assert_awaited_once()
    assert mock_state.set_state.call_args.args[1] == UserStates.ADMIN_SELECTING_LEAD
    meta = mock_state.set_metadata.call_args.args[1]
    assert "admin_leads_context" in meta
    assert "1" in meta["admin_leads_context"]
    assert "תל אביב" in _sent_text(mock_whatsapp)


# --- ADMIN_SELECTING_LEAD --------------------------------------------------


@pytest.mark.asyncio
async def test_lead_selection_valid_moves_to_action(
    patch_admin_collections, mock_state, mock_whatsapp
):
    lead_id = str(ObjectId())
    mock_state.get_metadata.return_value = {"admin_leads_context": {"1": lead_id}}

    await admin_flow.handle_admin_message(
        "admin@c.us",
        "1",
        UserStates.ADMIN_SELECTING_LEAD,
        mock_state,
        None,
        mock_whatsapp,
        None,
    )

    assert mock_state.set_state.call_args.args[1] == UserStates.ADMIN_SELECTING_ACTION
    saved_meta = mock_state.set_metadata.call_args.args[1]
    assert saved_meta["selected_lead_id"] == lead_id
    assert "למי להעביר" in _sent_text(mock_whatsapp)


@pytest.mark.asyncio
async def test_lead_selection_invalid_number_preserves_state(
    patch_admin_collections, mock_state, mock_whatsapp
):
    mock_state.get_metadata.return_value = {
        "admin_leads_context": {"1": str(ObjectId())}
    }

    await admin_flow.handle_admin_message(
        "admin@c.us",
        "99",
        UserStates.ADMIN_SELECTING_LEAD,
        mock_state,
        None,
        mock_whatsapp,
        None,
    )

    assert "לא חוקי" in _sent_text(mock_whatsapp)
    mock_state.set_state.assert_not_called()
    mock_state.clear_state.assert_not_called()


@pytest.mark.asyncio
async def test_lead_selection_cancel_clears_state(
    patch_admin_collections, mock_state, mock_whatsapp
):
    await admin_flow.handle_admin_message(
        "admin@c.us",
        "בטל",
        UserStates.ADMIN_SELECTING_LEAD,
        mock_state,
        None,
        mock_whatsapp,
        None,
    )

    mock_state.clear_state.assert_awaited_once_with("admin@c.us")
    assert "בוטל" in _sent_text(mock_whatsapp)


# --- ADMIN_SELECTING_ACTION ------------------------------------------------


@pytest.mark.asyncio
async def test_action_self_assign_assigns_lead_to_admin_pro(
    patch_admin_collections, mock_state, mock_whatsapp, monkeypatch
):
    db = patch_admin_collections
    monkeypatch.setattr(settings, "ADMIN_PHONE", "972524828796")

    admin_pro_id = ObjectId()
    await db.users.insert_one(
        {
            "_id": admin_pro_id,
            "phone_number": "972524828796",
            "role": "professional",
            "business_name": "מנהל המערכת",
        }
    )
    lead_id = ObjectId()
    await db.leads.insert_one(
        {
            "_id": lead_id,
            "status": LeadStatus.PENDING_ADMIN_REVIEW,
            "chat_id": "customer@c.us",
            "full_address": "תל אביב",
            "issue_type": "נזילה",
        }
    )
    mock_state.get_metadata.return_value = {"selected_lead_id": str(lead_id)}

    await admin_flow.handle_admin_message(
        "admin@c.us",
        "1",
        UserStates.ADMIN_SELECTING_ACTION,
        mock_state,
        None,
        mock_whatsapp,
        None,
    )

    updated = await db.leads.find_one({"_id": lead_id})
    assert updated["status"] == LeadStatus.NEW
    assert updated["pro_id"] == admin_pro_id
    mock_state.clear_state.assert_awaited()
    assert "הועבר" in _sent_text(mock_whatsapp)


@pytest.mark.asyncio
async def test_self_assign_resets_reassignment_lifecycle_after_escalation(
    patch_admin_collections, mock_state, mock_whatsapp, monkeypatch
):
    """PRO-63 Fix 2 — a human taking ownership of a lead escalated for
    exhausted reassignments must reset the lifecycle (reassignment_count,
    approval_nudged, reassign_offered, pro_notified_at, escalation_reason) or
    the very next Healer sweep re-escalates it straight off the admin and
    re-pages, forever."""
    db = patch_admin_collections
    monkeypatch.setattr(settings, "ADMIN_PHONE", "972524828796")

    admin_pro_id = ObjectId()
    await db.users.insert_one(
        {
            "_id": admin_pro_id,
            "phone_number": "972524828796",
            "role": "professional",
            "business_name": "מנהל המערכת",
        }
    )
    lead_id = ObjectId()
    await db.leads.insert_one(
        {
            "_id": lead_id,
            "status": LeadStatus.PENDING_ADMIN_REVIEW,
            "chat_id": "customer@c.us",
            "full_address": "תל אביב",
            "issue_type": "נזילה",
            "reassignment_count": WorkerConstants.MAX_REASSIGNMENTS,
            "escalation_reason": "max_reassignments_exhausted",
            "approval_nudged": True,
            "reassign_offered": True,
        }
    )
    mock_state.get_metadata.return_value = {"selected_lead_id": str(lead_id)}

    await admin_flow.handle_admin_message(
        "admin@c.us",
        "1",
        UserStates.ADMIN_SELECTING_ACTION,
        mock_state,
        None,
        mock_whatsapp,
        None,
    )

    updated = await db.leads.find_one({"_id": lead_id})
    assert updated["status"] == LeadStatus.NEW
    assert updated["reassignment_count"] == 0
    assert "escalation_reason" not in updated
    assert updated["approval_nudged"] is False
    assert updated["reassign_offered"] is False
    assert updated["pro_notified_at"] is not None


@pytest.mark.asyncio
async def test_action_self_assign_without_admin_pro_profile(
    patch_admin_collections, mock_state, mock_whatsapp, monkeypatch
):
    db = patch_admin_collections
    monkeypatch.setattr(settings, "ADMIN_PHONE", "972524828796")
    await db.users.delete_many({})
    lead_id = ObjectId()
    await db.leads.insert_one(
        {"_id": lead_id, "status": LeadStatus.PENDING_ADMIN_REVIEW}
    )
    mock_state.get_metadata.return_value = {"selected_lead_id": str(lead_id)}

    await admin_flow.handle_admin_message(
        "admin@c.us",
        "1",
        UserStates.ADMIN_SELECTING_ACTION,
        mock_state,
        None,
        mock_whatsapp,
        None,
    )

    assert "נסה אפשרות 2" in _sent_text(mock_whatsapp)
    # Lead untouched
    assert (await db.leads.find_one({"_id": lead_id}))[
        "status"
    ] == LeadStatus.PENDING_ADMIN_REVIEW


@pytest.mark.asyncio
async def test_action_show_pros_lists_and_enters_pro_selection(
    patch_admin_collections, mock_state, mock_whatsapp, monkeypatch
):
    db = patch_admin_collections
    lead_id = ObjectId()
    await db.leads.insert_one(
        {
            "_id": lead_id,
            "status": LeadStatus.PENDING_ADMIN_REVIEW,
            "issue_type": "נזילה",
            "full_address": "תל אביב",
        }
    )
    mock_state.get_metadata.return_value = {"selected_lead_id": str(lead_id)}

    pro = {"_id": ObjectId(), "business_name": "יוסי", "social_proof": {"rating": 4.8}}
    # determine_best_pro is imported inside the function from matching_service.
    fake = AsyncMock(side_effect=[pro, None])
    monkeypatch.setattr(matching_service, "determine_best_pro", fake)

    await admin_flow.handle_admin_message(
        "admin@c.us",
        "2",
        UserStates.ADMIN_SELECTING_ACTION,
        mock_state,
        None,
        mock_whatsapp,
        None,
    )

    assert mock_state.set_state.call_args.args[1] == UserStates.ADMIN_SELECTING_PRO
    saved_meta = mock_state.set_metadata.call_args.args[1]
    assert "admin_pros_context" in saved_meta
    assert "יוסי" in _sent_text(mock_whatsapp)


@pytest.mark.asyncio
async def test_action_show_pros_none_available(
    patch_admin_collections, mock_state, mock_whatsapp, monkeypatch
):
    db = patch_admin_collections
    lead_id = ObjectId()
    await db.leads.insert_one(
        {
            "_id": lead_id,
            "status": LeadStatus.PENDING_ADMIN_REVIEW,
            "issue_type": "נזילה",
            "full_address": "תל אביב",
        }
    )
    mock_state.get_metadata.return_value = {"selected_lead_id": str(lead_id)}
    monkeypatch.setattr(
        matching_service, "determine_best_pro", AsyncMock(return_value=None)
    )

    await admin_flow.handle_admin_message(
        "admin@c.us",
        "2",
        UserStates.ADMIN_SELECTING_ACTION,
        mock_state,
        None,
        mock_whatsapp,
        None,
    )

    assert "לא נמצאו אנשי מקצוע" in _sent_text(mock_whatsapp)
    mock_state.set_state.assert_not_called()


@pytest.mark.asyncio
async def test_action_invalid_option(
    patch_admin_collections, mock_state, mock_whatsapp
):
    mock_state.get_metadata.return_value = {"selected_lead_id": str(ObjectId())}

    await admin_flow.handle_admin_message(
        "admin@c.us",
        "5",
        UserStates.ADMIN_SELECTING_ACTION,
        mock_state,
        None,
        mock_whatsapp,
        None,
    )

    assert "אפשרות לא חוקית" in _sent_text(mock_whatsapp)


# --- ADMIN_SELECTING_PRO ---------------------------------------------------


@pytest.mark.asyncio
async def test_pro_selection_valid_assigns_lead(
    patch_admin_collections, mock_state, mock_whatsapp
):
    db = patch_admin_collections
    pro_id = ObjectId()
    await db.users.insert_one(
        {
            "_id": pro_id,
            "phone_number": "972501234567",
            "role": "professional",
            "business_name": "יוסי",
        }
    )
    lead_id = ObjectId()
    await db.leads.insert_one(
        {
            "_id": lead_id,
            "status": LeadStatus.PENDING_ADMIN_REVIEW,
            "chat_id": "customer@c.us",
            "full_address": "תל אביב",
            "issue_type": "נזילה",
        }
    )
    mock_state.get_metadata.return_value = {
        "selected_lead_id": str(lead_id),
        "admin_pros_context": {"1": str(pro_id)},
    }

    await admin_flow.handle_admin_message(
        "admin@c.us",
        "1",
        UserStates.ADMIN_SELECTING_PRO,
        mock_state,
        None,
        mock_whatsapp,
        None,
    )

    updated = await db.leads.find_one({"_id": lead_id})
    assert updated["status"] == LeadStatus.NEW
    assert updated["pro_id"] == pro_id
    mock_state.clear_state.assert_awaited()
    assert "הועבר" in _sent_text(mock_whatsapp)


@pytest.mark.asyncio
async def test_pro_selection_invalid_preserves_state(
    patch_admin_collections, mock_state, mock_whatsapp
):
    mock_state.get_metadata.return_value = {
        "selected_lead_id": str(ObjectId()),
        "admin_pros_context": {"1": str(ObjectId())},
    }

    await admin_flow.handle_admin_message(
        "admin@c.us",
        "7",
        UserStates.ADMIN_SELECTING_PRO,
        mock_state,
        None,
        mock_whatsapp,
        None,
    )

    assert "לא חוקי" in _sent_text(mock_whatsapp)
    mock_state.clear_state.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_admin_state_resets_silently(
    patch_admin_collections, mock_state, mock_whatsapp
):
    await admin_flow.handle_admin_message(
        "admin@c.us",
        "hello",
        "admin_unknown_state",
        mock_state,
        None,
        mock_whatsapp,
        None,
    )

    mock_state.clear_state.assert_awaited_once_with("admin@c.us")
    mock_whatsapp.send_message.assert_not_called()
