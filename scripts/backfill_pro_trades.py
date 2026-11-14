"""
Backfill: give every handyman / general pro an explicit list of trades (PRO-234).

Routing used to let a handyman or general pro fit every trade except electrical
(which needed the `handles_electrical` flag). It now offers a pro only the trades
in its `type` + `categories`. This script writes, for every handyman / general
pro the list has not been set on yet, the list that reproduces the old rule:
every trade except electrical, plus electrical where `handles_electrical` was
set. The operator trims it afterwards in the admin panel.

It also:
  * removes `handles_electrical` from every pro — the flag folds into the list
    and nothing reads it any more;
  * writes the `תחומי עבודה:` line into a non-empty stored persona prompt, so
    the AI names the work this pro takes (an empty prompt is left alone — the
    runtime default persona applies there);
  * stamps `trades_set_at`, which the admin form also writes. A pro carrying it
    is never re-expanded, so re-running cannot undo an operator's trim.

Usage:
    # Dry run (default) — report only, no writes
    python scripts/backfill_pro_trades.py

    # Write the lists
    python scripts/backfill_pro_trades.py --apply

Exit codes:
    0  nothing to do
    1  some pros need the backfill (dry run)
    3  no pro on record, so nothing was checked
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import users_collection  # noqa: E402
from app.core.pro_trades import (  # noqa: E402
    TYPES_THAT_MUST_LIST,
    legacy_categories,
    upsert_trades_line,
)
from app.services.matching_service import pro_trades  # noqa: E402

EXIT_OK = 0
EXIT_NEEDS_FIX = 1
EXIT_NOTHING_CHECKED = 3

_PROJECTION = {
    "_id": 1,
    "business_name": 1,
    "type": 1,
    "profession_type": 1,
    "categories": 1,
    "handles_electrical": 1,
    "system_prompt": 1,
    "trades_set_at": 1,
}


def plan_update(pro: dict, now: datetime):
    """The Mongo update this pro needs, or None. Pure, so tests can drive it.

    The list is written for any pro whose trades include handyman or general,
    not only one typed that way: `handyman` anywhere in `categories` is what
    made a pro fit everything before, so that pro is exactly the one whose
    reach would shrink on deploy.
    """
    update = {}
    if not pro.get("trades_set_at") and pro_trades(pro) & TYPES_THAT_MUST_LIST:
        categories = legacy_categories(pro)
        sets = {"categories": categories, "trades_set_at": now}
        prompt = pro.get("system_prompt") or ""
        new_prompt = upsert_trades_line(prompt, categories)
        if new_prompt != prompt:
            sets["system_prompt"] = new_prompt
        update["$set"] = sets
    if "handles_electrical" in pro:
        update["$unset"] = {"handles_electrical": ""}
    return update or None


async def run(*, apply: bool) -> int:
    print("🔍 Checking pros' trade lists")
    print("   (dry-run — no writes)" if not apply else "   (--apply — writing)")

    now = datetime.now(timezone.utc)
    updates = []
    checked = 0
    async for pro in users_collection.find({"role": "professional"}, _PROJECTION):
        checked += 1
        update = plan_update(pro, now)
        if not update:
            continue
        updates.append((pro, update))
        name = pro.get("business_name") or "?"
        sets = update.get("$set", {})
        if "categories" in sets:
            prompt_note = "  +prompt line" if "system_prompt" in sets else ""
            print(
                f"LIST     {pro['_id']}  {name}  → {', '.join(sets['categories'])}"
                f"{prompt_note}"
            )
        else:
            print(f"UNSET    {pro['_id']}  {name}  handles_electrical")

    if not checked:
        print("⚠️  No professional on record — nothing was checked.")
        return EXIT_NOTHING_CHECKED

    print()
    print(f"Checked {checked} pros: {len(updates)} to update")
    if apply:
        for pro, update in updates:
            await users_collection.update_one({"_id": pro["_id"]}, update)
        print(f"   updated {len(updates)}")

    if updates and not apply:
        return EXIT_NEEDS_FIX
    print("✅ Every handyman / general pro has an explicit trade list.")
    return EXIT_OK


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--apply", action="store_true", help="Write the trade lists")
    args = parser.parse_args()

    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    sys.exit(asyncio.run(run(apply=args.apply)))


if __name__ == "__main__":
    main()
