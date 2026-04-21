"""
Geocoding service — resolve Israeli city/address names to [lon, lat].

Why this exists
---------------
The matching service feeds `$geoNear` an explicit coordinate pair. Until
now that pair came from a hand-curated static dict (`ISRAEL_CITIES_COORDS`
in `app/core/constants.py`). The 2026-04-18 post-mortem showed that
cities not in the static dict (e.g. ראש העין) silently fall through to a
regex-on-service_areas fallback that almost never matches, and the lead
gets escalated to PENDING_ADMIN_REVIEW with no pro tried.

This module wraps Google Geocoding with a Redis cache so we can resolve
arbitrary Israeli locality names without a code deploy. The static dict
stays on the hot path as a zero-latency fast-path; we only call Google
on cache miss + static miss.

Design contract
---------------
`resolve_city_to_coords(name) -> (lon, lat) | None`

1. Static dict hit  → return instantly (no Redis, no network).
2. Redis cache hit  → return cached value (positive or negative).
3. Google Geocoding → `components=country:IL`, `language=he`.
4. Validate result is inside Israel's bounding box
   (lat 29.5-33.3, lon 34.2-35.9). Reject anything outside.
5. Cache: positive = infinite TTL (city coords don't move), negative =
   24h TTL (configurable, retry after a quota reset / spelling fix).

Returns `(lon, lat)` as a tuple of floats, matching the GeoJSON ordering
used by MongoDB's `$geoNear`. Returns `None` if the location can't be
resolved (caller falls back to the regex path or escalates).

No exceptions leak. Google/Redis failures degrade to `None` and log.
"""
from __future__ import annotations

import json
from typing import Optional, Tuple

import httpx

from app.core.config import settings
from app.core.constants import ISRAEL_CITIES_COORDS
from app.core.logger import logger
from app.core.redis_client import get_redis_client

# Israel bounding box — generous enough to include Eilat in the south
# (29.55) and Metula in the north (33.28), the Mediterranean coast in
# the west (34.27) and the Golan/Dead Sea in the east (35.89). Anything
# outside this is rejected — prevents Google from returning e.g. a
# match in Jordan or Egypt when the Israeli spelling is ambiguous.
ISRAEL_LAT_MIN, ISRAEL_LAT_MAX = 29.5, 33.3
ISRAEL_LON_MIN, ISRAEL_LON_MAX = 34.2, 35.9

_CACHE_PREFIX = "geo:city:"
# Sentinel stored in Redis for "we asked Google and it doesn't know".
# Chosen so it can never collide with a real serialized coord pair.
_NEGATIVE_CACHE_VALUE = "__NULL__"

_GOOGLE_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"


def _normalize(name: str) -> str:
    """Lowercase + strip + collapse whitespace. Keeps the cache key stable
    across trivial formatting differences ('Tel Aviv', '  tel aviv  ')."""
    return " ".join(name.lower().strip().split())


def _inside_israel(lat: float, lon: float) -> bool:
    return (ISRAEL_LAT_MIN <= lat <= ISRAEL_LAT_MAX
            and ISRAEL_LON_MIN <= lon <= ISRAEL_LON_MAX)


def _static_lookup(name: str) -> Optional[Tuple[float, float]]:
    """The pre-existing static dict — fast path. Returns (lon, lat) or None."""
    coords = ISRAEL_CITIES_COORDS.get(name.lower().strip())
    if coords:
        lon, lat = coords[0], coords[1]
        return (lon, lat)
    return None


async def _cache_get(key: str) -> Optional[str]:
    try:
        redis = await get_redis_client()
        return await redis.get(key)
    except Exception as e:
        logger.debug(f"geocoding cache get failed for {key}: {e}")
        return None


async def _cache_set(key: str, value: str, ttl: Optional[int] = None) -> None:
    """ttl=None means persist forever (positive cache)."""
    try:
        redis = await get_redis_client()
        if ttl is None:
            await redis.set(key, value)
        else:
            await redis.set(key, value, ex=ttl)
    except Exception as e:
        logger.debug(f"geocoding cache set failed for {key}: {e}")


async def _call_google(name: str) -> Optional[Tuple[float, float]]:
    """
    Hit Google Geocoding. Returns (lon, lat) on success, None otherwise.

    Constrained with:
      * `components=country:IL` — never match outside Israel
      * `language=he`           — handle Hebrew city names canonically
      * 5s timeout              — keep the dispatcher latency bounded
    """
    if not settings.GOOGLE_MAPS_API_KEY:
        logger.debug("Geocoding: GOOGLE_MAPS_API_KEY not set, skipping network call")
        return None

    params = {
        "address": name,
        "components": "country:IL",
        "language": "he",
        "key": settings.GOOGLE_MAPS_API_KEY,
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(_GOOGLE_GEOCODE_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning(f"Geocoding: Google API call failed for {name!r}: {e}")
        return None

    status = data.get("status")
    if status == "ZERO_RESULTS":
        logger.info(f"Geocoding: Google returned no results for {name!r}")
        return None
    if status != "OK":
        # OVER_QUERY_LIMIT, REQUEST_DENIED, INVALID_REQUEST, etc.
        logger.warning(f"Geocoding: Google status={status} for {name!r}: "
                       f"{data.get('error_message', '')}")
        return None

    results = data.get("results") or []
    if not results:
        return None

    loc = results[0].get("geometry", {}).get("location", {})
    lat = loc.get("lat")
    lon = loc.get("lng")
    if lat is None or lon is None:
        logger.warning(f"Geocoding: malformed Google response for {name!r}: {loc}")
        return None

    if not _inside_israel(float(lat), float(lon)):
        # Guards against Google resolving a name to a neighboring country
        # despite the country:IL hint (edge case: shared place names).
        logger.warning(f"Geocoding: {name!r} resolved to ({lat}, {lon}) — "
                       f"outside Israel bounds. Rejecting.")
        return None

    return (float(lon), float(lat))


async def resolve_city_to_coords(name: str) -> Optional[Tuple[float, float]]:
    """
    Resolve a city/locality name to (lon, lat).

    Returns None when the name is empty, can't be geocoded, or falls
    outside Israel. Never raises.
    """
    if not name or not name.strip():
        return None

    # 1. Static fast-path
    static_hit = _static_lookup(name)
    if static_hit is not None:
        return static_hit

    normalized = _normalize(name)
    cache_key = f"{_CACHE_PREFIX}{normalized}"

    # 2. Cache
    cached = await _cache_get(cache_key)
    if cached is not None:
        if cached == _NEGATIVE_CACHE_VALUE:
            logger.debug(f"Geocoding: negative cache hit for {name!r}")
            return None
        try:
            lon, lat = json.loads(cached)
            return (float(lon), float(lat))
        except (ValueError, TypeError) as e:
            # Corrupt cache entry — fall through to Google and overwrite.
            logger.warning(f"Geocoding: corrupt cache for {cache_key}: {e}")

    # 3. Google
    result = await _call_google(name)

    # 4. Cache the outcome (positive forever, negative for N seconds)
    if result is not None:
        lon, lat = result
        await _cache_set(cache_key, json.dumps([lon, lat]), ttl=None)
        logger.info(f"Geocoding: resolved {name!r} → ({lon}, {lat}) [cached ∞]")
        return result
    else:
        await _cache_set(
            cache_key,
            _NEGATIVE_CACHE_VALUE,
            ttl=settings.GEOCODING_NEGATIVE_TTL_SECONDS,
        )
        return None
