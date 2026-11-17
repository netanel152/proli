"""Environment guards on the destructive dev scripts.

``scripts/clear_history.py`` deletes every message, lead and review;
``scripts/reset_test.py`` deletes leads, messages and Redis state (``--all``
wholesale). Both refuse to run outside development and staging, with the same
allow-list shape as ``seed_db.assert_seed_allowed`` (tests/test_environment_config.py),
and both check before the first database call.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.constants import DEVELOPMENT_ENV, PRODUCTION_ENV, STAGING_ENV

import scripts.clear_history as clear_history
import scripts.reset_test as reset_test

_GUARDS = [
    (clear_history, "assert_clear_allowed"),
    (reset_test, "assert_reset_allowed"),
]


@pytest.mark.parametrize("module, guard", _GUARDS)
@pytest.mark.parametrize("env_value", [PRODUCTION_ENV, "prod", "", "qa"])
def test_guard_refuses_anything_but_development_and_staging(
    monkeypatch, module, guard, env_value
):
    monkeypatch.setattr(module, "settings", SimpleNamespace(ENVIRONMENT=env_value))
    with pytest.raises(SystemExit):
        getattr(module, guard)()


@pytest.mark.parametrize("module, guard", _GUARDS)
@pytest.mark.parametrize("env_value", [DEVELOPMENT_ENV, STAGING_ENV])
def test_guard_allows_development_and_staging(monkeypatch, module, guard, env_value):
    monkeypatch.setattr(module, "settings", SimpleNamespace(ENVIRONMENT=env_value))
    getattr(module, guard)()


@pytest.mark.asyncio
async def test_clear_history_refuses_production_before_prompting_or_deleting(
    monkeypatch,
):
    monkeypatch.setattr(
        clear_history, "settings", SimpleNamespace(ENVIRONMENT=PRODUCTION_ENV)
    )
    prompt = MagicMock(return_value="y")
    monkeypatch.setattr("builtins.input", prompt)
    collections = {}
    for name in ("messages_collection", "leads_collection", "reviews_collection"):
        collections[name] = MagicMock(delete_many=AsyncMock())
        monkeypatch.setattr(clear_history, name, collections[name])

    with pytest.raises(SystemExit):
        await clear_history.clear_history()

    prompt.assert_not_called()
    for coll in collections.values():
        coll.delete_many.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("argv", [["reset_test.py"], ["reset_test.py", "--all"]])
async def test_reset_test_refuses_production_before_connecting(monkeypatch, argv):
    monkeypatch.setattr(
        reset_test, "settings", SimpleNamespace(ENVIRONMENT=PRODUCTION_ENV)
    )
    monkeypatch.setattr("sys.argv", argv)
    mongo = MagicMock()
    redis_ctor = MagicMock()
    monkeypatch.setattr(reset_test, "AsyncIOMotorClient", mongo)
    monkeypatch.setattr(reset_test.redis, "Redis", redis_ctor)

    with pytest.raises(SystemExit):
        await reset_test.main()

    mongo.assert_not_called()
    redis_ctor.assert_not_called()
