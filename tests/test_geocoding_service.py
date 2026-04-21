"""
Tests for app/services/geocoding_service.py.

Covers the four layers of the resolution pipeline:
  1. Static dict fast-path (no Redis, no network).
  2. Redis cache (positive + negative hit).
  3. Google API call path (mocked httpx).
  4. Israel bounds validation (reject matches outside the country box).

All Redis and HTTP calls are mocked — these tests run offline.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import geocoding_service as geo


@pytest.fixture
def mock_redis(monkeypatch):
    """Replace the module-level redis client with an in-memory fake."""
    store = {}

    async def fake_get(key):
        return store.get(key)

    async def fake_set(key, value, ex=None):
        store[key] = value
        return True

    fake_client = MagicMock()
    fake_client.get = AsyncMock(side_effect=fake_get)
    fake_client.set = AsyncMock(side_effect=fake_set)

    async def fake_get_client():
        return fake_client

    monkeypatch.setattr(geo, "get_redis_client", fake_get_client)
    return store


@pytest.fixture
def mock_google_maps_key(monkeypatch):
    """Pretend the API key is set so the network path is enabled."""
    monkeypatch.setattr(geo.settings, "GOOGLE_MAPS_API_KEY", "test-key")


@pytest.mark.asyncio
async def test_empty_name_returns_none(mock_redis):
    assert await geo.resolve_city_to_coords("") is None
    assert await geo.resolve_city_to_coords("   ") is None


@pytest.mark.asyncio
async def test_static_dict_fast_path(mock_redis):
    """Tel Aviv is in the static dict → should not hit Redis or the network."""
    with patch("app.services.geocoding_service._call_google", new=AsyncMock()) as mock_google:
        result = await geo.resolve_city_to_coords("Tel Aviv")
        assert result == (34.7818, 32.0853)
        mock_google.assert_not_awaited()


@pytest.mark.asyncio
async def test_static_dict_hebrew(mock_redis):
    """The static dict has Hebrew keys too — case-insensitive lookup."""
    with patch("app.services.geocoding_service._call_google", new=AsyncMock()) as mock_google:
        result = await geo.resolve_city_to_coords("תל אביב")
        assert result == (34.7818, 32.0853)
        mock_google.assert_not_awaited()


@pytest.mark.asyncio
async def test_positive_cache_hit(mock_redis, mock_google_maps_key):
    """A cached coord pair short-circuits the Google call."""
    mock_redis["geo:city:ראש העין"] = json.dumps([34.9519, 32.0875])
    with patch("app.services.geocoding_service._call_google", new=AsyncMock()) as mock_google:
        result = await geo.resolve_city_to_coords("ראש העין")
        assert result == (34.9519, 32.0875)
        mock_google.assert_not_awaited()


@pytest.mark.asyncio
async def test_negative_cache_hit(mock_redis, mock_google_maps_key):
    """The `__NULL__` sentinel means Google already said no — don't retry."""
    mock_redis["geo:city:גיבריש"] = geo._NEGATIVE_CACHE_VALUE
    with patch("app.services.geocoding_service._call_google", new=AsyncMock()) as mock_google:
        result = await geo.resolve_city_to_coords("גיבריש")
        assert result is None
        mock_google.assert_not_awaited()


@pytest.mark.asyncio
async def test_google_success_caches_result(mock_redis, mock_google_maps_key):
    """Cache miss + Google OK → returns coords AND caches them with no TTL."""
    async def fake_google(name):
        return (34.9519, 32.0875)

    with patch("app.services.geocoding_service._call_google", side_effect=fake_google):
        result = await geo.resolve_city_to_coords("ראש העין")
    assert result == (34.9519, 32.0875)
    # The positive entry was persisted
    cached = mock_redis["geo:city:ראש העין"]
    assert json.loads(cached) == [34.9519, 32.0875]


@pytest.mark.asyncio
async def test_google_miss_caches_negative(mock_redis, mock_google_maps_key):
    """Google said no → sentinel value stored, subsequent calls short-circuit."""
    call_count = {"n": 0}

    async def fake_google(name):
        call_count["n"] += 1
        return None

    with patch("app.services.geocoding_service._call_google", side_effect=fake_google):
        assert await geo.resolve_city_to_coords("גיבריש") is None
        # Second call hits the negative cache, not Google
        assert await geo.resolve_city_to_coords("גיבריש") is None

    assert call_count["n"] == 1
    assert mock_redis["geo:city:גיבריש"] == geo._NEGATIVE_CACHE_VALUE


@pytest.mark.asyncio
async def test_corrupt_cache_falls_through_to_google(mock_redis, mock_google_maps_key):
    """Malformed cache entry shouldn't wedge the resolver."""
    mock_redis["geo:city:foo"] = "not-json"
    async def fake_google(name):
        return (35.0, 32.0)
    with patch("app.services.geocoding_service._call_google", side_effect=fake_google):
        result = await geo.resolve_city_to_coords("foo")
    assert result == (35.0, 32.0)


@pytest.mark.asyncio
async def test_call_google_no_key_returns_none(monkeypatch):
    """Without GOOGLE_MAPS_API_KEY the network path is a safe no-op."""
    monkeypatch.setattr(geo.settings, "GOOGLE_MAPS_API_KEY", None)
    result = await geo._call_google("whatever")
    assert result is None


@pytest.mark.asyncio
async def test_call_google_ok_response(mock_google_maps_key):
    """Happy-path parsing of a Google 200 OK payload."""
    payload = {
        "status": "OK",
        "results": [
            {"geometry": {"location": {"lat": 32.0853, "lng": 34.7818}}}
        ],
    }
    mock_resp = MagicMock()
    mock_resp.json.return_value = payload
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await geo._call_google("Tel Aviv")
    # Note: geojson ordering — (lon, lat)
    assert result == (34.7818, 32.0853)


@pytest.mark.asyncio
async def test_call_google_rejects_outside_israel(mock_google_maps_key):
    """A result outside the bounding box is rejected even on status=OK."""
    payload = {
        "status": "OK",
        "results": [
            {"geometry": {"location": {"lat": 40.7128, "lng": -74.0060}}}  # NYC
        ],
    }
    mock_resp = MagicMock()
    mock_resp.json.return_value = payload
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await geo._call_google("Some ambiguous name")
    assert result is None


@pytest.mark.asyncio
async def test_call_google_zero_results(mock_google_maps_key):
    payload = {"status": "ZERO_RESULTS", "results": []}
    mock_resp = MagicMock()
    mock_resp.json.return_value = payload
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await geo._call_google("כלום")
    assert result is None


def test_inside_israel_bounds():
    # Tel Aviv — inside
    assert geo._inside_israel(32.0853, 34.7818) is True
    # Eilat — inside (near south edge)
    assert geo._inside_israel(29.5569, 34.9519) is True
    # Metula — inside (near north edge)
    assert geo._inside_israel(33.2795, 35.5819) is True
    # Amman, Jordan — outside
    assert geo._inside_israel(31.9454, 35.9284) is False
    # Cairo, Egypt — outside
    assert geo._inside_israel(30.0444, 31.2357) is False
