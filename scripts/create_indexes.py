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
)

async def create_all_indexes():
    """
    Creates indexes for all collections to optimize query performance.
    """
    print("🚀 Starting index creation...")

    # --- Users Collection ---
    try:
        print("🔹 Indexing Users Collection...")
        # Unique phone number
        await users_collection.create_index([("phone_number", ASCENDING)], unique=True)
        print("  ✅ Created unique index: phone_number")
        
        # Text index for business name search
        await users_collection.create_index([("business_name", TEXT)])
        print("  ✅ Created text index: business_name")
        
        # Index for service areas lookup
        await users_collection.create_index([("service_areas", ASCENDING)])
        print("  ✅ Created index: service_areas")

        # Geo-spatial index for location-based routing
        await users_collection.create_index([("location", "2dsphere")])
        print("  ✅ Created 2dsphere index: location")

    except Exception as e:
        print(f"  ❌ Error indexing Users: {e}")

    # --- Leads Collection ---
    try:
        print("🔹 Indexing Leads Collection...")
        # Fast lookup by user
        await leads_collection.create_index([("chat_id", ASCENDING)])
        print("  ✅ Created index: chat_id")
        
        # Filter by status
        await leads_collection.create_index([("status", ASCENDING)])
        print("  ✅ Created index: status")
        
        # Sort by date
        await leads_collection.create_index([("created_at", ASCENDING)])
        print("  ✅ Created index: created_at")
        
        # Compound: Pro checking active jobs
        await leads_collection.create_index([("pro_id", ASCENDING), ("status", ASCENDING)])
        print("  ✅ Created compound index: pro_id + status")

        # Compound: Fast lookup for SOS monitor (Status + Date)
        await leads_collection.create_index([("status", ASCENDING), ("created_at", ASCENDING)])
        print("  ✅ Created compound index: status + created_at")

    except Exception as e:
        print(f"  ❌ Error indexing Leads: {e}")

    # --- Messages Collection ---
    try:
        print("🔹 Indexing Messages Collection...")
        # Lookup messages by chat
        await messages_collection.create_index([("chat_id", ASCENDING)])
        print("  ✅ Created index: chat_id")
        
        # Sort history by time
        await messages_collection.create_index([("timestamp", ASCENDING)])
        print("  ✅ Created index: timestamp")

    except Exception as e:
        print(f"  ❌ Error indexing Messages: {e}")

    # --- Slots Collection ---
    try:
        print("🔹 Indexing Slots Collection...")
        # Lookup slots by pro
        await slots_collection.create_index([("pro_id", ASCENDING)])
        print("  ✅ Created index: pro_id")
        
        # Compound: Fetch schedule sorted
        await slots_collection.create_index([("pro_id", ASCENDING), ("start_time", ASCENDING)])
        print("  ✅ Created compound index: pro_id + start_time")

    except Exception as e:
        print(f"  ❌ Error indexing Slots: {e}")

    # --- Audit Log Collection ---
    try:
        print("🔹 Indexing Audit Log Collection...")
        await audit_log_collection.create_index([("timestamp", DESCENDING)])
        print("  ✅ Created index: timestamp (descending)")
        await audit_log_collection.create_index([("admin_user", ASCENDING)])
        print("  ✅ Created index: admin_user")
    except Exception as e:
        print(f"  ❌ Error indexing Audit Log: {e}")

    # --- Consent Collection ---
    try:
        print("🔹 Indexing Consent Collection...")
        await consent_collection.create_index([("chat_id", ASCENDING)], unique=True)
        print("  ✅ Created unique index: chat_id")
    except Exception as e:
        print(f"  ❌ Error indexing Consent: {e}")

    # --- Admins Collection ---
    try:
        print("🔹 Indexing Admins Collection...")
        await admins_collection.create_index([("username", ASCENDING)], unique=True)
        print("  ✅ Created unique index: username")
    except Exception as e:
        print(f"  ❌ Error indexing Admins: {e}")

    print("✨ Index creation process completed.")

if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(create_all_indexes())
