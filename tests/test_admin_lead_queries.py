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

from datetime import datetime, timedelta, timezone

import mongomock
import pandas as pd
import pytest
from bson import ObjectId

from admin_panel.core.config import TRANS
from admin_panel.core.lead_queries import (
    EDITOR_COLUMNS,
    SKIP_LEAD_GONE,
    SKIP_NO_CHANGE,
    SKIP_UNRESOLVED,
    build_edit_form_payload,
    build_lead_row,
    client_label,
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


# --- PRO-163: client_label precedence — display_name > customer_name > phone ---


@pytest.mark.parametrize(
    "lead, expected",
    [
        (
            {
                "display_name": "משה כהן",
                "customer_name": "Danny",
                "chat_id": "972500000000@c.us",
            },
            "משה כהן",
        ),
        (
            {
                "display_name": "   ",
                "customer_name": "Danny",
                "chat_id": "972500000000@c.us",
            },
            "Danny",
        ),
        (
            {"customer_name": "Danny", "chat_id": "972500000000@c.us"},
            "Danny",
        ),
        (
            {
                "display_name": None,
                "customer_name": "   ",
                "chat_id": "972500000000@c.us",
            },
            "972500000000",
        ),
        (
            {"chat_id": "972500000000@c.us"},  # neither key present
            "972500000000",
        ),
    ],
    ids=[
        "display_name_wins_over_customer_name",
        "blank_display_name_falls_to_customer_name",
        "customer_name_used_when_display_name_absent",
        "whitespace_only_at_both_rungs_falls_to_phone",
        "both_absent_falls_to_phone",
    ],
)
def test_client_label_precedence(lead, expected):
    assert client_label(lead) == expected


# --- PRO-163: build_edit_form_payload ---


def test_build_edit_form_payload_non_blank_name_trimmed_into_set_ops():
    set_ops, unset_ops = build_edit_form_payload(
        status="new",
        details="d",
        details_touched=False,
        display_name="  משה כהן  ",
        pro_name=UNKNOWN_PRO_LABEL,
        pro_map_name_to_id={},
        unknown_pro_label=UNKNOWN_PRO_LABEL,
    )
    assert set_ops["display_name"] == "משה כהן"
    assert unset_ops == {}


@pytest.mark.parametrize("blank_name", ["", "   ", None])
def test_build_edit_form_payload_blank_name_unsets_display_name(blank_name):
    set_ops, unset_ops = build_edit_form_payload(
        status="new",
        details="d",
        details_touched=False,
        display_name=blank_name,
        pro_name=UNKNOWN_PRO_LABEL,
        pro_map_name_to_id={},
        unknown_pro_label=UNKNOWN_PRO_LABEL,
    )
    assert unset_ops == {"display_name": ""}
    assert "display_name" not in set_ops


def test_build_edit_form_payload_status_always_present():
    set_ops, _ = build_edit_form_payload(
        status="booked",
        details="burst pipe",
        details_touched=False,
        display_name="A",
        pro_name=UNKNOWN_PRO_LABEL,
        pro_map_name_to_id={},
        unknown_pro_label=UNKNOWN_PRO_LABEL,
    )
    assert set_ops["status"] == "booked"


def test_build_edit_form_payload_details_untouched_omits_details_and_issue_type():
    # The headline PRO-163 fix: the details box is prefilled with the
    # *composed* summary, and the form submits every input on every save —
    # so writing details back unconditionally stamped that whole composed
    # string into the pro-facing `issue_type` (the offer's "תקלה:" line,
    # Messages.Pro.NEW_LEAD_DETAILS) on a save that only touched something
    # else, like the client name. Untouched must mean untouched: no key.
    set_ops, _ = build_edit_form_payload(
        status="new",
        details="<issue> | <time> | <address>",
        details_touched=False,
        display_name="A",
        pro_name=UNKNOWN_PRO_LABEL,
        pro_map_name_to_id={},
        unknown_pro_label=UNKNOWN_PRO_LABEL,
    )
    assert "details" not in set_ops
    assert "issue_type" not in set_ops


def test_build_edit_form_payload_details_touched_writes_details_and_issue_type():
    set_ops, _ = build_edit_form_payload(
        status="new",
        details="burst pipe under sink",
        details_touched=True,
        display_name="A",
        pro_name=UNKNOWN_PRO_LABEL,
        pro_map_name_to_id={},
        unknown_pro_label=UNKNOWN_PRO_LABEL,
    )
    assert set_ops["details"] == "burst pipe under sink"
    assert set_ops["issue_type"] == "burst pipe under sink"


def test_build_edit_form_payload_unknown_pro_label_nulls_pro_id():
    set_ops, _ = build_edit_form_payload(
        status="new",
        details="d",
        details_touched=False,
        display_name="A",
        pro_name=UNKNOWN_PRO_LABEL,
        pro_map_name_to_id={"Some Pro": str(ObjectId())},
        unknown_pro_label=UNKNOWN_PRO_LABEL,
    )
    assert set_ops["pro_id"] is None


def test_build_edit_form_payload_known_pro_name_maps_to_object_id():
    pro_id = str(ObjectId())
    set_ops, _ = build_edit_form_payload(
        status="new",
        details="d",
        details_touched=False,
        display_name="A",
        pro_name="Some Pro",
        pro_map_name_to_id={"Some Pro": pro_id},
        unknown_pro_label=UNKNOWN_PRO_LABEL,
    )
    assert set_ops["pro_id"] == ObjectId(pro_id)


def test_build_edit_form_payload_unrecognized_pro_name_omits_pro_id_key():
    # Neither the unassigned sentinel nor a name present in the map — the
    # code has no "else" branch for this, so pin what it actually does:
    # no pro_id key at all, rather than assuming a default is applied.
    set_ops, _ = build_edit_form_payload(
        status="new",
        details="d",
        details_touched=False,
        display_name="A",
        pro_name="A Pro Who Vanished",
        pro_map_name_to_id={"Some Pro": str(ObjectId())},
        unknown_pro_label=UNKNOWN_PRO_LABEL,
    )
    assert "pro_id" not in set_ops


# --- PRO-163: round-trip through mongomock, the acceptance criterion ---


def test_display_name_round_trips_through_save_and_read_back(db):
    leads = db.leads
    lead_id = leads.insert_one(
        {"chat_id": "972500000000@c.us", "status": "new"}
    ).inserted_id

    set_ops, unset_ops = build_edit_form_payload(
        status="new",
        details="d",
        details_touched=False,
        display_name="  משה כהן  ",
        pro_name=UNKNOWN_PRO_LABEL,
        pro_map_name_to_id={},
        unknown_pro_label=UNKNOWN_PRO_LABEL,
    )
    update_op = {"$set": set_ops}
    if unset_ops:
        update_op["$unset"] = unset_ops
    leads.update_one({"_id": lead_id}, update_op)

    doc = leads.find_one({"_id": lead_id})
    assert client_label(doc) == "משה כהן"

    # Now clear the name: the field must disappear from the document, not
    # just read as falsy, so the phone fallback is reachable again.
    set_ops2, unset_ops2 = build_edit_form_payload(
        status="new",
        details="d",
        details_touched=False,
        display_name="   ",
        pro_name=UNKNOWN_PRO_LABEL,
        pro_map_name_to_id={},
        unknown_pro_label=UNKNOWN_PRO_LABEL,
    )
    update_op2 = {"$set": set_ops2}
    if unset_ops2:
        update_op2["$unset"] = unset_ops2
    leads.update_one({"_id": lead_id}, update_op2)

    doc2 = leads.find_one({"_id": lead_id})
    assert "display_name" not in doc2
    assert client_label(doc2) == "972500000000"


# --- PRO-163: i18n key parity for the Edit-Lead-form keys ---


@pytest.mark.parametrize(
    "key",
    [
        "client_name_label",
        "client_name_placeholder",
        "client_name_help",
        "phone_number_label",
        "phone_readonly_help",
        "status_label",
        "details_label",
        "professional_label",
        "save_changes_btn",
        "edit_lead_btn",
        "check_not_sent",
    ],
)
def test_edit_lead_form_i18n_keys_present_and_non_empty_in_both_languages(key):
    for lang, lang_dict in TRANS.items():
        assert key in lang_dict, f"{key} missing from TRANS[{lang}]"
        assert lang_dict[key].strip(), f"{key} is blank in TRANS[{lang}]"


# --- PRO-163: EDITOR_COLUMNS carries the raw display_name the Edit form
# prefills from, alongside the composed `client` cell — the pairing that
# stops a save blanking a name nobody touched. (No `_details` twin: the
# details box is prefilled from the composed cell itself, so there is
# nothing for a raw carrier to be read by — see the comment in
# lead_queries.py.) ---


def test_editor_columns_carries_the_raw_display_name_hidden_column():
    assert "_display_name" in EDITOR_COLUMNS


# --- PRO-163: build_lead_row — the compose site, now extracted and testable ---


def _row_lead_doc(**overrides):
    doc = {
        "_id": ObjectId(),
        "created_at": datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc),
        "chat_id": "972500000000@c.us",
        "status": "new",
        "pro_id": None,
    }
    doc.update(overrides)
    return doc


def test_build_lead_row_keys_exactly_match_editor_columns():
    row = build_lead_row(
        _row_lead_doc(),
        pro_map_id_to_name={},
        unknown_pro_label=UNKNOWN_PRO_LABEL,
    )
    assert set(row.keys()) == set(EDITOR_COLUMNS)


def test_build_lead_row_client_and_raw_display_name_pairing_with_name():
    row = build_lead_row(
        _row_lead_doc(display_name="משה כהן"),
        pro_map_id_to_name={},
        unknown_pro_label=UNKNOWN_PRO_LABEL,
    )
    assert row["client"] == "משה כהן"
    assert row["_display_name"] == "משה כהן"


def test_build_lead_row_client_and_raw_display_name_pairing_without_name():
    row = build_lead_row(
        _row_lead_doc(),
        pro_map_id_to_name={},
        unknown_pro_label=UNKNOWN_PRO_LABEL,
    )
    assert row["client"] == "972500000000"  # phone fallback via client_label
    assert row["_display_name"] == ""


def test_build_lead_row_synthesizes_details_summary_from_structured_fields():
    # No `details` text was ever typed for this lead — only the structured
    # AI-extracted fields — so the composed cell has to be built from them
    # rather than echoing a raw string. (There is no `_details` raw carrier:
    # the Edit form's details box is prefilled from this same composed
    # value, so "touched" is measured against it directly — see the comment
    # next to `_display_name` in EDITOR_COLUMNS.)
    row = build_lead_row(
        _row_lead_doc(
            issue_type="נזילה מתחת לכיור",
            appointment_time="10:00",
            full_address="תל אביב",
        ),
        pro_map_id_to_name={},
        unknown_pro_label=UNKNOWN_PRO_LABEL,
    )
    assert row["details_summary"] == "נזילה מתחת לכיור | 10:00 | תל אביב"


def test_build_lead_row_date_converted_to_israel_time():
    # 2026-01-15 is outside Israel's DST window, so the offset is a fixed
    # +2h (IST) — 10:00 UTC becomes 12:00 local.
    row = build_lead_row(
        _row_lead_doc(),
        pro_map_id_to_name={},
        unknown_pro_label=UNKNOWN_PRO_LABEL,
    )
    assert row["date"].hour == 12
    assert row["date"].utcoffset() == timedelta(hours=2)


def test_build_lead_row_professional_maps_known_pro_id_to_name():
    pro_id = ObjectId()
    row = build_lead_row(
        _row_lead_doc(pro_id=pro_id),
        pro_map_id_to_name={pro_id: "דני החשמלאי"},
        unknown_pro_label=UNKNOWN_PRO_LABEL,
    )
    assert row["professional"] == "דני החשמלאי"


def test_build_lead_row_professional_falls_back_to_unknown_label():
    row = build_lead_row(
        _row_lead_doc(pro_id=ObjectId()),  # not in the map
        pro_map_id_to_name={},
        unknown_pro_label=UNKNOWN_PRO_LABEL,
    )
    assert row["professional"] == UNKNOWN_PRO_LABEL


def test_build_lead_row_parses_legacy_deal_marker_into_details_summary():
    # Early leads packed the structured fields into a "[DEAL: ...]" marker
    # inside the free-text `details` instead of separate columns. Never had
    # a test before this extraction.
    row = build_lead_row(
        _row_lead_doc(details="פנייה ישנה [DEAL: 10:00|תל אביב|נזילה מתחת לכיור]"),
        pro_map_id_to_name={},
        unknown_pro_label=UNKNOWN_PRO_LABEL,
    )
    assert row["details_summary"] == "נזילה מתחת לכיור | 10:00 | תל אביב"


def test_build_lead_row_malformed_deal_marker_hits_except_and_leaves_defaults():
    # Real-world Mongo drift: `details` corrupted to a non-string whose
    # str() representation still contains the "[DEAL:" marker text, so the
    # guard enters the parse branch — but `.split` on a non-string raises
    # AttributeError. The parser must catch that rather than crash the
    # whole row (and, by extension, the whole leads table): reaching this
    # assert at all proves the except fired.
    row = build_lead_row(
        _row_lead_doc(details=["[DEAL: 10:00|תל אביב|נזילה מתחת לכיור]"]),
        pro_map_id_to_name={},
        unknown_pro_label=UNKNOWN_PRO_LABEL,
    )
    # Left the pre-try defaults in place rather than having successfully
    # parsed the marker into the clean composed form the successful-parse
    # test above pins.
    assert row["details_summary"] != "נזילה מתחת לכיור | 10:00 | תל אביב"
