"""PRO-277: one pro can never be booked twice for the same time.

``slots`` is unique on ``(pro_id, start_time)``; every generator inserts through
``app/core/slot_writes.py`` and treats a duplicate as "already there"; a database
built before the index is deduped at boot; and a claim refuses a slot whose range
overlaps one the pro already has taken.
"""

from datetime import datetime, timedelta, timezone

import mongomock
import pytest
from bson import ObjectId
from loguru import logger
from mongomock_motor import AsyncMongoMockClient
from pymongo import ASCENDING

import admin_panel.core.utils as panel_utils
import app.db.indexes as indexes
import app.services.matching_service as matching
from app.core.slot_writes import insert_slots_sync

SLOT_KEY = [("pro_id", ASCENDING), ("start_time", ASCENDING)]


@pytest.fixture
def records():
    seen = []
    sink = logger.add(
        lambda m: seen.append((m.record["level"].name, m.record["message"]))
    )
    yield seen
    logger.remove(sink)


def _slot(pro_id, start, hours=1, taken=False):
    return {
        "pro_id": pro_id,
        "start_time": start,
        "end_time": start + timedelta(hours=hours),
        "is_taken": taken,
    }


# --- generators ---


def test_generating_the_initial_schedule_twice_yields_one_set(monkeypatch):
    slots = mongomock.MongoClient().db.slots
    slots.create_index(SLOT_KEY, unique=True)
    monkeypatch.setattr(panel_utils, "slots_collection", slots)
    pro_id = ObjectId()

    panel_utils.create_initial_schedule(pro_id)
    first = slots.count_documents({})
    panel_utils.create_initial_schedule(pro_id)

    assert first > 0
    assert slots.count_documents({}) == first


def test_insert_skips_existing_slots_and_counts_only_new_ones():
    slots = mongomock.MongoClient().db.slots
    slots.create_index(SLOT_KEY, unique=True)
    pro_id = ObjectId()
    nine = datetime(2026, 9, 1, 6, 0)
    slots.insert_one(_slot(pro_id, nine))

    landed = insert_slots_sync(
        slots, [_slot(pro_id, nine), _slot(pro_id, nine + timedelta(hours=1))]
    )

    assert landed == 1
    assert slots.count_documents({}) == 2


# --- the boot migration ---


@pytest.mark.asyncio
async def test_boot_dedupes_free_copies_keeps_the_booked_one_and_goes_unique(
    records,
):
    db = AsyncMongoMockClient().slot_migration
    await db.slots.create_index(SLOT_KEY)  # the pre-PRO-277 index
    pro_id = ObjectId()
    nine = datetime(2026, 9, 1, 6, 0)
    free_a = (await db.slots.insert_one(_slot(pro_id, nine))).inserted_id
    booked = (await db.slots.insert_one(_slot(pro_id, nine, taken=True))).inserted_id
    free_b = (await db.slots.insert_one(_slot(pro_id, nine))).inserted_id
    await db.leads.insert_one({"status": "booked", "booked_slot_id": booked})
    lone = (
        await db.slots.insert_one(_slot(pro_id, nine + timedelta(hours=1)))
    ).inserted_id

    await indexes.prepare_unique_slot_index(db.slots)
    await db.slots.create_index(SLOT_KEY, unique=True)

    remaining = {d["_id"] for d in await db.slots.find({}).to_list(length=None)}
    assert remaining == {booked, lone}
    assert free_a not in remaining and free_b not in remaining
    info = await db.slots.index_information()
    assert info["pro_id_1_start_time_1"].get("unique") is True
    assert not [m for lvl, m in records if lvl == "ERROR"]


@pytest.mark.asyncio
async def test_boot_leaves_a_real_double_booking_to_a_human(records):
    db = AsyncMongoMockClient().slot_migration_conflict
    await db.slots.create_index(SLOT_KEY)
    pro_id = ObjectId()
    nine = datetime(2026, 9, 1, 6, 0)
    one = (await db.slots.insert_one(_slot(pro_id, nine, taken=True))).inserted_id
    two = (await db.slots.insert_one(_slot(pro_id, nine, taken=True))).inserted_id

    await indexes.prepare_unique_slot_index(db.slots)

    remaining = {d["_id"] for d in await db.slots.find({}).to_list(length=None)}
    assert remaining == {one, two}, "no taken slot is deleted automatically"
    info = await db.slots.index_information()
    assert not info["pro_id_1_start_time_1"].get("unique")
    assert [m for lvl, m in records if lvl == "ERROR" and "double booking" in m]


@pytest.mark.asyncio
async def test_create_all_indexes_runs_the_migration_from_the_table(monkeypatch):
    db = AsyncMongoMockClient().slot_migration_boot
    await db.slots.create_index(SLOT_KEY)
    pro_id = ObjectId()
    nine = datetime(2026, 9, 1, 6, 0)
    await db.slots.insert_many([_slot(pro_id, nine), _slot(pro_id, nine)])
    monkeypatch.setattr(
        indexes,
        "INDEX_SPECS",
        [("Slots", db.slots, indexes.SLOT_KEY, {"unique": True})],
    )

    assert await indexes.create_all_indexes(silent=True) == []

    assert await db.slots.count_documents({}) == 1
    info = await db.slots.index_information()
    assert info["pro_id_1_start_time_1"].get("unique") is True


def test_the_slot_index_is_unique_in_the_table():
    rows = [
        kwargs
        for _label, _coll, keys, kwargs in indexes.INDEX_SPECS
        if list(keys) == SLOT_KEY
    ]
    assert rows == [{"unique": True}]


# --- the claim ---


@pytest.fixture
def slots(monkeypatch, mock_db):
    monkeypatch.setattr(matching, "slots_collection", mock_db.slots)
    return mock_db.slots


def _tomorrow_at(hour):
    day = datetime.now(timezone.utc) + timedelta(days=1)
    return day.replace(hour=hour, minute=0, second=0, microsecond=0)


@pytest.mark.asyncio
async def test_two_approvals_for_the_same_time_cannot_both_book(slots):
    await slots.create_index(SLOT_KEY, unique=True)
    pro_id = ObjectId()
    ten = _tomorrow_at(10)
    await slots.insert_one(_slot(pro_id, ten))

    first = await matching.book_slot_for_lead(pro_id, ten, ten)
    second = await matching.book_slot_for_lead(pro_id, ten, ten)

    assert first is not None
    assert second is None


@pytest.mark.asyncio
async def test_a_slot_inside_a_taken_range_is_skipped(slots):
    """A taken 10:00-12:00 (the panel's 2-hour grid) and a free 11:00-12:00
    (a template's hour): the claim takes 12:00, not the overlapping 11:00."""
    pro_id = ObjectId()
    ten = _tomorrow_at(10)
    await slots.insert_one(_slot(pro_id, ten, hours=2, taken=True))
    inside = (
        await slots.insert_one(_slot(pro_id, ten + timedelta(hours=1)))
    ).inserted_id
    after = (
        await slots.insert_one(_slot(pro_id, ten + timedelta(hours=2)))
    ).inserted_id

    booked = await matching.book_slot_for_lead(pro_id, ten, ten + timedelta(hours=1))

    assert booked == after
    assert (await slots.find_one({"_id": inside}))["is_taken"] is False


@pytest.mark.asyncio
async def test_a_reschedule_does_not_clash_with_its_own_old_slot(slots):
    pro_id = ObjectId()
    ten = _tomorrow_at(10)
    old = (await slots.insert_one(_slot(pro_id, ten, hours=2, taken=True))).inserted_id
    new = await slots.find_one_and_update(
        {
            "_id": (
                await slots.insert_one(_slot(pro_id, ten + timedelta(hours=1)))
            ).inserted_id
        },
        {"$set": {"is_taken": True}},
    )

    assert await matching.overlapping_taken_slot(new) is not None
    assert await matching.overlapping_taken_slot(new, ignore_ids=[old]) is None
