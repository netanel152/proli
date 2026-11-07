"""
Backfill: store every pro's `phone_number` in the bare `9725…` form (PRO-224).

The bot finds a pro by `{"phone_number": {"$in": [phone, chat_id]}}`. The admin
panel used to save the phone exactly as typed (`050-123-4567`, `+972 50…`), so
such a pro was treated as a customer. The form now normalises on save; this
script finds and fixes the pros saved before that.

Usage:
    # Dry run (default) — report only, no writes
    python scripts/normalize_pro_phones.py

    # Write the normalised phone on every fixable pro
    python scripts/normalize_pro_phones.py --apply

Phones are printed masked (`...4567`). A phone that cannot be an Israeli mobile
is reported and never touched — correct it in the admin panel.

Exit codes:
    0  every pro's phone is already in the stored form
    1  some pros need fixing (dry run), or some phones cannot be normalised
    2  two users would end up with the same phone — nothing was written
    3  no pro on record, so nothing was checked
"""

import argparse
import asyncio
import os
import sys
from collections import defaultdict

from dotenv import load_dotenv

load_dotenv()
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import users_collection  # noqa: E402
from app.core.phone import mask_chat_id, to_il_mobile  # noqa: E402

EXIT_OK = 0
EXIT_NEEDS_FIX = 1
EXIT_DUPLICATE = 2
EXIT_NOTHING_CHECKED = 3


async def run(*, apply: bool) -> int:
    print("🔍 Checking pros' phone numbers")
    print("   (dry-run — no writes)" if not apply else "   (--apply — writing)")

    fixes = []  # (pro, normalised)
    invalid = 0
    checked = 0
    async for pro in users_collection.find(
        {"role": "professional"}, {"_id": 1, "business_name": 1, "phone_number": 1}
    ):
        checked += 1
        raw = pro.get("phone_number") or ""
        target = to_il_mobile(raw)
        if target == raw:
            continue
        name = pro.get("business_name") or "?"
        if not target:
            invalid += 1
            print(f"INVALID  {pro['_id']}  {name}  {mask_chat_id(raw)}")
        else:
            fixes.append((pro, target))
            print(f"FIX      {pro['_id']}  {name}  {mask_chat_id(raw)} → 972…")

    if not checked:
        print("⚠️  No professional on record — nothing was checked.")
        return EXIT_NOTHING_CHECKED

    # Every user's phone after the fix — `phone_number` is unique across users.
    owners = defaultdict(set)
    fixed_ids = {pro["_id"] for pro, _ in fixes}
    async for user in users_collection.find({}, {"_id": 1, "phone_number": 1}):
        if user["_id"] not in fixed_ids and user.get("phone_number"):
            owners[user["phone_number"]].add(user["_id"])
    for pro, target in fixes:
        owners[target].add(pro["_id"])
    clashes = {p: ids for p, ids in owners.items() if len(ids) > 1}

    print()
    print(f"Checked {checked} pros: {len(fixes)} to fix, {invalid} invalid")
    if clashes:
        print("❌ Refusing to write: these users would share a phone number —")
        for phone, ids in clashes.items():
            print(f"   {mask_chat_id(phone)}: {', '.join(str(i) for i in ids)}")
        print("   Merge or correct them in the admin panel, then re-run.")
        return EXIT_DUPLICATE

    if apply:
        for pro, target in fixes:
            await users_collection.update_one(
                {"_id": pro["_id"]}, {"$set": {"phone_number": target}}
            )
        print(f"   updated {len(fixes)}")

    if invalid or (fixes and not apply):
        return EXIT_NEEDS_FIX
    print("✅ Every pro's phone is in the stored form.")
    return EXIT_OK


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--apply", action="store_true", help="Write the normalised phone numbers"
    )
    args = parser.parse_args()

    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    sys.exit(asyncio.run(run(apply=args.apply)))


if __name__ == "__main__":
    main()
