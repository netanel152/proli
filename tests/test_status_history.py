"""
Tests for PRO-57: status_history tracking on every lead transition.

Covers:
  A. app.core.lead_history.status_history_entry (pure helper)
  B. app.services.lead_manager_service.set_lead_status (canonical writer)
  C. Full lead lifecycle -> ordered status_history via LeadManager + set_lead_status
  D. Backward compatibility with pre-existing leads that lack status_history
  E. admin_panel/views/analytics.py::_get_status_history_metrics importability
  F. Architectural guard: no direct '$set' of 'status' outside the helper
"""

import re
from pathlib import Path

import pytest
from bson import ObjectId
from datetime import datetime, timedelta, timezone

from app.core.constants import Actor, LeadStatus
from app.core.lead_history import status_history_entry
from app.services.lead_manager_service import LeadManager, set_lead_status


# ---------------------------------------------------------------------------
# A. status_history_entry — pure helper
# ---------------------------------------------------------------------------


def test_status_history_entry_has_expected_keys():
    entry = status_history_entry(LeadStatus.BOOKED, Actor.PRO)
    assert set(entry.keys()) == {"status", "at", "by"}


def test_status_history_entry_at_is_utc_aware_datetime():
    entry = status_history_entry(LeadStatus.NEW, Actor.SYSTEM)
    assert isinstance(entry["at"], datetime)
    assert entry["at"].tzinfo is not None
    assert entry["at"].utcoffset() == timezone.utc.utcoffset(None)


def test_status_history_entry_stores_enum_str_content_not_repr():
    """status/actor must equal their string content ("booked"/"pro"), not the
    Enum repr ("LeadStatus.BOOKED") -- guards against an accidental str() wrap."""
    entry = status_history_entry(LeadStatus.BOOKED, Actor.PRO)
    assert entry["by"] == Actor.PRO
    assert entry["status"] == LeadStatus.BOOKED
    assert entry["by"] == "pro"
    assert entry["status"] == "booked"
    assert "LeadStatus" not in str(entry["status"]) or entry["status"] == "booked"


# ---------------------------------------------------------------------------
# B. set_lead_status — canonical writer behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_lead_status_sets_status_and_appends_one_history_entry(mock_db):
    result = await mock_db.leads.insert_one(
        {
            "chat_id": "status_history_1@c.us",
            "status": LeadStatus.NEW,
            "status_history": [status_history_entry(LeadStatus.NEW, Actor.SYSTEM)],
        }
    )
    lead_id = result.inserted_id

    updated = await set_lead_status(lead_id, LeadStatus.BOOKED, Actor.PRO)

    assert updated is not None
    assert updated["status"] == LeadStatus.BOOKED
    assert "updated_at" in updated

    history = updated["status_history"]
    assert len(history) == 2
    assert history[-1]["by"] == Actor.PRO
    assert history[-1]["status"] == LeadStatus.BOOKED


@pytest.mark.asyncio
async def test_set_lead_status_applies_extra_set_in_same_update(mock_db):
    result = await mock_db.leads.insert_one(
        {
            "chat_id": "status_history_2@c.us",
            "status": LeadStatus.BOOKED,
            "status_history": [status_history_entry(LeadStatus.BOOKED, Actor.PRO)],
        }
    )
    lead_id = result.inserted_id
    completed_at = datetime.now(timezone.utc)

    updated = await set_lead_status(
        lead_id,
        LeadStatus.COMPLETED,
        Actor.CUSTOMER,
        extra_set={"completed_at": completed_at, "waiting_for_rating": True},
    )

    assert updated["status"] == LeadStatus.COMPLETED
    # Mongo (and mongomock) round-trips datetimes at millisecond precision and
    # may drop tzinfo, so compare loosely rather than for exact equality.
    stored = updated["completed_at"]
    assert abs(
        stored.replace(tzinfo=None) - completed_at.replace(tzinfo=None)
    ) < timedelta(seconds=1)
    assert updated["waiting_for_rating"] is True
    assert updated["status_history"][-1]["by"] == Actor.CUSTOMER


@pytest.mark.asyncio
async def test_set_lead_status_applies_extra_unset(mock_db):
    result = await mock_db.leads.insert_one(
        {
            "chat_id": "status_history_3@c.us",
            "status": LeadStatus.BOOKED,
            "booked_slot_id": ObjectId(),
            "status_history": [status_history_entry(LeadStatus.BOOKED, Actor.PRO)],
        }
    )
    lead_id = result.inserted_id

    updated = await set_lead_status(
        lead_id,
        LeadStatus.CANCELLED,
        Actor.CUSTOMER,
        extra_unset={"booked_slot_id": ""},
    )

    assert updated["status"] == LeadStatus.CANCELLED
    assert "booked_slot_id" not in updated


@pytest.mark.asyncio
async def test_set_lead_status_expected_status_guard_blocks_stale_transition(mock_db):
    result = await mock_db.leads.insert_one(
        {
            "chat_id": "status_history_4@c.us",
            "status": LeadStatus.NEW,
            "status_history": [status_history_entry(LeadStatus.NEW, Actor.SYSTEM)],
        }
    )
    lead_id = result.inserted_id

    # Guard expects CONTACTED but the lead is actually NEW -> no match, no write.
    updated = await set_lead_status(
        lead_id, LeadStatus.BOOKED, Actor.PRO, expected_status=LeadStatus.CONTACTED
    )

    assert updated is None
    doc = await mock_db.leads.find_one({"_id": lead_id})
    assert doc["status"] == LeadStatus.NEW
    assert len(doc["status_history"]) == 1


@pytest.mark.asyncio
async def test_set_lead_status_expected_status_guard_allows_matching_transition(
    mock_db,
):
    result = await mock_db.leads.insert_one(
        {
            "chat_id": "status_history_5@c.us",
            "status": LeadStatus.CONTACTED,
            "status_history": [
                status_history_entry(LeadStatus.CONTACTED, Actor.SYSTEM)
            ],
        }
    )
    lead_id = result.inserted_id

    updated = await set_lead_status(
        lead_id, LeadStatus.BOOKED, Actor.PRO, expected_status=LeadStatus.CONTACTED
    )

    assert updated is not None
    assert updated["status"] == LeadStatus.BOOKED
    assert len(updated["status_history"]) == 2


@pytest.mark.asyncio
async def test_set_lead_status_two_sequential_transitions_append_ordered_entries(
    mock_db,
):
    result = await mock_db.leads.insert_one(
        {
            "chat_id": "status_history_6@c.us",
            "status": LeadStatus.NEW,
            "status_history": [status_history_entry(LeadStatus.NEW, Actor.SYSTEM)],
        }
    )
    lead_id = result.inserted_id

    await set_lead_status(lead_id, LeadStatus.CONTACTED, Actor.SYSTEM)
    updated = await set_lead_status(lead_id, LeadStatus.BOOKED, Actor.PRO)

    history = updated["status_history"]
    assert len(history) == 3
    assert [h["status"] for h in history] == [
        LeadStatus.NEW,
        LeadStatus.CONTACTED,
        LeadStatus.BOOKED,
    ]
    assert [h["by"] for h in history] == [Actor.SYSTEM, Actor.SYSTEM, Actor.PRO]
    ats = [h["at"] for h in history]
    assert ats == sorted(ats)


# ---------------------------------------------------------------------------
# C. Full lifecycle -> ordered status_history (key acceptance criterion)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lead_lifecycle_contacted_new_booked_completed_full_ordered_history(
    mock_db,
):
    lead_manager = LeadManager()

    # CONTACTED (seeded automatically by create_lead_from_dict, actor=SYSTEM)
    lead = await lead_manager.create_lead_from_dict(
        chat_id="status_history_lifecycle@c.us",
        issue_type="Leaking pipe",
        status=LeadStatus.CONTACTED,
    )
    lead_id = lead["_id"]
    assert len(lead["status_history"]) == 1
    assert lead["status_history"][0]["by"] == Actor.SYSTEM
    assert lead["status_history"][0]["status"] == LeadStatus.CONTACTED

    # CONTACTED -> NEW (dispatcher assigns a pro), actor=SYSTEM
    await set_lead_status(lead_id, LeadStatus.NEW, Actor.SYSTEM)

    # NEW -> BOOKED (pro approves), actor=PRO, via LeadManager.update_lead_status
    pro_id = ObjectId()
    await lead_manager.update_lead_status(
        str(lead_id), LeadStatus.BOOKED, pro_id=pro_id, actor=Actor.PRO
    )

    # BOOKED -> COMPLETED (customer confirms), actor=CUSTOMER
    completed_at = datetime.now(timezone.utc)
    await set_lead_status(
        lead_id,
        LeadStatus.COMPLETED,
        Actor.CUSTOMER,
        extra_set={"completed_at": completed_at},
    )

    final = await mock_db.leads.find_one({"_id": lead_id})
    assert final["status"] == LeadStatus.COMPLETED
    assert final["pro_id"] == pro_id

    history = final["status_history"]
    assert [h["status"] for h in history] == [
        LeadStatus.CONTACTED,
        LeadStatus.NEW,
        LeadStatus.BOOKED,
        LeadStatus.COMPLETED,
    ]
    assert [h["by"] for h in history] == [
        Actor.SYSTEM,
        Actor.SYSTEM,
        Actor.PRO,
        Actor.CUSTOMER,
    ]

    ats = [h["at"] for h in history]
    assert ats == sorted(ats)


# ---------------------------------------------------------------------------
# D. Backward compatibility — leads created before PRO-57 lack status_history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_lead_status_creates_history_array_for_legacy_lead_without_field(
    mock_db,
):
    """A pre-PRO-57 lead document has no ``status_history`` key at all. The
    $push must still succeed and create the array rather than erroring."""
    result = await mock_db.leads.insert_one(
        {"chat_id": "legacy_no_history@c.us", "status": LeadStatus.BOOKED}
    )
    lead_id = result.inserted_id

    updated = await set_lead_status(lead_id, LeadStatus.COMPLETED, Actor.CUSTOMER)

    assert updated is not None
    assert updated["status"] == LeadStatus.COMPLETED
    assert updated["status_history"] == [
        {
            "status": updated["status_history"][0]["status"],
            "at": updated["status_history"][0]["at"],
            "by": updated["status_history"][0]["by"],
        }
    ]
    assert len(updated["status_history"]) == 1
    assert updated["status_history"][0]["by"] == Actor.CUSTOMER
    assert updated["status_history"][0]["status"] == LeadStatus.COMPLETED


# ---------------------------------------------------------------------------
# E. Analytics — _get_status_history_metrics uses a sync pymongo client, so we
# only assert importability/callability here. The aggregation logic itself
# needs a real Mongo instance (or a heavier pymongo-compatible in-memory
# fixture than mongomock_motor provides) and is left to manual verification /
# integration testing, per the task scope.
# ---------------------------------------------------------------------------


def test_status_history_metrics_function_is_importable_and_callable():
    from admin_panel.views import analytics

    assert callable(analytics._get_status_history_metrics)


# ---------------------------------------------------------------------------
# F. Architectural guard — direct '$set' of 'status' must not exist outside
# the canonical set_lead_status() writer.
# ---------------------------------------------------------------------------

_APP_DIR = Path(__file__).resolve().parent.parent / "app"
_HELPER_FILE = (_APP_DIR / "services" / "lead_manager_service.py").resolve()

# Flat (non-nested) '"$set": { ... }' blocks -- robust to black formatting
# (multi-line dicts, trailing commas) as long as the block itself contains no
# nested braces (e.g. no dict/list literal values inside it).
_SET_BLOCK_RE = re.compile(r'"\$set"\s*:\s*\{([^{}]*)\}', re.DOTALL)
_STATUS_KEY_RE = re.compile(r'["\']status["\']\s*:')


def _find_direct_status_set_offenders():
    offenders = []
    for path in sorted(_APP_DIR.rglob("*.py")):
        if path.resolve() == _HELPER_FILE:
            continue
        text = path.read_text(encoding="utf-8")
        for match in _SET_BLOCK_RE.finditer(text):
            if _STATUS_KEY_RE.search(match.group(1)):
                line_no = text.count("\n", 0, match.start()) + 1
                offenders.append(f"{path.relative_to(_APP_DIR.parent)}:{line_no}")
    return offenders


def test_no_direct_status_set_outside_helper():
    """Guard: every lead.status write must go through set_lead_status() (or
    LeadManager.update_lead_status, which delegates to it) so status_history
    stays complete. A future direct '$set' of 'status' anywhere else in app/
    should fail this test.

    Scope/limits (deliberate — this is a fast lexical tripwire, not a full
    dataflow analysis): it catches an *inline* flat ``{"$set": {"status": ...}}``
    literal. It does NOT catch a payload built into a variable first
    (``p = {"status": ...}; update_one(f, {"$set": p})``) or a ``$set`` block
    that contains a nested brace literal, and it scans only ``app/`` (not
    ``admin_panel/``, whose sync writes pair ``$set`` with a ``$push`` inline).
    Those shapes are avoided by convention in the migrated code; the guard's
    job is to stop the most common copy-paste regression, not to be a proof."""
    offenders = _find_direct_status_set_offenders()
    assert not offenders, (
        "Direct '$set' write of 'status' found outside set_lead_status() -- this "
        "bypasses status_history tracking (PRO-57). Migrate these call sites to "
        f"app.services.lead_manager_service.set_lead_status(): {offenders}"
    )
