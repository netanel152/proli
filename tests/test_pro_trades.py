"""PRO-234 — a pro's trades as an explicit list.

Covers the shared helpers in ``app/core/pro_trades.py`` and
``scripts/backfill_pro_trades.py`` (``plan_update`` is pure; ``run`` follows
``scripts/normalize_pro_phones.py`` and is driven against the shared async
``mock_db``, the script binding ``users_collection`` at import, so it is
monkeypatched directly as in tests/test_pro_phone_normalisation.py).

The routing side of the change lives in tests/test_matching_service.py and
tests/e2e/test_e2e_wrong_trade.py; the admin form's source pins in
tests/test_admin_set_pro.py.
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from bson import ObjectId

import scripts.backfill_pro_trades as script
from app.core.messages import Messages
from app.core.pro_trades import (
    LISTABLE_TRADES,
    PROMPT_TRADES_MARKER,
    categories_for_save,
    legacy_categories,
    needs_listed_trade,
    trades_line,
    upsert_trades_line,
)

NOW = datetime(2026, 11, 14, 9, 0, tzinfo=timezone.utc)
TYPE_LABELS = Messages.Onboarding.TYPE_LABELS


# --- categories_for_save / needs_listed_trade ---


@pytest.mark.parametrize(
    "ptype,selected,expected",
    [
        # Type first, even when it is also selected later; duplicates collapse.
        ("handyman", ["plumber", "handyman", "plumber"], ["handyman", "plumber"]),
        # Unknown codes and "general" (a pro type, never a lead's trade) are dropped.
        ("handyman", ["bogus", "general", "locksmith"], ["handyman", "locksmith"]),
        ("general", ["electrician"], ["general", "electrician"]),
        ("plumber", [], ["plumber"]),
        # Selection order is kept.
        ("handyman", ["painter", "cleaner"], ["handyman", "painter", "cleaner"]),
    ],
)
def test_categories_for_save_keeps_the_type_first_and_only_real_trades(
    ptype, selected, expected
):
    assert categories_for_save(ptype, selected) == expected


@pytest.mark.parametrize(
    "ptype,selected,needs",
    [
        ("handyman", [], True),
        ("general", [], True),
        # Nothing listable selected is the same as nothing selected.
        ("general", ["general", "bogus"], True),
        ("handyman", ["plumber"], False),
        ("general", ["cleaner"], False),
        # Every other type names its own work.
        ("plumber", [], False),
        ("electrician", [], False),
    ],
)
def test_needs_listed_trade_only_for_handyman_and_general_without_a_trade(
    ptype, selected, needs
):
    assert needs_listed_trade(ptype, selected) is needs


# --- legacy_categories ---


@pytest.mark.parametrize("ptype", ["handyman", "general"])
@pytest.mark.parametrize("flag", [True, False, None])
def test_legacy_categories_writes_the_old_fits_everything_rule_out(ptype, flag):
    """Every trade except electrical, plus electrical only where the flag was set."""
    pro = {"type": ptype}
    if flag is not None:
        pro["handles_electrical"] = flag

    cats = legacy_categories(pro)

    assert cats[0] == ptype
    assert ("electrician" in cats) is bool(flag)
    assert cats.count(ptype) == 1
    everything_else = [t for t in LISTABLE_TRADES if t != "electrician"]
    assert set(everything_else) <= set(cats)


# --- trades_line / upsert_trades_line ---


def test_trades_line_names_listable_trades_in_hebrew_and_skips_the_rest():
    assert trades_line(["general", "plumber", "bogus", "cleaner"]) == (
        f"{PROMPT_TRADES_MARKER}: {TYPE_LABELS['plumber']}, {TYPE_LABELS['cleaner']}"
    )
    assert trades_line(["general", "bogus"]) is None


def test_upsert_replaces_the_existing_trades_line_and_touches_nothing_else():
    old = trades_line(["cleaner"])
    prompt = f"פתיח\n{old}\nאזורי שירות: תל אביב"

    out = upsert_trades_line(prompt, ["plumber", "electrician"])

    new = trades_line(["plumber", "electrician"])
    assert out == f"פתיח\n{new}\nאזורי שירות: תל אביב"
    assert out.count(PROMPT_TRADES_MARKER) == 1


@pytest.mark.parametrize("prompt", ["פתיח", "פתיח\n", "פתיח\n\n"])
def test_upsert_appends_a_trades_line_when_the_prompt_has_none(prompt):
    assert upsert_trades_line(prompt, ["plumber"]) == (
        f"פתיח\n{trades_line(['plumber'])}"
    )


@pytest.mark.parametrize("prompt", ["", "   \n"])
def test_upsert_leaves_an_empty_prompt_empty(prompt):
    """The runtime falls back to the default persona on an empty prompt, and a lone
    trades line would replace it with nothing useful."""
    assert upsert_trades_line(prompt, ["plumber"]) == prompt


def test_upsert_with_no_listable_trade_returns_the_prompt_unchanged():
    prompt = f"פתיח\n{trades_line(['cleaner'])}"

    assert upsert_trades_line(prompt, ["general", "bogus"]) == prompt


# --- backfill: plan_update ---


def _pro(**fields):
    return {"_id": ObjectId(), "role": "professional", **fields}


@pytest.mark.parametrize("ptype", ["handyman", "general"])
def test_plan_expands_a_handyman_or_general_pro_that_was_never_set(ptype):
    pro = _pro(type=ptype, system_prompt="פתיח")

    plan = script.plan_update(pro, NOW)

    cats = legacy_categories(pro)
    assert plan["$set"]["categories"] == cats
    assert plan["$set"]["trades_set_at"] == NOW
    assert plan["$set"]["system_prompt"] == upsert_trades_line("פתיח", cats)
    assert "$unset" not in plan  # no flag on this pro


def test_plan_carries_the_electrical_flag_into_the_list_and_unsets_it():
    pro = _pro(type="general", handles_electrical=True, system_prompt="")

    plan = script.plan_update(pro, NOW)

    assert "electrician" in plan["$set"]["categories"]
    assert plan["$unset"] == {"handles_electrical": ""}
    # An empty prompt stays empty: the runtime default persona applies.
    assert "system_prompt" not in plan["$set"]


def test_plan_never_re_expands_a_list_that_was_already_set():
    """The admin form stamps `trades_set_at`, so a re-run cannot undo a trim."""
    pro = _pro(
        type="handyman",
        categories=["handyman", "plumber"],
        trades_set_at=NOW,
        handles_electrical=True,
    )

    plan = script.plan_update(pro, NOW)

    assert plan == {"$unset": {"handles_electrical": ""}}


@pytest.mark.parametrize(
    "pro",
    [
        {"type": "plumber", "categories": ["plumber"], "handles_electrical": False},
        {"type": "electrician", "handles_electrical": True},
        {"type": "handyman", "trades_set_at": NOW, "handles_electrical": False},
    ],
)
def test_plan_unsets_the_electrical_flag_on_any_pro_that_has_it(pro):
    plan = script.plan_update(_pro(**pro), NOW)

    assert plan == {"$unset": {"handles_electrical": ""}}


@pytest.mark.parametrize(
    "pro",
    [
        {"type": "plumber", "categories": ["plumber"]},
        {"type": "handyman", "categories": ["handyman"], "trades_set_at": NOW},
    ],
)
def test_plan_leaves_a_pro_with_nothing_to_do_alone(pro):
    assert script.plan_update(_pro(**pro), NOW) is None


# --- backfill: run ---


@pytest.fixture
def script_users(monkeypatch, mock_db):
    monkeypatch.setattr(script, "users_collection", mock_db.users)
    return mock_db.users


@pytest_asyncio.fixture(autouse=True)
async def _clean_users(mock_db):
    await mock_db.users.delete_many({})
    yield
    await mock_db.users.delete_many({})


@pytest.mark.asyncio
async def test_run_dry_run_reports_and_writes_nothing(script_users):
    handyman = _pro(type="handyman", handles_electrical=True, system_prompt="פתיח")
    await script_users.insert_one(handyman)

    assert await script.run(apply=False) == script.EXIT_NEEDS_FIX

    stored = await script_users.find_one({"_id": handyman["_id"]})
    assert stored["handles_electrical"] is True
    assert "trades_set_at" not in stored
    assert "categories" not in stored


@pytest.mark.asyncio
async def test_run_apply_writes_the_list_once_and_a_rerun_finds_nothing(script_users):
    handyman = _pro(type="handyman", handles_electrical=True, system_prompt="פתיח")
    plumber = _pro(type="plumber", categories=["plumber"])
    await script_users.insert_many([handyman, plumber])

    assert await script.run(apply=True) == script.EXIT_OK

    stored = await script_users.find_one({"_id": handyman["_id"]})
    assert stored["categories"] == legacy_categories(handyman)
    assert "electrician" in stored["categories"]
    assert "handles_electrical" not in stored
    assert stored["trades_set_at"]
    assert PROMPT_TRADES_MARKER in stored["system_prompt"]
    assert (await script_users.find_one({"_id": plumber["_id"]})) == plumber
    assert await script.run(apply=False) == script.EXIT_OK


@pytest.mark.asyncio
async def test_run_with_no_pros_checks_nothing(script_users):
    assert await script.run(apply=False) == script.EXIT_NOTHING_CHECKED
