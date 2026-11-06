"""Leads-table save logic for the admin panel — streamlit-free (PRO-161).

Extracted from ``admin_panel/views/home.py``'s Save button so the row-identity
rule has a real mongomock test. The view builds and renders the
``st.data_editor`` frame; this module owns what Save *does* with the result.

The bug this rode in on: the view cached the rendered frame **once per
session** (``if "original_leads_df" not in st.session_state``) and then
resolved each edited row's lead id by position against that stale copy::

    lead_id = st.session_state.original_leads_df.iloc[row_idx]["id"]

``get_leads_data()`` is ``@st.cache_data(ttl=30)`` and sorts
``created_at`` descending, so a single new inbound lead shifts every row by
one and the admin's edit landed on a *different lead's* document. Deleted
leads made the same line raise ``IndexError`` instead.

The rule this module enforces: **a row's identity comes from the frame the
editor returned, never from a snapshot taken at some other time.**
``st.data_editor``'s ``edited_rows`` keys are positional indices into the
frame handed to the widget in that same run, and ``edited_df`` is that frame
with the edits applied — so the two cannot diverge by construction. Any other
source of ids re-opens the bug.

Follows the PRO-140 ``analytics_queries.py`` / PRO-158 ``schedule_queries.py``
precedent: the collection is injected, tests pass mongomock.

**Admin edits deliberately bypass lifecycle order.** The status column offers
every ``LeadStatus``, so an admin can move a lead backwards
(``completed -> new``) or skip states (``contacted -> completed``). That is the
point of an operator override — the panel exists to unstick leads the flow got
wrong — and there is no ``VALID_TRANSITIONS`` helper in ``app/`` to validate
against. Recorded here so the absence reads as a decision, not an oversight.
"""

import pytz
from bson.objectid import ObjectId

from app.core.constants import Actor, LeadStatus
from app.core.lead_history import status_history_entry
from app.core.logger import logger
from app.core.phone import strip_suffix

#: Columns the leads editor frame carries. ``id`` and ``_chat_id`` are hidden
#: from the admin by ``column_config`` — hidden, not dropped: they are still
#: present in the frame ``st.data_editor`` returns, which is exactly why the
#: id can be read back out of it.
EDITOR_COLUMNS = [
    "id",
    "date",
    "client",
    "professional",
    "details_summary",
    # PRO-61: the status the operator sees and edits, as a *label* in their
    # language. `st.column_config.SelectboxColumn` has no `format_func`, so
    # the only way to show "נקבעה פגישה" instead of "booked" is for the cell
    # to hold the label; `status` stays as the raw, hidden, authoritative
    # value (the Kanban groups by it, the pill CSS class is keyed by it) and
    # the save path maps the edited label back through `status_by_label`.
    "status_label",
    "status",
    "_chat_id",
    # PRO-163: the *raw* display name behind the composed `client` cell,
    # carried hidden the way `_chat_id` already is. The Edit form prefills
    # the name box from this and never from `client`, which falls back to
    # the customer's own name or the phone — prefilling from the composed
    # value would save one of those back as an admin-authored name.
    #
    # There is deliberately no `_details` twin. The details box is prefilled
    # from the *composed* summary, so "did the admin touch it" has to be
    # measured against that same composed value; a raw carrier would be read
    # by nobody.
    "_display_name",
]

#: The panel renders every timestamp in Israel local time.
_IL_TZ = pytz.timezone("Asia/Jerusalem")

#: Editor column -> lead document field. ``details_summary`` writes both
#: ``details`` and ``issue_type``, so it is handled separately below.
_SIMPLE_FIELDS = {"status": "status"}

#: Why a row the admin edited was not written. The view renders these next to
#: the success so a skip is never silent — ``SKIP_LEAD_GONE`` in particular
#: means the edit is lost and has to be retyped.
SKIP_UNRESOLVED = "unresolved_row"
SKIP_NO_CHANGE = "no_change"
SKIP_LEAD_GONE = "lead_gone"
SKIP_SAVE_FAILED = "save_failed"


def client_label(lead):
    """The name to show for ``lead`` — the admin's label, the customer's own
    name, else the phone.

    PRO-163. The leads frame's ``client`` column is composed in exactly one
    place, so this is the only function that decides what an admin sees as the
    customer's name: the editor column, the Kanban card, the lead selectbox and
    the skipped-row warnings all read the composed value.

    Three rungs, in order:

    1. ``display_name`` — what an admin typed into the Edit form. Optional and
       additive, so a lead without it reads exactly as before and no migration
       is needed. Blank and whitespace-only count as absent rather than being
       stored, which is why clearing the input unsets the field: an empty
       string would render an anonymous row with no way back to the phone.
    2. ``customer_name`` — the name the AI extracted when the customer gave it
       (``prompts.py``). The pro already sees this on every lead offer as
       "לקוח: דני", so falling straight to the phone here meant the panel and
       the pro's WhatsApp disagreed about who the customer is.
    3. the phone number, ``@c.us`` stripped.
    """
    name = (lead.get("display_name") or "").strip()
    if name:
        return name
    known = (lead.get("customer_name") or "").strip()
    if known:
        return known
    return strip_suffix(lead.get("chat_id") or "")


def build_lead_row(lead, *, pro_map_id_to_name, unknown_pro_label, status_labels=None):
    """One lead document -> one row of the leads-editor frame.

    PRO-163. Extracted from ``views/home.py``'s ``get_leads_data`` closure,
    which sat behind ``@st.cache_data`` inside another function and so could
    not be reached from a test at all. That is exactly where this ticket's own
    regression lived: the Edit form prefills from a hidden column composed
    here, and while that column was missing the form read ``""`` every time and
    its blank-means-clear rule erased names nobody had touched.

    The row carries two values the admin never sees but the form depends on:
    ``_chat_id`` (identity) and ``_display_name`` (the raw value behind the
    composed ``client`` cell). ``client`` is for display and falls back; the
    raw carrier is how a submit tells a real edit from an untouched field.
    Composing and carrying them together, here, is what keeps that pair
    honest.

    Same move as ``save_lead_edits`` (PRO-161) and the PRO-140/PRO-158 query
    extractions: streamlit-free, so it has a test.

    ``status_labels`` (PRO-61) is ``{status: localized label}`` for the
    editable ``status_label`` twin column; a status it does not cover is shown
    as its raw value, never capitalised into fake English.
    """
    issue_type = lead.get("issue_type", lead.get("issue", lead.get("details", "")))
    appointment_time = lead.get("appointment_time", lead.get("time_preference", "?"))
    full_address = lead.get("full_address", lead.get("address", "?"))

    # Legacy shape: some early leads packed the structured fields into a
    # "[DEAL: time|address|issue]" marker inside the free-text details.
    if (
        not lead.get("issue_type")
        and not lead.get("issue")
        and "[DEAL:" in str(lead.get("details", ""))
    ):
        try:
            parts = lead["details"].split("[DEAL:")[1].split("]")[0].split("|")
            if len(parts) >= 3:
                appointment_time = parts[0].strip()
                full_address = parts[1].strip()
                issue_type = parts[2].strip()
        except (IndexError, KeyError, AttributeError):
            pass

    display_details = lead.get("details", "")
    if not display_details and issue_type:
        display_details = f"{issue_type} | {appointment_time} | {full_address}"
    if issue_type != lead.get("details", ""):
        display_details = f"{issue_type} | {appointment_time} | {full_address}"

    pro_name = pro_map_id_to_name.get(lead.get("pro_id"), unknown_pro_label)
    # `or`, not a default: an explicit null would otherwise blank the cell
    # of a required selectbox and drop the lead out of every Kanban column.
    status = lead.get("status") or "N/A"

    return {
        "id": str(lead["_id"]),
        "date": lead["created_at"].astimezone(_IL_TZ),
        "client": client_label(lead),
        "_display_name": (lead.get("display_name") or ""),
        "professional": pro_name,
        "details_summary": display_details,
        "status_label": (status_labels or {}).get(status, status),
        "status": status,
        "_chat_id": lead["chat_id"],
    }


def build_edit_form_payload(
    *,
    status,
    details,
    details_touched,
    display_name,
    pro_name,
    pro_map_name_to_id,
    unknown_pro_label,
):
    """Translate the Edit-Lead form's fields into ``($set, $unset)`` ops.

    PRO-163. Split out of the view for the same reason the table's save was
    (PRO-161): a payload built inline in a Streamlit callback cannot be tested,
    and this form has already shipped one that wrote the *editor's column
    names* onto the lead document — ``client``, ``phone_number``,
    ``details_summary`` — none of which any reader looks for, so every field
    but ``status`` was silently discarded.

    Unlike ``_build_update_payload`` this receives whole values rather than
    changed cells: the form submits every input on every save, so "unchanged"
    has to be passed in rather than detected. That is what ``details_touched``
    is for, and it is not a nicety — see below.
    """
    set_ops = {"status": status}
    unset_ops = {}

    if details_touched:
        # The editor shows one composed summary cell; the lead document keeps
        # the admin's text in both fields the rest of the app reads from.
        #
        # Only on a real edit. The details box is prefilled with the *composed*
        # summary ("<issue> | <time> | <address>"), so writing it back
        # unconditionally stamped that whole string into `issue_type` every
        # time the form was saved for any reason — including a save that only
        # set a client name. `issue_type` is pro-facing (the "תקלה:" line of
        # the lead offer) and is a matching input, so that is live corruption
        # of a field the customer never sees but the pro does.
        set_ops["details"] = details
        set_ops["issue_type"] = details

    name = (display_name or "").strip()
    if name:
        set_ops["display_name"] = name
    else:
        # Cleared, not blanked — see ``client_label``.
        unset_ops["display_name"] = ""

    if pro_name == unknown_pro_label:
        # PRO-60: clearing the pro must null pro_id, or matching, the healer
        # and pro-flow keep treating the lead as assigned.
        set_ops["pro_id"] = None
    elif pro_name in pro_map_name_to_id:
        set_ops["pro_id"] = ObjectId(pro_map_name_to_id[pro_name])

    return set_ops, unset_ops


def _resolve_lead_id(edited_df, row_idx):
    """Return the lead id at ``row_idx`` of the returned editor frame.

    Returns ``None`` rather than raising when the index is out of range or the
    id is unusable. The old code's ``.iloc`` on a stale frame could raise
    ``IndexError`` mid-loop and abandon the remaining edits after having
    already written some of them; skipping one row keeps the rest of the save
    atomic-per-row and lets the caller report the skip.
    """
    if row_idx is None or row_idx < 0 or row_idx >= len(edited_df):
        return None

    raw = edited_df.iloc[row_idx].get("id")
    if raw is None:
        return None

    lead_id = str(raw).strip()
    if not lead_id or lead_id.lower() in ("nan", "none"):
        return None
    if not ObjectId.is_valid(lead_id):
        return None
    return lead_id


def _build_update_payload(
    changed_data, *, pro_map_name_to_id, unknown_pro_label, status_by_label=None
):
    """Translate one editor row's changed cells into a ``$set`` payload."""
    payload = {}

    for column, field in _SIMPLE_FIELDS.items():
        if column in changed_data:
            payload[field] = changed_data[column]

    if "status_label" in changed_data:
        # PRO-61: the operator picked a *label*; only a label the map knows
        # becomes a status. An unknown one (a stale frame from before a
        # language switch) is dropped rather than written verbatim as a
        # status nobody else in the app would recognise.
        label = changed_data["status_label"]
        status = (status_by_label or {}).get(label)
        if status is None:
            logger.warning(
                f"[LeadsEditor] Ignoring status label {label!r} — not in the "
                f"current language's status map."
            )
        else:
            payload["status"] = status

    if "details_summary" in changed_data:
        # The editor shows one composed summary cell; the lead document keeps
        # the admin's text in both fields the rest of the app reads from.
        summary = changed_data["details_summary"]
        payload["details"] = summary
        payload["issue_type"] = summary

    if "professional" in changed_data:
        new_pro_name = changed_data["professional"]
        if new_pro_name == unknown_pro_label:
            # PRO-60: clearing the pro must null pro_id, or matching, the
            # healer and pro-flow keep treating the lead as assigned.
            payload["pro_id"] = None
        elif new_pro_name is None:
            # The cell was emptied: the same "unassigned" as the label above.
            payload["pro_id"] = None
        elif new_pro_name in pro_map_name_to_id:
            payload["pro_id"] = pro_map_name_to_id[new_pro_name]
        else:
            # A pro un-approved since the page rendered is not "unassigned":
            # writing None here would silently take the lead off its owner.
            logger.warning(
                "[LeadsEditor] Ignoring a professional no longer on the eligible "
                "list."
            )

    return payload


def apply_lead_changes(
    lead_id,
    set_ops,
    unset_ops=None,
    *,
    leads_collection,
    offer_pending=None,
    audit=None,
):
    """Write one lead's edits and decide what setting its pro *means*.

    Shared by the leads table and the Edit form, so the two cannot disagree:

    * A real pro change on a lead that is and stays ``PENDING_ADMIN_REVIEW`` is
      an assignment. It goes through ``offer_pending`` (the strip's path), which
      moves the status and sends the offer. The other fields are written first,
      so the offer carries the corrected details.
    * Any other pro change is a record correction — a mis-typed pro on a
      ``COMPLETED`` lead must not message anybody. It is written as is and
      ``record_only`` comes back true, so the caller says plainly that no offer
      went out. The same when no ``offer_pending`` was supplied: never a silent
      bare write.
    * A pro left standing on a waiting lead is reported too, even by an edit that
      never touched the pro: escalation keeps ``pro_id``
      (``monitor_service``), so a waiting lead holding a pro who was never told
      about it is a normal state, and the operator editing it should hear so.

    Fields that already hold the stored value are dropped rather than rewritten:
    a stale frame must not put back a status somebody else just moved, and a
    second run of the same Save (a double click during the send) finds the pro
    already set and sends nothing.

    Returns ``{"found", "written", "changes", "assignment", "record_only",
    "pro_id"}``. ``assignment`` is ``(outcome, pro_name)`` or ``None``;
    ``written`` lists the fields written directly, which excludes the routed
    ``pro_id``; ``pro_id`` is the pro the lead holds or was offered to.
    """
    oid = ObjectId(lead_id)
    set_ops = dict(set_ops)
    unset_ops = dict(unset_ops or {})
    update_op = {}
    offer_to = None
    record_only = False
    new_pro = None

    def _result(found, assignment=None):
        return {
            "found": found,
            "written": sorted([*set_ops, *unset_ops]) if found else [],
            "changes": set_ops,
            "assignment": assignment,
            "record_only": record_only and found,
            "pro_id": new_pro,
        }

    # Doubles as the existence check for every kind of edit.
    current = leads_collection.find_one({"_id": oid}, {"status": 1, "pro_id": 1})
    if current is None:
        logger.warning(f"[LeadsEditor] Lead {lead_id} vanished before it was read.")
        return _result(False)

    new_status = set_ops.get("status", current.get("status"))
    status_moved = new_status != current.get("status")
    if status_moved:
        # PRO-57: every real transition is appended, admin edits included.
        # But only a *real* one — the grid keeps an edit entry for a cell
        # toggled A->B->A, and pushing regardless would write duplicate
        # consecutive entries and skew the funnel.
        update_op["$push"] = {
            "status_history": status_history_entry(new_status, Actor.ADMIN)
        }
    else:
        set_ops.pop("status", None)

    new_pro = set_ops.get("pro_id", current.get("pro_id"))
    pro_moved = new_pro != current.get("pro_id")
    if not pro_moved:
        set_ops.pop("pro_id", None)

    waiting = new_status == LeadStatus.PENDING_ADMIN_REVIEW
    if (
        pro_moved
        and new_pro is not None
        and waiting
        and not status_moved
        and offer_pending is not None
    ):
        offer_to = set_ops.pop("pro_id")
    elif new_pro is not None and (pro_moved or waiting):
        # shortcut: a record-only write trusts the caller's eligibility instead of
        # re-reading the pro as the offer path does. The table and the form build
        # `pro_map_name_to_id` from APPROVED_PRO_FILTER on the same script run as
        # the Save, so the gap is that one run, and a pro un-approved inside it
        # stays recorded with nothing sent. Re-read here if the map ever gets cached.
        record_only = True

    if set_ops:
        update_op["$set"] = set_ops
    if unset_ops:
        update_op["$unset"] = unset_ops
    if update_op:
        result = leads_collection.update_one({"_id": oid}, update_op)
        # matched_count, not modified_count: a write of the value already held
        # is still a successful save. A delete landing between the read and this
        # write shows up here.
        if getattr(result, "matched_count", 0) == 0:
            logger.warning(
                f"[LeadsEditor] Lead {lead_id} vanished between read and write."
            )
            return _result(False)

    if offer_to is None:
        return _result(True)

    outcome, pro_name = offer_pending(lead_id, offer_to)
    _audit_quietly(
        audit,
        "assign_lead",
        {"lead_id": lead_id, "pro_id": str(offer_to), "outcome": outcome},
    )
    return _result(True, (outcome, pro_name))


def _audit_quietly(audit, action, details):
    """A failed audit write must not cost the operator an offer's outcome."""
    if audit is None:
        return
    try:
        audit(action, details)
    except Exception as e:
        logger.error(f"[LeadsEditor] Audit write for {action} failed: {e}")


def _flash_rows(change, client, pro_label):
    """``(assignments, record_only)`` rows for one lead's flash."""
    assignments = []
    if change["assignment"]:
        outcome, pro_name = change["assignment"]
        assignments.append({"client": client, "outcome": outcome, "pro_name": pro_name})
    record_only = (
        [{"client": client, "pro": str(pro_label or "")}]
        if change["record_only"]
        else []
    )
    return assignments, record_only


def is_noop(change):
    """Nothing was written, nothing was offered, nothing needs saying."""
    return not (change["written"] or change["assignment"] or change["record_only"])


def edit_form_flash(change, *, client, pro_label):
    """The ``leads_flash`` for one Edit-form save, shaped like the table's."""
    assignments, record_only = _flash_rows(change, client, pro_label)
    skipped_rows = (
        [{"client": client, "reason": SKIP_NO_CHANGE}] if is_noop(change) else []
    )
    return {
        "updated": 1 if change["written"] else 0,
        "skipped": len(skipped_rows),
        "skipped_rows": skipped_rows,
        "assignments": assignments,
        "record_only": record_only,
    }


def save_lead_edits(
    edited_df,
    edited_rows,
    *,
    leads_collection,
    pro_map_name_to_id,
    unknown_pro_label,
    audit=None,
    status_by_label=None,
    offer_pending=None,
):
    """Apply the leads editor's edits, resolving row identity safely.

    Args:
        edited_df: the frame ``st.data_editor`` **returned** this run. Row
            identity is read from here and nowhere else (PRO-161).
        edited_rows: ``st.session_state[<key>]["edited_rows"]`` — a mapping of
            positional row index to the cells that changed in that row.
        leads_collection: injected so tests can pass mongomock.
        pro_map_name_to_id: business name -> pro ``_id``.
        unknown_pro_label: the localized "unassigned" option in the pro column.
        audit: optional ``callable(action, details)`` for the audit log. The
            real one reads ``st.session_state``, so it is injected rather than
            imported — this module stays streamlit-free.
        status_by_label: ``{localized label: status}`` for the ``status_label``
            column (PRO-61); ``labels.status_by_label(T)`` in the view.
        offer_pending: ``callable(lead_id, pro_id) -> (outcome, pro_name)``,
            see :func:`apply_lead_changes`.

    Returns:
        ``{"updated": int, "skipped": int, "skipped_rows": list[dict],
        "assignments": list[dict], "record_only": list[dict]}``.

        ``updated`` counts rows that wrote a field. ``assignments`` holds
        ``{"client", "outcome", "pro_name"}`` per row that went through the offer
        path. ``record_only`` holds ``{"client", "pro"}`` per row left with a pro
        and no offer.

        Each entry in ``skipped_rows`` is ``{"client": str, "reason": str}``
        with ``reason`` one of ``SKIP_UNRESOLVED`` / ``SKIP_NO_CHANGE`` /
        ``SKIP_LEAD_GONE`` / ``SKIP_SAVE_FAILED``. A bare count would be its own small version of the
        bug this module exists to fix: "the lead was deleted under you" means
        the admin's edit is permanently lost and must be retyped, while "no
        writable field changed" is harmless — collapsing the two into one
        integer tells the operator nothing they can act on.
    """
    updated = 0
    skipped_rows = []
    assignments = []
    record_only = []
    name_by_id = {pro_id: name for name, pro_id in pro_map_name_to_id.items()}

    def _skip(idx, reason):
        client = ""
        if idx is not None and 0 <= idx < len(edited_df):
            client = str(edited_df.iloc[idx].get("client", "") or "")
        skipped_rows.append({"client": client, "reason": reason})

    for row_idx, changed_data in (edited_rows or {}).items():
        try:
            idx = int(row_idx)
        except (TypeError, ValueError):
            _skip(None, SKIP_UNRESOLVED)
            continue

        lead_id = _resolve_lead_id(edited_df, idx)
        if lead_id is None:
            # Not a silent drop: surfaced to the admin next to the success.
            logger.warning(
                f"[LeadsEditor] Skipped edited row {idx} — no resolvable lead id "
                f"in the returned frame ({len(edited_df)} rows)."
            )
            _skip(idx, SKIP_UNRESOLVED)
            continue

        payload = _build_update_payload(
            changed_data,
            pro_map_name_to_id=pro_map_name_to_id,
            unknown_pro_label=unknown_pro_label,
            status_by_label=status_by_label,
        )
        if not payload:
            _skip(idx, SKIP_NO_CHANGE)
            continue

        # One row raising must not abandon the rest, or lose the outcomes of
        # offers already sent for earlier rows.
        try:
            change = apply_lead_changes(
                lead_id,
                payload,
                leads_collection=leads_collection,
                offer_pending=offer_pending,
                audit=audit,
            )
        except Exception as e:
            logger.error(f"[LeadsEditor] Saving lead {lead_id} failed: {e}")
            _skip(idx, SKIP_SAVE_FAILED)
            continue
        if not change["found"]:
            _skip(idx, SKIP_LEAD_GONE)
            continue
        if is_noop(change):
            _skip(idx, SKIP_NO_CHANGE)
            continue

        row = edited_df.iloc[idx]
        pro_label = name_by_id.get(change["pro_id"]) or row.get("professional", "")
        row_assignments, row_record_only = _flash_rows(
            change, str(row.get("client", "") or ""), pro_label
        )
        assignments += row_assignments
        record_only += row_record_only

        if change["written"]:
            _audit_quietly(
                audit, "edit_lead", {"lead_id": lead_id, "changes": change["changes"]}
            )
            updated += 1

    return {
        "updated": updated,
        "skipped": len(skipped_rows),
        "skipped_rows": skipped_rows,
        "assignments": assignments,
        "record_only": record_only,
    }
