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

from bson.objectid import ObjectId

from app.core.constants import Actor
from app.core.lead_history import status_history_entry
from app.core.logger import logger

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
    "status",
    "_chat_id",
]

#: Editor column -> lead document field. ``details_summary`` writes both
#: ``details`` and ``issue_type``, so it is handled separately below.
_SIMPLE_FIELDS = {"status": "status"}

#: Why a row the admin edited was not written. The view renders these next to
#: the success so a skip is never silent — ``SKIP_LEAD_GONE`` in particular
#: means the edit is lost and has to be retyped.
SKIP_UNRESOLVED = "unresolved_row"
SKIP_NO_CHANGE = "no_change"
SKIP_LEAD_GONE = "lead_gone"


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


def _build_update_payload(changed_data, *, pro_map_name_to_id, unknown_pro_label):
    """Translate one editor row's changed cells into a ``$set`` payload."""
    payload = {}

    for column, field in _SIMPLE_FIELDS.items():
        if column in changed_data:
            payload[field] = changed_data[column]

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
        else:
            payload["pro_id"] = pro_map_name_to_id.get(new_pro_name)

    return payload


def save_lead_edits(
    edited_df,
    edited_rows,
    *,
    leads_collection,
    pro_map_name_to_id,
    unknown_pro_label,
    audit=None,
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

    Returns:
        ``{"updated": int, "skipped": int, "skipped_rows": list[dict]}``.

        Each entry in ``skipped_rows`` is ``{"client": str, "reason": str}``
        with ``reason`` one of ``SKIP_UNRESOLVED`` / ``SKIP_NO_CHANGE`` /
        ``SKIP_LEAD_GONE``. A bare count would be its own small version of the
        bug this module exists to fix: "the lead was deleted under you" means
        the admin's edit is permanently lost and must be retyped, while "no
        writable field changed" is harmless — collapsing the two into one
        integer tells the operator nothing they can act on.
    """
    updated = 0
    skipped_rows = []

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
        )
        if not payload:
            _skip(idx, SKIP_NO_CHANGE)
            continue

        update_op = {"$set": payload}

        if "status" in payload:
            # Read the stored status before writing, for two reasons at once:
            # it tells us the lead still exists, and it tells us whether this
            # is a real transition.
            current = leads_collection.find_one(
                {"_id": ObjectId(lead_id)}, {"status": 1}
            )
            if current is None:
                logger.warning(
                    f"[LeadsEditor] Lead {lead_id} vanished before its status "
                    f"was read."
                )
                _skip(idx, SKIP_LEAD_GONE)
                continue

            # PRO-57: every real transition is appended, admin edits included.
            # But only a *real* one — the grid keeps an edit entry for a cell
            # toggled A->B->A, and another actor may have already applied the
            # same move inside the 30s cache window. Pushing regardless would
            # write duplicate consecutive entries and skew the funnel. The
            # sibling edit form already guards this (`home.py`: `if new_status
            # != selected_lead.get("status")`); this matches it.
            if current.get("status") != payload["status"]:
                update_op["$push"] = {
                    "status_history": status_history_entry(
                        payload["status"], Actor.ADMIN
                    )
                }

        result = leads_collection.update_one({"_id": ObjectId(lead_id)}, update_op)

        # matched_count, not modified_count: re-saving a row to the value it
        # already holds is a successful save, not a skipped one.
        # Still reachable, and not a duplicate of the check above: the pre-read
        # only runs for status edits, so a details-only or pro-only edit has
        # this as its sole existence guard — and a delete landing *between* the
        # two calls shows up here too.
        if getattr(result, "matched_count", 0) == 0:
            logger.warning(
                f"[LeadsEditor] Lead {lead_id} vanished between read and write."
            )
            _skip(idx, SKIP_LEAD_GONE)
            continue

        if audit is not None:
            audit("edit_lead", {"lead_id": lead_id, "changes": payload})
        updated += 1

    return {
        "updated": updated,
        "skipped": len(skipped_rows),
        "skipped_rows": skipped_rows,
    }
