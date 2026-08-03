"""Staging coverage-matrix seed — 27 deterministic professionals (PRO-84).

STAGING ONLY. Refuses to run anywhere else; see ``assert_seed_allowed``.

Why this exists
---------------
``scripts/seed_db.py`` creates three professionals, which exercises roughly one
branch of ``matching_service.determine_best_pro``. You cannot verify radius
expansion, load balancing, rating sort or a coverage gap by hand without enough
critical mass in the database.

The tempting fix is 100 Faker profiles. That proves nothing: with random data you
cannot say in advance which pro *should* win, so there is no assertion to make.
The value is in 27 profiles positioned deliberately around
``WorkerConstants.GEO_RADIUS_STEPS = [10000, 20000, 30000]``, where every scenario
has one and only one correct answer.

Additive, idempotent and reversible: every document is tagged and ``--purge``
filters on that tag alone.

It is **not** safe to run alongside ``scripts/seed_db.py``, in either direction.
``seed_db.py`` opens with ``clear_db()`` — ``delete_many({})`` across every
collection, explicitly permitted in staging — so running it afterwards wipes the
whole matrix. And its ``נתנאל אינסטלציה`` sits on the exact Tel Aviv coordinate at
rating 4.9 with slots, which outranks the intended S02 winner and silently makes
TC-20 answer with the wrong professional. ``assert_no_foreign_pros`` therefore
refuses to seed while any untagged professional is present.

Why the ``9720000001XX`` block
------------------------------
A seeded professional is a message *recipient*: the moment a lead is routed to
one, the worker sends it a real job offer. So the numbers cannot merely be
*believed* unused — that belief is what produced PRO-72, where a QA script's
docstring called a number "virtual" and it turned out to be a real handset.

Every phone here is ``9720…``, which **cannot** be an Israeli subscriber: an
Israeli MSISDN is ``972`` followed by the national number with the trunk ``0``
stripped, so the digit after ``972`` is always one of 2,3,4,5,7,8,9 — never ``0``.
That is a property of the numbering plan, not an allocation fact anyone has to
look up. Same range as ``tests/e2e/reserved_numbers.py`` (PRO-83); the ``…100``+
offset keeps it clear of that module's ``…001``–``…009`` cast.

(PRO-84 originally specified ``972500000100``–``972500000199``. That is ``972`` +
``50`` + ``0000100``, and ``05X`` is a live allocated mobile prefix — whether
``050-000-0100`` is assigned is an allocation-table fact this repo cannot verify.
Deliberately deviating from the ticket to reuse the range that is provably
unreachable.)

Enforced in ``_assert_phone_block`` with ``SystemExit``, not ``assert`` — a bare
assert disappears under ``python -O``, and this is the check standing between a
typo and an outbound WhatsApp to a stranger.

Why the whole Sharon is left empty
----------------------------------
There is no professional in Herzliya, Ra'anana, Kfar Saba, Hod HaSharon, Netanya
or Hadera. One decision serving two scenarios: it puts Netanya's nearest pro in
the 20–30 km ring (S05, radius expansion) and leaves Hadera with nobody inside
30 km at all (S06, coverage gap). Modiin is empty for the same reason and feeds
S04. Filling the Sharon in later would silently destroy three scenarios.

Determinism
-----------
``random.seed(42)`` plus explicit data means two runs produce identical content.
Not byte-identical documents, and the script does not pretend otherwise: slot
times are "the next 7 days" and are necessarily wall-clock relative. Everything
that identifies a pro — phone, name, coordinates, rating, reviews, service areas —
is fixed, and ``created_at`` is anchored to ``_CREATED_AT`` rather than ``now`` so
re-running does not churn it.

Usage
-----
    python scripts/seed_coverage_matrix.py
    python scripts/seed_coverage_matrix.py --scenario load-balance
    python scripts/seed_coverage_matrix.py --scenario overload-shfela
    python scripts/seed_coverage_matrix.py --purge

The two ``--scenario`` flags are off by default and may be combined. They inject
active leads to push professionals over ``MAX_PRO_LOAD``, which deliberately
changes who wins — so the default seed is the clean state every other scenario is
asserted against. See ``docs/MANUAL_TEST_PLAN.md`` TC-19..TC-28.
"""

import argparse
import asyncio
import os
import random
import sys
from datetime import datetime, timedelta, timezone

# Add project root to sys.path before importing from `app`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings  # noqa: E402
from app.core.constants import (  # noqa: E402
    ISRAEL_CITIES_COORDS,
    STAGING_ENV,
    LeadStatus,
    WorkerConstants,
)
from app.core.database import (  # noqa: E402
    DB_NAME,
    consent_collection,
    leads_collection,
    reviews_collection,
    slots_collection,
    users_collection,
)

# --- Batch identity -------------------------------------------------------
# Every document this script writes carries this tag. `--purge` filters on it and
# nothing else, so a purge can never reach the admin account, a real lead, or a
# seed_db.py record.
SEED_BATCH = "coverage_v1"

# --- Safety ---------------------------------------------------------------
STAGING_DB_NAME = "proli_staging_db"
# A deny-list, and therefore only a backstop: rotate the production instance and
# this stops catching anything. The load-bearing guards are the two allow-lists
# above it (ENVIRONMENT must equal staging, DB must equal proli_staging_db).
# A proper allow-list needs a staging instance id to compare against, which
# arrives with PRO-29.
PRODUCTION_INSTANCE_ID = "7105567180"
# 972 followed by 0 is not a valid Israeli MSISDN — see the module docstring.
RESERVED_PREFIX = "9720"
PHONE_BLOCK_START = 972000000100
PHONE_BLOCK_END = 972000000199
# Synthetic customers for the artificial-load leads, kept inside the same block.
LOAD_CHAT_BLOCK_START = 972000000190

# --- Determinism ----------------------------------------------------------
RANDOM_SEED = 42
# Fixed anchor for `created_at`, so a re-run does not rewrite timestamps and a
# diff between two runs is empty. Arbitrary but stable.
_CREATED_AT = datetime(2026, 1, 1, 9, 0, 0, tzinfo=timezone.utc)
# Fixed so `rating * REVIEW_COUNT` is always a whole number and the seeded
# reviews average to exactly the stored rating rather than approximately.
REVIEW_COUNT = 10

# --- Synthetic locality tokens (S08 / S09) --------------------------------
# These must be strings no geocoder can resolve. Once `resolve_city_to_coords`
# returns coordinates, an empty radius sweep makes `determine_best_pro` return
# None — it never falls through to the regex branch (`elif location:` only runs
# when there are no coordinates at all). A real neighbourhood name would be
# geocoded by Google in staging and the text fallback would never execute.
# The two tokens are disjoint so S08 and S09 cannot match each other.
S08_AREA = "מתחם בדיקות פרולי"
S09_AREA = "מרחב דן בדיקות"


def _c(city: str) -> list[float]:
    """[lon, lat] for a city, straight from the app's own table."""
    return list(ISRAEL_CITIES_COORDS[city])


def _reviews_for(rating: float) -> list[int]:
    """Integer 1-5 review scores that average to exactly ``rating``.

    Starts from all-5s and walks entries down until the sum matches, so the
    result is deterministic and needs no search.
    """
    if not 1.0 <= rating <= 5.0:
        raise ValueError(
            f"rating {rating} is outside 1.0-5.0 — no set of 1-5 scores averages to it"
        )
    target = round(rating * REVIEW_COUNT)
    scores = [5] * REVIEW_COUNT
    deficit = sum(scores) - target
    i = 0
    while deficit > 0:
        room = scores[i] - 1  # never below 1
        take = min(room, deficit)
        scores[i] -= take
        deficit -= take
        i += 1
    return scores


def _pro(
    *,
    scenario: str,
    seq: int,
    phone_suffix: int,
    name: str,
    profession: str,
    city: str | None,
    rating: float,
    service_areas: list[str],
    is_active: bool = True,
    pending_approval: bool = False,
    with_location: bool = True,
) -> dict:
    """One professional document.

    ``business_name`` encodes the scenario so the admin panel is searchable by it:
    ``[S04] אינסטלציה לוד 01``.
    """
    phone = str(PHONE_BLOCK_START + phone_suffix)
    doc = {
        "business_name": f"[{scenario}] {name} {seq:02d}",
        "phone_number": phone,
        "role": "professional",
        "type": profession,
        "categories": [profession],
        "service_areas": service_areas,
        "is_active": is_active,
        "is_verified": not pending_approval,
        "social_proof": {"rating": rating, "review_count": REVIEW_COUNT},
        "plan": "basic",
        "system_prompt": "",
        "prices_for_prompt": "",
        "keywords": [],
        # Seeded explicitly: `record_no_show` $inc's this field, and upsert_pros
        # uses $set — so without it here, no-shows accumulated during testing would
        # survive a re-seed and quietly shift the rankings (the penalty is
        # 0.5/no-show and is unbounded).
        "no_show_count": 0,
        "created_at": _CREATED_AT,
        "seed_batch": SEED_BATCH,
        "seed_scenario": scenario,
        # Always written, never conditionally: $set leaves absent keys alone, so a
        # conditional write would let a stale True survive a re-seed and keep a pro
        # invisible to base_filter forever.
        "pending_approval": bool(pending_approval),
    }
    # S08/S09 deliberately omit `location` entirely — that is what forces the
    # text-fallback branch. A pro with no location is invisible to the geo path
    # (PRO-27), which is exactly the shape those scenarios need.
    if with_location:
        if city is None:
            raise ValueError(f"{doc['business_name']} is located but has no city")
        doc["location"] = {"type": "Point", "coordinates": _c(city)}
    return doc


def build_coverage_matrix() -> list[dict]:
    """The 27 professionals. Pure — no I/O, so it is directly testable.

    Ratings are chosen so every scenario has exactly one winner. Note that
    ``determine_best_pro`` scores ``10 if has_slots else 0`` plus rating, so the
    slot bonus dominates rating entirely: only S01 and S04 pros get slots, which
    is what keeps the filler and S07 pros from disturbing scenarios they happen to
    be geographically inside of.
    """
    pros: list[dict] = []

    # -- S01 · density + rating sort. Eight pros within 6 km of Tel Aviv,
    #    ratings 3.0 → 5.0. A TA customer must get the 5.0. Covers all seven
    #    profession types on its own.
    for seq, (city, profession, rating, name) in enumerate(
        [
            # 4.8, not 5.0, and deliberately below the Holon filler's 5.0: the only
            # thing that makes this the S01 winner is the +10 availability bonus.
            # Rate it 5.0 and deleting `slot_bonus` from `candidate_score` would
            # not change a single answer, so the invariant would go untested.
            ("תל אביב", "plumber", 4.8, "אינסטלציה תל אביב"),
            ("תל אביב", "electrician", 4.7, "חשמל תל אביב"),
            ("תל אביב", "handyman", 4.6, "הנדימן תל אביב"),
            # S02 loads the three above; this is the fourth and the documented
            # TC-20 winner.
            ("תל אביב", "locksmith", 4.5, "מנעולן תל אביב"),
            ("רמת גן", "plumber", 4.2, "אינסטלציה רמת גן"),
            ("רמת גן", "painter", 4.0, "צבע רמת גן"),
            ("גבעתיים", "cleaner", 3.5, "ניקיון גבעתיים"),
            ("בני ברק", "general", 3.0, "שירות בני ברק"),
        ],
        start=1,
    ):
        pros.append(
            _pro(
                scenario="S01",
                seq=seq,
                phone_suffix=seq - 1,  # …100–…107
                name=name,
                profession=profession,
                city=city,
                rating=rating,
                service_areas=[city],
            )
        )

    # -- S04 · expansion to 20 km. A Modiin customer has nobody inside 10 km;
    #    Lod 13.2 · Ramla 15.0 · Rehovot 19.2 km. Highest rating (Lod 01) wins.
    for seq, (city, profession, rating, name) in enumerate(
        [
            ("לוד", "plumber", 4.8, "אינסטלציה לוד"),
            ("לוד", "electrician", 4.1, "חשמל לוד"),
            ("רמלה", "handyman", 4.4, "הנדימן רמלה"),
            ("רחובות", "locksmith", 4.6, "מנעולן רחובות"),
        ],
        start=1,
    ):
        pros.append(
            _pro(
                scenario="S04",
                seq=seq,
                phone_suffix=9 + seq,  # …110–…113
                name=name,
                profession=profession,
                city=city,
                rating=rating,
                service_areas=[city],
            )
        )

    # -- S07 · geocoding. A Rosh HaAyin customer (absent from
    #    ISRAEL_CITIES_COORDS, so Google + the Redis cache must answer) is 6.6 km
    #    from Petah Tikva. Deliberately no slots: these two also sit 26.6 km from
    #    Netanya, inside S05's 30 km ring, and the missing slot bonus is what
    #    stops them winning S05.
    for seq, (profession, rating, name) in enumerate(
        [
            ("plumber", 4.3, "אינסטלציה פתח תקווה"),
            ("electrician", 4.0, "חשמל פתח תקווה"),
        ],
        start=1,
    ):
        pros.append(
            _pro(
                scenario="S07",
                seq=seq,
                phone_suffix=19 + seq,  # …120–…121
                name=name,
                profession=profession,
                city="פתח תקווה",
                rating=rating,
                service_areas=["פתח תקווה"],
            )
        )

    # -- S08 · text fallback (regex on service_areas, step 2). No `location`.
    for seq, (profession, rating, name) in enumerate(
        [
            ("plumber", 4.6, "אינסטלציה ניידת"),
            ("electrician", 4.4, "חשמל נייד"),
            ("handyman", 4.2, "הנדימן נייד"),
        ],
        start=1,
    ):
        pros.append(
            _pro(
                scenario="S08",
                seq=seq,
                phone_suffix=29 + seq,  # …130–…132
                name=name,
                profession=profession,
                city=None,
                rating=rating,
                service_areas=[S08_AREA],
                with_location=False,
            )
        )

    # -- S09 · reverse match (step 3: `area in location`). No `location`.
    for seq, (profession, rating, name) in enumerate(
        [
            ("painter", 4.5, "צבע אזורי"),
            ("cleaner", 4.1, "ניקיון אזורי"),
        ],
        start=1,
    ):
        pros.append(
            _pro(
                scenario="S09",
                seq=seq,
                phone_suffix=39 + seq,  # …140–…141
                name=name,
                profession=profession,
                city=None,
                rating=rating,
                service_areas=[S09_AREA],
                with_location=False,
            )
        )

    # -- S10 · ineligible. Four in Tel Aviv, all rated 5.0 so a broken
    #    `base_filter` fails loudly: they would sort to the top of every TA query.
    for seq, (profession, name, is_active, pending) in enumerate(
        [
            ("plumber", "ממתין לאישור א", True, True),
            ("electrician", "ממתין לאישור ב", True, True),
            ("handyman", "מושבת א", False, False),
            ("locksmith", "מושבת ב", False, False),
        ],
        start=1,
    ):
        pros.append(
            _pro(
                scenario="S10",
                seq=seq,
                phone_suffix=49 + seq,  # …150–…153
                name=name,
                profession=profession,
                city="תל אביב",
                rating=5.0,
                service_areas=["תל אביב"],
                is_active=is_active,
                pending_approval=pending,
            )
        )

    # -- Filler · profession spread across the southern ring. No slots, so they
    #    cannot outrank S01 despite Holon/Bat Yam/Or Yehuda being inside 10 km
    #    of Tel Aviv.
    for seq, (city, profession, rating, name) in enumerate(
        [
            # 5.0 and no slots — the highest-rated professional in the whole Tel
            # Aviv ring, and it must still lose. This is the trap that makes the
            # +10 availability bonus load-bearing rather than incidental.
            ("חולון", "electrician", 5.0, "חשמל חולון"),
            ("בת ים", "locksmith", 4.3, "מנעולן בת ים"),
            ("ראשון לציון", "painter", 4.2, "צבע ראשון לציון"),
            ("אור יהודה", "cleaner", 4.1, "ניקיון אור יהודה"),
        ],
        start=1,
    ):
        pros.append(
            _pro(
                scenario="FILL",
                seq=seq,
                phone_suffix=59 + seq,  # …160–…163
                name=name,
                profession=profession,
                city=city,
                rating=rating,
                service_areas=[city],
            )
        )

    return pros


# Pros that get calendar slots. Everyone else has none, which also exercises the
# "no availability" path in `scheduling_service.check_pro_availability`.
SLOTTED_SCENARIOS = ("S01", "S04")


def _assert_phone_block(pros: list[dict]) -> None:
    """Every number must be in the structurally-unreachable block.

    ``SystemExit``, never bare ``assert``: under ``python -O`` an assert is removed
    outright, and this is the last thing standing between a typo and a real
    outbound WhatsApp. Checks the prefix as well as the numeric range so moving
    the block cannot silently drop the property the range depends on.
    """
    for pro in pros:
        phone = pro["phone_number"]
        if not phone.isdigit():
            raise SystemExit(f"❌ Refusing to seed: non-numeric phone {phone!r}.")
        if not phone.startswith(RESERVED_PREFIX) or not (
            PHONE_BLOCK_START <= int(phone) <= PHONE_BLOCK_END
        ):
            raise SystemExit(
                f"❌ Refusing to seed: {pro['business_name']} has phone {phone}, "
                f"outside the reserved test block "
                f"{PHONE_BLOCK_START}-{PHONE_BLOCK_END}.\n"
                "   Seeded professionals receive real job offers."
            )
    phones = [p["phone_number"] for p in pros]
    if len(phones) != len(set(phones)):
        raise SystemExit("❌ Refusing to seed: duplicate phone numbers in the matrix.")


def assert_seed_allowed() -> None:
    """Three independent guards, all before the first write.

    Fails closed: an unrecognised environment is refused rather than allowed.
    """
    if settings.ENVIRONMENT != STAGING_ENV:
        raise SystemExit(
            f"❌ Refusing to seed: ENVIRONMENT={settings.ENVIRONMENT!r}.\n"
            f"   This script is staging-only (expected {STAGING_ENV!r}).\n"
            "   It writes 27 professionals that become live message recipients."
        )
    if DB_NAME != STAGING_DB_NAME:
        raise SystemExit(
            f"❌ Refusing to seed: target database is {DB_NAME!r}, "
            f"expected {STAGING_DB_NAME!r}.\n"
            "   ENVIRONMENT says staging but MONGO_URI points somewhere else."
        )
    if str(settings.GREEN_API_INSTANCE_ID).strip() == PRODUCTION_INSTANCE_ID:
        raise SystemExit(
            f"❌ Refusing to seed: GREEN_API_INSTANCE_ID is the production "
            f"instance ({PRODUCTION_INSTANCE_ID}).\n"
            "   Seeded pros receive real job offers — point this at the staging "
            "instance first (PRO-29)."
        )


async def assert_geo_index() -> None:
    """``$geoNear`` fails outright without a 2dsphere index, and the failure is
    swallowed by ``determine_best_pro``'s broad except — every lead would silently
    escalate to admin review. Check up front with a message that names the fix."""
    info = await users_collection.index_information()
    has_2dsphere = any(
        any(field == "location" and kind == "2dsphere" for field, kind in spec["key"])
        for spec in info.values()
    )
    if not has_2dsphere:
        raise SystemExit(
            "❌ Refusing to seed: users.location has no 2dsphere index.\n"
            "   Without it every $geoNear query fails and determine_best_pro "
            "silently returns None, so the whole coverage matrix would prove "
            "nothing.\n"
            "   Fix: python scripts/create_indexes.py"
        )


async def assert_no_foreign_pros() -> None:
    """The matrix's "exactly one correct answer" claim only holds if these 27 are
    the only professionals in the rings.

    ``seed_db.py``'s ``נתנאל אינסטלציה`` sits on the exact Tel Aviv coordinate at
    rating 4.9 *with* slots, so it scores 14.9 and beats the documented S02 winner
    (``[S01] מנעולן תל אביב 04``, 14.5). TC-20 would answer with the wrong pro and
    nothing would look broken. The same applies to any real professional onboarded
    into staging.
    """
    foreign = await users_collection.count_documents(
        {"role": "professional", "seed_batch": {"$ne": SEED_BATCH}}
    )
    if foreign:
        raise SystemExit(
            f"❌ Refusing to seed: {foreign} professional(s) in {DB_NAME} are not "
            f"part of seed_batch={SEED_BATCH!r}.\n"
            "   They compete in the same $geoNear rings and silently invalidate the "
            "documented winners — seed_db.py's pro sits on the Tel Aviv coordinate "
            "at 4.9 with slots and outranks the intended S02 answer.\n"
            "   Fix: remove them, or point MONGO_URI at a database used only for "
            "the coverage matrix."
        )


async def purge() -> None:
    """Remove only this batch. Never ``delete_many({})``."""
    tag = {"seed_batch": SEED_BATCH}

    # Leads produced by actually walking TC-19..TC-28 carry no tag, so they are
    # deliberately left alone — but they point at pros about to disappear, and a
    # dangling pro_id resolves to None in the stale-lead nudger and the admin
    # panel. Report rather than silently strand.
    seeded_ids = [doc["_id"] async for doc in users_collection.find(tag, {"_id": 1})]
    if seeded_ids:
        orphans = await leads_collection.count_documents(
            {"pro_id": {"$in": seeded_ids}, "seed_batch": {"$ne": SEED_BATCH}}
        )
        if orphans:
            print(
                f"   ⚠️  {orphans} untagged lead(s) reference these professionals "
                "and will be left with a dangling pro_id."
            )

    for label, collection in (
        ("users", users_collection),
        ("slots", slots_collection),
        ("reviews", reviews_collection),
        ("leads", leads_collection),
        ("consent", consent_collection),
    ):
        result = await collection.delete_many(tag)
        print(f"   {label}: removed {result.deleted_count}")
    print(f"✅ Purged seed_batch={SEED_BATCH!r}. Nothing else was touched.")


async def upsert_pros(pros: list[dict]) -> dict[str, object]:
    """Upsert by phone_number so a re-run updates in place — zero duplicates.

    Returns phone → _id for the callers that need to attach slots and reviews.
    """
    ids: dict[str, object] = {}
    for pro in pros:
        existing = await users_collection.find_one(
            {"phone_number": pro["phone_number"]}
        )
        if existing is not None and existing.get("seed_batch") != SEED_BATCH:
            # Otherwise the upsert would convert a stranger's document into a
            # seeded professional, and the next --purge would delete it.
            raise SystemExit(
                f"❌ Refusing to seed: {pro['phone_number']} already exists in "
                f"{DB_NAME} and is not part of seed_batch={SEED_BATCH!r}."
            )
        await users_collection.update_one(
            {"phone_number": pro["phone_number"]}, {"$set": pro}, upsert=True
        )
        doc = await users_collection.find_one({"phone_number": pro["phone_number"]})
        ids[pro["phone_number"]] = doc["_id"]
    print(f"👷 Upserted {len(pros)} professionals.")
    return ids


async def seed_consent(pros: list[dict]) -> None:
    """Without a consent record the gate intercepts the first message of every
    simulated flow and no scenario ever reaches the router."""
    for pro in pros:
        chat_id = f"{pro['phone_number']}@c.us"
        await consent_collection.update_one(
            {"chat_id": chat_id},
            {
                "$set": {
                    "chat_id": chat_id,
                    "accepted": True,
                    "timestamp": _CREATED_AT,
                    "seed_batch": SEED_BATCH,
                }
            },
            upsert=True,
        )
    print(f"✅ Seeded consent for {len(pros)} professionals.")


async def seed_reviews(pros: list[dict], ids: dict[str, object]) -> None:
    """Real review documents that average to exactly the stored rating, so the
    admin panel and `_handle_reviews` are not looking at a rating with nothing
    behind it."""
    await reviews_collection.delete_many({"seed_batch": SEED_BATCH})
    # Local RNG so determinism is a property of this function rather than of
    # whoever remembered to call random.seed() first.
    rng = random.Random(RANDOM_SEED)
    docs = []
    for pro in pros:
        scores = _reviews_for(pro["social_proof"]["rating"])
        for i, score in enumerate(scores):
            docs.append(
                {
                    "pro_id": ids[pro["phone_number"]],
                    # A synthetic customer, not the pro's own id — otherwise every
                    # seeded review reads as the professional reviewing himself in
                    # the admin panel.
                    "customer_chat_id": f"{LOAD_CHAT_BLOCK_START + (i % 10)}@c.us",
                    "rating": score,
                    "comment": rng.choice(REVIEW_COMMENTS) if score >= 4 else "",
                    "created_at": _CREATED_AT + timedelta(days=i),
                    "seed_batch": SEED_BATCH,
                }
            )
    if docs:
        await reviews_collection.insert_many(docs)
    print(f"⭐ Seeded {len(docs)} reviews.")


REVIEW_COMMENTS = [
    "הגיע בזמן, עבודה נקייה.",
    "שירות מהיר ומקצועי.",
    "מחיר הוגן, אסביר לכל שאלה.",
    "פתר את הבעיה בביקור אחד.",
]


async def seed_slots(pros: list[dict], ids: dict[str, object]) -> None:
    """Slots for the next 7 days, S01 and S04 only.

    Everyone else is left with none on purpose: `determine_best_pro` scores a
    +10 slot bonus, so "has availability" is the strongest signal in the ranking
    and the absence of it is what keeps geographically-nearby filler pros from
    winning scenarios they are not meant to.
    """
    await slots_collection.delete_many({"seed_batch": SEED_BATCH})
    today = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    docs = []
    for pro in pros:
        if pro["seed_scenario"] not in SLOTTED_SCENARIOS:
            continue
        for day in range(7):
            for hour in range(8, 18):
                start = today + timedelta(days=day, hours=hour)
                docs.append(
                    {
                        "pro_id": ids[pro["phone_number"]],
                        "start_time": start,
                        "end_time": start + timedelta(hours=1),
                        "is_taken": False,
                        "seed_batch": SEED_BATCH,
                    }
                )
    if docs:
        await slots_collection.insert_many(docs)
    slotted = [p for p in pros if p["seed_scenario"] in SLOTTED_SCENARIOS]
    print(f"📅 Seeded {len(docs)} slots for {len(slotted)} professionals.")


async def inject_load(pros: list[dict], ids: dict[str, object], scenario: str) -> None:
    """Push selected pros over ``MAX_PRO_LOAD`` with active leads.

    ``load-balance`` (S02) loads the three highest-rated S01 pros, so a Tel Aviv
    customer must fall through to the fourth. ``overload-shfela`` (S03) loads every
    S04 pro, so a Modiin customer gets nobody — note that `determine_best_pro`
    stops at the first radius that returns documents and never widens again after
    the load filter empties the candidate list.
    """
    if scenario == "load-balance":
        targets = sorted(
            [p for p in pros if p["seed_scenario"] == "S01"],
            key=lambda p: p["social_proof"]["rating"],
            reverse=True,
        )[:3]
    elif scenario == "overload-shfela":
        targets = [p for p in pros if p["seed_scenario"] == "S04"]
    else:
        raise SystemExit(f"Unknown scenario {scenario!r}")

    docs = []
    for pro in targets:
        for i in range(WorkerConstants.MAX_PRO_LOAD):
            docs.append(
                {
                    # Inside the reserved block too: these are lead chat_ids, and
                    # several monitor jobs message a lead's chat_id directly.
                    "chat_id": f"{LOAD_CHAT_BLOCK_START + i}@c.us",
                    "pro_id": ids[pro["phone_number"]],
                    "status": LeadStatus.BOOKED,
                    "issue_type": f"עומס מלאכותי {scenario}",
                    "full_address": "רחוב הבדים 4, מתחם בדיקות פרולי",
                    "created_at": _CREATED_AT,
                    "seed_batch": SEED_BATCH,
                }
            )
    if docs:
        await leads_collection.insert_many(docs)
    names = ", ".join(p["business_name"] for p in targets)
    print(
        f"🧱 {scenario}: {len(docs)} active leads across {len(targets)} pros ({names})"
    )


async def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Seed the staging coverage matrix (PRO-84). Staging only."
    )
    parser.add_argument(
        "--purge",
        action="store_true",
        help=f"Remove every document tagged seed_batch={SEED_BATCH!r} and exit.",
    )
    parser.add_argument(
        "--scenario",
        action="append",
        default=[],
        choices=["load-balance", "overload-shfela"],
        help="Inject artificial load. Repeatable. Off by default.",
    )
    args = parser.parse_args(argv)

    random.seed(RANDOM_SEED)

    assert_seed_allowed()
    print(f"Environment: {settings.ENVIRONMENT} · database: {DB_NAME}")

    if args.purge:
        await purge()
        return

    await assert_geo_index()
    await assert_no_foreign_pros()

    pros = build_coverage_matrix()
    _assert_phone_block(pros)

    ids = await upsert_pros(pros)
    await seed_consent(pros)
    await seed_reviews(pros, ids)
    await seed_slots(pros, ids)

    await leads_collection.delete_many({"seed_batch": SEED_BATCH})
    for scenario in args.scenario:
        await inject_load(pros, ids, scenario)

    print(
        f"\n✅ Coverage matrix ready: {len(pros)} professionals, "
        f"seed_batch={SEED_BATCH!r}."
    )
    print("   Scenarios TC-19..TC-28 are documented in docs/MANUAL_TEST_PLAN.md")
    if not args.scenario:
        print("   (no load injected — this is the clean state S01/S04..S10 expect)")


if __name__ == "__main__":
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
