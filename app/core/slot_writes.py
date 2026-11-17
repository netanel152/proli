"""Slot inserts that tolerate a slot already being there (PRO-277).

``slots`` carries a unique index on ``(pro_id, start_time)`` (``app/db/indexes.py``),
so one pro can never hold two documents for the same start time — the shape that
let two approvals each claim "their" copy and double-book the pro. Every slot
generator (the panel's bulk tab, the day editor, ``create_initial_schedule``, the
template regenerator) inserts through here: ``ordered=False`` so the rest of a batch
still lands, and a duplicate-key error counts as "already there" rather than as a
failure. Any other write error still raises.

Sync for the admin panel (PyMongo), async for the worker (Motor).
"""

from pymongo.errors import BulkWriteError

DUPLICATE_KEY = 11000


def inserted_despite_duplicates(err: BulkWriteError) -> int:
    """How many documents an unordered insert wrote, if duplicates were its only
    failures. Re-raises when anything other than a duplicate key went wrong."""
    errors = (err.details or {}).get("writeErrors") or []
    if any(e.get("code") != DUPLICATE_KEY for e in errors):
        raise err
    return int((err.details or {}).get("nInserted", 0))


def insert_slots_sync(collection, slots: list) -> int:
    """Insert ``slots``, skipping any that already exist. Returns how many landed."""
    if not slots:
        return 0
    try:
        return len(collection.insert_many(slots, ordered=False).inserted_ids)
    except BulkWriteError as err:
        return inserted_despite_duplicates(err)


async def insert_slots(collection, slots: list) -> int:
    """Async twin of :func:`insert_slots_sync`."""
    if not slots:
        return 0
    try:
        result = await collection.insert_many(slots, ordered=False)
        return len(result.inserted_ids)
    except BulkWriteError as err:
        return inserted_despite_duplicates(err)
