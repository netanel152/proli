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
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

import httpx

from app.core.config import settings
from app.core.constants import ISRAEL_CITIES_COORDS
from app.core.logger import logger, page_critical
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
# Sentinel for "we could not ask Google" (transient failure, short TTL). It is
# a distinct value from the definitive one so a reader that cares about the
# difference — the pro-approval check — can tell "this name is unresolvable"
# from "the geocoder was down a moment ago" without a second network call.
# `resolve_city_to_coords` treats both as a plain miss, exactly as before.
_TRANSIENT_CACHE_VALUE = "__RETRY__"

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

    ``page_critical`` is deliberate and is the only thing in this module
    that reaches an operator: Sentry is configured CRITICAL-only (and only
    sees stdlib records, PRO-113), so an ``error`` here would be a
    breadcrumb nobody sees. Geocoding being down
    is precisely the silent failure PRO-19 exists to surface — every city
    outside the static dict falls back to a regex that rarely matches, and
    those leads escalate to PENDING_ADMIN_REVIEW while the system looks
    healthy.

    Safe to page from here because it is rate-limited by construction: this
    runs at most once per ``GEOCODING_TRANSIENT_TTL_SECONDS`` window, so a
    sustained outage is ~1 event per window, not one per lookup. (Each
    ``reason`` renders a distinct message, so Sentry may open a distinct
    issue per reason rather than grouping — acceptable at this rate.)
    """
    page_critical(
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


# Outcome classes of one lookup. Strings, not an Enum, so they serialise
# straight into a log line or an audit entry.
GEOCODE_RESOLVED = "resolved"
# Google answered and the name really is unknown (or outside Israel).
GEOCODE_UNRESOLVED = "unresolved"
# The geocoder could not be asked: no key, quota, denial, network, breaker
# open. Says nothing about the name — a caller that gates on this must not
# treat it as a verdict.
GEOCODE_UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class GeocodeResult:
    """One lookup's outcome: where it landed, and why when it did not."""

    status: str
    coords: Optional[Tuple[float, float]] = None

    @property
    def resolved(self) -> bool:
        return self.status == GEOCODE_RESOLVED


async def geocode(name: str) -> GeocodeResult:
    """
    Resolve a city/locality name to (lon, lat), reporting *why* on a miss.

    Same pipeline as ``resolve_city_to_coords`` — static dict, Redis cache,
    circuit breaker, Google — and the same caching side effects. The only
    difference is the return type: a miss is either ``GEOCODE_UNRESOLVED``
    (Google answered; the name is unknown) or ``GEOCODE_UNAVAILABLE`` (we
    could not ask). Never raises.
    """
    if not name or not name.strip():
        return GeocodeResult(GEOCODE_UNRESOLVED)

    # 1. Static fast-path
    static_hit = _static_lookup(name)
    if static_hit is not None:
        return GeocodeResult(GEOCODE_RESOLVED, static_hit)

    normalized = _normalize(name)
    cache_key = f"{_CACHE_PREFIX}{normalized}"

    # 2. Cache
    cached = await _cache_get(cache_key)
    if cached is not None:
        if cached == _NEGATIVE_CACHE_VALUE:
            logger.debug(f"Geocoding: negative cache hit for {name!r}")
            return GeocodeResult(GEOCODE_UNRESOLVED)
        if cached == _TRANSIENT_CACHE_VALUE:
            logger.debug(f"Geocoding: transient-miss cache hit for {name!r}")
            return GeocodeResult(GEOCODE_UNAVAILABLE)
        try:
            lon, lat = json.loads(cached)
            return GeocodeResult(GEOCODE_RESOLVED, (float(lon), float(lat)))
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
        return GeocodeResult(GEOCODE_UNAVAILABLE)

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
            _TRANSIENT_CACHE_VALUE,
            ttl=settings.GEOCODING_TRANSIENT_TTL_SECONDS,
        )
        return GeocodeResult(GEOCODE_UNAVAILABLE)
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
            _TRANSIENT_CACHE_VALUE,
            ttl=settings.GEOCODING_TRANSIENT_TTL_SECONDS,
        )
        return GeocodeResult(GEOCODE_UNAVAILABLE)

    # 5. Cache the outcome (positive forever, definitive negative for 24h)
    if result is not None:
        lon, lat = result
        await _cache_set(cache_key, json.dumps([lon, lat]), ttl=None)
        # Highest-volume line in the module. `name` is often a full street
        # address, so it stays at debug — an address plus its exact
        # coordinates is the most identifying pair this service handles.
        logger.info(f"Geocoding: resolved a new location → ({lon}, {lat}) [cached ∞]")
        logger.debug(f"Geocoding: resolved {name!r} → ({lon}, {lat})")
        return GeocodeResult(GEOCODE_RESOLVED, result)

    await _cache_set(
        cache_key,
        _NEGATIVE_CACHE_VALUE,
        ttl=settings.GEOCODING_NEGATIVE_TTL_SECONDS,
    )
    return GeocodeResult(GEOCODE_UNRESOLVED)


async def resolve_city_to_coords(name: str) -> Optional[Tuple[float, float]]:
    """
    Resolve a city/locality name to (lon, lat).

    Returns None when the name is empty, can't be geocoded, or falls
    outside Israel. Never raises — a transient failure is logged and
    cached briefly, then reported to the caller as an ordinary miss.

    The routing hot path. Callers that need to know *why* a name missed
    (the pro-approval check) use ``geocode`` instead.
    """
    return (await geocode(name)).coords


# ---------------------------------------------------------------------------
# Pro service areas (PRO-27)
# ---------------------------------------------------------------------------
#
# Matching reaches a pro through `$geoNear` on the pro's GeoJSON `location`,
# so a pro without one is never offered a lead — the text fallback on
# `service_areas` only runs when the *lead's* location cannot be geocoded.
# These helpers turn a pro's `service_areas` list into that `location` plus a
# verdict the approval UI and the backfill script can act on, and they own
# the field names both writers use so the two cannot drift.

# Definitive misses at the last check — names Google does not know. Set only
# on the "approve anyway" path and by the backfill script; absent when clean.
SERVICE_AREAS_UNRESOLVED_FIELD = "service_areas_unresolved"
# True when the geocoder was unavailable for at least one area at the last
# check, so the verdict is incomplete and the pro needs a re-check
# (`scripts/check_pro_service_areas.py`). Approval is never blocked on it.
SERVICE_AREAS_GEOCODE_PENDING_FIELD = "service_areas_geocode_pending"
SERVICE_AREAS_CHECKED_AT_FIELD = "service_areas_checked_at"


@dataclass
class ServiceAreaResolution:
    """Every area of one pro, sorted into resolved / unresolved / unavailable.

    ``resolved`` keeps the input order, so ``location`` is the first area the
    pro listed that we could place — the one they presumably work from.
    """

    resolved: List[Tuple[str, Tuple[float, float]]] = field(default_factory=list)
    unresolved: List[str] = field(default_factory=list)
    unavailable: List[str] = field(default_factory=list)

    @property
    def location(self) -> Optional[dict]:
        """GeoJSON Point for `users.location` from the first resolved area."""
        if not self.resolved:
            return None
        _, (lon, lat) = self.resolved[0]
        return {"type": "Point", "coordinates": [lon, lat]}

    @property
    def clean(self) -> bool:
        """Every area resolved (and there was at least one)."""
        return bool(self.resolved) and not self.unresolved and not self.unavailable

    @property
    def needs_recheck(self) -> bool:
        """The geocoder was down for at least one area — verdict incomplete."""
        return bool(self.unavailable)

    @property
    def blocks_approval(self) -> bool:
        """Nothing can place this pro and nothing is pending a retry: either
        no areas at all or every one is a definitive miss. Approving would
        create exactly the silently-unmatchable pro this check exists to
        prevent, so there is no "approve anyway" for this case."""
        return not self.resolved and not self.unavailable

    def mongo_update(self, *, now: datetime, include_location: bool) -> dict:
        """The `update_one` document that records this verdict on the pro.

        ``include_location`` is the caller's call: approval always writes it
        (approval is the authoritative moment); the backfill script writes it
        only for pros that have none unless told to overwrite.
        """
        set_fields: dict = {SERVICE_AREAS_CHECKED_AT_FIELD: now}
        unset_fields: dict = {}
        if include_location and self.location is not None:
            set_fields["location"] = self.location
        if self.unresolved:
            set_fields[SERVICE_AREAS_UNRESOLVED_FIELD] = list(self.unresolved)
        else:
            unset_fields[SERVICE_AREAS_UNRESOLVED_FIELD] = ""
        if self.unavailable:
            set_fields[SERVICE_AREAS_GEOCODE_PENDING_FIELD] = True
        else:
            unset_fields[SERVICE_AREAS_GEOCODE_PENDING_FIELD] = ""
        update = {"$set": set_fields}
        if unset_fields:
            update["$unset"] = unset_fields
        return update


def parse_service_areas(text: str) -> List[str]:
    """Split an operator-typed comma list into area names — same rule as the
    WhatsApp onboarding step (Arabic comma tolerated, blanks dropped)."""
    return [a.strip() for a in (text or "").replace("،", ",").split(",") if a.strip()]


async def resolve_service_areas(areas) -> ServiceAreaResolution:
    """Geocode each of a pro's service areas and sort the outcomes.

    Blank entries are dropped, duplicates (after normalisation) are looked up
    once. Sequential on purpose: a handful of names, and the second lookup
    of an outage benefits from the breaker the first one opened.
    """
    result = ServiceAreaResolution()
    seen = set()
    for raw in areas or []:
        name = str(raw).strip()
        if not name:
            continue
        key = _normalize(name)
        if key in seen:
            continue
        seen.add(key)
        outcome = await geocode(name)
        if outcome.resolved:
            result.resolved.append((name, outcome.coords))
        elif outcome.status == GEOCODE_UNAVAILABLE:
            result.unavailable.append(name)
        else:
            result.unresolved.append(name)
    return result


# Per-area budget for the blocking bridge: one Google call is capped at 5s
# inside _call_google; the rest is Redis round-trips and slack.
_SYNC_PER_AREA_SECONDS = 6.0
_SYNC_MIN_TIMEOUT_SECONDS = 15.0


def resolve_service_areas_sync(areas) -> ServiceAreaResolution:
    """``resolve_service_areas`` for the synchronous admin panel.

    Runs on the process's one sync→async bridge loop (``app.core.sync_bridge``,
    shared with the WhatsApp facade) so the cached Redis client is never
    handed to a second loop. Never raises: a bridge failure (timeout, loop
    error) is reported as every area *unavailable* — the "could not check"
    verdict, which flags the pro for a re-check rather than blocking the
    operator on our own fault.
    """
    from app.core.sync_bridge import run_blocking

    # Same dedupe rule as resolve_service_areas, so the fallback below reports
    # the same set of names a successful run would have checked.
    names: List[str] = []
    seen = set()
    for raw in areas or []:
        name = str(raw).strip()
        if name and _normalize(name) not in seen:
            seen.add(_normalize(name))
            names.append(name)
    timeout = max(_SYNC_MIN_TIMEOUT_SECONDS, _SYNC_PER_AREA_SECONDS * len(names))
    try:
        return run_blocking(resolve_service_areas(names), timeout)
    except Exception as e:
        logger.error(f"Geocoding: service-area check could not run: {e}")
        return ServiceAreaResolution(unavailable=names)
