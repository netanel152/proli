"""PRO-250 — a failed index is never silent.

``create_all_indexes(silent=True)`` (every API boot) used to route the
per-index ``except`` through the same muted ``log()`` helper as the progress
lines, and ``app/main.py`` then logged "✅ MongoDB indexes verified." whatever
happened. A unique index that failed on duplicates (``users.phone_number`` is
exactly the case ``normalize_pro_phones.py`` exists for) or an options
conflict (a changed TTL) went unnoticed.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from loguru import logger
from mongomock_motor import AsyncMongoMockClient
from pymongo import ASCENDING, DESCENDING

import app.main as main_module
import app.db.indexes as create_indexes
from app.main import app


@pytest.fixture
def records():
    """Every loguru record emitted during the test, as (level, message)."""
    seen = []
    sink = logger.add(
        lambda m: seen.append((m.record["level"].name, m.record["message"]))
    )
    yield seen
    logger.remove(sink)


@pytest.fixture
def fresh_db():
    return AsyncMongoMockClient().proli_index_test


def _specs(db):
    return [
        ("Users", db.users, [("phone_number", ASCENDING)], {"unique": True}),
        ("Leads", db.leads, [("chat_id", ASCENDING), ("status", ASCENDING)], {}),
        (
            "Audit Log",
            db.audit_log,
            [("timestamp", DESCENDING), ("_id", DESCENDING)],
            {},
        ),
    ]


@pytest.mark.asyncio
async def test_all_indexes_in_place_returns_no_failures(monkeypatch, fresh_db, records):
    monkeypatch.setattr(create_indexes, "INDEX_SPECS", _specs(fresh_db))

    assert await create_indexes.create_all_indexes(silent=True) == []

    assert not [m for lvl, m in records if lvl == "ERROR"]
    info = await fresh_db.users.index_information()
    assert any(spec.get("unique") for spec in info.values())


@pytest.mark.asyncio
@pytest.mark.parametrize("silent", [True, False])
async def test_a_failed_index_is_logged_at_error_even_when_silent(
    monkeypatch, fresh_db, records, silent
):
    """The duplicate-phone shape: the unique index cannot be built. It is
    reported, returned, and the indexes after it are still created."""
    specs = _specs(fresh_db)
    broken = MagicMock()
    broken.name = "users"
    broken.create_index = AsyncMock(
        side_effect=Exception("E11000 duplicate key error collection: users")
    )
    broken.index_information = AsyncMock(return_value={})
    specs[0] = ("Users", broken, specs[0][2], specs[0][3])
    monkeypatch.setattr(create_indexes, "INDEX_SPECS", specs)

    failures = await create_indexes.create_all_indexes(silent=silent)

    assert [(f["label"], f["keys"]) for f in failures] == [
        ("Users", [("phone_number", ASCENDING)])
    ]
    assert "E11000" in failures[0]["error"]
    errors = [m for lvl, m in records if lvl == "ERROR"]
    assert len(errors) == 1 and "Users" in errors[0] and "E11000" in errors[0]
    assert len(await fresh_db.leads.index_information()) == 2  # _id_ + compound


@pytest.mark.asyncio
async def test_an_index_missing_from_the_table_is_reported_and_never_dropped(
    monkeypatch, fresh_db, records
):
    """The superseded timestamp-only audit_log index: warned about, kept."""
    await fresh_db.audit_log.create_index([("timestamp", DESCENDING)])
    monkeypatch.setattr(create_indexes, "INDEX_SPECS", _specs(fresh_db))

    assert await create_indexes.create_all_indexes(silent=True) == []

    unlisted = await create_indexes.find_unlisted_indexes()
    assert [(c, k) for c, _, k in unlisted] == [("audit_log", (("timestamp", -1),))]
    warnings = [m for lvl, m in records if lvl == "WARNING"]
    assert any("audit_log" in m and "Not dropped" in m for m in warnings)
    assert "timestamp_-1" in await fresh_db.audit_log.index_information()


# ------------------------------------------------------------- the boot line


def _boot(monkeypatch, create_result):
    mongo = MagicMock()
    mongo.admin.command = AsyncMock(return_value={"ok": 1})
    monkeypatch.setattr(main_module, "mongo_client", mongo)
    redis = MagicMock()
    redis.ping = AsyncMock(return_value=True)
    monkeypatch.setattr(main_module, "get_redis_client", AsyncMock(return_value=redis))
    monkeypatch.setattr(main_module, "create_all_indexes", create_result)
    monkeypatch.setattr(main_module, "close_redis_client", AsyncMock())
    monkeypatch.setattr(main_module, "_close_shared_http_client", AsyncMock())
    with TestClient(app):  # runs the lifespan startup and shutdown
        pass


def test_boot_says_verified_only_when_every_index_is_in_place(monkeypatch, records):
    _boot(monkeypatch, AsyncMock(return_value=[]))

    assert any("indexes verified" in m for lvl, m in records if lvl == "INFO")
    assert not [m for lvl, m in records if lvl == "ERROR"]


def test_boot_reports_failed_indexes_instead_of_verified(monkeypatch, records):
    failed = [{"label": "Users", "keys": [("phone_number", 1)], "error": "E11000"}]
    _boot(monkeypatch, AsyncMock(return_value=failed))

    assert not any("indexes verified" in m for _, m in records)
    errors = [m for lvl, m in records if lvl == "ERROR"]
    assert any("1 MongoDB index" in m and "Users" in m for m in errors)


def test_boot_survives_index_creation_raising(monkeypatch, records):
    _boot(monkeypatch, AsyncMock(side_effect=RuntimeError("no primary")))

    assert not any("indexes verified" in m for _, m in records)
    assert any("no primary" in m for lvl, m in records if lvl == "ERROR")
