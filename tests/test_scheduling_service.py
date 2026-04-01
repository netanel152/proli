"""
Tests for scheduling_service.py: schedule templates, availability, no-shows.
"""
import pytest
import pytest_asyncio
from bson import ObjectId
from datetime import datetime, timezone
from app.services.scheduling_service import (
    get_schedule_template,
    save_schedule_template,
    check_pro_availability,
    get_available_slots,
    record_no_show,
    get_no_show_count,
)

SCHED_PRO_ID = ObjectId()


@pytest_asyncio.fixture
async def sched_db(mock_db):
    """Create a pro for scheduling tests."""
    existing = await mock_db.users.find_one({"_id": SCHED_PRO_ID})
    if not existing:
        await mock_db.users.insert_one({
            "_id": SCHED_PRO_ID,
            "role": "professional",
            "is_active": True,
            "business_name": "Sched Pro",
            "no_show_count": 0,
        })
    return mock_db, str(SCHED_PRO_ID), SCHED_PRO_ID


# --- Schedule Templates ---

@pytest.mark.asyncio
async def test_get_schedule_template_none(sched_db):
    db, pro_id_str, _ = sched_db
    result = await get_schedule_template(pro_id_str)
    assert result is None


@pytest.mark.asyncio
async def test_save_and_get_template(sched_db):
    db, pro_id_str, pro_oid = sched_db

    template = {
        "monday": {"start": "08:00", "end": "18:00", "enabled": True},
        "tuesday": {"start": "09:00", "end": "17:00", "enabled": True},
        "slot_duration_minutes": 60,
    }
    saved = await save_schedule_template(pro_id_str, template)
    assert saved is True

    result = await get_schedule_template(pro_id_str)
    assert result is not None
    assert result["monday"]["start"] == "08:00"
    assert result["slot_duration_minutes"] == 60


@pytest.mark.asyncio
async def test_get_template_nonexistent_pro(mock_db):
    result = await get_schedule_template(str(ObjectId()))
    assert result is None


# --- Availability Checks ---

@pytest.mark.asyncio
async def test_check_availability_with_slot(sched_db):
    db, _, pro_oid = sched_db
    from datetime import timedelta
    future = datetime.now(timezone.utc) + timedelta(days=1)

    await db.slots.insert_one({
        "pro_id": pro_oid,
        "start_time": future,
        "end_time": future + timedelta(hours=1),
        "is_taken": False,
    })

    result = await check_pro_availability(pro_oid)
    assert result is True


@pytest.mark.asyncio
async def test_check_availability_no_slot(mock_db):
    """Fresh pro with no slots at all."""
    empty_pro = ObjectId()
    await mock_db.users.insert_one({"_id": empty_pro, "role": "professional", "is_active": True})
    result = await check_pro_availability(empty_pro)
    assert result is False


@pytest.mark.asyncio
async def test_check_availability_all_taken(mock_db):
    """Pro with only taken slots."""
    from datetime import timedelta
    taken_pro = ObjectId()
    await mock_db.users.insert_one({"_id": taken_pro, "role": "professional", "is_active": True})
    future = datetime.now(timezone.utc) + timedelta(days=1)

    await mock_db.slots.insert_one({
        "pro_id": taken_pro, "start_time": future,
        "end_time": future + timedelta(hours=1), "is_taken": True,
    })

    result = await check_pro_availability(taken_pro)
    assert result is False


# --- Available Slots ---

@pytest.mark.asyncio
async def test_get_available_slots(sched_db):
    db, pro_id_str, pro_oid = sched_db
    from datetime import timedelta
    # Use a different pro to avoid data from other tests
    diff_pro = ObjectId()
    await db.users.insert_one({"_id": diff_pro, "role": "professional", "is_active": True})
    future = datetime.now(timezone.utc) + timedelta(days=2)

    await db.slots.insert_one({
        "pro_id": diff_pro, "start_time": future,
        "end_time": future + timedelta(hours=1), "is_taken": False,
    })
    await db.slots.insert_one({
        "pro_id": diff_pro, "start_time": future,
        "end_time": future + timedelta(hours=1), "is_taken": True,
    })

    slots = await get_available_slots(str(diff_pro))
    assert len(slots) == 1  # Only the not-taken one


# --- No-Show Tracking ---

@pytest.mark.asyncio
async def test_record_no_show(sched_db):
    _, pro_id_str, _ = sched_db

    count = await record_no_show(pro_id_str)
    assert count == 1

    count = await record_no_show(pro_id_str)
    assert count == 2


@pytest.mark.asyncio
async def test_get_no_show_count(mock_db):
    """Use a fresh pro to avoid shared state."""
    fresh_pro = ObjectId()
    await mock_db.users.insert_one({
        "_id": fresh_pro, "role": "professional",
        "is_active": True, "no_show_count": 0,
    })

    count = await get_no_show_count(fresh_pro)
    assert count == 0

    await record_no_show(fresh_pro)
    count = await get_no_show_count(fresh_pro)
    assert count == 1


@pytest.mark.asyncio
async def test_no_show_nonexistent_pro(mock_db):
    count = await get_no_show_count(ObjectId())
    assert count == 0
