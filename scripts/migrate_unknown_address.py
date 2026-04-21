"""
Migration: unset `full_address: "Unknown Address"` from leads.

Companion to the 2026-04-18 fix that makes `full_address` nullable and stops
persisting the magic "Unknown Address" sentinel. Historical docs still carry
the sentinel — this script unsets the field so the new read paths
(`.get("full_address") or "לא ידוע"`) render correctly and the Healer's Patch
#5 skip triggers.

Usage:
    # Dry run — count only, no writes
    python scripts/migrate_unknown_address.py --dry-run

    # Apply
    python scripts/migrate_unknown_address.py

Idempotent: safe to run multiple times. A second run will find 0 matches.
"""
import argparse
import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import leads_collection  # noqa: E402
from app.core.constants import Defaults  # noqa: E402


MATCH = {"full_address": Defaults.UNKNOWN_ADDRESS}


async def run(dry_run: bool) -> int:
    count = await leads_collection.count_documents(MATCH)
    print(f"🔍 Found {count} leads with full_address == {Defaults.UNKNOWN_ADDRESS!r}")

    if count == 0:
        print("✅ Nothing to migrate.")
        return 0

    if dry_run:
        print("   (dry-run — no writes)")
        cursor = leads_collection.find(MATCH, {"_id": 1, "chat_id": 1, "status": 1}).limit(10)
        async for doc in cursor:
            print(f"   · {doc['_id']}  chat={doc.get('chat_id')}  status={doc.get('status')}")
        if count > 10:
            print(f"   · ... and {count - 10} more")
        return count

    result = await leads_collection.update_many(MATCH, {"$unset": {"full_address": ""}})
    print(f"✅ Unset full_address on {result.modified_count} leads.")
    # Verify
    remaining = await leads_collection.count_documents(MATCH)
    if remaining != 0:
        print(f"⚠️  {remaining} leads still match after update — investigate.")
        return remaining
    print("✅ Verified: 0 leads remain with the legacy sentinel.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Count only, no writes")
    args = parser.parse_args()

    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
