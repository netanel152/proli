"""Setting a lead's pro from the panel: the offer goes out, or the operator hears
that it did not.

``lead_queries.apply_lead_changes`` is the routing the leads table and the Edit
form share; ``assignment.offer_pending_lead`` is the entry point it and the
strip use. ``test_admin_rerun_during_send.py`` runs the real view; the rest of its
wiring is pinned by source at the bottom.
"""

import re
from pathlib import Path
from unittest.mock import MagicMock

import mongomock
import pandas as pd
import pytest
from bson import ObjectId
from pymongo.errors import PyMongoError
from streamlit.runtime.scriptrunner import RerunData, RerunException

from admin_panel.core import assignment
from admin_panel.core.lead_queries import (
    SKIP_LEAD_GONE,
    SKIP_NO_CHANGE,
    SKIP_SAVE_FAILED,
    SKIP_STALE_PRO,
    apply_lead_changes,
    build_edit_form_payload,
    new_save_report,
    record_change,
    save_lead_edits,
)

UNKNOWN = "-- unassigned --"
PRO_ID = ObjectId()
PENDING = "pending_admin_review"


@pytest.fixture
def leads():
    return mongomock.MongoClient()["proli_test"]["leads"]


def _lead(leads, status=PENDING, pro_id=None, **extra):
    doc = {"status": status, "pro_id": pro_id, "issue_type": "old", **extra}
    return leads.insert_one(doc).inserted_id


class _Offer:
    """Records calls; stands in for ``home._offer_pending``."""

    def __init__(self, outcome=assignment.ASSIGN_SENT):
        self.calls = []
        self.outcome = outcome

    def __call__(self, lead_id, pro_id):
        self.calls.append((lead_id, pro_id))
        return self.outcome, "Dana"


def _save_row(leads, lead_id, changed, offer, frame_pro=None, **kwargs):
    """One edited row. The frame carries the stored pro unless told it is stale."""
    if frame_pro is None:
        stored = leads.find_one({"_id": lead_id}) or {}
        frame_pro = str(stored.get("pro_id") or "")
    frame = pd.DataFrame(
        [
            {
                "id": str(lead_id),
                "client": "Yossi",
                "professional": "Held By",
                "_pro_id": frame_pro,
            }
        ]
    )
    return save_lead_edits(
        frame,
        {0: changed},
        leads_collection=leads,
        pro_map_name_to_id={"Dana": PRO_ID},
        unknown_pro_label=UNKNOWN,
        status_by_label={"Waiting": PENDING, "Booked": "booked"},
        offer_pending=offer,
        **kwargs,
    )


class _BrokenFor:
    """A collection whose writes to one lead fail, as in a database outage."""

    def __init__(self, leads, broken_id):
        self._leads = leads
        self._broken_id = broken_id

    def find_one(self, *args, **kwargs):
        return self._leads.find_one(*args, **kwargs)

    def update_one(self, query, update):
        if query["_id"] == self._broken_id:
            raise PyMongoError("connection lost")
        return self._leads.update_one(query, update)


# --- the leads table ---


def test_pro_on_a_waiting_lead_goes_through_the_offer_path(leads):
    lead_id = _lead(leads)
    offer = _Offer()
    audit = MagicMock()

    result = _save_row(leads, lead_id, {"professional": "Dana"}, offer, audit=audit)

    assert offer.calls == [(str(lead_id), PRO_ID)]
    assert result["assignments"] == [
        {"client": "Yossi", "outcome": assignment.ASSIGN_SENT, "pro_name": "Dana"}
    ]
    assert result["record_only"] == []
    # The offer path owns the write: no bare pro_id, and no "1 saved" over it.
    assert result["updated"] == 0
    assert leads.find_one({"_id": lead_id})["pro_id"] is None
    audit.assert_called_once_with(
        "assign_lead",
        {
            "lead_id": str(lead_id),
            "pro_id": str(PRO_ID),
            "outcome": assignment.ASSIGN_SENT,
        },
    )


def test_other_fields_are_written_before_the_offer_reads_them(leads):
    lead_id = _lead(leads)
    seen = []

    def offer(_lead_id, _pro_id):
        seen.append(leads.find_one({"_id": lead_id})["issue_type"])
        return assignment.ASSIGN_SENT, "Dana"

    result = _save_row(
        leads, lead_id, {"professional": "Dana", "details_summary": "fixed"}, offer
    )

    assert seen == ["fixed"]
    assert result["updated"] == 1


@pytest.mark.parametrize("status", ["new", "booked", "completed", "closed"])
def test_pro_on_any_other_lead_is_record_only_and_says_so(leads, status):
    lead_id = _lead(leads, status=status)
    offer = _Offer()

    result = _save_row(leads, lead_id, {"professional": "Dana"}, offer)

    assert offer.calls == []
    doc = leads.find_one({"_id": lead_id})
    assert (doc["status"], doc["pro_id"]) == (status, PRO_ID)
    assert result["updated"] == 1
    assert result["record_only"] == [{"client": "Yossi", "pro": "Dana"}]


def test_waiting_lead_moved_out_with_a_pro_is_record_only(leads):
    """An explicit status choice wins: the operator asked for this record."""
    lead_id = _lead(leads)
    offer = _Offer()

    result = _save_row(
        leads, lead_id, {"professional": "Dana", "status_label": "Booked"}, offer
    )

    assert offer.calls == []
    doc = leads.find_one({"_id": lead_id})
    assert (doc["status"], doc["pro_id"]) == ("booked", PRO_ID)
    assert result["record_only"] == [{"client": "Yossi", "pro": "Dana"}]


def test_lead_moved_into_waiting_while_holding_a_pro_is_reported(leads):
    """Status alone can produce a waiting lead with a pro and no offer."""
    lead_id = _lead(leads, status="new", pro_id=PRO_ID)
    offer = _Offer()

    result = _save_row(leads, lead_id, {"status_label": "Waiting"}, offer)

    assert offer.calls == []
    assert leads.find_one({"_id": lead_id})["status"] == PENDING
    # Named from the stored id, not the frame's possibly stale "Held By".
    assert result["held_pro"] == [{"client": "Yossi", "pro": "Dana"}]
    assert result["record_only"] == []


def test_no_offer_path_supplied_is_never_a_silent_write(leads):
    lead_id = _lead(leads)

    result = _save_row(leads, lead_id, {"professional": "Dana"}, None)

    assert leads.find_one({"_id": lead_id})["pro_id"] == PRO_ID
    assert result["record_only"] == [{"client": "Yossi", "pro": "Dana"}]


def test_save_replayed_after_the_offer_sends_nothing_more(leads):
    """A double click re-runs Save with the same edited_rows mid-send."""
    lead_id = _lead(leads, status="new", pro_id=PRO_ID)
    offer = _Offer()

    result = _save_row(leads, lead_id, {"professional": "Dana"}, offer)

    assert offer.calls == []
    assert result["record_only"] == []
    assert result["updated"] == 0
    assert result["skipped_rows"] == [{"client": "Yossi", "reason": SKIP_NO_CHANGE}]


def test_details_edit_on_a_waiting_lead_holding_a_pro_warns_without_offering(leads):
    """Escalation keeps pro_id, so this lead is normal, and this save sent nothing."""
    lead_id = _lead(leads, pro_id=PRO_ID)
    offer = _Offer()

    result = _save_row(leads, lead_id, {"details_summary": "fixed"}, offer)

    assert offer.calls == []
    assert result["updated"] == 1
    assert result["held_pro"] == [{"client": "Yossi", "pro": "Dana"}]
    assert result["record_only"] == []


def test_status_toggled_back_on_a_waiting_lead_holding_a_pro_still_warns(leads):
    lead_id = _lead(leads, pro_id=PRO_ID)

    result = _save_row(leads, lead_id, {"status_label": "Waiting"}, _Offer())

    assert result["updated"] == 0
    assert result["skipped_rows"] == []
    assert result["held_pro"] == [{"client": "Yossi", "pro": "Dana"}]


def test_details_edit_on_a_lead_deleted_under_it_is_reported_gone(leads):
    result = _save_row(leads, ObjectId(), {"details_summary": "x"}, _Offer())

    assert result["skipped_rows"][0]["reason"] == SKIP_LEAD_GONE


def test_pro_no_longer_on_the_eligible_list_does_not_unassign(leads):
    held = ObjectId()
    lead_id = _lead(leads, status="new", pro_id=held)

    result = _save_row(leads, lead_id, {"professional": "Un-approved"}, _Offer())

    assert leads.find_one({"_id": lead_id})["pro_id"] == held
    assert result["skipped_rows"][0]["reason"] == SKIP_NO_CHANGE


def test_a_database_error_on_one_row_keeps_earlier_offers_and_saves_the_rest(leads):
    """The reachable failure: ``offer_pending_lead`` itself never raises."""
    sent, broken, details = [_lead(leads) for _ in range(3)]
    offer = _Offer()

    frame = pd.DataFrame(
        [
            {"id": str(lead), "client": name, "professional": UNKNOWN}
            for lead, name in ((sent, "A"), (broken, "B"), (details, "C"))
        ]
    )
    result = save_lead_edits(
        frame,
        {
            0: {"professional": "Dana"},
            1: {"professional": "Dana", "details_summary": "lost"},
            2: {"details_summary": "fixed"},
        },
        leads_collection=_BrokenFor(leads, broken),
        pro_map_name_to_id={"Dana": PRO_ID},
        unknown_pro_label=UNKNOWN,
        offer_pending=offer,
    )

    assert offer.calls == [(str(sent), PRO_ID)]  # the failed row was never offered
    assert [row["client"] for row in result["assignments"]] == ["A"]
    assert result["skipped_rows"] == [{"client": "B", "reason": SKIP_SAVE_FAILED}]
    assert result["updated"] == 1
    assert result["unprocessed"] == 0
    assert leads.find_one({"_id": details})["issue_type"] == "fixed"


def test_an_interrupted_batch_keeps_the_outcome_and_counts_what_is_unconfirmed(leads):
    """Nothing in the loop should yield. If something ever does, the report that the
    view already holds must still carry the first offer and say the rest is open."""
    first, second = _lead(leads), _lead(leads)
    report = new_save_report()
    frame = pd.DataFrame(
        [
            {"id": str(lead), "client": name, "professional": UNKNOWN}
            for lead, name in ((first, "A"), (second, "B"))
        ]
    )
    audit = MagicMock(side_effect=RerunException(RerunData()))

    with pytest.raises(RerunException):
        save_lead_edits(
            frame,
            {0: {"professional": "Dana"}, 1: {"professional": "Dana"}},
            leads_collection=leads,
            pro_map_name_to_id={"Dana": PRO_ID},
            unknown_pro_label=UNKNOWN,
            offer_pending=_Offer(),
            audit=audit,
            report=report,
        )

    assert [row["client"] for row in report["assignments"]] == ["A"]
    assert report["unprocessed"] == 2


def test_a_failing_audit_does_not_lose_the_offers_outcome(leads):
    lead_id = _lead(leads)

    result = _save_row(
        leads,
        lead_id,
        {"professional": "Dana"},
        _Offer(),
        audit=MagicMock(side_effect=RuntimeError("audit down")),
    )

    assert result["assignments"][0]["outcome"] == assignment.ASSIGN_SENT


def test_clearing_a_waiting_leads_pro_sends_nothing(leads):
    lead_id = _lead(leads, pro_id=PRO_ID)
    offer = _Offer()

    result = _save_row(leads, lead_id, {"professional": UNKNOWN}, offer)

    assert offer.calls == []
    assert leads.find_one({"_id": lead_id})["pro_id"] is None
    assert result["record_only"] == []


def test_stale_offer_is_reported_and_not_counted_as_saved(leads):
    lead_id = _lead(leads)

    result = _save_row(
        leads, lead_id, {"professional": "Dana"}, _Offer(assignment.ASSIGN_STALE)
    )

    assert result["updated"] == 0
    assert result["assignments"][0]["outcome"] == assignment.ASSIGN_STALE


def test_lead_deleted_under_a_pro_edit_skips_and_never_offers(leads):
    offer = _Offer()

    result = _save_row(leads, ObjectId(), {"professional": "Dana"}, offer)

    assert offer.calls == []
    assert result["skipped_rows"][0]["reason"] == SKIP_LEAD_GONE


# --- a pro change against a frame that is out of date ---


def test_stale_frame_cannot_overwrite_a_newer_owner(leads):
    newer = ObjectId()
    lead_id = _lead(leads, status="new", pro_id=newer)
    offer = _Offer()

    result = _save_row(
        leads,
        lead_id,
        {"professional": "Dana", "details_summary": "edit"},
        offer,
        frame_pro="",
    )

    doc = leads.find_one({"_id": lead_id})
    assert (doc["pro_id"], doc["issue_type"]) == (newer, "old")
    assert offer.calls == []
    assert result["updated"] == 0
    assert result["skipped_rows"] == [{"client": "Yossi", "reason": SKIP_STALE_PRO}]


def test_stale_frame_cannot_clear_a_newer_owner(leads):
    newer = ObjectId()
    lead_id = _lead(leads, status="new", pro_id=newer)

    result = _save_row(
        leads, lead_id, {"professional": UNKNOWN}, _Offer(), frame_pro=str(ObjectId())
    )

    assert leads.find_one({"_id": lead_id})["pro_id"] == newer
    assert result["skipped_rows"][0]["reason"] == SKIP_STALE_PRO


def test_stale_frame_cannot_send_an_offer_on_a_waiting_lead(leads):
    lead_id = _lead(leads, pro_id=ObjectId())
    offer = _Offer()

    _save_row(leads, lead_id, {"professional": "Dana"}, offer, frame_pro="")

    assert offer.calls == []


def test_stale_frame_with_no_pro_change_still_saves_the_other_fields(leads):
    lead_id = _lead(leads, status="new", pro_id=ObjectId())

    result = _save_row(
        leads, lead_id, {"details_summary": "edit"}, _Offer(), frame_pro=""
    )

    assert result["updated"] == 1
    assert leads.find_one({"_id": lead_id})["issue_type"] == "edit"


# --- the Edit form's payload through the same routing ---


def _form_payload(**overrides):
    fields = {
        "status": PENDING,
        "details": "",
        "details_touched": False,
        "display_name": "Avi",
        "pro_name": "Dana",
        "pro_map_name_to_id": {"Dana": PRO_ID},
        "unknown_pro_label": UNKNOWN,
        "current_status": PENDING,
        "current_pro_name": "Dana",
        "current_name": "Avi",
    }
    return build_edit_form_payload(**{**fields, **overrides})


def test_edit_form_writes_nothing_for_boxes_left_untouched():
    assert _form_payload() == ({}, {})


def test_edit_form_untouched_unassigned_box_does_not_unassign():
    assert _form_payload(pro_name=UNKNOWN, current_pro_name=UNKNOWN) == ({}, {})


def test_edit_form_untouched_blank_name_box_clears_nothing():
    assert _form_payload(display_name="", current_name="") == ({}, {})


@pytest.mark.parametrize(
    "overrides, expected_set, expected_unset",
    [
        ({"status": "booked"}, {"status": "booked"}, {}),
        ({"pro_name": UNKNOWN}, {"pro_id": None}, {}),
        ({"current_pro_name": UNKNOWN}, {"pro_id": PRO_ID}, {}),
        ({"display_name": "New"}, {"display_name": "New"}, {}),
        ({"display_name": ""}, {}, {"display_name": ""}),
    ],
)
def test_edit_form_writes_only_the_box_that_changed(
    overrides, expected_set, expected_unset
):
    assert _form_payload(**overrides) == (expected_set, expected_unset)


def test_edit_form_shares_the_routing_and_keeps_its_unset(leads):
    lead_id = _lead(leads, display_name="Old")
    offer = _Offer()
    set_ops, unset_ops = build_edit_form_payload(
        status=PENDING,
        details="",
        details_touched=False,
        display_name="",
        pro_name="Dana",
        pro_map_name_to_id={"Dana": PRO_ID},
        unknown_pro_label=UNKNOWN,
    )

    change = apply_lead_changes(
        str(lead_id), set_ops, unset_ops, leads_collection=leads, offer_pending=offer
    )

    assert offer.calls == [(str(lead_id), PRO_ID)]
    assert change["assignment"] == (assignment.ASSIGN_SENT, "Dana")
    assert change["written"] == ["display_name"]
    assert "display_name" not in leads.find_one({"_id": lead_id})


def test_edit_form_correcting_a_completed_lead_sends_nothing(leads):
    lead_id = _lead(leads, status="completed")
    offer = _Offer()

    change = apply_lead_changes(
        str(lead_id), {"pro_id": PRO_ID}, leads_collection=leads, offer_pending=offer
    )

    assert offer.calls == []
    assert change["record_only"] is True
    assert leads.find_one({"_id": lead_id})["pro_id"] == PRO_ID


def test_edit_form_saving_nothing_reports_no_change(leads):
    lead_id = _lead(leads, status="new")
    report = new_save_report()

    change = apply_lead_changes(str(lead_id), {}, leads_collection=leads)
    record_change(report, change, client="Yossi", pro_label="")

    assert change["found"] is True
    assert report["updated"] == 0
    assert report["skipped_rows"] == [{"client": "Yossi", "reason": SKIP_NO_CHANGE}]


def test_edit_form_report_carries_the_offer_without_claiming_a_save(leads):
    lead_id = _lead(leads)
    report = new_save_report()

    change = apply_lead_changes(
        str(lead_id), {"pro_id": PRO_ID}, leads_collection=leads, offer_pending=_Offer()
    )
    record_change(report, change, client="Yossi", pro_label="Dana")

    assert report["updated"] == 0
    assert report["skipped_rows"] == []
    assert report["assignments"] == [
        {"client": "Yossi", "outcome": assignment.ASSIGN_SENT, "pro_name": "Dana"}
    ]


def test_a_held_pro_the_panel_cannot_name_is_left_blank_for_the_view(leads):
    lead_id = _lead(leads, pro_id=ObjectId())
    report = new_save_report()

    change = apply_lead_changes(
        str(lead_id), {"details": "x"}, leads_collection=leads, offer_pending=_Offer()
    )
    record_change(
        report, change, client="Yossi", pro_label=UNKNOWN, unknown_pro_label=UNKNOWN
    )

    assert report["held_pro"] == [{"client": "Yossi", "pro": ""}]


# --- offer_pending_lead ---


def test_offer_assigns_the_re_read_pro():
    pro = {"_id": "p1", "business_name": "Dana"}
    assign_sync = MagicMock(return_value=(assignment.ASSIGN_SENT, "Dana"))

    result = assignment.offer_pending_lead(
        "lead1", "p1", find_pro=lambda _: pro, assign_sync=assign_sync
    )

    assert result == (assignment.ASSIGN_SENT, "Dana")
    assign_sync.assert_called_once_with("lead1", pro)


def test_offer_to_a_pro_no_longer_eligible_assigns_nothing():
    assign_sync = MagicMock()

    outcome, _ = assignment.offer_pending_lead(
        "lead1", "p1", find_pro=lambda _: None, assign_sync=assign_sync
    )

    assert outcome == assignment.ASSIGN_PRO_GONE
    assign_sync.assert_not_called()


def test_offer_with_a_raising_lookup_does_not_propagate():
    def find_pro(_):
        raise RuntimeError("db down")

    assign_sync = MagicMock()
    outcome, _ = assignment.offer_pending_lead(
        "lead1", "p1", find_pro=find_pro, assign_sync=assign_sync
    )

    # Nothing was written, so "may be assigned anyway" would be false; and it says
    # nothing about the pro, so "no longer available" would be too.
    assert outcome == assignment.ASSIGN_PRO_LOOKUP_FAILED
    assign_sync.assert_not_called()


# --- the view's wiring, by source ---


def test_view_cannot_set_a_pro_outside_the_routing():
    """Dropping ``offer_pending`` would degrade every waiting lead to record-only;
    a lead write back in the view would skip the routing altogether; and the
    Create form, which writes a pro without offering, must say so."""
    source = (Path(__file__).parents[1] / "admin_panel/views/home.py").read_text(
        encoding="utf-8"
    )
    code = re.sub(r"(?m)#.*$", "", source)
    assert code.count("offer_pending=_offer_pending") == 2
    assert "leads_collection.update_one" not in code
    assert '"create_lead_no_offer"' in code
    assert code.count('"leads_pro_help"') == 2
    # Every assignment outcome, including the strip's, gets its own sentence.
    assert "ASSIGN_PRO_GONE" in code
    assert "ASSIGN_PRO_LOOKUP_FAILED" in code
