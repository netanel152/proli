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


def save_daily_schedule(
    slots_collection,
    edited_df: pd.DataFrame,
    original_ids: set[str],
    pro_id,
    selected_date,
    tz,
) -> dict:
    """Apply the admin's Daily-tab edits to ``slots_collection``.

    ``original_ids`` are the (stringified) slot ids that were loaded into the
    editor; anything the admin removed is deleted, rows whose ``_id`` is still
    one of them are updated in place, and id-less rows are inserted. Times
    arrive as local ``datetime.time`` values and are stored UTC, exactly as
    the view always did.

    Returns ``{"deleted": n, "updated": n, "inserted": n, "skipped": n}`` so
    the view can tell the operator what actually happened — ``skipped`` counts
    rows dropped for a missing/unparseable time, and the view warns instead of
    celebrating when nothing was written at all.

    A frame lacking the ``_id`` column altogether is treated the same as an
    editor showing no rows: every ``original_ids`` entry is deleted. The view
    can no longer produce that shape (it builds with ``EDITOR_COLUMNS``), but
    any other caller should know the degrade is "empty editor", not "no-op".
    """
    # Missing columns (a frame built without the explicit schema) must degrade
    # to "column of nothing", never to KeyError — that crash is this ticket.
    id_series = (
        edited_df["_id"] if "_id" in edited_df.columns else pd.Series(dtype=object)
    )

    ids_to_delete = original_ids - set(id_series.dropna().astype(str))
    deleted = 0
    if ids_to_delete:
        result = slots_collection.delete_many(
            {"_id": {"$in": [ObjectId(oid) for oid in ids_to_delete]}}
        )
        deleted = result.deleted_count

    updated = 0
    skipped = 0
    new_slots = []
    for _, row in edited_df.iterrows():
        raw_start, raw_end = row.get("start_time"), row.get("end_time")
        s_time = None if _is_missing(raw_start) else _coerce_time(raw_start)
        e_time = None if _is_missing(raw_end) else _coerce_time(raw_end)
        # Half-filled or unparseable rows are skipped — and counted, so the
        # view can say so instead of reporting success over a no-op.
        if s_time is None or e_time is None:
            skipped += 1
            continue

        try:
            dt_start = tz.localize(datetime.combine(selected_date, s_time)).astimezone(
                pytz.utc
            )
            dt_end = tz.localize(datetime.combine(selected_date, e_time)).astimezone(
                pytz.utc
            )
        except Exception:
            skipped += 1
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
        elif isinstance(row_id, str) and row_id in original_ids:
            result = slots_collection.update_one(
                {"_id": ObjectId(row_id)}, {"$set": slot_data}
            )
            # matched_count, not call count: a slot another session deleted
            # between load and save must not be reported as updated.
            updated += result.matched_count
        # A non-empty id that is not in original_ids cannot come from this
        # editor (rows are loaded from this day's query) — leave it alone
        # rather than guess, matching the old behaviour.

    inserted = 0
    if new_slots:
        result = slots_collection.insert_many(new_slots)
        inserted = len(result.inserted_ids)

    return {
        "deleted": deleted,
        "updated": updated,
        "inserted": inserted,
        "skipped": skipped,
    }
