"""
PRO-27 — scripts/check_pro_service_areas.py, the backfill/audit script that
proves every approved pro's `service_areas` can actually be placed on the map.

`run()` is exercised against `mock_db.users` (module-scoped mongomock, per
conftest's `mock_db` fixture) with a fake async `resolver` standing in for
`resolve_service_areas` — no Redis, no network. The module binds
`users_collection` from `app.core.database` at import time, so the shared
autouse patch in conftest (which patches the *database* module, not this
script's already-bound name) does not reach it; each test monkeypatches
`script.users_collection` directly, per the module's own convention (see
tests/test_backup_script.py).
"""

from bson import ObjectId

import pytest
import pytest_asyncio

import scripts.check_pro_service_areas as script
from app.services.geocoding_service import (
    SERVICE_AREAS_CHECKED_AT_FIELD,
    SERVICE_AREAS_GEOCODE_PENDING_FIELD,
    SERVICE_AREAS_UNRESOLVED_FIELD,
    ServiceAreaResolution,
)

TLV = ("Tel Aviv", (34.7818, 32.0853))


@pytest.fixture(autouse=True)
def _bind_script_to_mock_db(monkeypatch, mock_db):
    """Point the script's already-bound `users_collection` name at the shared
    mongomock db, and start each test with a clean `users` collection —
    `mock_db` is module-scoped, so documents would otherwise leak between
    tests in this file."""
    monkeypatch.setattr(script, "users_collection", mock_db.users)
    return mock_db


@pytest_asyncio.fixture(autouse=True)
async def _clean_users(mock_db):
    await mock_db.users.delete_many({})
    yield


def _resolver_from(mapping):
    """Build a fake async resolver: service_areas tuple -> ServiceAreaResolution."""

    async def resolver(areas):
        return mapping[tuple(areas)]

    return resolver


def _pro(**overrides):
    doc = {
        "_id": ObjectId(),
        "role": "professional",
        "is_active": True,
        "business_name": "Pro",
        "service_areas": [],
    }
    doc.update(overrides)
    return doc


# ---------------------------------------------------------------------------
# verdict — one word per pro, table-driven
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "resolution,has_location,expected",
    [
        (ServiceAreaResolution(resolved=[TLV]), True, "OK"),
        (ServiceAreaResolution(resolved=[TLV]), False, "MISSING_LOCATION"),
        (
            ServiceAreaResolution(resolved=[TLV], unresolved=["גיבריש"]),
            True,
            "PARTIAL",
        ),
        (ServiceAreaResolution(unresolved=["גיבריש"]), False, "UNREACHABLE"),
        (ServiceAreaResolution(), False, "UNREACHABLE"),  # no service areas at all
        (ServiceAreaResolution(unavailable=["ראש העין"]), False, "UNAVAILABLE"),
    ],
    ids=[
        "ok",
        "missing-location",
        "partial",
        "unreachable-junk-areas",
        "unreachable-no-areas",
        "unavailable",
    ],
)
def test_verdict(resolution, has_location, expected):
    pro = {"location": {"coordinates": [1, 2]}} if has_location else {}
    assert script.verdict(pro, resolution) == expected


# ---------------------------------------------------------------------------
# run() — dry run vs --apply, and the exit codes that gate the checklist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_dry_run_writes_nothing_and_returns_unresolved_exit(mock_db):
    await mock_db.users.insert_one(_pro(business_name="No Areas Pro"))
    resolver = _resolver_from({(): ServiceAreaResolution()})

    code = await script.run(
        apply=False, include_inactive=False, overwrite_location=False, resolver=resolver
    )

    assert code == script.EXIT_UNRESOLVED
    doc = await mock_db.users.find_one({"business_name": "No Areas Pro"})
    # mongo_update always stamps checked_at, even when nothing else changes —
    # its absence is the real proof a dry run performs no update_one at all.
    assert SERVICE_AREAS_CHECKED_AT_FIELD not in doc
    assert SERVICE_AREAS_UNRESOLVED_FIELD not in doc
    assert SERVICE_AREAS_GEOCODE_PENDING_FIELD not in doc
    assert "location" not in doc


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "existing_location,overwrite_location,expect_written",
    [
        (None, False, True),
        ({"type": "Point", "coordinates": [1.0, 2.0]}, False, False),
        ({"type": "Point", "coordinates": [1.0, 2.0]}, True, True),
    ],
    ids=[
        "missing-location-gets-set",
        "existing-location-kept-without-overwrite",
        "existing-location-replaced-with-overwrite",
    ],
)
async def test_run_apply_writes_location_only_when_missing_or_overwritten(
    mock_db, existing_location, overwrite_location, expect_written
):
    doc = _pro(business_name="Clean Pro", service_areas=["Tel Aviv"])
    if existing_location is not None:
        doc["location"] = existing_location
    await mock_db.users.insert_one(doc)
    resolver = _resolver_from({("Tel Aviv",): ServiceAreaResolution(resolved=[TLV])})

    code = await script.run(
        apply=True,
        include_inactive=False,
        overwrite_location=overwrite_location,
        resolver=resolver,
    )

    assert code == script.EXIT_OK
    updated = await mock_db.users.find_one({"business_name": "Clean Pro"})
    if expect_written:
        assert updated["location"] == {
            "type": "Point",
            "coordinates": [34.7818, 32.0853],
        }
    else:
        assert updated["location"] == existing_location


@pytest.mark.asyncio
async def test_run_apply_sets_unresolved_field_on_partial_pro(mock_db):
    await mock_db.users.insert_one(
        _pro(
            business_name="Partial Pro",
            service_areas=["Tel Aviv", "גיבריש"],
            location={"type": "Point", "coordinates": [34.7818, 32.0853]},
        )
    )
    resolver = _resolver_from(
        {
            ("Tel Aviv", "גיבריש"): ServiceAreaResolution(
                resolved=[TLV], unresolved=["גיבריש"]
            )
        }
    )

    code = await script.run(
        apply=True, include_inactive=False, overwrite_location=False, resolver=resolver
    )

    assert code == script.EXIT_UNRESOLVED
    updated = await mock_db.users.find_one({"business_name": "Partial Pro"})
    assert updated[SERVICE_AREAS_UNRESOLVED_FIELD] == ["גיבריש"]


@pytest.mark.asyncio
async def test_run_apply_sets_pending_flag_and_clears_stale_fields(mock_db):
    """Two pros in one run: an UNAVAILABLE one earns the pending flag, and a
    now-clean one that previously carried both verdict fields loses them."""
    await mock_db.users.insert_one(
        _pro(business_name="Down Pro", service_areas=["ראש העין"])
    )
    await mock_db.users.insert_one(
        _pro(
            business_name="Now Clean Pro",
            service_areas=["Tel Aviv"],
            location={"type": "Point", "coordinates": [34.7818, 32.0853]},
            **{
                SERVICE_AREAS_UNRESOLVED_FIELD: ["stale"],
                SERVICE_AREAS_GEOCODE_PENDING_FIELD: True,
            },
        )
    )
    resolver = _resolver_from(
        {
            ("ראש העין",): ServiceAreaResolution(unavailable=["ראש העין"]),
            ("Tel Aviv",): ServiceAreaResolution(resolved=[TLV]),
        }
    )

    code = await script.run(
        apply=True, include_inactive=False, overwrite_location=False, resolver=resolver
    )

    assert code == script.EXIT_UNAVAILABLE
    down = await mock_db.users.find_one({"business_name": "Down Pro"})
    assert down[SERVICE_AREAS_GEOCODE_PENDING_FIELD] is True

    clean = await mock_db.users.find_one({"business_name": "Now Clean Pro"})
    assert SERVICE_AREAS_UNRESOLVED_FIELD not in clean
    assert SERVICE_AREAS_GEOCODE_PENDING_FIELD not in clean


@pytest.mark.asyncio
async def test_run_returns_ok_when_every_pro_is_clean_and_located(mock_db):
    await mock_db.users.insert_one(
        _pro(
            business_name="Clean Pro",
            service_areas=["Tel Aviv"],
            location={"type": "Point", "coordinates": [34.7818, 32.0853]},
        )
    )
    resolver = _resolver_from({("Tel Aviv",): ServiceAreaResolution(resolved=[TLV])})

    code = await script.run(
        apply=False, include_inactive=False, overwrite_location=False, resolver=resolver
    )

    assert code == script.EXIT_OK


@pytest.mark.asyncio
async def test_run_include_inactive_flag_controls_which_pros_are_checked(mock_db):
    """pro_filter excludes is_active=False by default (routing's own base
    filter) and always excludes pending_approval=True, even with the flag —
    a pro still mid-onboarding was never a routing target to begin with."""
    await mock_db.users.insert_one(
        _pro(
            business_name="Paused Pro",
            is_active=False,
            service_areas=["Tel Aviv"],
            location={"type": "Point", "coordinates": [34.7818, 32.0853]},
        )
    )
    await mock_db.users.insert_one(
        _pro(
            business_name="Pending Pro",
            pending_approval=True,
            service_areas=["Tel Aviv"],
            location={"type": "Point", "coordinates": [34.7818, 32.0853]},
        )
    )

    seen = []

    async def counting_resolver(areas):
        seen.append(tuple(areas))
        return ServiceAreaResolution(resolved=[TLV])

    code = await script.run(
        apply=False,
        include_inactive=False,
        overwrite_location=False,
        resolver=counting_resolver,
    )
    # Both excluded by the default active-only filter — nothing matched, so
    # this is not a clean bill of health, it's an empty run (EXIT_NOTHING_CHECKED).
    assert code == script.EXIT_NOTHING_CHECKED
    assert seen == []

    seen.clear()
    code = await script.run(
        apply=False,
        include_inactive=True,
        overwrite_location=False,
        resolver=counting_resolver,
    )
    assert code == script.EXIT_OK
    # --include-inactive picks up the paused pro but never the pending one
    assert seen == [("Tel Aviv",)]


# ---------------------------------------------------------------------------
# EXIT_NOTHING_CHECKED — an empty run is a fact, not a shrug (the PRO-27 fix)
# ---------------------------------------------------------------------------


async def _never_called_resolver(areas):
    raise AssertionError("resolver must not be called when nothing is checked")


@pytest.mark.asyncio
async def test_run_reports_no_professionals_on_record_when_collection_is_empty(
    mock_db, capsys
):
    # _clean_users leaves mock_db.users empty at the start of every test.
    code = await script.run(
        apply=False,
        include_inactive=False,
        overwrite_location=False,
        resolver=_never_called_resolver,
    )

    assert code == script.EXIT_NOTHING_CHECKED
    out = capsys.readouterr().out
    assert "no professionals at all" in out


@pytest.mark.asyncio
async def test_run_reports_all_pending_when_every_pro_awaits_approval(mock_db, capsys):
    await mock_db.users.insert_one(
        _pro(business_name="Pending One", pending_approval=True)
    )
    await mock_db.users.insert_one(
        _pro(business_name="Pending Two", pending_approval=True)
    )

    code = await script.run(
        apply=False,
        include_inactive=False,
        overwrite_location=False,
        resolver=_never_called_resolver,
    )

    assert code == script.EXIT_NOTHING_CHECKED
    out = capsys.readouterr().out
    assert "awaiting approval" in out


@pytest.mark.asyncio
async def test_run_reports_skipped_paused_pros_and_points_at_include_inactive(
    mock_db, capsys
):
    await mock_db.users.insert_one(_pro(business_name="Paused Pro", is_active=False))

    code = await script.run(
        apply=False,
        include_inactive=False,
        overwrite_location=False,
        resolver=_never_called_resolver,
    )

    assert code == script.EXIT_NOTHING_CHECKED
    out = capsys.readouterr().out
    assert "1 approved pro" in out
    assert "--include-inactive" in out


# A non-empty run whose verdict is EXIT_UNRESOLVED is already covered by
# test_run_dry_run_writes_nothing_and_returns_unresolved_exit above (one pro
# checked, resolution UNREACHABLE) — checked=1 there, so the new
# `if not checked: return EXIT_NOTHING_CHECKED` branch never has a chance to
# intercept it. No separate test added for that; this comment records why.
