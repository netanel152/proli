"""
Backfill check: can every active pro's service areas be placed on the map?

Matching reaches a pro only through `$geoNear` on the pro's GeoJSON
`location`. A pro whose `service_areas` never resolved to coordinates has no
`location`, is never offered a lead, and nothing says so — the 2026-04-18
failure class. Approval now runs this check per pro; this script runs it
across the pros approved before that, and is the evidence that the backfill
was actually done.

Usage:
    # Dry run (default) — report only, no writes
    python scripts/check_pro_service_areas.py

    # Also check paused / inactive pros
    python scripts/check_pro_service_areas.py --include-inactive

    # Write: set `location` on pros that have none (from the first area that
    # resolves), record unresolved areas / the geocoder-down flag per pro
    python scripts/check_pro_service_areas.py --apply

    # Same, but replace an existing `location` too
    python scripts/check_pro_service_areas.py --apply --overwrite-location

Exit codes (so the run can gate a checklist):
    0  every checked pro has all areas resolved
    1  at least one pro has an area the geocoder does not know — or none at
       all — and needs its `service_areas` corrected in the admin panel
    2  the geocoder was unavailable for at least one area; the verdict is
       incomplete — re-run once `geo:unavailable` has cleared (60s)
    3  no pro matched the filter, so nothing was checked. Deliberately not 0:
       "every checked pro is fine" is vacuously true of an empty set, and a
       green run that examined nothing is indistinguishable from a green run
       that examined everything — the exact failure this check exists to
       prevent, aimed at itself. The report says which population is empty.

Uses the same pipeline as routing (static dict → Redis cache → Google), so it
needs the same env: `REDIS_URL`, and `GOOGLE_MAPS_API_KEY` for anything
outside `ISRAEL_CITIES_COORDS`. Without the key every non-static name is
"unavailable", never "unresolved" — the script will not mark a pro's area
wrong on the strength of a missing key.
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
from app.services.geocoding_service import (  # noqa: E402
    SERVICE_AREAS_GEOCODE_PENDING_FIELD,
    SERVICE_AREAS_UNRESOLVED_FIELD,
    ServiceAreaResolution,
    resolve_service_areas,
)

EXIT_OK = 0
EXIT_UNRESOLVED = 1
EXIT_UNAVAILABLE = 2
EXIT_NOTHING_CHECKED = 3

_PROJECTION = {
    "_id": 1,
    "business_name": 1,
    "service_areas": 1,
    "location": 1,
    "is_active": 1,
    SERVICE_AREAS_UNRESOLVED_FIELD: 1,
    SERVICE_AREAS_GEOCODE_PENDING_FIELD: 1,
}


def pro_filter(include_inactive: bool) -> dict:
    """The pros routing can actually offer a lead to (`determine_best_pro`'s
    base filter), or every approved pro with --include-inactive."""
    query = {"role": "professional", "pending_approval": {"$ne": True}}
    if not include_inactive:
        query["is_active"] = True
    return query


def _has_location(pro: dict) -> bool:
    coords = (pro.get("location") or {}).get("coordinates")
    return isinstance(coords, (list, tuple)) and len(coords) == 2


def verdict(pro: dict, resolution: ServiceAreaResolution) -> str:
    """One word per pro for the report and the exit code."""
    if not resolution.resolved:
        # Nothing places this pro. A definitive miss among the areas needs a
        # correction regardless of what else is pending (UNREACHABLE, exit 1);
        # only when *every* area is merely unchecked is the verdict incomplete.
        if resolution.unresolved or not resolution.unavailable:
            return "UNREACHABLE"
        return "UNAVAILABLE"
    if resolution.unresolved:
        return "PARTIAL"  # reachable via the resolved area(s); junk remains
    if resolution.unavailable:
        return "UNAVAILABLE"  # reachable, but part of the verdict is pending
    if not _has_location(pro):
        return "MISSING_LOCATION"  # areas fine, doc never got the point
    return "OK"


def _print_pro(pro: dict, resolution: ServiceAreaResolution, label: str) -> None:
    name = pro.get("business_name") or "?"
    loc = "set" if _has_location(pro) else "MISSING"
    print(f"{label:17} {pro['_id']}  {name}  (location: {loc})")
    for area, (lon, lat) in resolution.resolved:
        print(f"   ✓ {area}  → ({lon}, {lat})")
    for area in resolution.unresolved:
        print(f"   ✗ {area}  — not found (correct it in the admin panel)")
    for area in resolution.unavailable:
        print(f"   ? {area}  — geocoder unavailable, re-check later")
    if not (resolution.resolved or resolution.unresolved or resolution.unavailable):
        print("   ✗ no service areas on record")


async def _report_empty_population(include_inactive: bool) -> None:
    """Say *why* nothing was checked, so an empty run is a fact rather than a
    shrug. A zero-row check is not evidence of anything, and the difference
    between "no professionals exist yet" and "they all sit behind the filter"
    is the difference between a pre-pilot database and a misconfigured one.
    """
    print()
    print("⚠️  No pro matched the filter — nothing was checked, and this run")
    print("   proves nothing about anybody's service areas.")
    try:
        total = await users_collection.count_documents({"role": "professional"})
        pending = await users_collection.count_documents(
            {"role": "professional", "pending_approval": True}
        )
        approved = total - pending
        inactive = await users_collection.count_documents(
            {
                "role": "professional",
                "pending_approval": {"$ne": True},
                "is_active": {"$ne": True},
            }
        )
    except Exception as exc:  # pragma: no cover — diagnostics must never mask
        print(f"   (could not count the wider population: {type(exc).__name__})")
        return

    print(
        f"   professionals on record: {total}  "
        f"(approved: {approved}, awaiting approval: {pending})"
    )
    if not total:
        print("   The collection holds no professionals at all — expected before")
        print("   the pilot onboards anyone, and a misconfigured MONGO_URI otherwise.")
    elif not approved:
        print("   Every one of them is still awaiting approval, so none is a")
        print("   routing target yet. Approve one and the check has something to do.")
    elif inactive and not include_inactive:
        print(f"   {inactive} approved pro(s) are paused (is_active=False) and were")
        print("   skipped. Re-run with --include-inactive to check them too.")


async def run(
    *,
    apply: bool,
    include_inactive: bool,
    overwrite_location: bool,
    resolver=resolve_service_areas,
) -> int:
    query = pro_filter(include_inactive)
    counts = {
        "OK": 0,
        "MISSING_LOCATION": 0,
        "PARTIAL": 0,
        "UNREACHABLE": 0,
        "UNAVAILABLE": 0,
    }
    checked = 0
    written = 0

    print(
        f"🔍 Checking service areas ({'all approved' if include_inactive else 'active'} pros)"
    )
    print(
        "   (dry-run — no writes)" if not apply else "   (--apply — writing verdicts)"
    )

    cursor = users_collection.find(query, _PROJECTION).sort("business_name", 1)
    async for pro in cursor:
        checked += 1
        resolution = await resolver(pro.get("service_areas") or [])
        label = verdict(pro, resolution)
        counts[label] += 1
        _print_pro(pro, resolution, label)

        if apply:
            update = resolution.mongo_update(
                now=datetime.now(timezone.utc),
                include_location=overwrite_location or not _has_location(pro),
            )
            await users_collection.update_one({"_id": pro["_id"]}, update)
            written += 1
            if "location" in update["$set"]:
                print("   → location written")

    print()
    print(f"Checked {checked} pros" + (f", updated {written}" if apply else ""))
    print(
        "   OK: {OK}  missing-location: {MISSING_LOCATION}  partial: {PARTIAL}  "
        "unreachable: {UNREACHABLE}  geocoder-unavailable: {UNAVAILABLE}".format(
            **counts
        )
    )

    if not checked:
        await _report_empty_population(include_inactive)
        return EXIT_NOTHING_CHECKED

    if counts["PARTIAL"] or counts["UNREACHABLE"]:
        print("❌ Some pros have service areas the geocoder does not know — fix them.")
        return EXIT_UNRESOLVED
    if counts["UNAVAILABLE"]:
        print("⚠️  Geocoder unavailable for some areas — verdict incomplete, re-run.")
        return EXIT_UNAVAILABLE
    if counts["MISSING_LOCATION"] and not apply:
        print("⚠️  Some pros resolve but carry no `location` — re-run with --apply.")
        return EXIT_UNRESOLVED
    print("✅ Every checked pro can be placed on the map.")
    return EXIT_OK


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write location (where missing) and the per-pro verdict fields",
    )
    parser.add_argument(
        "--include-inactive",
        action="store_true",
        help="Also check pros with is_active=False",
    )
    parser.add_argument(
        "--overwrite-location",
        action="store_true",
        help="With --apply: replace an existing location from the first resolved area",
    )
    args = parser.parse_args()

    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    code = asyncio.run(
        run(
            apply=args.apply,
            include_inactive=args.include_inactive,
            overwrite_location=args.overwrite_location,
        )
    )
    sys.exit(code)


if __name__ == "__main__":
    main()
