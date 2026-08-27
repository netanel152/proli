"""Tests for admin_panel/core/lead_queries.py — PRO-161.

Pins the leads-table Save regression: row identity must come from the frame
``st.data_editor`` returned in *this* run (``edited_df``), never from a
stale positional snapshot. The headline test below reproduces the exact
failure mode — a reordered frame (new lead arrived at the top, shifting
every row index by one) — and asserts the edit lands on the lead that is
actually at that row index in the returned frame, not the lead that used
to be there under the old snapshot-based code.

Also covers the structured ``skipped_rows`` result (``{"client", "reason"}``
per skip, ``reason`` one of ``SKIP_UNRESOLVED`` / ``SKIP_NO_CHANGE`` /
``SKIP_LEAD_GONE``) and the no-duplicate-status_history-entry guard: a
status "change" to the value the document already holds still counts as
``updated`` (matched_count, not modified_count) but must not push a
duplicate consecutive status_history entry.

Extracted into a streamlit-free module (collection injected, mongomock in
tests, PRO-140/PRO-158 precedent).
"""

from datetime import datetime, timezone

import mongomock
import pandas as pd
import pytest
from bson import ObjectId

from admin_panel.core.lead_queries import (
    EDITOR_COLUMNS,
    SKIP_LEAD_GONE,
    SKIP_NO_CHANGE,
    SKIP_UNRESOLVED,
    save_lead_edits,
)
from app.core.constants import Actor
from app.core.lead_history import status_history_entry


@pytest.fixture
def db():
    return mongomock.MongoClient()["proli_test"]


UNKNOWN_PRO_LABEL = "-- לא משויך --"


def _lead_doc(**overrides):
    doc = {
        "client_name": "לקוח",
        "phone": "972500000000",
        "status": "new",
        "details": "old details",
        "issue_type": "old details",
        "pro_id": None,
        "created_at": datetime.now(timezone.utc),
        "status_history": [],
    }
    doc.update(overrides)
    return doc


def _frame(rows):
    return pd.DataFrame(rows, columns=EDITOR_COLUMNS)


# --- THE regression: reordered frame, row identity from edited_df only ---


@pytest.mark.parametrize("row_key", [2, "2"])
def test_reordered_frame_edits_the_lead_actually_at_that_row_not_the_stale_one(
    db, row_key
):
    """The real PRO-161 shape: a new lead arrives and shifts every row down
    by one. edited_rows={2: {...}} must resolve against the frame that was
    PASSED IN (post-reorder), not some earlier snapshot. This is exactly the
    assertion the old positional-snapshot code would fail: it would have
    written to whatever lead used to sit at row 2 before the reorder.

    Parametrized over an int and a str row key: save_lead_edits explicitly
    does int(row_idx), so st.data_editor handing back either shape must
    resolve identically."""
    leads = db.leads

    # Original (pre-reorder) order: lead_a, lead_b, lead_c
    lead_a = leads.insert_one(_lead_doc(client_name="A")).inserted_id
    lead_b = leads.insert_one(_lead_doc(client_name="B")).inserted_id
    lead_c = leads.insert_one(_lead_doc(client_name="C", status="booked")).inserted_id

    # A new lead arrives and the table re-sorts created_at desc -> everyone
    # shifts down by one. The frame ACTUALLY handed to save_lead_edits now
    # has: new_lead, lead_a, lead_b, lead_c.
    lead_new = leads.insert_one(_lead_doc(client_name="NEW")).inserted_id

    reordered_df = _frame(
        [
            {
                "id": str(lead_new),
                "date": "",
                "client": "NEW",
                "professional": UNKNOWN_PRO_LABEL,
                "details_summary": "old details",
                "status": "new",
                "_chat_id": "1",
            },
            {
                "id": str(lead_a),
                "date": "",
                "client": "A",
                "professional": UNKNOWN_PRO_LABEL,
                "details_summary": "old details",
                "status": "new",
                "_chat_id": "2",
            },
            {
                "id": str(lead_b),
                "date": "",
                "client": "B",
                "professional": UNKNOWN_PRO_LABEL,
                "details_summary": "old details",
                "status": "new",
                "_chat_id": "3",
            },
            {
                "id": str(lead_c),
                "date": "",
                "client": "C",
                "professional": UNKNOWN_PRO_LABEL,
                "details_summary": "old details",
                "status": "booked",
                "_chat_id": "4",
            },
        ]
    )

    # Admin edited what they saw as row 2 in the CURRENT (post-reorder) view
    # -> that is lead_b, not lead_c (which sat at index 2 under the stale
    # snapshot from before the new lead arrived).
    edited_rows = {row_key: {"status": "booked"}}

    result = save_lead_edits(
        reordered_df,
        edited_rows,
        leads_collection=leads,
        pro_map_name_to_id={},
        unknown_pro_label=UNKNOWN_PRO_LABEL,
    )

    assert result == {"updated": 1, "skipped": 0, "skipped_rows": []}

    doc_b = leads.find_one({"_id": lead_b})
    assert doc_b["status"] == "booked"
    assert len(doc_b["status_history"]) == 1

    # Neighbours (including the lead that used to sit at row 2 pre-reorder)
    # are untouched.
    doc_a = leads.find_one({"_id": lead_a})
    doc_c = leads.find_one({"_id": lead_c})
    doc_new = leads.find_one({"_id": lead_new})
    assert doc_a["status"] == "new"
    assert doc_a["status_history"] == []
    assert doc_c["status"] == "booked"  # was already booked, untouched by this save
    assert doc_c["status_history"] == []
    assert doc_new["status"] == "new"
    assert doc_new["status_history"] == []


# --- status_history shape (PRO-57) ---


def test_status_change_appends_status_history_entry_with_admin_actor(db):
    leads = db.leads
    lead_id = leads.insert_one(_lead_doc()).inserted_id
    df = _frame(
        [
            {
                "id": str(lead_id),
                "date": "",
                "client": "A",
                "professional": UNKNOWN_PRO_LABEL,
                "details_summary": "old details",
                "status": "completed",
                "_chat_id": "1",
            }
        ]
    )

    result = save_lead_edits(
        df,
        {0: {"status": "completed"}},
        leads_collection=leads,
        pro_map_name_to_id={},
        unknown_pro_label=UNKNOWN_PRO_LABEL,
    )

    assert result == {"updated": 1, "skipped": 0, "skipped_rows": []}
    doc = leads.find_one({"_id": lead_id})
    assert doc["status"] == "completed"
    assert len(doc["status_history"]) == 1
    entry = doc["status_history"][0]
    assert set(entry.keys()) == set(
        status_history_entry("completed", Actor.ADMIN).keys()
    )
    assert entry["status"] == "completed"
    assert entry["by"] == Actor.ADMIN
    assert isinstance(entry["at"], datetime)


# --- non-status edit writes both details+issue_type, no status_history ---


def test_details_summary_edit_writes_details_and_issue_type_no_status_history(db):
    leads = db.leads
    lead_id = leads.insert_one(_lead_doc()).inserted_id
    df = _frame(
        [
            {
                "id": str(lead_id),
                "date": "",
                "client": "A",
                "professional": UNKNOWN_PRO_LABEL,
                "details_summary": "burst pipe under sink",
                "status": "new",
                "_chat_id": "1",
            }
        ]
    )

    result = save_lead_edits(
        df,
        {0: {"details_summary": "burst pipe under sink"}},
        leads_collection=leads,
        pro_map_name_to_id={},
        unknown_pro_label=UNKNOWN_PRO_LABEL,
    )

    assert result == {"updated": 1, "skipped": 0, "skipped_rows": []}
    doc = leads.find_one({"_id": lead_id})
    assert doc["details"] == "burst pipe under sink"
    assert doc["issue_type"] == "burst pipe under sink"
    assert doc["status_history"] == []


# --- professional column: unknown label nulls pro_id, real name maps ---


def test_professional_set_to_unknown_label_nulls_pro_id(db):
    leads = db.leads
    old_pro_id = ObjectId()
    lead_id = leads.insert_one(_lead_doc(pro_id=old_pro_id)).inserted_id
    df = _frame(
        [
            {
                "id": str(lead_id),
                "date": "",
                "client": "A",
                "professional": UNKNOWN_PRO_LABEL,
                "details_summary": "old details",
                "status": "new",
                "_chat_id": "1",
            }
        ]
    )

    result = save_lead_edits(
        df,
        {0: {"professional": UNKNOWN_PRO_LABEL}},
        leads_collection=leads,
        pro_map_name_to_id={"Some Pro": ObjectId()},
        unknown_pro_label=UNKNOWN_PRO_LABEL,
    )

    assert result == {"updated": 1, "skipped": 0, "skipped_rows": []}
    doc = leads.find_one({"_id": lead_id})
    assert doc["pro_id"] is None


def test_professional_set_to_real_name_maps_to_pro_id(db):
    leads = db.leads
    lead_id = leads.insert_one(_lead_doc()).inserted_id
    new_pro_id = ObjectId()
    df = _frame(
        [
            {
                "id": str(lead_id),
                "date": "",
                "client": "A",
                "professional": "Dana the Plumber",
                "details_summary": "old details",
                "status": "new",
                "_chat_id": "1",
            }
        ]
    )

    result = save_lead_edits(
        df,
        {0: {"professional": "Dana the Plumber"}},
        leads_collection=leads,
        pro_map_name_to_id={"Dana the Plumber": new_pro_id},
        unknown_pro_label=UNKNOWN_PRO_LABEL,
    )

    assert result == {"updated": 1, "skipped": 0, "skipped_rows": []}
    doc = leads.find_one({"_id": lead_id})
    assert doc["pro_id"] == new_pro_id


# --- row index out of range for the frame ---


def test_row_index_out_of_range_is_skipped_not_crashed(db):
    leads = db.leads
    lead_id = leads.insert_one(_lead_doc()).inserted_id
    df = _frame(
        [
            {
                "id": str(lead_id),
                "date": "",
                "client": "A",
                "professional": UNKNOWN_PRO_LABEL,
                "details_summary": "old details",
                "status": "new",
                "_chat_id": "1",
            }
        ]
    )

    # index 5 doesn't exist in a 1-row frame -- old code's .iloc would raise
    # IndexError mid-loop.
    result = save_lead_edits(
        df,
        {5: {"status": "completed"}},
        leads_collection=leads,
        pro_map_name_to_id={},
        unknown_pro_label=UNKNOWN_PRO_LABEL,
    )

    assert result == {
        "updated": 0,
        "skipped": 1,
        "skipped_rows": [{"client": "", "reason": SKIP_UNRESOLVED}],
    }
    doc = leads.find_one({"_id": lead_id})
    assert doc["status"] == "new"


def test_non_integer_row_key_is_skipped_unresolved_with_empty_client(db):
    """A row key that doesn't parse as int (int(row_idx) raises) can't even
    look up a client name -- reason SKIP_UNRESOLVED, client stays ''."""
    leads = db.leads
    lead_id = leads.insert_one(_lead_doc()).inserted_id
    df = _frame(
        [
            {
                "id": str(lead_id),
                "date": "",
                "client": "A",
                "professional": UNKNOWN_PRO_LABEL,
                "details_summary": "old details",
                "status": "new",
                "_chat_id": "1",
            }
        ]
    )

    result = save_lead_edits(
        df,
        {"not-an-int": {"status": "completed"}},
        leads_collection=leads,
        pro_map_name_to_id={},
        unknown_pro_label=UNKNOWN_PRO_LABEL,
    )

    assert result == {
        "updated": 0,
        "skipped": 1,
        "skipped_rows": [{"client": "", "reason": SKIP_UNRESOLVED}],
    }
    doc = leads.find_one({"_id": lead_id})
    assert doc["status"] == "new"


# --- unparseable / missing / non-ObjectId id cell ---


@pytest.mark.parametrize(
    "bad_id",
    [None, "", "not-an-object-id", "nan", "None"],
)
def test_unparseable_id_cell_is_skipped_not_crashed(db, bad_id):
    leads = db.leads
    df = _frame(
        [
            {
                "id": bad_id,
                "date": "",
                "client": "A",
                "professional": UNKNOWN_PRO_LABEL,
                "details_summary": "old details",
                "status": "new",
                "_chat_id": "1",
            }
        ]
    )

    result = save_lead_edits(
        df,
        {0: {"status": "completed"}},
        leads_collection=leads,
        pro_map_name_to_id={},
        unknown_pro_label=UNKNOWN_PRO_LABEL,
    )

    assert result == {
        "updated": 0,
        "skipped": 1,
        "skipped_rows": [{"client": "A", "reason": SKIP_UNRESOLVED}],
    }
    assert leads.count_documents({}) == 0


# --- changed cells produce no writable field ---


def test_row_with_no_writable_field_is_skipped(db):
    leads = db.leads
    lead_id = leads.insert_one(_lead_doc()).inserted_id
    df = _frame(
        [
            {
                "id": str(lead_id),
                "date": "2026-08-27",  # not an editable/writable column
                "client": "A",
                "professional": UNKNOWN_PRO_LABEL,
                "details_summary": "old details",
                "status": "new",
                "_chat_id": "1",
            }
        ]
    )

    # "date" and "client" are not in _SIMPLE_FIELDS / handled specially.
    result = save_lead_edits(
        df,
        {0: {"date": "2026-08-27", "client": "A"}},
        leads_collection=leads,
        pro_map_name_to_id={},
        unknown_pro_label=UNKNOWN_PRO_LABEL,
    )

    assert result == {
        "updated": 0,
        "skipped": 1,
        "skipped_rows": [{"client": "A", "reason": SKIP_NO_CHANGE}],
    }
    doc = leads.find_one({"_id": lead_id})
    assert doc["status"] == "new"
    assert doc["status_history"] == []


# --- lead deleted from the collection before the save lands ---


def test_lead_deleted_before_save_lands_is_skipped_and_not_audited(db):
    """find_one returning None (lead deleted before the save reads current
    status) yields SKIP_LEAD_GONE, no write, no audit call."""
    leads = db.leads
    stale_id = ObjectId()  # never inserted -- simulates deleted-out-from-under
    df = _frame(
        [
            {
                "id": str(stale_id),
                "date": "",
                "client": "A",
                "professional": UNKNOWN_PRO_LABEL,
                "details_summary": "old details",
                "status": "completed",
                "_chat_id": "1",
            }
        ]
    )

    audit_calls = []

    result = save_lead_edits(
        df,
        {0: {"status": "completed"}},
        leads_collection=leads,
        pro_map_name_to_id={},
        unknown_pro_label=UNKNOWN_PRO_LABEL,
        audit=lambda action, details: audit_calls.append((action, details)),
    )

    assert result == {
        "updated": 0,
        "skipped": 1,
        "skipped_rows": [{"client": "A", "reason": SKIP_LEAD_GONE}],
    }
    assert audit_calls == []
    assert leads.count_documents({}) == 0


# --- audit callable ---


def test_audit_called_once_per_successful_row_with_action_and_details(db):
    leads = db.leads
    lead_id = leads.insert_one(_lead_doc()).inserted_id
    df = _frame(
        [
            {
                "id": str(lead_id),
                "date": "",
                "client": "A",
                "professional": UNKNOWN_PRO_LABEL,
                "details_summary": "old details",
                "status": "completed",
                "_chat_id": "1",
            }
        ]
    )

    audit_calls = []

    result = save_lead_edits(
        df,
        {0: {"status": "completed"}},
        leads_collection=leads,
        pro_map_name_to_id={},
        unknown_pro_label=UNKNOWN_PRO_LABEL,
        audit=lambda action, details: audit_calls.append((action, details)),
    )

    assert result == {"updated": 1, "skipped": 0, "skipped_rows": []}
    assert len(audit_calls) == 1
    action, details = audit_calls[0]
    assert action == "edit_lead"
    assert details == {"lead_id": str(lead_id), "changes": {"status": "completed"}}


def test_audit_none_does_not_crash(db):
    leads = db.leads
    lead_id = leads.insert_one(_lead_doc()).inserted_id
    df = _frame(
        [
            {
                "id": str(lead_id),
                "date": "",
                "client": "A",
                "professional": UNKNOWN_PRO_LABEL,
                "details_summary": "old details",
                "status": "completed",
                "_chat_id": "1",
            }
        ]
    )

    result = save_lead_edits(
        df,
        {0: {"status": "completed"}},
        leads_collection=leads,
        pro_map_name_to_id={},
        unknown_pro_label=UNKNOWN_PRO_LABEL,
        audit=None,
    )

    assert result == {"updated": 1, "skipped": 0, "skipped_rows": []}


# --- matched_count, not modified_count / no duplicate status_history push ---


def test_resaving_unchanged_status_still_counts_as_updated_no_duplicate_history(db):
    """Pin: the code deliberately checks matched_count, not modified_count --
    re-saving a row to the value it already holds is a successful save, not a
    skip. But it must NOT push a duplicate consecutive status_history entry:
    the two behaviors have to agree, not contradict."""
    leads = db.leads
    lead_id = leads.insert_one(_lead_doc(status="completed")).inserted_id
    df = _frame(
        [
            {
                "id": str(lead_id),
                "date": "",
                "client": "A",
                "professional": UNKNOWN_PRO_LABEL,
                "details_summary": "old details",
                "status": "completed",  # same value it already holds
                "_chat_id": "1",
            }
        ]
    )

    result = save_lead_edits(
        df,
        {0: {"status": "completed"}},
        leads_collection=leads,
        pro_map_name_to_id={},
        unknown_pro_label=UNKNOWN_PRO_LABEL,
    )

    assert result == {"updated": 1, "skipped": 0, "skipped_rows": []}
    doc = leads.find_one({"_id": lead_id})
    assert doc["status"] == "completed"
    assert doc["status_history"] == []  # no duplicate entry pushed


def test_status_change_to_different_value_appends_exactly_one_entry(db):
    leads = db.leads
    lead_id = leads.insert_one(_lead_doc(status="new")).inserted_id
    df = _frame(
        [
            {
                "id": str(lead_id),
                "date": "",
                "client": "A",
                "professional": UNKNOWN_PRO_LABEL,
                "details_summary": "old details",
                "status": "booked",
                "_chat_id": "1",
            }
        ]
    )

    result = save_lead_edits(
        df,
        {0: {"status": "booked"}},
        leads_collection=leads,
        pro_map_name_to_id={},
        unknown_pro_label=UNKNOWN_PRO_LABEL,
    )

    assert result == {"updated": 1, "skipped": 0, "skipped_rows": []}
    doc = leads.find_one({"_id": lead_id})
    assert doc["status"] == "booked"
    assert len(doc["status_history"]) == 1
    assert doc["status_history"][0]["status"] == "booked"
    assert doc["status_history"][0]["by"] == Actor.ADMIN
