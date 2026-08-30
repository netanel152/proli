"""MongoDB index definitions — the single source of truth (PRO-12).

Nobody has to remember to run this by hand: ``app/main.py``'s lifespan calls
``create_all_indexes(silent=True)`` on every API boot, and ``create_index`` is
idempotent (MongoDB skips indexes that already exist). A manual run
(``python scripts/create_indexes.py``) is only useful to prime a brand-new
database before its first API boot, or to eyeball the full list.

To add an index, add one row to ``INDEX_SPECS`` below — both the boot hook and
the CLI execute exactly that table, so they can never drift apart.
"""

import asyncio
import sys
import os
from pymongo import ASCENDING, DESCENDING, TEXT

# Add the project root to the python path to allow imports from 'app'
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import (
    users_collection,
    leads_collection,
    messages_collection,
    slots_collection,
    audit_log_collection,
    consent_collection,
    admins_collection,
    wa_delivery_collection,
    db,
)

# Every index in the system: (collection label, collection, keys, kwargs).
# Rationale that isn't obvious from the keys lives in a comment on the row.
INDEX_SPECS = [
    # --- Users ---
    ("Users", users_collection, [("phone_number", ASCENDING)], {"unique": True}),
    ("Users", users_collection, [("business_name", TEXT)], {}),
    ("Users", users_collection, [("service_areas", ASCENDING)], {}),
    # $geoNear in matching_service requires the 2dsphere type — a plain
    # ascending index on `location` breaks matching entirely.
    ("Users", users_collection, [("location", "2dsphere")], {}),
    # --- Leads ---
    ("Leads", leads_collection, [("chat_id", ASCENDING)], {}),
    ("Leads", leads_collection, [("status", ASCENDING)], {}),
    ("Leads", leads_collection, [("created_at", ASCENDING)], {}),
    ("Leads", leads_collection, [("pro_id", ASCENDING), ("status", ASCENDING)], {}),
    ("Leads", leads_collection, [("status", ASCENDING), ("created_at", ASCENDING)], {}),
    ("Leads", leads_collection, [("chat_id", ASCENDING), ("status", ASCENDING)], {}),
    # PRO-162: the SOS Reporter's dedup clause. Its query is
    # status + created_at + an $or over admin_reported_at, and every clause of
    # an $or must be indexed or the whole query degrades to a collection scan.
    # Deliberately NOT sparse: almost no lead carries the field, which makes
    # `{"sparse": True}` look like free savings — but a sparse index cannot
    # serve the `$exists: False` clause that is the whole point of the filter.
    ("Leads", leads_collection, [("admin_reported_at", ASCENDING)], {}),
    # PRO-117: second clause of the fat-finger guard's $or in
    # _recently_responded_lead — every clause of an $or must be indexed
    # or the whole query degrades to a collection scan.
    (
        "Leads",
        leads_collection,
        [("rejected_by", ASCENDING), ("last_rejected_at", ASCENDING)],
        {},
    ),
    # --- Messages ---
    ("Messages", messages_collection, [("chat_id", ASCENDING)], {}),
    (
        "Messages",
        messages_collection,
        [("timestamp", ASCENDING)],
        {"expireAfterSeconds": 7776000, "background": True},  # 90 days TTL
    ),
    # --- Slots ---
    ("Slots", slots_collection, [("pro_id", ASCENDING)], {}),
    ("Slots", slots_collection, [("pro_id", ASCENDING), ("start_time", ASCENDING)], {}),
    # --- Audit Log ---
    # PRO-142: compound, and the `_id` half is not optional. The viewer
    # pages with skip/limit and sorts `[("timestamp", -1), ("_id", -1)]` —
    # `_id` breaks ties so an entry cannot land on two pages or none. An
    # index only serves a sort whose key is a *prefix* of it, so a
    # timestamp-only index would leave every page doing a blocking
    # in-memory sort, and that path errors at the 32MB find-sort limit
    # rather than spilling to disk. This supersedes the timestamp-only
    # index it replaces, which was a prefix of this one.
    (
        "Audit Log",
        audit_log_collection,
        [("timestamp", DESCENDING), ("_id", DESCENDING)],
        {},
    ),
    ("Audit Log", audit_log_collection, [("admin_user", ASCENDING)], {}),
    # --- Consent ---
    ("Consent", consent_collection, [("chat_id", ASCENDING)], {"unique": True}),
    # --- Admins ---
    ("Admins", admins_collection, [("username", ASCENDING)], {"unique": True}),
    # --- WhatsApp Delivery Statuses (PRO-89) ---
    # Unique: every status callback does an upsert keyed on wamid, and 3-4
    # callbacks per message (sent/delivered/read) would otherwise race into
    # duplicate docs on an unindexed scan.
    (
        "WA Delivery",
        wa_delivery_collection,
        [("wa_message_id", ASCENDING)],
        {"unique": True},
    ),
    # Delivery facts have no business value after the lead has long since
    # closed; 30 days matches the debugging horizon, not the data model.
    (
        "WA Delivery",
        wa_delivery_collection,
        [("created_at", ASCENDING)],
        {"expireAfterSeconds": 30 * 24 * 3600, "background": True},
    ),
    # --- Admin Sessions ---
    ("Admin Sessions", db.admin_sessions, [("_token", ASCENDING)], {"unique": True}),
    (
        "Admin Sessions",
        db.admin_sessions,
        [("expiry", ASCENDING)],
        {"expireAfterSeconds": 0},
    ),
]


async def create_all_indexes(silent: bool = False):
    """Create every index in INDEX_SPECS.

    Safe to call on every startup -- MongoDB skips existing indexes.
    Set silent=True to suppress print output (e.g. when called from app
    startup). One failing index is logged and skipped so the rest still
    get created.
    """

    def log(msg):
        if not silent:
            print(msg)

    log("Starting index creation...")

    current_label = None
    for label, collection, keys, kwargs in INDEX_SPECS:
        if label != current_label:
            log(f"Indexing {label} Collection...")
            current_label = label
        try:
            await collection.create_index(keys, **kwargs)
        except Exception as e:
            log(f"  Error indexing {label} {keys}: {e}")

    log("Index creation completed.")


if __name__ == "__main__":
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(create_all_indexes())
