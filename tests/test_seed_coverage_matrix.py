"""PRO-84 — the staging coverage matrix, verified rather than merely described.

A seed script is usually tested by asserting its shape: right number of rows, right
fields. That would miss the point here. The matrix's whole claim is that **each
scenario has exactly one correct answer** from `determine_best_pro` — so most of
this file seeds the 27 professionals into mongomock and runs the *real* routing
engine against them, asserting the winner by name.

That makes the matrix executable: if someone re-tunes the scoring, moves a city, or
fills in the Sharon, these tests fail here instead of the operator discovering it
by hand in staging.

`$geoNear` is emulated by `tests/e2e/geo_shim.py` (built for PRO-83) — the one
Mongo operator mongomock lacks. The progressive 10/20/30 km radius stepping, the
`base_filter`, the `MAX_PRO_LOAD` skip and the composite score all execute for real.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio

from app.core.constants import ISRAEL_CITIES_COORDS, LeadStatus, WorkerConstants
from scripts.seed_coverage_matrix import (
    PHONE_BLOCK_END,
    PHONE_BLOCK_START,
    REVIEW_COUNT,
    S08_AREA,
    S09_AREA,
    SEED_BATCH,
    SLOTTED_SCENARIOS,
    STAGING_DB_NAME,
    _assert_phone_block,
    _reviews_for,
    build_coverage_matrix,
)
from tests.e2e.geo_shim import GeoAwareCollection, haversine_meters

# Rosh HaAyin is deliberately absent from ISRAEL_CITIES_COORDS — that absence is
# what S07 tests. In staging Google resolves it; here the test geocoder does.
ROSH_HAAYIN = [34.9556, 32.0956]


# ===========================================================================
# The matrix itself — pure, no I/O
# ===========================================================================


def test_the_matrix_is_twenty_seven_professionals():
    assert len(build_coverage_matrix()) == 27


def test_every_phone_is_inside_the_reserved_block():
    """The guard that matters most: a seeded pro is a message *recipient*, so a
    stray digit here is an outbound WhatsApp to a stranger."""
    pros = build_coverage_matrix()
    _assert_phone_block(pros)  # raises on violation
    for pro in pros:
        assert PHONE_BLOCK_START <= int(pro["phone_number"]) <= PHONE_BLOCK_END


def test_the_block_does_not_collide_with_seed_db():
    """`seed_db.py` uses …0002 / …0099 / the admin number. The two seeds have to
    coexist, and `--purge` must never be able to reach the wrong rows."""
    # Built from parts so this file contains no contiguous copy of a real number
    # (`tests/e2e/reserved_numbers.py` uses the same technique and explains why).
    seeded = {p["phone_number"] for p in build_coverage_matrix()}
    for other in ("97252" + "4828796", "97250" + "0000002", "97250" + "0000099"):
        assert other not in seeded


def test_every_document_carries_the_batch_tag():
    for pro in build_coverage_matrix():
        assert pro["seed_batch"] == SEED_BATCH


def test_business_names_encode_their_scenario():
    """`[S04] אינסטלציה לוד 01` — searchable in the admin panel."""
    for pro in build_coverage_matrix():
        assert pro["business_name"].startswith(f"[{pro['seed_scenario']}]")


def test_the_matrix_is_deterministic():
    assert build_coverage_matrix() == build_coverage_matrix()


def test_all_seven_profession_types_are_covered():
    """The seven `PROFESSION_CONFIG` keys — note there is no AC-technician type in
    this codebase, so `general` is the seventh."""
    expected = {
        "plumber",
        "electrician",
        "handyman",
        "locksmith",
        "painter",
        "cleaner",
        "general",
    }
    assert {p["type"] for p in build_coverage_matrix()} == expected


def test_only_s01_and_s04_are_meant_to_have_slots():
    """The slot bonus is +10, which dominates rating entirely — so who has slots
    is the single most load-bearing decision in the matrix."""
    assert SLOTTED_SCENARIOS == ("S01", "S04")


def test_seeded_reviews_average_to_exactly_the_stored_rating():
    for pro in build_coverage_matrix():
        rating = pro["social_proof"]["rating"]
        scores = _reviews_for(rating)
        assert len(scores) == REVIEW_COUNT == pro["social_proof"]["review_count"]
        assert all(1 <= s <= 5 for s in scores)
        assert sum(scores) / len(scores) == pytest.approx(rating)


def test_text_fallback_pros_have_no_location():
    """S08/S09 must be invisible to the geo path — that is what forces the regex
    and reverse-match branches (see PRO-27)."""
    for pro in build_coverage_matrix():
        if pro["seed_scenario"] in ("S08", "S09"):
            assert "location" not in pro
        else:
            assert pro["location"]["type"] == "Point"


def test_the_two_fallback_tokens_cannot_cross_match():
    """S09's driving string must not contain S08's area, or step 3 would pick up
    S08's pros and the reverse-match branch would never be isolated."""
    s09_input = f"רחוב הבדים 4, {S09_AREA}"
    assert S08_AREA not in s09_input
    assert S09_AREA in s09_input


def test_ineligible_pros_are_rated_five_so_a_filter_break_is_loud():
    ineligible = [p for p in build_coverage_matrix() if p["seed_scenario"] == "S10"]
    assert len(ineligible) == 4
    assert all(p["social_proof"]["rating"] == 5.0 for p in ineligible)
    assert sum(1 for p in ineligible if p.get("pending_approval")) == 2
    assert sum(1 for p in ineligible if not p["is_active"]) == 2


# ===========================================================================
# Geography — the matrix's distances, recomputed against the app's own table
# ===========================================================================


def _km(city_a: str, city_b_coords: list[float]) -> float:
    return haversine_meters(ISRAEL_CITIES_COORDS[city_a], city_b_coords) / 1000


@pytest.mark.parametrize(
    "origin,city,low,high",
    [
        # S04 — Modiin's ring: nothing inside 10 km, three inside 20 km.
        ("מודיעין", "לוד", 12.5, 14.0),
        ("מודיעין", "רמלה", 14.0, 16.0),
        ("מודיעין", "רחובות", 18.5, 20.0),
        ("מודיעין", "פתח תקווה", 24.0, 25.5),
        # S05 — Netanya: nothing inside 20 km, the S01 cluster inside 30 km.
        ("נתניה", "תל אביב", 26.5, 28.0),
        ("נתניה", "רמת גן", 27.5, 29.0),
        ("נתניה", "בני ברק", 25.5, 27.0),
        # S06 — Hadera: nothing within 30 km at all.
        ("חדרה", "תל אביב", 40.0, 42.0),
        ("חדרה", "כפר סבא", 28.0, 30.0),
    ],
)
def test_documented_distances_still_hold(origin, city, low, high):
    """If someone edits ISRAEL_CITIES_COORDS, the scenarios silently stop testing
    what they claim. This is the tripwire."""
    assert low < _km(origin, ISRAEL_CITIES_COORDS[city]) < high


def test_modiin_has_nobody_inside_the_first_radius():
    """S04's premise: step 1 must come back empty."""
    first_radius_km = WorkerConstants.GEO_RADIUS_STEPS[0] / 1000
    for pro in build_coverage_matrix():
        if "location" not in pro:
            continue
        distance = _km("מודיעין", pro["location"]["coordinates"])
        assert distance > first_radius_km


def test_hadera_has_nobody_inside_the_widest_radius():
    """S06's premise: the coverage gap is real, not an artefact of load filtering."""
    widest_km = WorkerConstants.GEO_RADIUS_STEPS[-1] / 1000
    for pro in build_coverage_matrix():
        if "location" not in pro:
            continue
        assert _km("חדרה", pro["location"]["coordinates"]) > widest_km


def test_the_sharon_is_empty():
    """One decision serving S05 and S06. Filling it in breaks both."""
    sharon = {"הרצליה", "רעננה", "כפר סבא", "הוד השרון", "נתניה", "חדרה", "מודיעין"}
    for pro in build_coverage_matrix():
        assert not (
            set(pro["service_areas"]) & sharon
        ), f"{pro['business_name']} serves the Sharon, which must stay empty"


def test_petah_tikva_sits_on_the_first_radius_boundary():
    """Documented trap: PT is ~10.01 km from Tel Aviv, i.e. exactly on
    GEO_RADIUS_STEPS[0], so whether it is in or out depends on floating-point
    detail. No scenario may depend on the answer — PT is only ever reached from
    Rosh HaAyin (6.6 km, comfortably inside)."""
    from_tlv = _km("תל אביב", ISRAEL_CITIES_COORDS["פתח תקווה"])
    assert 9.8 < from_tlv < 10.3, "PT is no longer the boundary case documented"

    from_rosh = haversine_meters(ROSH_HAAYIN, ISRAEL_CITIES_COORDS["פתח תקווה"]) / 1000
    assert from_rosh < 8, "S07 relies on PT being well inside the first radius"


# ===========================================================================
# The routing engine, run for real against the seeded matrix
# ===========================================================================


@pytest.fixture
def mock_db():
    """Function-scoped override of the module-scoped fixture in tests/conftest.py.

    Every test here seeds the same 27 professionals with the same phone numbers.
    On a shared database that means 27×N documents with duplicate phones, so the
    `_id` a test injects leads against is not the `_id` the router happens to
    return — load balancing silently stops being observable. Also keeps the purge
    test from counting another test's leads.
    """
    from mongomock_motor import AsyncMongoMockClient

    return AsyncMongoMockClient().proli_db


@pytest_asyncio.fixture
async def seeded(mock_db, monkeypatch):
    """Load the coverage matrix into mongomock and wire the real routing engine.

    Two substitutions, both necessary and both narrow: `$geoNear` (mongomock has
    no implementation) and the geocoder (a paid external API). Everything else —
    radius stepping, base_filter, load balancing, the composite score — is the
    production code path.
    """
    import app.services.matching_service as matching_service
    import app.services.scheduling_service as scheduling_service

    pros = build_coverage_matrix()
    await mock_db.users.insert_many([dict(p) for p in pros])

    by_phone = {}
    async for doc in mock_db.users.find({}):
        by_phone[doc["phone_number"]] = doc

    # Slots for S01/S04 only — the +10 availability bonus is what separates the
    # scenarios, so this has to match the script exactly.
    now = datetime.now(timezone.utc)
    slots = [
        {
            "pro_id": by_phone[p["phone_number"]]["_id"],
            "start_time": now + timedelta(hours=2 + i),
            "end_time": now + timedelta(hours=3 + i),
            "is_taken": False,
        }
        for p in pros
        if p["seed_scenario"] in SLOTTED_SCENARIOS
        for i in range(3)
    ]
    await mock_db.slots.insert_many(slots)

    known = dict(ISRAEL_CITIES_COORDS)
    known["ראש העין"] = ROSH_HAAYIN  # what Google returns in staging

    async def fake_resolve(name):
        if not name or not name.strip():
            return None
        lowered = name.strip().lower()
        if lowered in known:
            return tuple(known[lowered])
        for city, coords in known.items():
            if city in lowered:
                return tuple(coords)
        return None  # the synthetic S08/S09 tokens land here

    monkeypatch.setattr(
        matching_service, "users_collection", GeoAwareCollection(mock_db.users)
    )
    monkeypatch.setattr(matching_service, "resolve_city_to_coords", fake_resolve)
    monkeypatch.setattr(matching_service, "leads_collection", mock_db.leads)
    monkeypatch.setattr(scheduling_service, "slots_collection", mock_db.slots)

    return {"pros": pros, "by_phone": by_phone, "db": mock_db}


async def _route(location: str):
    from app.services.matching_service import determine_best_pro

    return await determine_best_pro(issue_type="נזילה", location=location)


async def _overload(seeded, scenario: str):
    """Mirror of the script's `inject_load`."""
    targets = [p for p in seeded["pros"] if p["seed_scenario"] == scenario]
    if scenario == "S01":
        targets = sorted(
            targets, key=lambda p: p["social_proof"]["rating"], reverse=True
        )[:3]
    await seeded["db"].leads.insert_many(
        [
            {
                "chat_id": f"{PHONE_BLOCK_END}{i}@c.us",
                "pro_id": seeded["by_phone"][p["phone_number"]]["_id"],
                "status": LeadStatus.BOOKED,
            }
            for p in targets
            for i in range(WorkerConstants.MAX_PRO_LOAD)
        ]
    )


@pytest.mark.asyncio
async def test_s01_tel_aviv_customer_gets_the_highest_rated_pro(seeded):
    """TC-19 — rating sort inside a dense cluster."""
    chosen = await _route("תל אביב")

    assert chosen is not None
    assert chosen["business_name"] == "[S01] אינסטלציה תל אביב 01"
    assert chosen["social_proof"]["rating"] == 4.8


@pytest.mark.asyncio
async def test_s02_load_balancing_skips_the_top_three(seeded):
    """TC-20 — the three best are at MAX_PRO_LOAD, so the fourth wins."""
    await _overload(seeded, "S01")

    chosen = await _route("תל אביב")

    assert chosen is not None
    assert chosen["business_name"] == "[S01] מנעולן תל אביב 04"
    assert chosen["social_proof"]["rating"] == 4.5


@pytest.mark.asyncio
async def test_s03_everyone_overloaded_returns_none(seeded):
    """TC-21 — `determine_best_pro` stops at the first radius that returns
    documents; it does not widen again once the load filter empties the list."""
    await _overload(seeded, "S04")

    # Positive control first: `determine_best_pro` swallows every exception and
    # returns None, so without this a broken shim would make the assertion below
    # pass for entirely the wrong reason.
    assert await _route("תל אביב") is not None

    assert await _route("מודיעין") is None


@pytest.mark.asyncio
async def test_s04_expands_to_the_second_radius(seeded):
    """TC-22 — nothing within 10 km of Modiin, so step 2 answers."""
    chosen = await _route("מודיעין")

    assert chosen is not None
    assert chosen["business_name"] == "[S04] אינסטלציה לוד 01"


@pytest.mark.asyncio
async def test_s05_expands_to_the_third_radius(seeded):
    """TC-23 — Netanya has nothing inside 20 km; the Tel Aviv cluster is caught by
    the 30 km step. The S07 Petah Tikva pros are also inside that ring, and are
    outranked purely because they have no slots."""
    chosen = await _route("נתניה")

    assert chosen is not None
    assert chosen["business_name"] == "[S01] אינסטלציה תל אביב 01"


@pytest.mark.asyncio
async def test_s06_coverage_gap_returns_none(seeded):
    """TC-24 — Hadera. Caller escalates to PENDING_ADMIN_REVIEW; PRO-77 requires
    this to be a warning, never a critical page."""
    # Positive control — see test_s03 for why.
    assert await _route("תל אביב") is not None

    assert await _route("חדרה") is None


@pytest.mark.asyncio
async def test_s07_geocoded_city_outside_the_static_table(seeded):
    """TC-25 — Rosh HaAyin is absent from ISRAEL_CITIES_COORDS, so this only works
    if the geocoder answers. Exactly the 2026-04-18 post-mortem shape."""
    assert "ראש העין" not in ISRAEL_CITIES_COORDS

    chosen = await _route("ראש העין")

    assert chosen is not None
    assert chosen["business_name"] == "[S07] אינסטלציה פתח תקווה 01"


@pytest.mark.asyncio
async def test_s08_text_fallback_via_regex(seeded):
    """TC-26 — an unresolvable locality drops routing into the service_areas regex."""
    chosen = await _route(S08_AREA)

    assert chosen is not None
    assert chosen["business_name"] == "[S08] אינסטלציה ניידת 01"


@pytest.mark.asyncio
async def test_s09_reverse_match(seeded):
    """TC-27 — the full address matches no service_areas regex, so step 3's
    `area in location` is the only thing that can find anyone."""
    chosen = await _route(f"רחוב הבדים 4, {S09_AREA}")

    assert chosen is not None
    assert chosen["business_name"] == "[S09] צבע אזורי 01"


@pytest.mark.asyncio
async def test_s10_ineligible_pros_are_never_routed(seeded):
    """TC-28 — all four are rated 5.0 and sit in Tel Aviv, so a broken base_filter
    would put them at the top of S01's result rather than failing quietly."""
    for location in ("תל אביב", "נתניה"):
        chosen = await _route(location)
        assert chosen is not None
        assert not chosen["business_name"].startswith("[S10]")
        assert chosen["is_active"] is True
        assert not chosen.get("pending_approval")


@pytest.mark.asyncio
async def test_the_slot_bonus_beats_a_higher_rating(seeded):
    """The invariant the whole matrix leans on, made non-vacuous.

    `[FILL] חשמל חולון 01` is rated 5.0 — the highest in the Tel Aviv ring — and is
    7.8 km away, well inside the first radius. It has no slots. The winner is rated
    4.8 *with* slots. So the only thing producing the documented answer is
    `candidate_score`'s +10 availability bonus: delete that term and this test
    fails, which is exactly what it is for.
    """
    ring = {
        p["business_name"]: p["social_proof"]["rating"]
        for p in seeded["pros"]
        if p["seed_scenario"] in ("S01", "FILL")
    }
    assert ring["[FILL] חשמל חולון 01"] == 5.0
    assert ring["[S01] אינסטלציה תל אביב 01"] == 4.8

    chosen = await _route("תל אביב")

    assert chosen["business_name"] == "[S01] אינסטלציה תל אביב 01"
    assert chosen["seed_scenario"] in SLOTTED_SCENARIOS


# ===========================================================================
# Safety guards
# ===========================================================================


@pytest.mark.parametrize(
    "environment,db_name,transmits",
    [
        ("production", STAGING_DB_NAME, False),
        ("development", STAGING_DB_NAME, False),
        ("staging", "proli_db", False),
        # PRO-86: the third guard used to compare GREEN_API_INSTANCE_ID against
        # the production instance. It now asks whether the configured transport
        # can reach a handset at all.
        ("staging", STAGING_DB_NAME, True),
    ],
)
def test_seed_is_refused_outside_staging(monkeypatch, environment, db_name, transmits):
    """Each guard fires on its own, before any write."""
    import scripts.seed_coverage_matrix as seed_module

    monkeypatch.setattr(seed_module.settings, "ENVIRONMENT", environment)
    monkeypatch.setattr(seed_module, "DB_NAME", db_name)
    monkeypatch.setattr(
        seed_module, "build_provider", lambda: _StubProvider(transmits=transmits)
    )

    with pytest.raises(SystemExit):
        seed_module.assert_seed_allowed()


class _StubProvider:
    """Minimal stand-in — assert_seed_allowed only reads `transmits`/`name`."""

    def __init__(self, transmits: bool):
        self.transmits = transmits
        self.name = "cloud" if transmits else "dryrun"


def test_seed_is_allowed_on_a_clean_staging_target(monkeypatch):
    import scripts.seed_coverage_matrix as seed_module

    monkeypatch.setattr(seed_module.settings, "ENVIRONMENT", "staging")
    monkeypatch.setattr(seed_module, "DB_NAME", STAGING_DB_NAME)
    monkeypatch.setattr(
        seed_module, "build_provider", lambda: _StubProvider(transmits=False)
    )

    seed_module.assert_seed_allowed()  # must not raise


def test_a_transmitting_provider_alone_blocks_the_seed(monkeypatch):
    """PRO-86 regression: staging env + staging DB is not sufficient. If the
    configured transport can reach real handsets, 27 seeded professionals would
    become live recipients."""
    import scripts.seed_coverage_matrix as seed_module

    monkeypatch.setattr(seed_module.settings, "ENVIRONMENT", "staging")
    monkeypatch.setattr(seed_module, "DB_NAME", STAGING_DB_NAME)
    monkeypatch.setattr(
        seed_module, "build_provider", lambda: _StubProvider(transmits=True)
    )

    with pytest.raises(SystemExit, match="can send"):
        seed_module.assert_seed_allowed()


def test_the_phone_guard_rejects_a_number_outside_the_block():
    """The offending number is itself from the reserved range — deliberately not a
    real one. Writing a genuine handset into a test fixture to prove it gets
    rejected is how PRO-72 started."""
    pros = build_coverage_matrix()
    pros[0] = dict(pros[0], phone_number="972000000999")

    with pytest.raises(SystemExit, match="outside the reserved test block"):
        _assert_phone_block(pros)


def test_the_phone_guard_survives_python_dash_o():
    """`python -O` strips bare asserts. This guard is the last thing between a
    typo and an outbound WhatsApp, so it must raise, not assert."""
    import scripts.seed_coverage_matrix as seed_module

    source = Path(seed_module.__file__).read_text(encoding="utf-8")
    body = source.split("def _assert_phone_block")[1].split("\ndef ")[0]
    assert "raise SystemExit" in body
    assert "\n        assert " not in body, "a bare assert here vanishes under -O"


def test_every_phone_is_structurally_unreachable():
    """Not "we believe this block is unused" — 972 followed by 0 cannot be an
    Israeli MSISDN, because the digit after the country code is the national
    number with the trunk 0 stripped."""
    for pro in build_coverage_matrix():
        assert pro["phone_number"].startswith("9720")


def test_the_seed_script_cannot_send_anything():
    """Zero outbound messages during a seed, enforced structurally rather than by
    reading the code: the module reaches no send path at all.

    PRO-86 note — the script does now import ``build_provider``, which is a
    *capability check* (``provider.transmits``) and not an egress. The forbidden
    list below is therefore the send surface, not the provider package: acquiring
    the facade (``get_whatsapp``) or calling any send method stays banned."""
    import scripts.seed_coverage_matrix as seed_module

    source = Path(seed_module.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "notification_service",
        "get_whatsapp",
        "send_message",
        "send_text",
        "send_file",
    ):
        assert forbidden not in source, f"the seed script references {forbidden}"


@pytest.mark.asyncio
async def test_purge_only_removes_this_batch(mock_db, monkeypatch):
    """The admin account, real leads and seed_db.py's rows must all survive."""
    import scripts.seed_coverage_matrix as seed_module

    await mock_db.users.insert_many(
        [
            {"phone_number": "972500000100", "seed_batch": SEED_BATCH},
            {"phone_number": "972524828796"},  # seed_db.py's pro
            {"phone_number": "972500000002", "seed_batch": "other_batch"},
        ]
    )
    await mock_db.leads.insert_one({"chat_id": "real@c.us", "status": LeadStatus.NEW})

    for name, collection in (
        ("users_collection", mock_db.users),
        ("slots_collection", mock_db.slots),
        ("reviews_collection", mock_db.reviews),
        ("leads_collection", mock_db.leads),
        ("consent_collection", mock_db.consent),
    ):
        monkeypatch.setattr(seed_module, name, collection)

    await seed_module.purge()

    survivors = {d["phone_number"] for d in await mock_db.users.find({}).to_list(10)}
    assert survivors == {"972524828796", "972500000002"}
    assert await mock_db.leads.count_documents({}) == 1


# ===========================================================================
# assert_geo_index — 2dsphere detection against realistic index_information()
# shapes. Swallowed by determine_best_pro's broad except if missed, so this is
# the guard that stops a whole run "succeeding" while proving nothing.
# ===========================================================================


class _FakeIndexCollection:
    """Minimal stand-in for `users_collection` exposing only `index_information`,
    shaped like Motor's real return value: {index_name: {"key": [(field, kind), ...]}}.
    """

    def __init__(self, index_info: dict):
        self._index_info = index_info

    async def index_information(self):
        return self._index_info


@pytest.mark.asyncio
async def test_assert_geo_index_passes_when_2dsphere_present_on_location(monkeypatch):
    import scripts.seed_coverage_matrix as seed_module

    info = {
        "_id_": {"key": [("_id", 1)]},
        "location_2dsphere": {"key": [("location", "2dsphere")]},
    }
    monkeypatch.setattr(seed_module, "users_collection", _FakeIndexCollection(info))

    await seed_module.assert_geo_index()  # must not raise


@pytest.mark.asyncio
async def test_assert_geo_index_raises_when_no_index_exists(monkeypatch):
    import scripts.seed_coverage_matrix as seed_module

    info = {"_id_": {"key": [("_id", 1)]}}
    monkeypatch.setattr(seed_module, "users_collection", _FakeIndexCollection(info))

    with pytest.raises(SystemExit, match="2dsphere"):
        await seed_module.assert_geo_index()


@pytest.mark.asyncio
async def test_assert_geo_index_raises_when_location_has_the_wrong_index_kind(
    monkeypatch,
):
    """A plain ascending index on `location` (e.g. a typo in create_indexes.py)
    must not be mistaken for the 2dsphere index `$geoNear` actually requires."""
    import scripts.seed_coverage_matrix as seed_module

    info = {
        "_id_": {"key": [("_id", 1)]},
        "location_1": {"key": [("location", 1)]},
    }
    monkeypatch.setattr(seed_module, "users_collection", _FakeIndexCollection(info))

    with pytest.raises(SystemExit, match="2dsphere"):
        await seed_module.assert_geo_index()


@pytest.mark.asyncio
async def test_assert_geo_index_raises_when_2dsphere_is_on_a_different_field(
    monkeypatch,
):
    """A 2dsphere index on some other field does not satisfy a `location`
    geo query — the check must key on the field name, not merely the index kind."""
    import scripts.seed_coverage_matrix as seed_module

    info = {
        "_id_": {"key": [("_id", 1)]},
        "service_area_2dsphere": {"key": [("service_area", "2dsphere")]},
    }
    monkeypatch.setattr(seed_module, "users_collection", _FakeIndexCollection(info))

    with pytest.raises(SystemExit, match="2dsphere"):
        await seed_module.assert_geo_index()


@pytest.mark.asyncio
async def test_assert_geo_index_passes_with_a_compound_index_containing_location(
    monkeypatch,
):
    """`location` need not be the sole key of the index — a compound index that
    includes it as a 2dsphere member still satisfies `$geoNear`."""
    import scripts.seed_coverage_matrix as seed_module

    info = {
        "_id_": {"key": [("_id", 1)]},
        "type_1_location_2dsphere": {"key": [("type", 1), ("location", "2dsphere")]},
    }
    monkeypatch.setattr(seed_module, "users_collection", _FakeIndexCollection(info))

    await seed_module.assert_geo_index()  # must not raise


# ===========================================================================
# _reviews_for — boundary ratings
# ===========================================================================


def test_reviews_for_a_perfect_rating_has_no_deficit_to_walk_down():
    scores = _reviews_for(5.0)
    assert scores == [5] * REVIEW_COUNT
    assert sum(scores) / len(scores) == 5.0


def test_reviews_for_three_point_zero():
    scores = _reviews_for(3.0)
    assert len(scores) == REVIEW_COUNT
    assert all(1 <= s <= 5 for s in scores)
    assert sum(scores) / len(scores) == pytest.approx(3.0)


def test_reviews_for_a_low_rating_walks_every_entry_down_to_the_floor():
    """1.0 needs every one of the ten entries dragged down to the floor of 1 —
    a rating a single entry could never absorb alone, so this exercises the
    loop's `i += 1` continuation across the full list."""
    scores = _reviews_for(1.0)
    assert scores == [1] * REVIEW_COUNT
    assert sum(scores) / len(scores) == 1.0


def test_reviews_for_never_emits_a_score_outside_one_to_five():
    for rating in (1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0):
        scores = _reviews_for(rating)
        assert all(1 <= s <= 5 for s in scores), f"rating {rating} produced {scores}"
        assert sum(scores) / len(scores) == pytest.approx(rating)


# ===========================================================================
# inject_load — target selection and the unknown-scenario guard
# ===========================================================================


@pytest.mark.asyncio
async def test_inject_load_balance_targets_exactly_the_top_three_s01_pros(
    mock_db, monkeypatch
):
    import scripts.seed_coverage_matrix as seed_module

    pros = build_coverage_matrix()
    ids = {p["phone_number"]: p["phone_number"] for p in pros}
    monkeypatch.setattr(seed_module, "leads_collection", mock_db.leads)

    await seed_module.inject_load(pros, ids, "load-balance")

    docs = await mock_db.leads.find({}).to_list(100)
    loaded_pro_ids = {d["pro_id"] for d in docs}

    s01 = [p for p in pros if p["seed_scenario"] == "S01"]
    top_three = sorted(s01, key=lambda p: p["social_proof"]["rating"], reverse=True)[:3]
    expected_ids = {p["phone_number"] for p in top_three}

    assert loaded_pro_ids == expected_ids
    assert len(docs) == 3 * WorkerConstants.MAX_PRO_LOAD
    assert all(d["status"] == LeadStatus.BOOKED for d in docs)
    assert all("load-balance" in d["issue_type"] for d in docs)

    lowest_two = {
        p["phone_number"]
        for p in sorted(s01, key=lambda p: p["social_proof"]["rating"])[:2]
    }
    assert not (
        loaded_pro_ids & lowest_two
    ), "the two worst-rated S01 pros must stay untouched"


@pytest.mark.asyncio
async def test_inject_overload_shfela_targets_all_four_s04_pros(mock_db, monkeypatch):
    import scripts.seed_coverage_matrix as seed_module

    pros = build_coverage_matrix()
    ids = {p["phone_number"]: p["phone_number"] for p in pros}
    monkeypatch.setattr(seed_module, "leads_collection", mock_db.leads)

    await seed_module.inject_load(pros, ids, "overload-shfela")

    docs = await mock_db.leads.find({}).to_list(100)
    loaded_pro_ids = {d["pro_id"] for d in docs}
    s04_phones = {p["phone_number"] for p in pros if p["seed_scenario"] == "S04"}

    assert len(s04_phones) == 4
    assert loaded_pro_ids == s04_phones
    assert len(docs) == 4 * WorkerConstants.MAX_PRO_LOAD
    assert all("overload-shfela" in d["issue_type"] for d in docs)


@pytest.mark.asyncio
async def test_inject_load_rejects_an_unknown_scenario_name():
    pros = build_coverage_matrix()
    import scripts.seed_coverage_matrix as seed_module

    with pytest.raises(SystemExit, match="Unknown scenario"):
        await seed_module.inject_load(pros, {}, "not-a-real-scenario")


# ===========================================================================
# seed_consent / seed_reviews / seed_slots — batch tagging and slot scoping
# ===========================================================================


@pytest.mark.asyncio
async def test_seed_consent_tags_every_document_with_the_batch(mock_db, monkeypatch):
    import scripts.seed_coverage_matrix as seed_module

    pros = build_coverage_matrix()
    monkeypatch.setattr(seed_module, "consent_collection", mock_db.consent)

    await seed_module.seed_consent(pros)

    docs = await mock_db.consent.find({}).to_list(100)
    assert len(docs) == len(pros)
    assert all(d["seed_batch"] == SEED_BATCH for d in docs)
    assert all(d["accepted"] is True for d in docs)
    assert {d["chat_id"] for d in docs} == {f"{p['phone_number']}@c.us" for p in pros}


@pytest.mark.asyncio
async def test_seed_reviews_tags_every_document_with_the_batch(mock_db, monkeypatch):
    import scripts.seed_coverage_matrix as seed_module

    pros = build_coverage_matrix()
    ids = {p["phone_number"]: p["phone_number"] for p in pros}
    monkeypatch.setattr(seed_module, "reviews_collection", mock_db.reviews)

    await seed_module.seed_reviews(pros, ids)

    docs = await mock_db.reviews.find({}).to_list(1000)
    assert len(docs) == len(pros) * REVIEW_COUNT
    assert all(d["seed_batch"] == SEED_BATCH for d in docs)
    assert all(1 <= d["rating"] <= 5 for d in docs)


@pytest.mark.asyncio
async def test_seed_reviews_rerun_does_not_duplicate_documents(mock_db, monkeypatch):
    """`seed_reviews` deletes its own batch before inserting, so a re-run must not
    double the review count."""
    import scripts.seed_coverage_matrix as seed_module

    pros = build_coverage_matrix()
    ids = {p["phone_number"]: p["phone_number"] for p in pros}
    monkeypatch.setattr(seed_module, "reviews_collection", mock_db.reviews)

    await seed_module.seed_reviews(pros, ids)
    await seed_module.seed_reviews(pros, ids)

    assert await mock_db.reviews.count_documents({}) == len(pros) * REVIEW_COUNT


@pytest.mark.asyncio
async def test_seed_slots_tags_every_document_and_skips_non_slotted_scenarios(
    mock_db, monkeypatch
):
    import scripts.seed_coverage_matrix as seed_module

    pros = build_coverage_matrix()
    ids = {p["phone_number"]: p["phone_number"] for p in pros}
    monkeypatch.setattr(seed_module, "slots_collection", mock_db.slots)

    await seed_module.seed_slots(pros, ids)

    docs = await mock_db.slots.find({}).to_list(10000)
    assert docs, "expected slots for the S01/S04 pros"
    assert all(d["seed_batch"] == SEED_BATCH for d in docs)

    slotted_phones = {
        p["phone_number"] for p in pros if p["seed_scenario"] in SLOTTED_SCENARIOS
    }
    unslotted_phones = {
        p["phone_number"] for p in pros if p["seed_scenario"] not in SLOTTED_SCENARIOS
    }
    slotted_pro_ids = {d["pro_id"] for d in docs}

    assert slotted_pro_ids == slotted_phones
    assert not (slotted_pro_ids & unslotted_phones), "a non-S01/S04 pro got slots"
    # 7 days x 10 hourly slots (08:00-17:00 inclusive-start) per slotted pro.
    assert len(docs) == len(slotted_phones) * 7 * 10


# ===========================================================================
# upsert_pros — idempotent re-run
# ===========================================================================


@pytest.mark.asyncio
async def test_upsert_pros_rerun_does_not_duplicate_users(mock_db, monkeypatch):
    """A stray extra field, or an insert instead of an in-place update, turns 27
    professionals into 54 the second time the seed script runs."""
    import scripts.seed_coverage_matrix as seed_module

    pros = build_coverage_matrix()
    monkeypatch.setattr(seed_module, "users_collection", mock_db.users)

    await seed_module.upsert_pros(pros)
    await seed_module.upsert_pros(pros)

    assert await mock_db.users.count_documents({}) == len(pros) == 27


# ===========================================================================
# main() — argument parsing
# ===========================================================================


@pytest.mark.asyncio
async def test_main_purge_short_circuits_before_any_seeding_step(monkeypatch):
    """--purge must call purge() and return immediately — none of the seeding
    functions (or the geo-index guard) may run."""
    from unittest.mock import AsyncMock

    import scripts.seed_coverage_matrix as seed_module

    monkeypatch.setattr(seed_module.settings, "ENVIRONMENT", "staging")
    monkeypatch.setattr(seed_module, "DB_NAME", STAGING_DB_NAME)
    monkeypatch.setattr(
        seed_module, "build_provider", lambda: _StubProvider(transmits=False)
    )

    purge_mock = AsyncMock()
    assert_geo_index_mock = AsyncMock()
    assert_no_foreign_pros_mock = AsyncMock()
    upsert_pros_mock = AsyncMock(return_value={})
    seed_consent_mock = AsyncMock()
    seed_reviews_mock = AsyncMock()
    seed_slots_mock = AsyncMock()
    inject_load_mock = AsyncMock()

    monkeypatch.setattr(seed_module, "purge", purge_mock)
    monkeypatch.setattr(seed_module, "assert_geo_index", assert_geo_index_mock)
    monkeypatch.setattr(
        seed_module, "assert_no_foreign_pros", assert_no_foreign_pros_mock
    )
    monkeypatch.setattr(seed_module, "upsert_pros", upsert_pros_mock)
    monkeypatch.setattr(seed_module, "seed_consent", seed_consent_mock)
    monkeypatch.setattr(seed_module, "seed_reviews", seed_reviews_mock)
    monkeypatch.setattr(seed_module, "seed_slots", seed_slots_mock)
    monkeypatch.setattr(seed_module, "inject_load", inject_load_mock)

    await seed_module.main(["--purge"])

    purge_mock.assert_awaited_once()
    assert_geo_index_mock.assert_not_awaited()
    assert_no_foreign_pros_mock.assert_not_awaited()
    upsert_pros_mock.assert_not_awaited()
    seed_consent_mock.assert_not_awaited()
    seed_reviews_mock.assert_not_awaited()
    seed_slots_mock.assert_not_awaited()
    inject_load_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_main_scenario_flag_is_repeatable_and_dispatched_in_order(monkeypatch):
    from unittest.mock import AsyncMock

    import scripts.seed_coverage_matrix as seed_module

    monkeypatch.setattr(seed_module.settings, "ENVIRONMENT", "staging")
    monkeypatch.setattr(seed_module, "DB_NAME", STAGING_DB_NAME)
    monkeypatch.setattr(
        seed_module, "build_provider", lambda: _StubProvider(transmits=False)
    )

    monkeypatch.setattr(seed_module, "assert_geo_index", AsyncMock())
    # Also stubbed: it counts untagged professionals, which would otherwise reach
    # the real MongoDB this suite never talks to.
    monkeypatch.setattr(seed_module, "assert_no_foreign_pros", AsyncMock())
    monkeypatch.setattr(seed_module, "upsert_pros", AsyncMock(return_value={}))
    monkeypatch.setattr(seed_module, "seed_consent", AsyncMock())
    monkeypatch.setattr(seed_module, "seed_reviews", AsyncMock())
    monkeypatch.setattr(seed_module, "seed_slots", AsyncMock())
    inject_load_mock = AsyncMock()
    monkeypatch.setattr(seed_module, "inject_load", inject_load_mock)

    class _FakeLeadsCollection:
        async def delete_many(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(seed_module, "leads_collection", _FakeLeadsCollection())

    await seed_module.main(
        ["--scenario", "load-balance", "--scenario", "overload-shfela"]
    )

    assert inject_load_mock.await_count == 2
    called_scenarios = [call.args[2] for call in inject_load_mock.await_args_list]
    assert called_scenarios == ["load-balance", "overload-shfela"]


@pytest.mark.asyncio
async def test_main_scenario_flag_rejects_an_unknown_scenario_name():
    """argparse `choices` validates before any of the seeding guards run — this
    must fail even without any of the safety/DB monkeypatching the other main()
    tests need."""
    import scripts.seed_coverage_matrix as seed_module

    with pytest.raises(SystemExit):
        await seed_module.main(["--scenario", "not-a-real-scenario"])
