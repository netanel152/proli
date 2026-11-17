"""``/docs``, ``/redoc`` and ``/openapi.json`` are off in production.

The interactive docs and the schema behind them map every route and payload
shape of an internet-facing API (Railway puts no proxy in front). Development
and staging keep them.
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.config import settings
from app.core.constants import DEVELOPMENT_ENV, PRODUCTION_ENV, STAGING_ENV

_PATHS = ("/docs", "/redoc", "/openapi.json")


def _cfg(env):
    return SimpleNamespace(is_production=env == PRODUCTION_ENV)


def _served(kwargs):
    app = FastAPI(**kwargs)

    @app.get("/ping")
    def ping():
        return {"ok": True}

    client = TestClient(app)
    return {path: client.get(path).status_code for path in _PATHS}


def test_production_serves_none_of_the_three():
    assert _served(main_module.api_docs_kwargs(_cfg(PRODUCTION_ENV))) == {
        path: 404 for path in _PATHS
    }


@pytest.mark.parametrize("env", [DEVELOPMENT_ENV, STAGING_ENV])
def test_development_and_staging_keep_them(env):
    assert main_module.api_docs_kwargs(_cfg(env)) == {}
    assert _served(main_module.api_docs_kwargs(_cfg(env))) == {
        path: 200 for path in _PATHS
    }


def test_the_running_app_is_built_with_the_rule():
    """app.main.app is constructed through api_docs_kwargs, not around it."""
    expected = main_module.api_docs_kwargs(settings)
    app = main_module.app
    assert app.openapi_url == expected.get("openapi_url", "/openapi.json")
    assert app.docs_url == expected.get("docs_url", "/docs")
    assert app.redoc_url == expected.get("redoc_url", "/redoc")
