import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timedelta, timezone
from bson import ObjectId
from app.services import matching_service

# --- Task 1: Test Booking Logic ---


@pytest.mark.asyncio
async def test_book_slot_for_lead_success(mock_db, monkeypatch):
    """
    Test that a slot is correctly booked when it exists and is free.
    """
    # 1. Patch the slots_collection in matching_service (crucial as it might not be patched in conftest)
    monkeypatch.setattr(matching_service, "slots_collection", mock_db.slots)

    # 2. Setup Data
    pro_id = ObjectId()
    lead_created_at = datetime.now(timezone.utc)

    # Logic in matching_service:
    # estimated_time = lead_created_at + 1h (rounded to hour)
    # window = +/- 2h
    # So if now is 10:30, est is 11:30 -> 12:00? No, replace(minute=0) + 1h.
    # 10:30 -> 10:00 + 1h = 11:00.
    # Window: 09:00 to 13:00.

    # Let's create a slot at 11:00
    slot_time = lead_created_at.replace(minute=0, second=0, microsecond=0) + timedelta(
        hours=1
    )

    await mock_db.slots.insert_one(
        {"pro_id": pro_id, "is_taken": False, "start_time": slot_time}
    )

    # 3. Action
    result = await matching_service.book_slot_for_lead(str(pro_id), lead_created_at)

    # 4. Assertion — returns the booked slot's ObjectId, not a bool.
    updated_slot = await mock_db.slots.find_one({"pro_id": pro_id})
    assert result == updated_slot["_id"]
    assert isinstance(result, ObjectId)

    # Verify DB update
    assert updated_slot["is_taken"] is True


@pytest.mark.asyncio
async def test_book_slot_for_lead_no_slot(mock_db, monkeypatch):
    """
    Test that booking fails gracefully when no slot is available.
    """
    monkeypatch.setattr(matching_service, "slots_collection", mock_db.slots)

    pro_id = ObjectId()
    lead_created_at = datetime.now(timezone.utc)

    # Ensure DB is empty for this pro
    await mock_db.slots.delete_many({"pro_id": pro_id})

    result = await matching_service.book_slot_for_lead(str(pro_id), lead_created_at)
    assert result is None


@pytest.mark.asyncio
async def test_book_slot_for_lead_already_taken(mock_db, monkeypatch):
    """
    Test that booking fails if the only matching slot is already taken.
    """
    monkeypatch.setattr(matching_service, "slots_collection", mock_db.slots)

    pro_id = ObjectId()
    lead_created_at = datetime.now(timezone.utc)
    slot_time = lead_created_at.replace(minute=0, second=0, microsecond=0) + timedelta(
        hours=1
    )

    await mock_db.slots.insert_one(
        {"pro_id": pro_id, "is_taken": True, "start_time": slot_time}  # Already taken
    )

    result = await matching_service.book_slot_for_lead(str(pro_id), lead_created_at)
    assert result is None


# --- PRO-120: appointment-centered booking ---


@pytest.mark.asyncio
async def test_book_slot_for_lead_centers_on_appointment_datetime(mock_db, monkeypatch):
    """
    When appointment_datetime is provided, the search window must be
    centered on it — not on the lead's created_at (round-up-to-next-hour)
    fallback. A slot near created_at must be ignored in favor of the one
    near the requested appointment time.
    """
    monkeypatch.setattr(matching_service, "slots_collection", mock_db.slots)

    pro_id = ObjectId()
    # now()-anchored — book_slot_for_lead clamps its search window against
    # the wall clock, so hard-coded absolute dates expire.
    now = datetime.now(timezone.utc)
    # Lead created a day and a bit ago.
    lead_created_at = now - timedelta(hours=17)
    # Customer requested a day out.
    appointment_datetime = now + timedelta(days=1)

    # A free slot where the ASAP fallback would land (created_at is stale, so
    # the forward clamp puts the estimate an hour out from *now*) — inside
    # that window, well outside the +/-2h window centered on the request.
    decoy_slot_time = now + timedelta(hours=1)
    await mock_db.slots.insert_one(
        {"pro_id": pro_id, "is_taken": False, "start_time": decoy_slot_time}
    )
    # A free slot exactly at the requested appointment time.
    target_slot_time = appointment_datetime
    await mock_db.slots.insert_one(
        {"pro_id": pro_id, "is_taken": False, "start_time": target_slot_time}
    )

    result = await matching_service.book_slot_for_lead(
        str(pro_id), lead_created_at, appointment_datetime=appointment_datetime
    )

    target_slot = await mock_db.slots.find_one(
        {"pro_id": pro_id, "start_time": target_slot_time}
    )
    decoy_slot = await mock_db.slots.find_one(
        {"pro_id": pro_id, "start_time": decoy_slot_time}
    )

    assert result == target_slot["_id"]
    assert target_slot["is_taken"] is True
    # The decoy near created_at must remain untouched.
    assert decoy_slot["is_taken"] is False


@pytest.mark.asyncio
async def test_book_slot_for_lead_no_round_up_on_requested_time(mock_db, monkeypatch):
    """
    The window must be centered on appointment_datetime as-is (no round-up
    to the next hour). A slot exactly 2h before the requested 15:00 time
    (13:00) is reachable — and therefore bookable as the two-phase claim's
    earlier-slot fallback — only because the window is centered on 15:00.
    A rounded-up 16:00 center would start its window at 14:00 and exclude
    13:00 entirely, so booking it here pins the center, not a preference
    for booking the earlier slot over a later one (see the "prefers
    at/after" test for that ordering).
    """
    monkeypatch.setattr(matching_service, "slots_collection", mock_db.slots)

    pro_id = ObjectId()
    now = datetime.now(timezone.utc)
    appointment_datetime = now + timedelta(days=1)
    slot_time = appointment_datetime - timedelta(hours=2)

    await mock_db.slots.insert_one(
        {"pro_id": pro_id, "is_taken": False, "start_time": slot_time}
    )

    result = await matching_service.book_slot_for_lead(
        str(pro_id),
        lead_created_at=now,
        appointment_datetime=appointment_datetime,
    )

    booked = await mock_db.slots.find_one({"pro_id": pro_id, "start_time": slot_time})
    assert result == booked["_id"]
    assert booked["is_taken"] is True


@pytest.mark.asyncio
async def test_book_slot_for_lead_asap_fallback_none_appointment(mock_db, monkeypatch):
    """
    Explicit appointment_datetime=None reproduces the legacy ASAP behavior:
    created_at is rounded up to the next hour to center the window.

    Dates are now()-anchored (not hard-coded) because book_slot_for_lead
    clamps the ASAP estimate forward against the wall clock — a created_at
    stuck in a fixed past year would otherwise get overridden by the
    now-based floor and silently stop exercising the created_at-rounding
    path this test targets. `base` sits at :15 within the *current* hour
    (matching the legacy "14:15 -> round up to 15:00" example) and
    `lead_created_at = base + 1h` is guaranteed to land far enough in the
    future that the created_at-based term wins the `max(...)` clamp in
    production, so the round-up assertion below still holds.
    """
    monkeypatch.setattr(matching_service, "slots_collection", mock_db.slots)

    pro_id = ObjectId()
    now = datetime.now(timezone.utc)
    base = now.replace(minute=15, second=0, microsecond=0)
    lead_created_at = base + timedelta(hours=1)
    # e.g. HH:15 -> rounds up to (HH+1):00
    slot_time = lead_created_at.replace(minute=0, second=0, microsecond=0) + timedelta(
        hours=1
    )

    await mock_db.slots.insert_one(
        {"pro_id": pro_id, "is_taken": False, "start_time": slot_time}
    )

    result = await matching_service.book_slot_for_lead(
        str(pro_id), lead_created_at, appointment_datetime=None
    )

    booked = await mock_db.slots.find_one({"pro_id": pro_id, "start_time": slot_time})
    assert result == booked["_id"]
    assert booked["is_taken"] is True


@pytest.mark.asyncio
async def test_book_slot_for_lead_prefers_slot_at_requested_time(mock_db, monkeypatch):
    """
    Two-phase atomic claim: within the +/-2h window, a slot at/after the
    requested time is preferred over an earlier in-window slot — the
    earlier-slot fallback only runs when the at/after phase finds nothing.
    Both slots are free and both are inside the window; the at-the-requested-
    time slot must win, and the earlier one must stay untouched.
    """
    monkeypatch.setattr(matching_service, "slots_collection", mock_db.slots)

    pro_id = ObjectId()
    appointment_datetime = datetime.now(timezone.utc) + timedelta(days=1)
    at_requested_time = appointment_datetime
    earlier_in_window_time = appointment_datetime - timedelta(hours=2)

    await mock_db.slots.insert_one(
        {"pro_id": pro_id, "is_taken": False, "start_time": earlier_in_window_time}
    )
    await mock_db.slots.insert_one(
        {"pro_id": pro_id, "is_taken": False, "start_time": at_requested_time}
    )

    result = await matching_service.book_slot_for_lead(
        str(pro_id),
        lead_created_at=datetime.now(timezone.utc),
        appointment_datetime=appointment_datetime,
    )

    at_requested_slot = await mock_db.slots.find_one(
        {"pro_id": pro_id, "start_time": at_requested_time}
    )
    earlier_slot = await mock_db.slots.find_one(
        {"pro_id": pro_id, "start_time": earlier_in_window_time}
    )

    assert result == at_requested_slot["_id"]
    assert at_requested_slot["is_taken"] is True
    assert earlier_slot["is_taken"] is False


@pytest.mark.asyncio
async def test_book_slot_for_lead_asap_takes_soonest_free_slot(mock_db, monkeypatch):
    """
    ASAP ordering differs from the appointment_datetime branch: an ASAP
    lead means "soonest", so the earliest free future in-window slot wins —
    NOT the slot at/after the rounded-up estimated hour. Two free slots
    exist: one shortly after creation (before the estimated hour) and one
    exactly at the estimated hour. Called WITHOUT appointment_datetime, the
    sooner slot must be booked.
    """
    monkeypatch.setattr(matching_service, "slots_collection", mock_db.slots)

    pro_id = ObjectId()
    # Minute-of-hour is pinned deliberately: `sooner` must land strictly
    # before the rounded-up estimated hour, which only holds below :45.
    # An hour out keeps created_at ahead of the forward clamp in production.
    created_at = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(
        minute=15, second=0, microsecond=0
    )
    estimated_hour = created_at.replace(minute=0, second=0, microsecond=0) + timedelta(
        hours=1
    )
    sooner_slot_time = created_at + timedelta(minutes=15)
    at_estimated_hour_slot_time = estimated_hour

    await mock_db.slots.insert_one(
        {"pro_id": pro_id, "is_taken": False, "start_time": sooner_slot_time}
    )
    await mock_db.slots.insert_one(
        {"pro_id": pro_id, "is_taken": False, "start_time": at_estimated_hour_slot_time}
    )

    result = await matching_service.book_slot_for_lead(str(pro_id), created_at)

    sooner_slot = await mock_db.slots.find_one(
        {"pro_id": pro_id, "start_time": sooner_slot_time}
    )
    later_slot = await mock_db.slots.find_one(
        {"pro_id": pro_id, "start_time": at_estimated_hour_slot_time}
    )

    assert result == sooner_slot["_id"]
    assert sooner_slot["is_taken"] is True
    assert later_slot["is_taken"] is False


@pytest.mark.asyncio
async def test_book_slot_for_lead_past_window_returns_none(mock_db, monkeypatch):
    """
    Past-time guard: when the requested time's whole +/-2h window has
    already elapsed (target + 2h <= now), nothing is booked — even if a
    free slot exists at that stale time — and the function returns None.
    """
    monkeypatch.setattr(matching_service, "slots_collection", mock_db.slots)

    pro_id = ObjectId()
    # target + 2h <= now, well clear of any clock-skew edge case.
    appointment_datetime = datetime.now(timezone.utc) - timedelta(hours=3)

    await mock_db.slots.insert_one(
        {"pro_id": pro_id, "is_taken": False, "start_time": appointment_datetime}
    )

    result = await matching_service.book_slot_for_lead(
        str(pro_id),
        lead_created_at=datetime.now(timezone.utc),
        appointment_datetime=appointment_datetime,
    )

    assert result is None
    untouched = await mock_db.slots.find_one(
        {"pro_id": pro_id, "start_time": appointment_datetime}
    )
    assert untouched["is_taken"] is False


@pytest.mark.asyncio
async def test_book_slot_for_lead_non_datetime_appointment_falls_back_to_asap(
    mock_db, monkeypatch
):
    """
    Type guard: a non-datetime appointment_datetime (e.g. a string on a
    legacy/hand-edited lead doc) must not raise — it's logged and treated
    as None, falling back to the ASAP created_at-based estimate.
    """
    monkeypatch.setattr(matching_service, "slots_collection", mock_db.slots)

    pro_id = ObjectId()
    lead_created_at = datetime.now(timezone.utc) + timedelta(hours=1, minutes=15)
    # 14:15-past-the-hour analog: rounds up to the next hour.
    estimated_time = lead_created_at.replace(
        minute=0, second=0, microsecond=0
    ) + timedelta(hours=1)

    await mock_db.slots.insert_one(
        {"pro_id": pro_id, "is_taken": False, "start_time": estimated_time}
    )

    result = await matching_service.book_slot_for_lead(
        str(pro_id),
        lead_created_at,
        appointment_datetime="not-a-datetime",
    )

    booked = await mock_db.slots.find_one(
        {"pro_id": pro_id, "start_time": estimated_time}
    )
    assert result == booked["_id"]
    assert booked["is_taken"] is True


@pytest.mark.asyncio
async def test_book_slot_for_lead_naive_appointment_datetime_no_crash(
    mock_db, monkeypatch
):
    """
    Mongo returns naive datetimes for appointment_datetime — the function
    must normalize to UTC-aware internally instead of crashing on the
    naive/aware arithmetic, and still book the correct slot.
    """
    monkeypatch.setattr(matching_service, "slots_collection", mock_db.slots)

    pro_id = ObjectId()
    lead_created_at = datetime.now(timezone.utc)
    # now()-anchored, future-proof — book_slot_for_lead clamps its window
    # against the wall clock, so a fixed-year date eventually expires.
    future = datetime.now(timezone.utc) + timedelta(days=1)
    # Naive datetime, as Mongo would hand back.
    appointment_datetime = future.replace(tzinfo=None)

    # The stored slot's start_time is deliberately tz-aware — mongomock
    # stores exactly what it's given, and real Mongo hands back naive
    # datetimes on read regardless of what was written. The normalization
    # under test here is the *parameter's* (appointment_datetime), not the
    # slot document's, so this stays aware to isolate that.
    slot_time = future
    await mock_db.slots.insert_one(
        {"pro_id": pro_id, "is_taken": False, "start_time": slot_time}
    )

    result = await matching_service.book_slot_for_lead(
        str(pro_id), lead_created_at, appointment_datetime=appointment_datetime
    )

    booked = await mock_db.slots.find_one({"pro_id": pro_id, "start_time": slot_time})
    assert result == booked["_id"]
    assert booked["is_taken"] is True
