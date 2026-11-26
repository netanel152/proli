"""`scripts/prepare_staging_e2e.py` — the staging-only E2E reset-and-seed.

The first half is the guard, because the guard is the point: a script that resets
chats and deletes leads must be structurally unable to run against production,
whatever `ENVIRONMENT` says and whichever secret the workflow resolved. The second
half runs the real script against mongomock and fakeredis and asserts what a
tester would find on their handsets: an approved handset pro, an extra pro behind
it in the ranking, a clean slate for both numbers, and nothing written on a dry run.
"""

import ast
import re
from datetime import datetime, timedelta
from pathlib import Path

import mongomock
import pytest
import pytest_asyncio
from mongomock_motor import AsyncMongoMockClient
from pymongo import ASCENDING

import admin_panel.core.utils as panel_utils
import app.services.data_management_service as data_management
import app.services.matching_service as matching_service
import app.services.pro_onboarding_service as onboarding
import app.services.scheduling_service as scheduling_service
import scripts.prepare_staging_e2e as prep
from app.core.constants import ISRAEL_CITIES_COORDS, UserStates
from app.core.pro_trades import LISTABLE_TRADES
from app.services.context_manager_service import ContextManager
from app.services.geocoding_service import ServiceAreaResolution
from app.services.state_manager_service import StateManager
from scripts.seed_coverage_matrix import (
    PHONE_BLOCK_END,
    STAGING_DB_NAME as MATRIX_STAGING_DB_NAME,
)
from tests.e2e.geo_shim import GeoAwareCollection

REPO_ROOT = Path(__file__).resolve().parent.parent
CUSTOMER = "97250" + "1111111"
PRO = "97250" + "2222222"
CITY = "תל אביב"
TRADE = "plumber"
SLOT_KEY = [("pro_id", ASCENDING), ("start_time", ASCENDING)]
REDIS_HOST = "staging-redis.internal.example"
REDIS_URL = f"redis://:pw@{REDIS_HOST}:6379/2"


# ===========================================================================
# The guard
# ===========================================================================


def test_the_staging_database_name_is_the_one_the_coverage_matrix_uses():
    assert prep.STAGING_DB_NAME == MATRIX_STAGING_DB_NAME == "proli_staging_db"


@pytest.mark.parametrize("environment", ["development", "staging"])
def test_the_staging_database_passes(environment):
    prep.assert_staging_target(environment, "proli_staging_db", "proli_staging_db")


@pytest.mark.parametrize(
    "db_name",
    ["proli_db", "proli_prod", "proli_production_db", "", "proli_staging_db2", "test"],
)
def test_any_other_database_is_refused(db_name):
    with pytest.raises(prep.Refused):
        prep.assert_staging_target("staging", db_name)


def test_both_connections_must_name_the_staging_database():
    """The async connection and the admin helper's sync one are separate clients
    built from separate settings; either one pointing elsewhere refuses."""
    with pytest.raises(prep.Refused):
        prep.assert_staging_target("staging", "proli_staging_db", "proli_db")
    with pytest.raises(prep.Refused):
        prep.assert_staging_target("staging", "proli_db", "proli_staging_db")


@pytest.mark.parametrize(
    "environment", ["production", "prod", "", "preview", "STAGING"]
)
def test_an_environment_off_the_allow_list_is_refused_even_on_the_staging_db(
    environment,
):
    """Fails closed: a value added to the vocabulary later is refused until
    someone adds it here on purpose."""
    with pytest.raises(prep.Refused):
        prep.assert_staging_target(environment, "proli_staging_db")


# ===========================================================================
# Inputs
# ===========================================================================


def test_inputs_come_back_in_the_form_the_bot_looks_them_up_by():
    customer, pro, city, trade = prep.normalize_inputs(
        "050-111-1111", "+972 50 222 2222", "  תל   אביב ", "plumber"
    )
    assert (customer, pro) == (CUSTOMER, PRO)
    assert city == "תל אביב"
    assert trade == "plumber"


@pytest.mark.parametrize(
    "customer, pro, city, trade",
    [
        ("12345", PRO, CITY, TRADE),
        (CUSTOMER, "not a number", CITY, TRADE),
        (CUSTOMER, CUSTOMER, CITY, TRADE),
        (CUSTOMER, PRO, "", TRADE),
        (CUSTOMER, PRO, "x" * 61, TRADE),
        (CUSTOMER, PRO, "תל אביב\n--apply", TRADE),
        (CUSTOMER, PRO, "תל אביב ```", TRADE),
        (CUSTOMER, PRO, "תל אביב\u200f", TRADE),
        (CUSTOMER, PRO, "תל אביב 1", TRADE),
        (CUSTOMER, PRO, "<b>תל אביב</b>", TRADE),
        (CUSTOMER, PRO, CITY, "general"),
        (CUSTOMER, PRO, CITY, "astronaut"),
        # A reserved number is not a handset, so it cannot be passed as one.
        (CUSTOMER, prep.NO_HANDSET_PHONE, CITY, TRADE),
    ],
)
def test_bad_input_is_refused(customer, pro, city, trade):
    with pytest.raises(prep.Refused):
        prep.normalize_inputs(customer, pro, city, trade)


def test_the_extra_pros_number_belongs_to_nobody():
    """`972` + `0…` is not a valid Israeli mobile; that is the whole safety
    argument for sending offers to it."""
    assert prep.NO_HANDSET_PHONE.startswith(prep.RESERVED_PREFIX)
    from app.core.phone import to_il_mobile

    assert to_il_mobile(prep.NO_HANDSET_PHONE) == ""
    # …and it stays out of the coverage matrix's block, so neither purge can
    # reach the other's rows.
    assert int(prep.NO_HANDSET_PHONE) > PHONE_BLOCK_END


def test_every_listable_trade_is_a_choice_the_script_accepts():
    for trade in LISTABLE_TRADES:
        assert prep.normalize_inputs(CUSTOMER, PRO, CITY, trade)[3] == trade


# ===========================================================================
# The run, against mongomock + fakeredis
# ===========================================================================


@pytest_asyncio.fixture
async def world(monkeypatch):
    """One mongomock store behind both the async and the sync handle, because the
    script reads through Motor and the panel's `create_initial_schedule` writes
    through PyMongo — in staging they are the same database too."""
    sync = mongomock.MongoClient()
    sync_db = sync[prep.STAGING_DB_NAME]
    sync_db.slots.create_index(SLOT_KEY, unique=True)
    db = AsyncMongoMockClient(mock_mongo_client=sync)[prep.STAGING_DB_NAME]

    for name in ("users", "leads", "messages", "slots", "consent"):
        monkeypatch.setattr(prep, f"{name}_collection", getattr(db, name))
    monkeypatch.setattr(onboarding, "users_collection", db.users)
    monkeypatch.setattr(data_management, "consent_collection", db.consent)
    monkeypatch.setattr(panel_utils, "slots_collection", sync_db.slots)

    async def resolver(areas):
        result = ServiceAreaResolution()
        for area in areas:
            if area in ISRAEL_CITIES_COORDS:
                lon, lat = ISRAEL_CITIES_COORDS[area]
                result.resolved.append((area, (lon, lat)))
            else:
                result.unresolved.append(area)
        return result

    return {"db": db, "resolver": resolver}


async def _go(world, **overrides):
    out = []
    kwargs = dict(
        customer_phone=CUSTOMER,
        pro_phone=PRO,
        city=CITY,
        trade=TRADE,
        apply=True,
        resolver=world["resolver"],
        environment="staging",
        db_names=("proli_staging_db", "proli_staging_db"),
        redis_url=REDIS_URL,
        out=out.append,
    )
    kwargs.update(overrides)
    code = await prep.run(**kwargs)
    return code, "\n".join(out)


async def _handset_and_extra(db):
    handset = await db.users.find_one({"phone_number": PRO})
    extra = await db.users.find_one({"phone_number": prep.NO_HANDSET_PHONE})
    return handset, extra


@pytest.mark.asyncio
async def test_a_refused_target_reads_and_writes_nothing(world):
    db = world["db"]
    await db.leads.insert_one({"chat_id": f"{CUSTOMER}@c.us", "status": "new"})

    code, text = await _go(world, db_names=("proli_db", "proli_db"))

    assert code == prep.EXIT_REFUSED
    assert "Refusing" in text
    assert await db.leads.count_documents({}) == 1
    assert await db.users.count_documents({}) == 0


@pytest.mark.asyncio
async def test_an_unreachable_redis_refuses_before_any_write(world, monkeypatch):
    class _Down:
        async def ping(self):
            raise ConnectionError("down")

    async def _client():
        return _Down()

    monkeypatch.setattr(prep, "get_redis_client", _client)
    db = world["db"]
    await db.leads.insert_one({"chat_id": f"{CUSTOMER}@c.us", "status": "new"})

    code, text = await _go(world)

    assert code == prep.EXIT_REFUSED
    assert "Redis is not reachable" in text
    assert await db.leads.count_documents({}) == 1


@pytest.mark.asyncio
async def test_a_dry_run_changes_nothing_and_says_so(world):
    db = world["db"]
    await db.leads.insert_one({"chat_id": f"{CUSTOMER}@c.us", "status": "new"})
    await db.messages.insert_one({"chat_id": f"{CUSTOMER}@c.us", "text": "hi"})
    await StateManager.set_state(f"{CUSTOMER}@c.us", UserStates.AWAITING_ADDRESS)
    await ContextManager.update_history(f"{CUSTOMER}@c.us", "user", "hi")

    code, text = await _go(world, apply=False)

    assert code == prep.EXIT_OK
    assert "Dry run: nothing was written" in text
    assert "leads:    1" in text  # the report says what it WOULD remove
    assert await db.leads.count_documents({}) == 1
    assert await db.messages.count_documents({}) == 1
    assert await db.users.count_documents({}) == 0
    assert await db.slots.count_documents({}) == 0
    assert await db.consent.count_documents({}) == 0
    assert await StateManager.get_state(f"{CUSTOMER}@c.us") == (
        UserStates.AWAITING_ADDRESS
    )
    assert await ContextManager.get_history(f"{CUSTOMER}@c.us")


@pytest.mark.asyncio
async def test_the_summary_never_prints_a_whole_phone_number(world):
    _, text = await _go(world)
    assert CUSTOMER not in text
    assert PRO not in text
    assert "2222" in text  # the masked tail is there
    assert REDIS_HOST not in text and ":pw@" not in text


@pytest.mark.asyncio
async def test_apply_resets_both_chats(world):
    db = world["db"]
    customer_chat, pro_chat = f"{CUSTOMER}@c.us", f"{PRO}@c.us"
    other_chat = "97250" + "9999999@c.us"
    await db.leads.insert_many(
        [
            {"chat_id": customer_chat, "status": "new"},
            {"chat_id": pro_chat, "status": "booked"},
            {"chat_id": other_chat, "status": "new"},
        ]
    )
    await db.messages.insert_many(
        [{"chat_id": customer_chat, "text": "a"}, {"chat_id": other_chat, "text": "b"}]
    )
    for chat in (customer_chat, pro_chat):
        await StateManager.set_state(chat, UserStates.AWAITING_ADDRESS)
        await StateManager.set_metadata(chat, {"k": "v"})
        await StateManager.hold_message(chat, "held")
        await ContextManager.update_history(chat, "user", "hi")
    await StateManager.set_state(other_chat, UserStates.AWAITING_ADDRESS)

    code, _ = await _go(world)

    assert code == prep.EXIT_OK
    left = [d["chat_id"] async for d in db.leads.find({})]
    assert left == [other_chat], "only the two handsets' chats are reset"
    assert [d["chat_id"] async for d in db.messages.find({})] == [other_chat]
    for chat in (customer_chat, pro_chat):
        assert await StateManager.get_state(chat) == UserStates.IDLE
        assert await StateManager.get_metadata(chat) == {}
        assert await StateManager.take_held_messages(chat) is None
        assert not await ContextManager.get_history(chat)
    assert await StateManager.get_state(other_chat) == UserStates.AWAITING_ADDRESS


@pytest.mark.asyncio
async def test_apply_leaves_the_customers_consent_alone(world):
    db = world["db"]
    await db.consent.insert_one({"chat_id": f"{CUSTOMER}@c.us", "accepted": True})

    await _go(world)

    assert await db.consent.count_documents({"chat_id": f"{CUSTOMER}@c.us"}) == 1
    assert await db.consent.count_documents({"chat_id": f"{PRO}@c.us"}) == 1


@pytest.mark.asyncio
async def test_apply_seeds_an_approved_handset_pro_and_an_extra_pro(world):
    db = world["db"]

    code, _ = await _go(world)
    handset, extra = await _handset_and_extra(db)

    assert code == prep.EXIT_OK
    for pro in (handset, extra):
        assert pro["role"] == "professional"
        assert pro["is_active"] is True
        assert pro["pending_approval"] is False
        assert pro["type"] == TRADE
        assert pro["categories"] == [TRADE]
        assert pro["service_areas"] == [CITY]
        assert pro["seed_batch"] == prep.E2E_BATCH
        assert pro["trades_set_at"]
        assert pro["system_prompt"]
        assert pro["location"] == {
            "type": "Point",
            "coordinates": list(ISRAEL_CITIES_COORDS[CITY]),
        }
    assert handset["social_proof"]["rating"] > extra["social_proof"]["rating"]
    assert extra["phone_number"] == prep.NO_HANDSET_PHONE
    assert handset["business_name"].startswith("[E2E]")


@pytest.mark.asyncio
async def test_each_pro_gets_a_week_of_free_slots(world):
    db = world["db"]
    await _go(world)
    handset, extra = await _handset_and_extra(db)

    horizon = datetime.utcnow() + timedelta(days=8)
    for pro in (handset, extra):
        slots = [s async for s in db.slots.find({"pro_id": pro["_id"]})]
        assert slots, "no calendar"
        assert all(s["is_taken"] is False for s in slots)
        assert all(s["start_time"] > datetime.utcnow() for s in slots)
        assert all(s["start_time"] < horizon for s in slots)


@pytest.mark.asyncio
async def test_the_handset_pro_skips_the_consent_gate(world):
    db = world["db"]
    await _go(world)
    record = await db.consent.find_one({"chat_id": f"{PRO}@c.us"})
    assert record["accepted"] is True
    assert record["seed_batch"] == prep.E2E_BATCH


@pytest.mark.asyncio
async def test_a_rerun_leaves_exactly_one_batch_and_clears_what_the_run_booked(world):
    db = world["db"]
    await _go(world)
    handset, _ = await _handset_and_extra(db)
    await db.leads.insert_one(
        {"chat_id": "97250" + "9999999@c.us", "pro_id": handset["_id"]}
    )
    await db.slots.update_many({"pro_id": handset["_id"]}, {"$set": {"is_taken": True}})

    code, _ = await _go(world)
    handset, extra = await _handset_and_extra(db)

    assert code == prep.EXIT_OK
    assert await db.users.count_documents({}) == 2
    assert await db.leads.count_documents({}) == 0, "a lead held by an E2E pro goes"
    assert await db.slots.count_documents({"is_taken": True}) == 0
    assert await db.consent.count_documents({"chat_id": f"{PRO}@c.us"}) == 1


@pytest.mark.asyncio
async def test_changing_the_pro_phone_removes_the_old_handset(world):
    db = world["db"]
    await _go(world)
    new_pro = "97250" + "3333333"

    await _go(world, pro_phone=new_pro)

    phones = {d["phone_number"] async for d in db.users.find({})}
    assert phones == {new_pro, prep.NO_HANDSET_PHONE}


@pytest.mark.asyncio
async def test_a_foreign_approved_pro_on_the_phone_is_never_touched(world):
    db = world["db"]
    await db.users.insert_one(
        {
            "phone_number": PRO,
            "role": "professional",
            "business_name": "real pro",
            "pending_approval": False,
        }
    )
    await db.leads.insert_one({"chat_id": f"{CUSTOMER}@c.us"})

    code, text = await _go(world)

    assert code == prep.EXIT_REFUSED
    assert "never overwritten" in text
    assert await db.users.count_documents({}) == 1
    assert (await db.users.find_one({}))["business_name"] == "real pro"
    assert await db.leads.count_documents({}) == 1


@pytest.mark.asyncio
async def test_an_unapproved_registration_on_the_phone_is_replaced(world):
    db = world["db"]
    await db.users.insert_one(
        {"phone_number": PRO, "role": "professional", "pending_approval": True}
    )

    code, _ = await _go(world)

    assert code == prep.EXIT_OK
    docs = [d async for d in db.users.find({"phone_number": PRO})]
    assert len(docs) == 1 and docs[0]["pending_approval"] is False


@pytest.mark.asyncio
async def test_an_unplaceable_city_writes_nothing(world):
    db = world["db"]
    await db.leads.insert_one({"chat_id": f"{CUSTOMER}@c.us"})

    code, text = await _go(world, city="עיר שלא קיימת")

    assert code == prep.EXIT_CITY_UNRESOLVED
    assert "could not be placed on the map" in text
    assert await db.users.count_documents({}) == 0
    assert await db.leads.count_documents({}) == 1


@pytest.mark.asyncio
async def test_a_geocoder_outage_is_not_approved_into_silence(world):
    async def down(areas):
        result = ServiceAreaResolution()
        result.unavailable.extend(areas)
        return result

    code, _ = await _go(world, resolver=down)

    assert code == prep.EXIT_CITY_UNRESOLVED
    assert await world["db"].users.count_documents({}) == 0


@pytest.mark.parametrize("trade", LISTABLE_TRADES)
@pytest.mark.asyncio
async def test_every_trade_is_approved_with_a_listed_trade(world, trade):
    await _go(world, trade=trade)
    handset, extra = await _handset_and_extra(world["db"])
    for pro in (handset, extra):
        assert pro["type"] == trade
        assert trade in pro["categories"]


# --- what the run is FOR: who the real routing engine picks ------------------


@pytest_asyncio.fixture
async def routed(world, monkeypatch):
    db = world["db"]
    await _go(world)

    async def fake_resolve(name):
        coords = ISRAEL_CITIES_COORDS.get((name or "").strip())
        return tuple(coords) if coords else None

    monkeypatch.setattr(
        matching_service, "users_collection", GeoAwareCollection(db.users)
    )
    monkeypatch.setattr(matching_service, "resolve_city_to_coords", fake_resolve)
    monkeypatch.setattr(matching_service, "leads_collection", db.leads)
    monkeypatch.setattr(scheduling_service, "slots_collection", db.slots)
    return db


@pytest.mark.asyncio
async def test_a_lead_in_the_city_is_offered_to_the_handset_pro_first(routed):
    handset, _ = await _handset_and_extra(routed)

    chosen = await matching_service.determine_best_pro(
        issue_type="תקלה בבית", location=CITY, require_trade=False
    )

    assert chosen["_id"] == handset["_id"]


@pytest.mark.asyncio
async def test_a_rejected_offer_goes_to_the_extra_pro(routed):
    handset, extra = await _handset_and_extra(routed)

    chosen = await matching_service.determine_best_pro(
        issue_type="תקלה בבית",
        location=CITY,
        excluded_pro_ids=[str(handset["_id"])],
        require_trade=False,
    )

    assert chosen["_id"] == extra["_id"]
    assert matching_service.trade_fits(extra, TRADE)


# ===========================================================================
# Drift: the script mirrors the panel's Approve click
# ===========================================================================


def _panel_approval_set_keys() -> set[str]:
    source = (REPO_ROOT / "admin_panel" / "views" / "professionals.py").read_text(
        encoding="utf-8"
    )
    func = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == "_approve_pending_pro"
    )
    keys = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Dict):
            literal = {k.value for k in node.keys if isinstance(k, ast.Constant)}
            if "is_active" in literal:
                keys |= literal
    return keys


def test_the_script_writes_every_field_the_panels_approve_click_writes():
    panel_keys = _panel_approval_set_keys()
    assert {"is_active", "pending_approval", "system_prompt"} <= panel_keys

    resolution = ServiceAreaResolution(resolved=[(CITY, (34.78, 32.08))])
    pro = {
        "business_name": "x",
        "type": TRADE,
        "service_areas": [CITY],
        "prices_for_prompt": "",
    }
    update = prep.approval_update(
        pro, resolution, rating=5.0, now=datetime(2026, 1, 1)
    )["$set"]

    assert panel_keys <= set(update), panel_keys - set(update)


def test_the_script_never_sends_the_approval_whatsapp():
    """The panel's approve click messages the pro; this run must not, because the
    pro is told nothing by a test fixture."""
    source = (REPO_ROOT / "scripts" / "prepare_staging_e2e.py").read_text(
        encoding="utf-8"
    )
    code = re.sub(r'""".*?"""', "", source, flags=re.S)
    for forbidden in ("_notify_pro_approved", "send_text_sync", "send_message("):
        assert forbidden not in code


# ===========================================================================
# Review findings: the wiring, Redis, swallowed failures, throttles
# ===========================================================================


@pytest.mark.asyncio
async def test_the_real_wiring_reads_the_connected_databases(world, monkeypatch):
    """No `db_names` override, so `run` reads `DB_NAME` and the panel helper's
    own handle. Hermetic: every collection is mongomock and the run is a dry run,
    so a developer `.env` that points at staging cannot be written to by this."""
    out = []
    kwargs = dict(
        customer_phone=CUSTOMER,
        pro_phone=PRO,
        city=CITY,
        trade=TRADE,
        apply=False,
        resolver=world["resolver"],
        environment="staging",
        redis_url=REDIS_URL,
        out=out.append,
    )
    monkeypatch.setattr(prep, "DB_NAME", "proli_db")
    monkeypatch.setattr(
        panel_utils, "db", mongomock.MongoClient()[prep.STAGING_DB_NAME]
    )
    assert await prep.run(**kwargs) == prep.EXIT_REFUSED
    assert "proli_db" in "\n".join(out)

    monkeypatch.setattr(prep, "DB_NAME", prep.STAGING_DB_NAME)
    monkeypatch.setattr(panel_utils, "db", mongomock.MongoClient()["proli_db"])
    assert await prep.run(**kwargs) == prep.EXIT_REFUSED

    monkeypatch.setattr(
        panel_utils, "db", mongomock.MongoClient()[prep.STAGING_DB_NAME]
    )
    assert await prep.run(**kwargs) == prep.EXIT_OK


@pytest.mark.parametrize(
    "url",
    [
        "",
        "redis://localhost:6379/0",
        "redis://127.0.0.1:6379/0",
        "redis://[::1]:6379/0",
        "redis://:pw@LOCALHOST:6379",
    ],
)
def test_a_loopback_or_unset_redis_is_not_staging(url):
    assert prep.redis_target_problem(url)


def test_a_remote_redis_passes():
    assert prep.redis_target_problem(REDIS_URL) is None


@pytest.mark.asyncio
async def test_a_loopback_redis_refuses_before_any_write(world):
    db = world["db"]
    await db.leads.insert_one({"chat_id": f"{CUSTOMER}@c.us"})

    code, text = await _go(world, redis_url="redis://localhost:6379/0")

    assert code == prep.EXIT_REFUSED
    assert "not at staging's Redis" in text
    assert await db.leads.count_documents({}) == 1
    assert await db.users.count_documents({}) == 0


@pytest.mark.asyncio
async def test_unset_redis_settings_refuse_rather_than_crash(world, monkeypatch):
    monkeypatch.setattr(prep.settings, "REDIS_URL", None)
    code, _ = await _go(world, redis_url=None)
    assert code == prep.EXIT_REFUSED


@pytest.mark.asyncio
async def test_a_redis_clear_that_is_swallowed_does_not_report_success(
    world, monkeypatch
):
    """`StateManager` turns a failed DEL into a log line; the read-back catches it."""

    async def swallowed(chat_id):
        return None

    monkeypatch.setattr(prep.StateManager, "clear_state", swallowed)
    await StateManager.set_state(f"{CUSTOMER}@c.us", UserStates.AWAITING_ADDRESS)

    code, text = await _go(world)

    assert code == prep.EXIT_INCOMPLETE
    assert "Redis still holds chat state" in text
    assert "✅" not in text


@pytest.mark.asyncio
async def test_a_pro_left_without_slots_does_not_report_success(world, monkeypatch):
    monkeypatch.setattr(prep, "create_initial_schedule", lambda pro_id: None)

    code, text = await _go(world)

    assert code == prep.EXIT_INCOMPLETE
    assert "has no calendar slots" in text
    assert "✅" not in text


@pytest.mark.asyncio
async def test_a_failure_part_way_says_so_and_a_rerun_heals_it(world, monkeypatch):
    real = prep._create_pending_pro
    calls = {"n": 0}

    async def flaky(chat_id, data):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("boom")
        return await real(chat_id, data)

    monkeypatch.setattr(prep, "_create_pending_pro", flaky)
    code, text = await _go(world)
    assert code == prep.EXIT_INCOMPLETE
    assert "stopped part-way" in text and "safe to run it again" in text
    assert "boom" not in text  # the exception type, never its message

    monkeypatch.setattr(prep, "_create_pending_pro", real)
    code, _ = await _go(world)
    assert code == prep.EXIT_OK
    assert await world["db"].users.count_documents({}) == 2


@pytest.mark.asyncio
async def test_the_throttle_counters_are_reset_but_the_service_window_is_not(world):
    from app.core.redis_client import get_redis_client

    redis = await get_redis_client()
    for chat in (f"{CUSTOMER}@c.us", f"{PRO}@c.us"):
        await redis.set(f"rate_limit:{chat}", "9")
        await redis.set(f"rl:inbound:{chat}", "9")
        await redis.set(f"ai:daily:{chat}:2026-11-26", "40")
        await redis.set(f"wa:window:{chat}", "open")
    other = "97250" + "9999999@c.us"
    await redis.set(f"ai:daily:{other}:2026-11-26", "40")

    code, _ = await _go(world)

    assert code == prep.EXIT_OK
    for chat in (f"{CUSTOMER}@c.us", f"{PRO}@c.us"):
        assert not await redis.exists(f"rate_limit:{chat}")
        assert not await redis.exists(f"rl:inbound:{chat}")
        assert not await redis.exists(f"ai:daily:{chat}:2026-11-26")
        assert await redis.exists(f"wa:window:{chat}"), "mirrors Meta's window"
    assert await redis.exists(f"ai:daily:{other}:2026-11-26")


@pytest.mark.asyncio
async def test_the_consent_record_is_the_apps_own_write_plus_the_batch_tag(world):
    await _go(world)
    record = await world["db"].consent.find_one({"chat_id": f"{PRO}@c.us"})
    assert record["accepted"] is True
    assert record["seed_batch"] == prep.E2E_BATCH
    assert record["timestamp"]
