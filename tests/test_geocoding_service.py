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

import httpx
import pytest
from pydantic import SecretStr

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
def mock_redis_with_ttl(monkeypatch):
    """Like `mock_redis`, but also records the `ex=` kwarg passed to
    `redis.set` per key, so the TTL split (PRO-19) is observable.

    Returns (store, ttls) where `ttls[key]` is the last `ex` value used
    (None means "no expiry" / persist forever).
    """
    store = {}
    ttls = {}

    async def fake_get(key):
        return store.get(key)

    async def fake_set(key, value, ex=None):
        store[key] = value
        ttls[key] = ex
        return True

    fake_client = MagicMock()
    fake_client.get = AsyncMock(side_effect=fake_get)
    fake_client.set = AsyncMock(side_effect=fake_set)

    async def fake_get_client():
        return fake_client

    monkeypatch.setattr(geo, "get_redis_client", fake_get_client)
    return store, ttls


@pytest.fixture
def mock_google_maps_key(monkeypatch):
    """Pretend the API key is set so the network path is enabled."""
    # PRO-94: the field is a SecretStr, and the service unwraps it — patch with
    # the same type so the test exercises the real call, not a str shortcut.
    monkeypatch.setattr(geo.settings, "GOOGLE_MAPS_API_KEY", SecretStr("test-key"))


@pytest.mark.asyncio
async def test_empty_name_returns_none(mock_redis):
    assert await geo.resolve_city_to_coords("") is None
    assert await geo.resolve_city_to_coords("   ") is None


@pytest.mark.asyncio
async def test_static_dict_fast_path(mock_redis):
    """Tel Aviv is in the static dict → should not hit Redis or the network."""
    with patch(
        "app.services.geocoding_service._call_google", new=AsyncMock()
    ) as mock_google:
        result = await geo.resolve_city_to_coords("Tel Aviv")
        assert result == (34.7818, 32.0853)
        mock_google.assert_not_awaited()


@pytest.mark.asyncio
async def test_static_dict_hebrew(mock_redis):
    """The static dict has Hebrew keys too — case-insensitive lookup."""
    with patch(
        "app.services.geocoding_service._call_google", new=AsyncMock()
    ) as mock_google:
        result = await geo.resolve_city_to_coords("תל אביב")
        assert result == (34.7818, 32.0853)
        mock_google.assert_not_awaited()


@pytest.mark.asyncio
async def test_positive_cache_hit(mock_redis, mock_google_maps_key):
    """A cached coord pair short-circuits the Google call."""
    mock_redis["geo:city:ראש העין"] = json.dumps([34.9519, 32.0875])
    with patch(
        "app.services.geocoding_service._call_google", new=AsyncMock()
    ) as mock_google:
        result = await geo.resolve_city_to_coords("ראש העין")
        assert result == (34.9519, 32.0875)
        mock_google.assert_not_awaited()


@pytest.mark.asyncio
async def test_negative_cache_hit(mock_redis, mock_google_maps_key):
    """The `__NULL__` sentinel means Google already said no — don't retry."""
    mock_redis["geo:city:גיבריש"] = geo._NEGATIVE_CACHE_VALUE
    with patch(
        "app.services.geocoding_service._call_google", new=AsyncMock()
    ) as mock_google:
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
async def test_call_google_no_key_raises_unavailable(monkeypatch):
    """Without GOOGLE_MAPS_API_KEY the call is a transient, not a definitive,
    failure — it must raise GeocodingUnavailable and short-circuit before
    any network client is constructed (PRO-19)."""
    monkeypatch.setattr(geo.settings, "GOOGLE_MAPS_API_KEY", None)
    with patch("httpx.AsyncClient") as mock_client_cls:
        with pytest.raises(geo.GeocodingUnavailable):
            await geo._call_google("whatever")
        mock_client_cls.assert_not_called()


@pytest.mark.asyncio
async def test_call_google_ok_response(mock_google_maps_key):
    """Happy-path parsing of a Google 200 OK payload."""
    payload = {
        "status": "OK",
        "results": [{"geometry": {"location": {"lat": 32.0853, "lng": 34.7818}}}],
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


@pytest.mark.asyncio
@pytest.mark.parametrize("status", sorted(geo._TRANSIENT_GOOGLE_STATUSES))
async def test_call_google_raises_for_transient_statuses(mock_google_maps_key, status):
    """Every status in _TRANSIENT_GOOGLE_STATUSES must raise
    GeocodingUnavailable, not return None — parametrized over the frozenset
    itself so a future added status is automatically covered."""
    payload = {"status": status, "results": [], "error_message": "boom"}
    mock_resp = MagicMock()
    mock_resp.json.return_value = payload
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(geo.GeocodingUnavailable):
            await geo._call_google("whatever")


@pytest.mark.asyncio
async def test_call_google_raises_for_unrecognized_status(mock_google_maps_key):
    """An unrecognized/unknown status is treated as transient — better to
    retry in 60s than pin a possibly-valid city as dead for 24h."""
    payload = {"status": "WEIRD_NEW_STATUS", "results": []}
    mock_resp = MagicMock()
    mock_resp.json.return_value = payload
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(geo.GeocodingUnavailable):
            await geo._call_google("whatever")


@pytest.mark.asyncio
async def test_call_google_raises_for_ok_with_empty_results(mock_google_maps_key):
    """status=OK with an empty results list is a Google-side anomaly, not a
    verdict on the name — must raise, not return None."""
    payload = {"status": "OK", "results": []}
    mock_resp = MagicMock()
    mock_resp.json.return_value = payload
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(geo.GeocodingUnavailable):
            await geo._call_google("whatever")


@pytest.mark.asyncio
async def test_call_google_raises_for_malformed_payload(mock_google_maps_key):
    """A result missing lat/lng inside geometry.location must raise, not
    silently be treated as a definitive miss."""
    payload = {"status": "OK", "results": [{"geometry": {"location": {}}}]}
    mock_resp = MagicMock()
    mock_resp.json.return_value = payload
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(geo.GeocodingUnavailable):
            await geo._call_google("whatever")


@pytest.mark.asyncio
async def test_call_google_raises_on_http_layer_exception(mock_google_maps_key):
    """A network/timeout exception from the HTTP client must be chained
    into GeocodingUnavailable, not propagate as-is or return None."""
    mock_client = MagicMock()
    mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("boom"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(geo.GeocodingUnavailable) as excinfo:
            await geo._call_google("whatever")
        assert excinfo.value.__cause__ is not None


@pytest.mark.asyncio
async def test_resolve_caches_definitive_miss_with_negative_ttl(
    mock_redis_with_ttl, mock_google_maps_key
):
    """A definitive miss (ZERO_RESULTS) must cache with the full 24h TTL —
    Google gave a real verdict on this name. Exercised end-to-end through
    the real `_call_google` (httpx mocked at the transport layer), not by
    stubbing `_call_google` itself, so this actually tests the ZERO_RESULTS
    branch and not just resolve_city_to_coords's None handling."""
    store, ttls = mock_redis_with_ttl

    payload = {"status": "ZERO_RESULTS", "results": []}
    mock_resp = MagicMock()
    mock_resp.json.return_value = payload
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await geo.resolve_city_to_coords("גיבריש")

    assert result is None
    key = "geo:city:גיבריש"
    assert store[key] == geo._NEGATIVE_CACHE_VALUE
    assert ttls[key] == geo.settings.GEOCODING_NEGATIVE_TTL_SECONDS
    assert ttls[key] == 86400
    # A definitive miss must not open the breaker.
    assert geo._UNAVAILABLE_KEY not in store


@pytest.mark.asyncio
async def test_resolve_caches_invalid_request_with_transient_ttl_and_opens_circuit(
    mock_redis_with_ttl, mock_google_maps_key
):
    """INVALID_REQUEST is back in _TRANSIENT_GOOGLE_STATUSES (with the
    breaker in place, bounding the retry cost to ~one probe per window is
    preferable to pinning every address touched by a param-construction
    bug as unresolvable for 24h after the fix ships). End-to-end through
    resolve_city_to_coords it must earn only the short transient TTL and
    must open the circuit breaker, exactly like the other transient
    statuses."""
    store, ttls = mock_redis_with_ttl

    payload = {
        "status": "INVALID_REQUEST",
        "results": [],
        "error_message": "boom",
    }
    mock_resp = MagicMock()
    mock_resp.json.return_value = payload
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await geo.resolve_city_to_coords("גיבריש")

    assert result is None
    key = "geo:city:גיבריש"
    assert store[key] == geo._NEGATIVE_CACHE_VALUE
    assert ttls[key] == geo.settings.GEOCODING_TRANSIENT_TTL_SECONDS
    assert ttls[key] == 60
    # The circuit must be opened, same as any other transient status.
    assert store[geo._UNAVAILABLE_KEY] == "1"
    assert ttls[geo._UNAVAILABLE_KEY] == geo.settings.GEOCODING_TRANSIENT_TTL_SECONDS


@pytest.mark.asyncio
async def test_resolve_caches_transient_failure_with_short_ttl_via_mock(
    mock_redis_with_ttl, mock_google_maps_key
):
    """A transient failure (simulated by patching _call_google to raise)
    must cache with the short transient TTL, never the 24h one, and must
    not propagate GeocodingUnavailable — resolve_city_to_coords returns
    None instead."""
    store, ttls = mock_redis_with_ttl

    with patch(
        "app.services.geocoding_service._call_google",
        side_effect=geo.GeocodingUnavailable("quota exhausted"),
    ):
        result = await geo.resolve_city_to_coords("ראש העין")

    assert result is None
    key = "geo:city:ראש העין"
    assert store[key] == geo._NEGATIVE_CACHE_VALUE
    assert ttls[key] == geo.settings.GEOCODING_TRANSIENT_TTL_SECONDS
    assert ttls[key] == 60


@pytest.mark.asyncio
async def test_resolve_caches_transient_failure_with_short_ttl_via_missing_key(
    mock_redis_with_ttl, monkeypatch
):
    """End-to-end: GOOGLE_MAPS_API_KEY missing → _call_google raises
    GeocodingUnavailable for real → resolve_city_to_coords must still
    return None (never raise) and cache with the short transient TTL."""
    store, ttls = mock_redis_with_ttl
    monkeypatch.setattr(geo.settings, "GOOGLE_MAPS_API_KEY", None)
    result = await geo.resolve_city_to_coords("ראש העין")

    assert result is None
    key = "geo:city:ראש העין"
    assert store[key] == geo._NEGATIVE_CACHE_VALUE
    assert ttls[key] == geo.settings.GEOCODING_TRANSIENT_TTL_SECONDS
    assert ttls[key] == 60


@pytest.mark.asyncio
async def test_transient_failure_is_retryable_after_short_ttl_expiry(
    mock_redis_with_ttl, mock_google_maps_key
):
    """A transient failure must NOT poison the name for 24h: once the short
    TTL entry expires (simulated by clearing the store), a subsequent
    successful Google call must resolve and cache with ttl=None (forever),
    not be blocked by a stale negative entry."""
    store, ttls = mock_redis_with_ttl

    with patch(
        "app.services.geocoding_service._call_google",
        side_effect=geo.GeocodingUnavailable("quota exhausted"),
    ):
        first = await geo.resolve_city_to_coords("ראש העין")

    assert first is None
    key = "geo:city:ראש העין"
    assert store[key] == geo._NEGATIVE_CACHE_VALUE
    assert ttls[key] == 60

    # Simulate the short TTL expiring — this also trips the circuit
    # breaker (PRO-19 follow-up), which shares the same transient TTL, so
    # it must be cleared too or the second call short-circuits before ever
    # reaching Google.
    store.pop(key, None)
    ttls.pop(key, None)
    store.pop(geo._UNAVAILABLE_KEY, None)
    ttls.pop(geo._UNAVAILABLE_KEY, None)

    async def fake_google(name):
        return (34.9519, 32.0875)

    with patch("app.services.geocoding_service._call_google", side_effect=fake_google):
        second = await geo.resolve_city_to_coords("ראש העין")

    assert second == (34.9519, 32.0875)
    assert json.loads(store[key]) == [34.9519, 32.0875]
    assert ttls[key] is None


@pytest.mark.asyncio
async def test_resolve_caches_out_of_israel_with_definitive_ttl(
    mock_redis_with_ttl, mock_google_maps_key
):
    """A match outside Israel's bounding box is a definitive miss (Google
    answered, the answer is just geographically wrong) — end-to-end it
    must earn the full 24h negative TTL, symmetrical to ZERO_RESULTS.
    Exercised through the real `_call_google`: httpx is mocked to return a
    status=OK payload with real out-of-Israel coordinates (NYC), so the
    bounding-box rejection branch is actually run, not assumed."""
    store, ttls = mock_redis_with_ttl

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
        result = await geo.resolve_city_to_coords("Some ambiguous name")

    assert result is None
    key = "geo:city:some ambiguous name"
    assert store[key] == geo._NEGATIVE_CACHE_VALUE
    assert ttls[key] == geo.settings.GEOCODING_NEGATIVE_TTL_SECONDS
    assert ttls[key] == 86400
    # A definitive (geographic) miss must not open the breaker either.
    assert geo._UNAVAILABLE_KEY not in store


# ---------------------------------------------------------------------------
# Circuit breaker (geo:unavailable)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transient_failure_opens_circuit_with_short_ttl(
    mock_redis_with_ttl, mock_google_maps_key
):
    """A transient failure must trip the breaker key with the transient
    TTL (60s), so subsequent lookups short-circuit before hitting Google."""
    store, ttls = mock_redis_with_ttl

    with patch(
        "app.services.geocoding_service._call_google",
        side_effect=geo.GeocodingUnavailable("quota exhausted"),
    ):
        result = await geo.resolve_city_to_coords("ראש העין")

    assert result is None
    assert store[geo._UNAVAILABLE_KEY] == "1"
    assert ttls[geo._UNAVAILABLE_KEY] == geo.settings.GEOCODING_TRANSIENT_TTL_SECONDS
    assert ttls[geo._UNAVAILABLE_KEY] == 60


@pytest.mark.asyncio
async def test_open_circuit_short_circuits_before_calling_google(
    mock_redis_with_ttl, mock_google_maps_key
):
    """With the breaker key pre-seeded, resolve_city_to_coords must return
    None WITHOUT ever awaiting _call_google — that's the whole point of the
    breaker (bounding an outage to one probe per window)."""
    store, ttls = mock_redis_with_ttl
    store[geo._UNAVAILABLE_KEY] = "1"

    with patch(
        "app.services.geocoding_service._call_google", new=AsyncMock()
    ) as mock_google:
        result = await geo.resolve_city_to_coords("ראש העין")

    assert result is None
    mock_google.assert_not_awaited()


@pytest.mark.asyncio
async def test_definitive_miss_does_not_open_circuit(
    mock_redis_with_ttl, mock_google_maps_key
):
    """A definitive miss (Google gave a real verdict) must NOT trip the
    breaker — the breaker is reserved for transient/systemic failures."""
    store, ttls = mock_redis_with_ttl

    async def fake_google(name):
        return None

    with patch("app.services.geocoding_service._call_google", side_effect=fake_google):
        result = await geo.resolve_city_to_coords("גיבריש")

    assert result is None
    assert geo._UNAVAILABLE_KEY not in store


@pytest.mark.asyncio
async def test_open_circuit_logs_critical(mock_redis_with_ttl, mock_google_maps_key):
    """_open_circuit is the one line in this module that actually pages an
    operator (Sentry is CRITICAL-only). Verify it emits page_critical (PRO-113,
    the only operator-paging primitive) — following the monkeypatched-
    MagicMock pattern already used elsewhere in tests/ (e.g.
    test_whatsapp_state_monitor.py) rather than caplog, since loguru routes
    through InterceptHandler."""
    mock_page_critical = MagicMock()

    with patch("app.services.geocoding_service.page_critical", mock_page_critical):
        await geo._open_circuit("quota exhausted")

    mock_page_critical.assert_called_once()


@pytest.mark.asyncio
async def test_open_circuit_via_resolve_logs_critical_exactly_once(
    mock_redis_with_ttl, mock_google_maps_key
):
    """End-to-end: a transient Google failure that trips the breaker inside
    resolve_city_to_coords must produce exactly one page_critical call —
    the rate-limiting property (at most once per window) depends on this
    being the only call site."""
    store, ttls = mock_redis_with_ttl
    mock_page_critical = MagicMock()

    with patch(
        "app.services.geocoding_service.page_critical", mock_page_critical
    ), patch(
        "app.services.geocoding_service._call_google",
        side_effect=geo.GeocodingUnavailable("quota exhausted"),
    ):
        result = await geo.resolve_city_to_coords("ראש העין")

    assert result is None
    mock_page_critical.assert_called_once()


@pytest.mark.asyncio
async def test_open_circuit_never_blocks_static_dict_fast_path(
    mock_redis_with_ttl, mock_google_maps_key
):
    """The breaker must never turn a Google outage into a total geocoding
    outage: the static dict is checked before the circuit-breaker read, so
    a name that's in ISRAEL_CITIES_COORDS must still resolve while
    geo:unavailable is set."""
    store, ttls = mock_redis_with_ttl
    store[geo._UNAVAILABLE_KEY] = "1"

    with patch(
        "app.services.geocoding_service._call_google", new=AsyncMock()
    ) as mock_google:
        result = await geo.resolve_city_to_coords("Tel Aviv")

    assert result == (34.7818, 32.0853)
    mock_google.assert_not_awaited()


@pytest.mark.asyncio
async def test_open_circuit_never_blocks_positive_cache_hit(
    mock_redis_with_ttl, mock_google_maps_key
):
    """Same safety property, for the Redis positive-cache path: a name
    that's already cached must still resolve from cache while the breaker
    is open, since the cache read happens before the circuit-breaker check."""
    store, ttls = mock_redis_with_ttl
    store["geo:city:ראש העין"] = json.dumps([34.9519, 32.0875])
    store[geo._UNAVAILABLE_KEY] = "1"

    with patch(
        "app.services.geocoding_service._call_google", new=AsyncMock()
    ) as mock_google:
        result = await geo.resolve_city_to_coords("ראש העין")

    assert result == (34.9519, 32.0875)
    mock_google.assert_not_awaited()


# ---------------------------------------------------------------------------
# Catch-all: unexpected parse errors from _call_google must not escape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unexpected_attribute_error_is_swallowed_with_short_ttl(
    mock_redis_with_ttl, mock_google_maps_key
):
    """A non-dict JSON body (e.g. from a proxy/WAF) makes _call_google raise
    AttributeError. resolve_city_to_coords must not propagate it, must
    return None, and must cache at the short transient TTL — a single odd
    response says nothing about the city."""
    store, ttls = mock_redis_with_ttl

    with patch(
        "app.services.geocoding_service._call_google",
        side_effect=AttributeError("bad payload"),
    ):
        result = await geo.resolve_city_to_coords("ראש העין")

    assert result is None
    key = "geo:city:ראש העין"
    assert store[key] == geo._NEGATIVE_CACHE_VALUE
    assert ttls[key] == geo.settings.GEOCODING_TRANSIENT_TTL_SECONDS
    assert ttls[key] == 60
    # A single malformed payload must not disable geocoding process-wide.
    assert geo._UNAVAILABLE_KEY not in store


@pytest.mark.asyncio
async def test_unexpected_value_error_is_swallowed_with_short_ttl(
    mock_redis_with_ttl, mock_google_maps_key
):
    """A non-numeric lat/lng makes float() raise ValueError inside
    _call_google. Same contract as the AttributeError case."""
    store, ttls = mock_redis_with_ttl

    with patch(
        "app.services.geocoding_service._call_google",
        side_effect=ValueError("bad lat"),
    ):
        result = await geo.resolve_city_to_coords("ראש העין")

    assert result is None
    key = "geo:city:ראש העין"
    assert store[key] == geo._NEGATIVE_CACHE_VALUE
    assert ttls[key] == geo.settings.GEOCODING_TRANSIENT_TTL_SECONDS
    assert ttls[key] == 60
    assert geo._UNAVAILABLE_KEY not in store


# ---------------------------------------------------------------------------
# Contract under Redis failure — _cache_get/_cache_set swallow internally,
# resolve_city_to_coords must never raise regardless.
# ---------------------------------------------------------------------------


@pytest.fixture
def broken_redis(monkeypatch):
    """get_redis_client raises outright — simulates Redis being fully down,
    not just returning an error response."""

    async def fake_get_client():
        raise RuntimeError("redis down")

    monkeypatch.setattr(geo, "get_redis_client", fake_get_client)


@pytest.mark.asyncio
async def test_resolve_survives_redis_failure_on_transient_path(
    broken_redis, mock_google_maps_key
):
    """Even with Redis fully unreachable, a transient Google failure must
    still resolve to None without raising."""
    with patch(
        "app.services.geocoding_service._call_google",
        side_effect=geo.GeocodingUnavailable("quota exhausted"),
    ):
        result = await geo.resolve_city_to_coords("ראש העין")
    assert result is None


@pytest.mark.asyncio
async def test_resolve_survives_redis_failure_on_catch_all_path(
    broken_redis, mock_google_maps_key
):
    """Even with Redis fully unreachable, an unexpected parse error must
    still resolve to None without raising."""
    with patch(
        "app.services.geocoding_service._call_google",
        side_effect=AttributeError("bad payload"),
    ):
        result = await geo.resolve_city_to_coords("ראש העין")
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
