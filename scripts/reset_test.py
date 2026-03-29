"""
Reset test environment for WhatsApp bot testing.
Usage: python scripts/reset_test.py [--all] [--customer PHONE] [--pro PHONE]

Examples:
    python scripts/reset_test.py                    # Reset default test phones
    python scripts/reset_test.py --all              # Reset everything (full wipe)
    python scripts/reset_test.py --customer 972523651414  # Reset specific customer
"""
import asyncio
import argparse
import redis
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime, timezone

MONGO_URI = "mongodb://localhost:27017/proli_db"
REDIS_HOST = "localhost"
REDIS_PORT = 6379

# Default test phones
DEFAULT_PRO = "972524828796"
DEFAULT_CUSTOMER = "972523651414"


async def reset_customer(db, r, phone):
    chat_id = f"{phone}@c.us"
    leads = await db.leads.delete_many({"chat_id": chat_id})
    msgs = await db.messages.delete_many({"chat_id": chat_id})
    r.delete(f"state:{chat_id}")
    # Clear context (chat history in Redis)
    r.delete(f"context:{chat_id}")
    print(f"  Customer {phone}: {leads.deleted_count} leads, {msgs.deleted_count} messages deleted, state cleared")


async def reset_pro(db, r, phone):
    chat_id = f"{phone}@c.us"
    r.set(f"state:{chat_id}", "pro_mode")
    print(f"  Pro {phone}: state set to pro_mode")


async def ensure_consent(db, phone):
    chat_id = f"{phone}@c.us"
    await db.consent.update_one(
        {"chat_id": chat_id},
        {"$set": {"chat_id": chat_id, "accepted": True, "timestamp": datetime.now(timezone.utc)}},
        upsert=True,
    )
    print(f"  Consent ensured for {phone}")


async def full_reset(db, r):
    """Wipe all test data — leads, messages, consent, reviews, Redis states."""
    leads = await db.leads.delete_many({})
    msgs = await db.messages.delete_many({})
    reviews = await db.reviews.delete_many({})
    consent = await db.consent.delete_many({})
    print(f"  MongoDB: {leads.deleted_count} leads, {msgs.deleted_count} messages, {reviews.deleted_count} reviews, {consent.deleted_count} consent records deleted")

    # Clear all state/context keys in Redis
    for key in r.keys("state:*"):
        r.delete(key)
    for key in r.keys("context:*"):
        r.delete(key)
    for key in r.keys("webhook:*"):
        r.delete(key)
    print("  Redis: all state/context/webhook keys cleared")


async def main():
    parser = argparse.ArgumentParser(description="Reset Proli test environment")
    parser.add_argument("--all", action="store_true", help="Full wipe of all test data")
    parser.add_argument("--customer", type=str, default=DEFAULT_CUSTOMER, help="Customer phone to reset")
    parser.add_argument("--pro", type=str, default=DEFAULT_PRO, help="Pro phone to reset")
    args = parser.parse_args()

    client = AsyncIOMotorClient(MONGO_URI)
    db = client["proli_db"]
    r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True)

    print("🔄 Resetting test environment...\n")

    if args.all:
        await full_reset(db, r)
        print()
        # Re-create consent + pro state for defaults
        await ensure_consent(db, DEFAULT_PRO)
        await ensure_consent(db, DEFAULT_CUSTOMER)
        await reset_pro(db, r, DEFAULT_PRO)
    else:
        await reset_customer(db, r, args.customer)
        await ensure_consent(db, args.customer)
        await reset_pro(db, r, args.pro)

    print("\n✅ Done. Ready for testing.")
    client.close()
    r.close()


if __name__ == "__main__":
    asyncio.run(main())
