from unittest.mock import MagicMock

import pytest

import app.core.http_client as http_client_module
from app.core.http_client import get_http_client, close_http_client


@pytest.fixture(autouse=True)
def _reset_http_client_singleton():
    """Each test starts with a clean slate — no leaked client from a prior test."""
    http_client_module._http_client = None
    yield
    http_client_module._http_client = None


@pytest.mark.asyncio
async def test_close_after_get_closes_client_and_resets_singleton():
    client = await get_http_client()
    assert client.is_closed is False

    await close_http_client()

    assert client.is_closed is True
    assert http_client_module._http_client is None


@pytest.mark.asyncio
async def test_double_close_is_silent_noop():
    client = await get_http_client()
    await close_http_client()
    assert client.is_closed is True
    assert http_client_module._http_client is None

    # Second close: nothing to close, must not raise.
    await close_http_client()

    assert http_client_module._http_client is None


@pytest.mark.asyncio
async def test_close_without_ever_opening_is_noop():
    assert http_client_module._http_client is None

    await close_http_client()

    assert http_client_module._http_client is None


@pytest.mark.asyncio
async def test_close_swallows_aclose_error_and_logs_warning_and_resets_singleton(
    monkeypatch,
):
    client = await get_http_client()

    async def _raising_aclose():
        raise RuntimeError("boom during shutdown")

    monkeypatch.setattr(client, "aclose", _raising_aclose)

    warning_mock = MagicMock()
    monkeypatch.setattr(http_client_module.logger, "warning", warning_mock)

    # Must not raise, despite aclose() raising internally.
    await close_http_client()

    warning_mock.assert_called_once()
    warning_msg = warning_mock.call_args[0][0]
    assert "boom during shutdown" in warning_msg
    # Singleton is reset to None even though the underlying close failed.
    assert http_client_module._http_client is None


@pytest.mark.asyncio
async def test_get_http_client_after_close_returns_fresh_open_client():
    first_client = await get_http_client()
    await close_http_client()

    second_client = await get_http_client()

    assert second_client is not first_client
    assert second_client.is_closed is False
    assert http_client_module._http_client is second_client
