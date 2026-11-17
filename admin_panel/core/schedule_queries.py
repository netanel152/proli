"""Daily-schedule save logic for the admin panel — streamlit-free (PRO-158).

Extracted from ``admin_panel/views/schedule.py``'s Save button so the
empty-day path has a real mongomock test. The view builds the
``st.data_editor`` frame; this module owns what Save *does* with the result:
delete the rows the admin removed, update the ones that already exist, insert
the ones that are new.

The bug this rode in on: ``pd.DataFrame([])`` has **zero columns** — the
editor's ``column_config`` styles columns, it does not create them — so on a
day with no existing slots the very first read (``edited_df["_id"]``) raised
``KeyError`` and the Daily tab could never create a pro's first slot for a
date. The view now builds the frame with an explicit schema, and this function
is written to survive whatever shape the editor hands back.

Follows the PRO-140 ``analytics_queries.py`` precedent: the collection is
injected, tests pass mongomock.
"""

from datetime import datetime, time as dt_time

import pandas as pd
import pytz
from bson.objectid import ObjectId
from pymongo.errors import DuplicateKeyError

from app.core.constants import LeadStatus
from app.core.slot_writes import insert_slots_sync

#: The editor frame's schema. The view builds its DataFrame with exactly these
#: columns so they exist even when the day has no slots yet (PRO-158).
EDITOR_COLUMNS = ["_id", "start_time", "end_time", "is_taken"]


def _is_missing(value) -> bool:
    """True for every none-ish shape an editor cell can come back as.

    ``st.data_editor`` reports an untouched cell of a freshly added row as
    ``None``/NaN — but an empty string has also been observed from
    object-dtype frames, and the old ``pd.isna``-only check on ``_id``
    silently dropped such a row (neither updated nor inserted).
    """
    if value is None or value == "":
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _coerce_time(value):
    """Normalize an editor time cell to ``datetime.time``, or ``None``.

    On a populated day ``st.data_editor`` returns ``datetime.time`` — but on
    an **empty** day the frame is all-null, pyarrow types every column
    ``EMPTY``, and Streamlit's ``_parse_value`` passes the browser's raw
    string (``'09:00:00.000'``) through untouched. Feeding that to
    ``datetime.combine`` raised ``TypeError``, the bare except swallowed it,
    and PRO-158's "fix" silently saved nothing while announcing success —
    caught in review before it shipped. ``None`` means "skip this row".
    """
    if isinstance(value, dt_time):
        return value
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.time()
    if isinstance(value, str):
        # A colon-less string ("0900", "2026-01-01") parses to a silently
        # wrong midnight instead of failing. TimeColumn can never emit one,
        # but this function is documented as tolerant of other callers.
        if ":" not in value:
            return None
        try:
            return pd.Timestamp(value).time()
        except (ValueError, TypeError):
            return None
    return None


#: Lead statuses whose ``booked_slot_id`` no longer holds the slot.
_RELEASED_STATUSES = (
    LeadStatus.COMPLETED,
    LeadStatus.CANCELLED,
    LeadStatus.CLOSED,
    LeadStatus.REJECTED,
)


def _as_utc(value):
    """Mongo hands datetimes back naive (UTC); compare them aware."""
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=pytz.utc)
    return value


def _held_slot_ids(leads_collection, slot_ids) -> set[str]:
    """Slots a live lead's ``booked_slot_id`` points to (PRO-278)."""
    if leads_collection is None or not slot_ids:
        return set()
    return {
        str(lead["booked_slot_id"])
        for lead in leads_collection.find(
            {
                "booked_slot_id": {"$in": [ObjectId(i) for i in slot_ids]},
                "status": {"$nin": list(_RELEASED_STATUSES)},
            },
            {"booked_slot_id": 1},
        )
    }


def save_daily_schedule(
    slots_collection,
    edited_df: pd.DataFrame,
    original_slots,
    pro_id,
    selected_date,
    tz,
    leads_collection=None,
) -> dict:
    """Apply the admin's Daily-tab edits to ``slots_collection``.

    ``original_slots`` are the slot documents that were loaded into the editor.
    Anything the admin removed is deleted, rows whose ``_id`` is one of them
    are updated in place, and id-less rows are inserted. Times arrive as local
    ``datetime.time`` values and are stored UTC, exactly as the view always did.

    PRO-278: the worker books slots while the editor is open, so a save writes
    only what the admin changed, never the whole row. Each update and delete is
    conditional on the ``is_taken`` the editor was built from. A slot that a
    live lead's ``booked_slot_id`` points to (when ``leads_collection`` is
    given, as the view does) is never set free or deleted from here.

    Returns counts so the view can say what happened:

    * ``deleted``, ``updated``, ``inserted``: rows written.
    * ``skipped``: rows dropped for a missing or unparseable time.
    * ``held``: rows left alone because a live lead holds the slot.
    * ``stale``: rows that changed in the database after the editor loaded them.
    * ``duplicates``: rows whose start time this pro already has a slot at
      (the unique index, PRO-277).

    A frame lacking the ``_id`` column altogether is treated the same as an
    editor showing no rows: every original slot is a candidate for deletion.
    The view can no longer produce that shape (it builds with
    ``EDITOR_COLUMNS``), but any other caller should know the degrade is
    "empty editor", not "no-op".
    """
    originals = {str(doc["_id"]): doc for doc in original_slots}
    held = _held_slot_ids(leads_collection, list(originals))

    # Missing columns (a frame built without the explicit schema) must degrade
    # to "column of nothing", never to KeyError — that crash is PRO-158.
    id_series = (
        edited_df["_id"] if "_id" in edited_df.columns else pd.Series(dtype=object)
    )

    counts = dict.fromkeys(
        ("deleted", "updated", "inserted", "skipped", "held", "stale", "duplicates"),
        0,
    )

    for oid in sorted(set(originals) - set(id_series.dropna().astype(str))):
        if oid in held:
            counts["held"] += 1
            continue
        result = slots_collection.delete_one(
            {"_id": ObjectId(oid), "is_taken": originals[oid].get("is_taken")}
        )
        if result.deleted_count:
            counts["deleted"] += 1
        else:
            counts["stale"] += 1

    new_slots = []
    for _, row in edited_df.iterrows():
        raw_start, raw_end = row.get("start_time"), row.get("end_time")
        s_time = None if _is_missing(raw_start) else _coerce_time(raw_start)
        e_time = None if _is_missing(raw_end) else _coerce_time(raw_end)
        # Half-filled or unparseable rows are skipped — and counted, so the
        # view can say so instead of reporting success over a no-op.
        if s_time is None or e_time is None:
            counts["skipped"] += 1
            continue

        try:
            dt_start = tz.localize(datetime.combine(selected_date, s_time)).astimezone(
                pytz.utc
            )
            dt_end = tz.localize(datetime.combine(selected_date, e_time)).astimezone(
                pytz.utc
            )
        except Exception:
            counts["skipped"] += 1
            continue

        # A new row's unticked checkbox can come back None/NaN, and Mongo
        # would store is_taken: None — invisible to every is_taken:False
        # query, i.e. a slot that exists but can never be booked. Coerce.
        raw_taken = row.get("is_taken")
        is_taken = False if _is_missing(raw_taken) else bool(raw_taken)

        slot_data = {
            "pro_id": pro_id,
            "start_time": dt_start,
            "end_time": dt_end,
            "is_taken": is_taken,
        }

        row_id = row.get("_id")
        if _is_missing(row_id):
            new_slots.append(slot_data)
            continue
        if not (isinstance(row_id, str) and row_id in originals):
            # A non-empty id that is not one of the loaded slots cannot come
            # from this editor — leave it alone rather than guess.
            continue

        original = originals[row_id]
        changes = {
            field: value
            for field, value in slot_data.items()
            if field != "pro_id" and _as_utc(original.get(field)) != value
        }
        if not changes:
            continue
        if row_id in held and changes.get("is_taken") is False:
            counts["held"] += 1
            continue
        try:
            result = slots_collection.update_one(
                {"_id": ObjectId(row_id), "is_taken": original.get("is_taken")},
                {"$set": changes},
            )
        except DuplicateKeyError:
            counts["duplicates"] += 1
            continue
        # matched_count, not call count: a slot that was booked, or deleted by
        # another session, between load and save is not reported as updated.
        if result.matched_count:
            counts["updated"] += 1
        else:
            counts["stale"] += 1

    if new_slots:
        inserted = insert_slots_sync(slots_collection, new_slots)
        counts["inserted"] = inserted
        counts["duplicates"] += len(new_slots) - inserted

    return counts
