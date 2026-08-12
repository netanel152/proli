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

Definitive vs transient misses (PRO-19)
---------------------------------------
Not every "no coordinates" outcome means the same thing, and caching them
identically is what makes a geocoding outage outlive its cause:

* **Definitive** — `ZERO_RESULTS`, or a match outside Israel. Google
  answered; this name really is unresolvable. Negative-cached for the
  full `GEOCODING_NEGATIVE_TTL_SECONDS` (24h).
* **Transient / misconfigured** — no API key, `REQUEST_DENIED`,
  `OVER_QUERY_LIMIT`, network error, timeout, HTTP 5xx. Google never
  gave a verdict on the *name*; the failure is about us. These raise
  `GeocodingUnavailable` and are cached for only
  `GEOCODING_TRANSIENT_TTL_SECONDS` (60s).

Without that split, a lapsed billing account or a deploy that forgot the
key would pin every city name attempted during the window as
"unresolvable" for 24 hours *after* the fix — leads still escalating to
PENDING_ADMIN_REVIEW while the system looks healthy. That is the exact
2026-04-18 failure mode. A short TTL (rather than no caching) keeps a
sustained outage from turning every inbound message into a fresh 5s
timeout on the dispatcher's hot path.
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

# Circuit-breaker key. Set for GEOCODING_TRANSIENT_TTL_SECONDS after any
# transient failure; while present, lookups skip Google entirely instead of
# each paying the 5s timeout. Self-healing — it simply expires.
_UNAVAILABLE_KEY = "geo:unavailable"

_GOOGLE_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"

# Google statuses that say something about *us*, not about the name we
# asked for. Treated as transient so a definitive 24h negative entry is
# never written on their account.
_TRANSIENT_GOOGLE_STATUSES = frozenset(
    {
        "REQUEST_DENIED",  # key invalid / API not enabled / referer blocked
        "OVER_QUERY_LIMIT",  # quota exhausted or billing lapsed
        "OVER_DAILY_LIMIT",  # legacy billing-disabled status
        "UNKNOWN_ERROR",  # Google-side hiccup, explicitly retryable
        # Malformed params — our bug, not a bad city. Deterministic for
        # identical params, so retrying can't fix it; but it is deploy-
        # fixable, and giving it the definitive 24h TTL would pin every
        # address touched by the bug as unresolvable for a day *after* the
        # fix ships — the exact poisoning this issue exists to remove. The
        # breaker bounds the retry cost to one probe per window.
        "INVALID_REQUEST",
    }
)


class GeocodingUnavailable(Exception):
    """Geocoding could not be attempted or completed for reasons unrelated
    to the name being looked up (missing key, quota, denial, network).

    Never escapes this module: ``resolve_city_to_coords`` catches it and
    degrades to ``None``, exactly as before. It exists only so the caller
    can tell "Google says this city does not exist" apart from "we could
    not ask Google" and pick the right negative-cache TTL.
    """


def _normalize(name: str) -> str:
    """Lowercase + strip + collapse whitespace. Keeps the cache key stable
    across trivial formatting differences ('Tel Aviv', '  tel aviv  ')."""
    return " ".join(name.lower().strip().split())


def _inside_israel(lat: float, lon: float) -> bool:
    return (
        ISRAEL_LAT_MIN <= lat <= ISRAEL_LAT_MAX
        and ISRAEL_LON_MIN <= lon <= ISRAEL_LON_MAX
    )


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


async def _open_circuit(reason: str) -> None:
    """Trip the breaker for one transient-TTL window, and page.

    ``logger.critical`` is deliberate and is the only thing in this module
    that reaches an operator: Sentry is configured CRITICAL-only, so an
    ``error`` here would be a breadcrumb nobody sees. Geocoding being down
    is precisely the silent failure PRO-19 exists to surface — every city
    outside the static dict falls back to a regex that rarely matches, and
    those leads escalate to PENDING_ADMIN_REVIEW while the system looks
    healthy.

    Safe to page from here because it is rate-limited by construction: this
    runs at most once per ``GEOCODING_TRANSIENT_TTL_SECONDS`` window, so a
    sustained outage is ~1 event per window (which Sentry then groups),
    not one per lookup.
    """
    logger.critical(
        f"Geocoding unavailable — circuit opened for "
        f"{settings.GEOCODING_TRANSIENT_TTL_SECONDS}s ({reason}). Cities "
        f"outside the static dict will not resolve; their leads will escalate."
    )
    await _cache_set(
        _UNAVAILABLE_KEY, "1", ttl=settings.GEOCODING_TRANSIENT_TTL_SECONDS
    )


async def _call_google(name: str) -> Optional[Tuple[float, float]]:
    """
    Hit Google Geocoding.

    Returns (lon, lat) on success, or None for a *definitive* miss —
    Google answered and the name is unresolvable (ZERO_RESULTS) or the
    match fell outside Israel.

    Raises ``GeocodingUnavailable`` when the lookup could not be completed
    for reasons unrelated to the name (no key, denial, quota, network,
    HTTP error). The caller uses that distinction to choose the
    negative-cache TTL — see the module docstring.

    Constrained with:
      * `components=country:IL` — never match outside Israel
      * `language=he`           — handle Hebrew city names canonically
      * 5s timeout              — keep the dispatcher latency bounded
    """
    if not settings.GOOGLE_MAPS_API_KEY:
        # Config gap, not a bad city name — must not be cached as a
        # definitive miss, or a deploy that forgot the key poisons every
        # name it sees for 24h without a single network call.
        # Volume is bounded by the circuit breaker in resolve_city_to_coords:
        # once this trips, further lookups short-circuit for the transient TTL.
        logger.error(
            "Geocoding: GOOGLE_MAPS_API_KEY not set — cities outside the "
            "static dict cannot be resolved and their leads will escalate."
        )
        raise GeocodingUnavailable("GOOGLE_MAPS_API_KEY not set")

    params = {
        "address": name,
        "components": "country:IL",
        "language": "he",
        "key": settings.GOOGLE_MAPS_API_KEY.get_secret_value(),
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(_GOOGLE_GEOCODE_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        # Timeout, DNS, connection reset, HTTP 5xx via raise_for_status —
        # all transient. Retry sooner rather than blacklisting the name.
        # error (not warning) to match the status-failure branch below: a
        # total Google outage is exactly this shape, and it must not be the
        # one failure mode that produces no ERROR line. `name` is kept out
        # of the message — it can be a full street address (PII), not just
        # a city; it goes to debug instead.
        logger.error(f"Geocoding: Google API call failed: {e}")
        logger.debug(f"Geocoding: failing lookup was {name!r}")
        raise GeocodingUnavailable(f"Google API call failed: {e}") from e

    status = data.get("status")
    if status == "ZERO_RESULTS":
        # Definitive: Google looked and found nothing under country:IL.
        logger.info("Geocoding: Google returned no results")
        logger.debug(f"Geocoding: unresolved lookup was {name!r}")
        return None
    if status in _TRANSIENT_GOOGLE_STATUSES:
        # Key, quota/billing, or a Google-side fault. logger.error (not
        # warning) so an operator can separate "geocoding is broken" from
        # the ordinary stream of unresolvable-name warnings. `name` is
        # withheld from error level — it can be a full street address.
        logger.error(
            f"Geocoding: Google status={status}: {data.get('error_message', '')}"
        )
        logger.debug(f"Geocoding: failing lookup was {name!r}")
        raise GeocodingUnavailable(f"Google status={status}")
    if status != "OK":
        # Unrecognized status — treat as transient. Better to retry in 60s
        # than to pin a possibly-valid city as dead for a day.
        logger.error(
            f"Geocoding: unexpected Google status={status}: "
            f"{data.get('error_message', '')}"
        )
        logger.debug(f"Geocoding: failing lookup was {name!r}")
        raise GeocodingUnavailable(f"Google status={status}")

    results = data.get("results") or []
    if not results:
        # status=OK with an empty result list is a Google-side anomaly,
        # not a verdict on the name.
        logger.error("Geocoding: Google status=OK but returned no results")
        raise GeocodingUnavailable("Google returned OK with no results")

    loc = results[0].get("geometry", {}).get("location", {})
    lat = loc.get("lat")
    lon = loc.get("lng")
    if lat is None or lon is None:
        # Same reasoning: a malformed payload says nothing about the city.
        logger.error(f"Geocoding: malformed Google response: {loc}")
        raise GeocodingUnavailable("malformed Google response")

    if not _inside_israel(float(lat), float(lon)):
        # Guards against Google resolving a name to a neighboring country
        # despite the country:IL hint (edge case: shared place names).
        logger.warning(
            f"Geocoding: lookup resolved to ({lat}, {lon}) — "
            f"outside Israel bounds. Rejecting."
        )
        logger.debug(f"Geocoding: rejected lookup was {name!r}")
        return None

    return (float(lon), float(lat))


async def resolve_city_to_coords(name: str) -> Optional[Tuple[float, float]]:
    """
    Resolve a city/locality name to (lon, lat).

    Returns None when the name is empty, can't be geocoded, or falls
    outside Israel. Never raises — a transient failure is logged and
    cached briefly, then reported to the caller as an ordinary miss.
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
            # cache_key embeds the normalized lookup — debug only, same
            # reason as the name itself.
            logger.warning(f"Geocoding: corrupt cache entry, refetching: {e}")
            logger.debug(f"Geocoding: corrupt key was {cache_key}")

    # 3. Circuit breaker. A per-name short TTL bounds *repeat* lookups, but
    #    the names here are often full addresses and so near-unique per
    #    customer — during a Google outage almost every lookup would be a
    #    first occurrence paying the full 5s timeout on the dispatcher's hot
    #    path. This collapses an outage to roughly one probe per transient-TTL
    #    window across the whole deployment (the key lives in shared Redis).
    #    "Roughly" because the check-then-act is not atomic: concurrent worker
    #    jobs can each start a probe before the first _open_circuit lands, so
    #    a window admits up to `max_jobs` probes, not exactly one. Benign and
    #    self-healing. Same shape as the `wa:instance:paused` breaker in
    #    whatsapp_client_service; fails open (a Redis error returns None).
    if await _cache_get(_UNAVAILABLE_KEY):
        logger.debug("Geocoding: circuit open (recent transient failure), skipping")
        return None

    # 4. Google. A transient failure (no key, quota, denial, network) is
    #    NOT a statement about this city, so it gets the short TTL — see
    #    the module docstring for why that distinction matters.
    try:
        result = await _call_google(name)
    except GeocodingUnavailable as e:
        # Already logged at error inside _call_google with the status and
        # Google's error_message — debug here so an outage isn't logged twice.
        logger.debug(
            f"Geocoding: transient failure ({e}) — caching miss for "
            f"{settings.GEOCODING_TRANSIENT_TTL_SECONDS}s only, will retry."
        )
        await _open_circuit(str(e))
        await _cache_set(
            cache_key,
            _NEGATIVE_CACHE_VALUE,
            ttl=settings.GEOCODING_TRANSIENT_TTL_SECONDS,
        )
        return None
    except Exception as e:
        # Deliberately does NOT open the circuit. A false trip disables
        # geocoding for *every* name for a full window, and that failure mode
        # (leads escalating with no pro tried) is worse than the cost of not
        # tripping. A parse error also means Google answered, so there is no
        # 5s stall to amortise — the breaker's whole justification is absent.
        # Trade-off: a systemic bad-gateway would stay unbounded here; a
        # repeat-count trip is the follow-up if that is ever observed.
        #
        # Module contract: no exceptions leak. _call_google parses an
        # untrusted JSON body outside its own try (a non-dict payload from a
        # proxy/WAF raises AttributeError; a non-numeric lat raises
        # ValueError). Letting that escape would be worse than a miss: it
        # unwinds past determine_best_pro's regex-on-service_areas fallback,
        # so the lead escalates with no pro tried — the exact PRO-19 failure.
        # An unexpected parse error says nothing about the city → short TTL.
        logger.error(f"Geocoding: unexpected error during lookup: {e}")
        logger.debug(f"Geocoding: failing lookup was {name!r}")
        await _cache_set(
            cache_key,
            _NEGATIVE_CACHE_VALUE,
            ttl=settings.GEOCODING_TRANSIENT_TTL_SECONDS,
        )
        return None

    # 5. Cache the outcome (positive forever, definitive negative for 24h)
    if result is not None:
        lon, lat = result
        await _cache_set(cache_key, json.dumps([lon, lat]), ttl=None)
        # Highest-volume line in the module. `name` is often a full street
        # address, so it stays at debug — an address plus its exact
        # coordinates is the most identifying pair this service handles.
        logger.info(f"Geocoding: resolved a new location → ({lon}, {lat}) [cached ∞]")
        logger.debug(f"Geocoding: resolved {name!r} → ({lon}, {lat})")
        return result
    else:
        await _cache_set(
            cache_key,
            _NEGATIVE_CACHE_VALUE,
            ttl=settings.GEOCODING_NEGATIVE_TTL_SECONDS,
        )
        return None
