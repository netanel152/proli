"""Tests for admin_panel/core/schedule_queries.py — PRO-158.

Pins two stacked regressions on the Daily-tab Save button:

1. ``pd.DataFrame([])`` has ZERO columns (``column_config`` styles columns,
   it does not create them), so on a day with no existing slots the Save
   handler crashed with ``KeyError: '_id'`` and the first slot of a date
   could never be created.
2. The fix for (1) still didn't work on that exact path: on a truly empty
   day the editor's frame is all-null, pyarrow types every column ``EMPTY``,
   and Streamlit's own ``_parse_value`` passes the browser's raw time string
   (``'09:00:00.000'``) through untouched instead of a ``datetime.time``.
   Feeding that string straight to ``datetime.combine`` raised ``TypeError``
   inside a bare except, so the "fix" silently saved nothing while the view
   announced success. ``_coerce_time`` normalizes both shapes (``time``
   objects from a populated day, raw strings from an empty one) and rows
   that still don't parse are counted in the new ``skipped`` key instead of
   vanishing.

Extracted into a streamlit-free module (collection injected, mongomock in
tests, PRO-140 precedent) so both shapes have a real test.
"""

from datetime import date, datetime, time

import mongomock
import pandas as pd
import pytest
import pytz
from bson import ObjectId

from admin_panel.core.schedule_queries import EDITOR_COLUMNS, save_daily_schedule


@pytest.fixture
def db():
    return mongomock.MongoClient()["proli_test"]


TZ = pytz.timezone("Asia/Jerusalem")
DATE = date(2026, 9, 1)


# --- THE regression: a truly empty day, raw browser time strings ---


def test_save_empty_day_string_times_inserts_new_rows_without_crashing(db):
    """The real PRO-158 shape: on an empty day the editor hands back raw
    browser strings ('09:00:00.000'), not datetime.time. This must insert,
    not silently no-op while the view claims success."""
    slots = db.slots
    pro_id = ObjectId()
    edited_df = pd.DataFrame(
        [
            {
                "_id": None,
                "start_time": "09:00:00.000",
                "end_time": "10:00:00.000",
                "is_taken": False,
            },
            {
                "_id": None,
                "start_time": "11:00:00.000",
                "end_time": "12:00:00.000",
                "is_taken": None,
            },
        ],
        columns=EDITOR_COLUMNS,
    )

    result = save_daily_schedule(slots, edited_df, set(), pro_id, DATE, TZ)

    assert result == {"deleted": 0, "updated": 0, "inserted": 2, "skipped": 0}
    docs = list(slots.find({"pro_id": pro_id}).sort("start_time", 1))
    assert len(docs) == 2
    # 09:00 IDT (UTC+3 in September) -> 06:00 UTC
    expected_first_start = (
        TZ.localize(datetime.combine(DATE, time(9, 0)))
        .astimezone(pytz.utc)
        .replace(tzinfo=None)
    )
    assert docs[0]["start_time"] == expected_first_start
    assert docs[0]["is_taken"] is False
    assert docs[1]["is_taken"] is False  # None coerced


# --- the populated-day shape: datetime.time objects (also real) ---


def test_save_populated_day_time_objects_inserts_new_rows(db):
    """A mixed day (existing slots already loaded) hands back real
    datetime.time objects for new rows — the other real shape."""
    slots = db.slots
    pro_id = ObjectId()
    edited_df = pd.DataFrame(
        [
            {
                "_id": None,
                "start_time": time(9, 0),
                "end_time": time(10, 0),
                "is_taken": None,
            },
            {
                "_id": None,
                "start_time": time(11, 0),
                "end_time": time(12, 0),
                "is_taken": None,
            },
        ],
        columns=EDITOR_COLUMNS,
    )

    result = save_daily_schedule(slots, edited_df, set(), pro_id, DATE, TZ)

    assert result == {"deleted": 0, "updated": 0, "inserted": 2, "skipped": 0}
    assert slots.count_documents({"pro_id": pro_id}) == 2


# --- belt-and-braces: a frame with no columns at all ---


def test_save_columnless_frame_degrades_without_crashing(db):
    """pd.DataFrame([]) — the literal shape that used to crash — must
    degrade to an empty id-series rather than raise. With no rows visible to
    the editor, every previously-loaded id is treated as removed."""
    slots = db.slots
    pro_id = ObjectId()
    existing_id = slots.insert_one(
        {
            "pro_id": pro_id,
            "start_time": datetime(2026, 9, 1, 6, 0),
            "end_time": datetime(2026, 9, 1, 7, 0),
            "is_taken": False,
        }
    ).inserted_id
    original_ids = {str(existing_id)}

    edited_df = pd.DataFrame([])  # zero columns

    result = save_daily_schedule(slots, edited_df, original_ids, pro_id, DATE, TZ)

    assert result == {"deleted": 1, "updated": 0, "inserted": 0, "skipped": 0}
    assert slots.count_documents({}) == 0


# --- is_taken coercion ---


def test_is_taken_none_and_nan_coerced_to_literal_false(db):
    """is_taken: None (or NaN) is invisible to every is_taken:False query —
    an unbookable slot. Must be coerced to the literal boolean False."""
    slots = db.slots
    pro_id = ObjectId()
    edited_df = pd.DataFrame(
        [
            {
                "_id": None,
                "start_time": time(9, 0),
                "end_time": time(10, 0),
                "is_taken": None,
            },
            {
                "_id": None,
                "start_time": time(11, 0),
                "end_time": time(12, 0),
                "is_taken": float("nan"),
            },
        ],
        columns=EDITOR_COLUMNS,
    )

    save_daily_schedule(slots, edited_df, set(), pro_id, DATE, TZ)

    docs = list(slots.find({"pro_id": pro_id}))
    assert len(docs) == 2
    for doc in docs:
        assert doc["is_taken"] is False


# --- update path ---


def test_update_existing_row_updates_in_place_no_duplicate(db):
    slots = db.slots
    pro_id = ObjectId()
    existing_id = slots.insert_one(
        {
            "pro_id": pro_id,
            "start_time": datetime(2026, 9, 1, 6, 0),
            "end_time": datetime(2026, 9, 1, 7, 0),
            "is_taken": False,
        }
    ).inserted_id
    original_ids = {str(existing_id)}

    edited_df = pd.DataFrame(
        [
            {
                "_id": str(existing_id),
                "start_time": time(10, 0),
                "end_time": time(11, 0),
                "is_taken": True,
            }
        ],
        columns=EDITOR_COLUMNS,
    )

    result = save_daily_schedule(slots, edited_df, original_ids, pro_id, DATE, TZ)

    assert result == {"deleted": 0, "updated": 1, "inserted": 0, "skipped": 0}
    assert slots.count_documents({}) == 1  # no duplicate created
    doc = slots.find_one({"_id": existing_id})
    assert doc["is_taken"] is True
    # mongomock (like real pymongo) stores BSON datetimes as naive UTC.
    expected_start = (
        TZ.localize(datetime.combine(DATE, time(10, 0)))
        .astimezone(pytz.utc)
        .replace(tzinfo=None)
    )
    assert doc["start_time"] == expected_start


# --- delete path ---


def test_delete_row_missing_from_editor_is_removed(db):
    slots = db.slots
    pro_id = ObjectId()
    existing_id = slots.insert_one(
        {
            "pro_id": pro_id,
            "start_time": datetime(2026, 9, 1, 6, 0),
            "end_time": datetime(2026, 9, 1, 7, 0),
            "is_taken": False,
        }
    ).inserted_id
    original_ids = {str(existing_id)}

    # admin removed the row in the editor -> frame has the schema but no rows
    edited_df = pd.DataFrame([], columns=EDITOR_COLUMNS)

    result = save_daily_schedule(slots, edited_df, original_ids, pro_id, DATE, TZ)

    assert result == {"deleted": 1, "updated": 0, "inserted": 0, "skipped": 0}
    assert slots.count_documents({}) == 0


# --- timezone ---


def test_new_slot_local_time_stored_as_utc(db):
    slots = db.slots
    pro_id = ObjectId()
    edited_df = pd.DataFrame(
        [
            {
                "_id": None,
                "start_time": time(9, 0),
                "end_time": time(10, 0),
                "is_taken": False,
            }
        ],
        columns=EDITOR_COLUMNS,
    )

    save_daily_schedule(slots, edited_df, set(), pro_id, DATE, TZ)

    doc = slots.find_one({"pro_id": pro_id})
    # 09:00 IDT (Israel Daylight Time, UTC+3 in September) -> 06:00 UTC.
    # mongomock (like real pymongo) stores BSON datetimes as naive UTC.
    expected_start = (
        TZ.localize(datetime.combine(DATE, time(9, 0)))
        .astimezone(pytz.utc)
        .replace(tzinfo=None)
    )
    assert doc["start_time"] == expected_start
    assert doc["start_time"].hour == 6


# --- half-filled rows ---


def test_half_filled_rows_skipped_and_counted_without_crash(db):
    slots = db.slots
    pro_id = ObjectId()
    edited_df = pd.DataFrame(
        [
            {
                "_id": None,
                "start_time": time(9, 0),
                "end_time": None,
                "is_taken": False,
            },
            {
                "_id": None,
                "start_time": None,
                "end_time": time(10, 0),
                "is_taken": False,
            },
        ],
        columns=EDITOR_COLUMNS,
    )

    result = save_daily_schedule(slots, edited_df, set(), pro_id, DATE, TZ)

    assert result == {"deleted": 0, "updated": 0, "inserted": 0, "skipped": 2}
    assert slots.count_documents({}) == 0


# --- unparseable time string ---


def test_unparseable_time_string_skipped_and_counted_without_crash(db):
    """An empty-day row whose browser string doesn't parse as a time
    (rather than merely being absent) must skip cleanly, not raise, and
    must be counted so the view can warn instead of claiming success."""
    slots = db.slots
    pro_id = ObjectId()
    edited_df = pd.DataFrame(
        [
            {
                "_id": None,
                "start_time": "not-a-time",
                "end_time": "10:00:00.000",
                "is_taken": False,
            }
        ],
        columns=EDITOR_COLUMNS,
    )

    result = save_daily_schedule(slots, edited_df, set(), pro_id, DATE, TZ)

    assert result == {"deleted": 0, "updated": 0, "inserted": 0, "skipped": 1}
    assert slots.count_documents({}) == 0


# --- the "" row-id shape ---


def test_empty_string_id_treated_as_new_row_not_dropped(db):
    """The old pd.isna-only check on _id silently discarded a row whose id
    came back as "" (an object-dtype quirk) — neither updated nor inserted.
    It must be treated as a brand-new row instead."""
    slots = db.slots
    pro_id = ObjectId()
    edited_df = pd.DataFrame(
        [
            {
                "_id": "",
                "start_time": time(9, 0),
                "end_time": time(10, 0),
                "is_taken": False,
            }
        ],
        columns=EDITOR_COLUMNS,
    )

    result = save_daily_schedule(slots, edited_df, set(), pro_id, DATE, TZ)

    assert result == {"deleted": 0, "updated": 0, "inserted": 1, "skipped": 0}
    assert slots.count_documents({"pro_id": pro_id}) == 1



# --- colon-less time strings (silently-wrong-midnight guard) ---


def test_colonless_time_strings_skipped_and_counted_without_wrong_midnight(db):
    """pd.Timestamp("0900") and pd.Timestamp("2026-01-01") both parse to a
    silently-wrong midnight instead of raising. TimeColumn can never emit
    a colon-less string, but _coerce_time guards it anyway -- must skip,
    not insert a bogus 00:00 slot."""
    slots = db.slots
    pro_id = ObjectId()
    edited_df = pd.DataFrame(
        [
            {
                "_id": None,
                "start_time": "0900",
                "end_time": "10:00:00.000",
                "is_taken": False,
            },
            {
                "_id": None,
                "start_time": "2026-01-01",
                "end_time": "10:00:00.000",
                "is_taken": False,
            },
        ],
        columns=EDITOR_COLUMNS,
    )

    result = save_daily_schedule(slots, edited_df, set(), pro_id, DATE, TZ)

    assert result == {"deleted": 0, "updated": 0, "inserted": 0, "skipped": 2}
    assert slots.count_documents({}) == 0


# --- stale id: matched_count, not call count ---


def test_stale_id_concurrently_deleted_reports_zero_updated_no_crash(db):
    """An id present in original_ids and in the frame, but whose document
    was concurrently deleted from the collection (another admin session),
    must not be reported as updated -- update_one matches nothing."""
    slots = db.slots
    pro_id = ObjectId()
    stale_id = ObjectId()  # never inserted into slots
    original_ids = {str(stale_id)}

    edited_df = pd.DataFrame(
        [
            {
                "_id": str(stale_id),
                "start_time": time(9, 0),
                "end_time": time(10, 0),
                "is_taken": False,
            }
        ],
        columns=EDITOR_COLUMNS,
    )

    result = save_daily_schedule(slots, edited_df, original_ids, pro_id, DATE, TZ)

    assert result == {"deleted": 0, "updated": 0, "inserted": 0, "skipped": 0}
    assert slots.count_documents({}) == 0
