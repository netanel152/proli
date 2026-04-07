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
    db,
)

async def create_all_indexes(silent: bool = False):
    """
    Creates indexes for all collections to optimize query performance.
    Safe to call on every startup -- MongoDB skips existing indexes.
    Set silent=True to suppress print output (e.g. when called from app startup).
    """
    def log(msg):
        if not silent:
            print(msg)

    log("Starting index creation...")

    # --- Users Collection ---
    try:
        log("Indexing Users Collection...")
        await users_collection.create_index([("phone_number", ASCENDING)], unique=True)
        await users_collection.create_index([("business_name", TEXT)])
        await users_collection.create_index([("service_areas", ASCENDING)])
        await users_collection.create_index([("location", "2dsphere")])
        log("  Users: done")
    except Exception as e:
        log(f"  Error indexing Users: {e}")

    # --- Leads Collection ---
    try:
        log("Indexing Leads Collection...")
        await leads_collection.create_index([("chat_id", ASCENDING)])
        await leads_collection.create_index([("status", ASCENDING)])
        await leads_collection.create_index([("created_at", ASCENDING)])
        await leads_collection.create_index([("pro_id", ASCENDING), ("status", ASCENDING)])
        await leads_collection.create_index([("status", ASCENDING), ("created_at", ASCENDING)])
        await leads_collection.create_index([("chat_id", ASCENDING), ("status", ASCENDING)])
        log("  Leads: done")
    except Exception as e:
        log(f"  Error indexing Leads: {e}")

    # --- Messages Collection ---
    try:
        log("Indexing Messages Collection...")
        await messages_collection.create_index([("chat_id", ASCENDING)])
        await messages_collection.create_index(
            [("timestamp", ASCENDING)],
            expireAfterSeconds=7776000,  # 90 days TTL
            background=True
        )
        log("  Messages: done")
    except Exception as e:
        log(f"  Error indexing Messages: {e}")

    # --- Slots Collection ---
    try:
        log("Indexing Slots Collection...")
        await slots_collection.create_index([("pro_id", ASCENDING)])
        await slots_collection.create_index([("pro_id", ASCENDING), ("start_time", ASCENDING)])
        log("  Slots: done")
    except Exception as e:
        log(f"  Error indexing Slots: {e}")

    # --- Audit Log Collection ---
    try:
        log("Indexing Audit Log Collection...")
        await audit_log_collection.create_index([("timestamp", DESCENDING)])
        await audit_log_collection.create_index([("admin_user", ASCENDING)])
        log("  Audit Log: done")
    except Exception as e:
        log(f"  Error indexing Audit Log: {e}")

    # --- Consent Collection ---
    try:
        log("Indexing Consent Collection...")
        await consent_collection.create_index([("chat_id", ASCENDING)], unique=True)
        log("  Consent: done")
    except Exception as e:
        log(f"  Error indexing Consent: {e}")

    # --- Admins Collection ---
    try:
        log("Indexing Admins Collection...")
        await admins_collection.create_index([("username", ASCENDING)], unique=True)
        log("  Admins: done")
    except Exception as e:
        log(f"  Error indexing Admins: {e}")

    # --- Admin Sessions Collection ---
    try:
        log("Indexing Admin Sessions Collection...")
        admin_sessions_col = db.admin_sessions
        await admin_sessions_col.create_index([("_token", ASCENDING)], unique=True)
        await admin_sessions_col.create_index(
            [("expiry", ASCENDING)],
            expireAfterSeconds=0
        )
        log("  Admin Sessions: done")
    except Exception as e:
        log(f"  Error indexing Admin Sessions: {e}")

    log("Index creation completed.")

if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(create_all_indexes())
