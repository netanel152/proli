"""
Scheduling Service - Recurring availability templates and slot management.

Supports weekly recurring schedules, availability checks for matching,
and no-show tracking.
"""

from datetime import datetime, timedelta, timezone, time
from bson.objectid import ObjectId
from app.core.database import users_collection, slots_collection, leads_collection
from app.core.constants import LeadStatus
from app.core.logger import logger
import pytz

IL_TZ = pytz.timezone("Asia/Jerusalem")


# --- Recurring Schedule Templates ---

async def get_schedule_template(pro_id: str) -> dict | None:
    """
    Get a pro's recurring weekly schedule template.
    Stored on the user document as 'schedule_template'.

    Template format:
    {
        "monday":    {"start": "08:00", "end": "18:00", "enabled": True},
        "tuesday":   {"start": "08:00", "end": "18:00", "enabled": True},
        ...
        "slot_duration_minutes": 60,
    }
    """
    pro = await users_collection.find_one({"_id": ObjectId(pro_id)})
    if not pro:
        return None
    return pro.get("schedule_template")


async def save_schedule_template(pro_id: str, template: dict) -> bool:
    """Save a recurring weekly schedule template for a pro."""
    result = await users_collection.update_one(
        {"_id": ObjectId(pro_id)},
        {"$set": {"schedule_template": template}},
    )
    return result.modified_count > 0


async def generate_slots_from_template(pro_id: str, days_ahead: int = 14) -> int:
    """
    Generate concrete slot documents from a pro's recurring template.
    Skips days that already have slots. Returns count of new slots created.
    """
    template = await get_schedule_template(pro_id)
    if not template:
        return 0

    oid = ObjectId(pro_id)
    slot_duration = template.get("slot_duration_minutes", 60)
    day_names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

    now = datetime.now(IL_TZ)
    new_slots = []

    for offset in range(days_ahead):
        target_date = (now + timedelta(days=offset)).date()
        day_name = day_names[target_date.weekday()]
        day_config = template.get(day_name)

        if not day_config or not day_config.get("enabled", False):
            continue

        # Check if slots already exist for this day
        day_start_utc = IL_TZ.localize(datetime.combine(target_date, time.min)).astimezone(pytz.utc)
        day_end_utc = IL_TZ.localize(datetime.combine(target_date, time.max)).astimezone(pytz.utc)

        existing = await slots_collection.count_documents({
            "pro_id": oid,
            "start_time": {"$gte": day_start_utc, "$lte": day_end_utc},
        })
        if existing > 0:
            continue

        # Parse hours
        try:
            start_h, start_m = map(int, day_config["start"].split(":"))
            end_h, end_m = map(int, day_config["end"].split(":"))
        except (ValueError, KeyError):
            continue

        slot_start = IL_TZ.localize(datetime.combine(target_date, time(start_h, start_m)))
        day_end = IL_TZ.localize(datetime.combine(target_date, time(end_h, end_m)))

        while slot_start + timedelta(minutes=slot_duration) <= day_end:
            slot_end = slot_start + timedelta(minutes=slot_duration)
            new_slots.append({
                "pro_id": oid,
                "start_time": slot_start.astimezone(pytz.utc),
                "end_time": slot_end.astimezone(pytz.utc),
                "is_taken": False,
                "created_at": datetime.now(timezone.utc),
            })
            slot_start = slot_end

    if new_slots:
        await slots_collection.insert_many(new_slots)
        logger.info(f"Generated {len(new_slots)} slots for pro {pro_id} ({days_ahead} days)")

    return len(new_slots)


async def regenerate_all_templates() -> int:
    """Regenerate slots for all active pros with templates. Called by scheduler."""
    total = 0
    cursor = users_collection.find({
        "is_active": True,
        "role": "professional",
        "schedule_template": {"$exists": True},
    })
    async for pro in cursor:
        count = await generate_slots_from_template(str(pro["_id"]))
        total += count
    if total > 0:
        logger.info(f"Template regeneration: created {total} total slots")
    return total


# --- Availability Checks ---

async def check_pro_availability(pro_id, requested_time: datetime | None = None) -> bool:
    """
    Check if a pro has available slots. Used by matching_service.
    If requested_time provided, checks within +-2 hour window.
    Otherwise checks for any future available slot.
    """
    oid = ObjectId(pro_id) if isinstance(pro_id, str) else pro_id
    now_utc = datetime.now(timezone.utc)

    if requested_time:
        if requested_time.tzinfo is None:
            requested_time = requested_time.replace(tzinfo=timezone.utc)
        window_start = requested_time - timedelta(hours=2)
        window_end = requested_time + timedelta(hours=2)
    else:
        window_start = now_utc
        window_end = now_utc + timedelta(days=7)

    slot = await slots_collection.find_one({
        "pro_id": oid,
        "is_taken": False,
        "start_time": {"$gte": window_start, "$lte": window_end},
    })

    return slot is not None


async def get_available_slots(pro_id: str, date: datetime | None = None, limit: int = 20) -> list:
    """Get available (not taken) slots for a pro, optionally filtered by date."""
    oid = ObjectId(pro_id)
    query = {"pro_id": oid, "is_taken": False}

    if date:
        tz = IL_TZ
        day_start = tz.localize(datetime.combine(date, time.min)).astimezone(pytz.utc)
        day_end = tz.localize(datetime.combine(date, time.max)).astimezone(pytz.utc)
        query["start_time"] = {"$gte": day_start, "$lte": day_end}
    else:
        query["start_time"] = {"$gte": datetime.now(timezone.utc)}

    cursor = slots_collection.find(query).sort("start_time", 1).limit(limit)
    return await cursor.to_list(length=limit)


# --- No-Show Tracking ---

async def record_no_show(pro_id) -> int:
    """
    Increment no-show count for a pro. Returns new count.
    Called when a Tier 3 stale lead is detected.
    """
    oid = ObjectId(pro_id) if isinstance(pro_id, str) else pro_id
    result = await users_collection.find_one_and_update(
        {"_id": oid},
        {"$inc": {"no_show_count": 1}},
        return_document=True,
    )
    count = result.get("no_show_count", 0) if result else 0
    logger.info(f"No-show recorded for pro {pro_id}, total: {count}")
    return count


async def get_no_show_count(pro_id) -> int:
    """Get the no-show count for a pro."""
    oid = ObjectId(pro_id) if isinstance(pro_id, str) else pro_id
    pro = await users_collection.find_one({"_id": oid})
    return pro.get("no_show_count", 0) if pro else 0
