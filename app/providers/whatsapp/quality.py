"""PRO-90 — number-level quality state, tracked in Redis.

The worker's watchdog (``monitor_service.check_whatsapp_quality``) writes one
snapshot of what Meta says about the number; ``/health`` and the non-essential
send gate read it from any process, which is why it lives in Redis rather than
on the provider.

The watchdog polls instead of consuming the ``phone_number_quality_update``
webhook because a subscription nothing can confirm fails silently, and silence
is also what a healthy GREEN looks like. A poll that stops working is visible;
a webhook that was never delivered is not.

Failure posture, deliberately one-directional: not knowing never reads as
healthy. A failed or stale poll reports ``UNKNOWN`` on ``/health`` (the last
reading moves to ``last_known``), and in production an unreadable or absent
snapshot pauses non-essential sends. Dispatch never consults this module.
"""

import json
import time
from typing import Any

from app.core.config import settings
from app.core.constants import WorkerConstants
from app.core.logger import logger
from app.core.redis_client import get_redis_client

_SNAPSHOT_KEY = "wa:quality:snapshot"
_BUSINESS_INITIATED_KEY = "wa:quality:business_initiated"
_PAGED_KEY_PREFIX = "wa:quality:paged:"

UNKNOWN = "UNKNOWN"
# NA/UNKNOWN is Meta's "not rated yet", the normal state of a fresh number. It
# is reported as-is but does not page: nothing has left GREEN.
_RATINGS_NOT_ALARMING = {"GREEN", "NA", UNKNOWN}

# Meta's limit is unique recipients outside the service window per moving 24h,
# so the counter is a rolling set rather than a calendar-day integer: a burst
# either side of midnight is one window to Meta and would be two days here.
#
# shortcut: this counts one environment's number, while Meta shares the limit
# across every number in the business portfolio. Add the other numbers' traffic
# if a portfolio ever holds more than one live number.
_BUSINESS_INITIATED_WINDOW_SECONDS = 86400
# Meta's page shows only TIER_250 as an exact string; the others are inferred
# from its 2,000/10,000/100,000 tiers. Anything else takes the configured cap.
_TIER_CAPS = {
    "TIER_250": 250,
    "TIER_2K": 2000,
    "TIER_10K": 10000,
    "TIER_100K": 100000,
}

# Stand-in for the Redis page claim while Redis cannot answer: condition to the
# monotonic time it last paged. Per-process, so replicas may each page once.
_LOCAL_PAGE_CLAIMS: dict[str, float] = {}


async def read_snapshot() -> dict[str, Any] | None:
    """The stored snapshot, ``None`` when absent. Raises on a Redis error so
    each caller picks its own failure direction."""
    redis = await get_redis_client()
    raw = await redis.get(_SNAPSHOT_KEY)
    return json.loads(raw) if raw else None


async def write_snapshot(snapshot: dict[str, Any]) -> None:
    """No TTL: staleness is computed from ``last_attempt_at``, so a dead
    watchdog shows up as ``stale`` rather than as a missing key."""
    redis = await get_redis_client()
    await redis.set(_SNAPSHOT_KEY, json.dumps(snapshot))


def merge_reading(
    reading: dict[str, Any] | None, previous: dict[str, Any]
) -> dict[str, Any]:
    """Fold one poll into the previous snapshot.

    A value Meta did not return keeps its last known value, so a condition
    cannot clear itself just because the poll failed. A poll counts as good only
    when it carried both the rating and the health status: a 200 without either
    is blindness too. A missing limit is not, it only leaves the tier unknown.
    """
    snapshot = dict(previous)
    if reading:
        snapshot.update({k: v for k, v in reading.items() if v is not None})
    poll_ok = bool(
        reading and reading.get("quality_rating") and reading.get("can_send_message")
    )
    snapshot["poll_ok"] = poll_ok
    snapshot["last_attempt_at"] = time.time()
    if poll_ok:
        snapshot["last_success_at"] = snapshot["last_attempt_at"]
        snapshot["consecutive_failures"] = 0
    else:
        snapshot["consecutive_failures"] = previous.get("consecutive_failures", 0) + 1
    return snapshot


def active_conditions(snapshot: dict[str, Any]) -> list[str]:
    """Every standing problem, each a stable string the page claim keys on.

    ``status`` values other than FLAGGED are absent on purpose: the deauth
    watchdog already pages on them and halts outbound.
    """
    conditions = []
    rating = snapshot.get("quality_rating")
    if rating and rating not in _RATINGS_NOT_ALARMING:
        conditions.append(f"rating:{rating}")
    if snapshot.get("phone_status") == "FLAGGED":
        conditions.append("phone:FLAGGED")
    can_send = snapshot.get("can_send_message")
    if can_send and can_send != "AVAILABLE":
        conditions.append(f"waba:{can_send}")
    failures = snapshot.get("consecutive_failures", 0)
    if failures >= WorkerConstants.WA_QUALITY_POLL_FAILURE_THRESHOLD:
        conditions.append("poll:failed")
    return conditions


async def claim_page(condition: str) -> bool:
    """True at most once per cooldown per ``condition``, standing or flapping.

    YELLOW then RED are distinct conditions and each page.
    """
    cooldown = WorkerConstants.WA_QUALITY_PAGE_COOLDOWN_HOURS * 3600
    try:
        redis = await get_redis_client()
        return bool(
            await redis.set(
                f"{_PAGED_KEY_PREFIX}{condition}", "1", ex=cooldown, nx=True
            )
        )
    except Exception as e:
        logger.warning(f"[WA Quality] page claim fell back to in-process: {e}")
        now = time.monotonic()
        last = _LOCAL_PAGE_CLAIMS.get(condition)
        if last is not None and now - last < cooldown:
            return False
        _LOCAL_PAGE_CLAIMS[condition] = now
        return True


async def record_business_initiated(chat_id: str) -> None:
    """Count one recipient against Meta's limit. Never blocks a send."""
    try:
        redis = await get_redis_client()
        now = time.time()
        await redis.zadd(_BUSINESS_INITIATED_KEY, {chat_id: now})
        await redis.zremrangebyscore(
            _BUSINESS_INITIATED_KEY, 0, now - _BUSINESS_INITIATED_WINDOW_SECONDS
        )
        await redis.expire(
            _BUSINESS_INITIATED_KEY, _BUSINESS_INITIATED_WINDOW_SECONDS * 2
        )
    except Exception as e:
        logger.warning(f"[WA Quality] could not count business-initiated send: {e}")


async def business_initiated_count() -> int | None:
    """Unique recipients in the last 24h, ``None`` when Redis cannot answer."""
    try:
        redis = await get_redis_client()
        return await redis.zcount(
            _BUSINESS_INITIATED_KEY,
            time.time() - _BUSINESS_INITIATED_WINDOW_SECONDS,
            "+inf",
        )
    except Exception as e:
        logger.warning(f"[WA Quality] could not read business-initiated count: {e}")
        return None


def business_initiated_cap(tier: str | None) -> int:
    """The live limit's cap, else the configured one. Meta's docs name only
    ``TIER_250`` outright, so an unrecognized value must still give a usable cap."""
    return _TIER_CAPS.get(tier, settings.WA_BUSINESS_INITIATED_CAP)


def poll_status(snapshot: dict[str, Any] | None, transmits: bool) -> str:
    if not transmits:
        return "not_applicable"
    if snapshot is None:
        return "never"
    age_seconds = time.time() - snapshot.get("last_attempt_at", 0)
    if age_seconds > WorkerConstants.WA_QUALITY_STALE_MINUTES * 60:
        return "stale"
    return "ok" if snapshot.get("poll_ok") else "failed"


def window_state(snapshot: dict[str, Any] | None, status: str) -> str:
    """Whether the *number* can send: ``open``, ``limited``, ``blocked`` or
    ``unknown``. The 24h service window is per recipient (``window.py``) and has
    no single value, so this reports the number-level gate. The state table is in
    docs/RUNBOOK_WHATSAPP_OUTAGE.md §8.
    """
    if status != "ok":
        return "unknown"
    phone_status = snapshot.get("phone_status")
    can_send = snapshot.get("can_send_message")
    if not phone_status:
        return "unknown"
    if phone_status not in ("CONNECTED", "FLAGGED") or can_send == "BLOCKED":
        return "blocked"
    if phone_status == "FLAGGED" or can_send == "LIMITED":
        return "limited"
    return "open" if can_send == "AVAILABLE" else "unknown"


async def health_fields(transmits: bool) -> dict[str, Any]:
    """What authenticated ``/health`` shows. Never raises."""
    try:
        snapshot = await read_snapshot()
        status = poll_status(snapshot, transmits)
    except Exception as e:
        logger.warning(f"[WA Quality] snapshot unreadable for /health: {e}")
        snapshot, status = None, "unreadable"

    stored = snapshot or {}
    fresh = status == "ok"
    tier = stored.get("messaging_tier")
    fields = {
        "quality_rating": stored.get("quality_rating", UNKNOWN) if fresh else UNKNOWN,
        "messaging_tier": tier or UNKNOWN if fresh else UNKNOWN,
        "window_state": window_state(stored, status),
        "can_send_message": (
            stored.get("can_send_message", UNKNOWN) if fresh else UNKNOWN
        ),
        "quality_poll": {
            "status": status,
            "last_success_at": stored.get("last_success_at"),
            "consecutive_failures": stored.get("consecutive_failures", 0),
        },
        "conditions": stored.get("conditions", []),
        "tightened": _is_tightened(stored, status),
        "alerts_enabled": settings.is_production,
        "business_initiated_24h": {
            "count": await business_initiated_count(),
            "cap": business_initiated_cap(tier),
        },
    }
    if not fresh and stored.get("quality_rating"):
        fields["last_known"] = {
            "quality_rating": stored.get("quality_rating"),
            "messaging_tier": tier,
            "can_send_message": stored.get("can_send_message"),
        }
    return fields


def _is_tightened(stored: dict[str, Any], status: str) -> bool:
    """Staging observes and production acts, so only production ever tightens.
    A watchdog that is not running there tightens too: it cannot vouch for the
    number. Outside those cases the last decision stands, so one failed poll
    neither tightens nor releases."""
    if not settings.is_production or status == "not_applicable":
        return False
    return status in ("never", "stale", "unreadable") or bool(stored.get("tightened"))


async def non_essential_sends_paused() -> bool:
    """Whether the pro-finish rating prompt and loyalty offers should hold back.

    Only those two. The scheduled completion check must never ask this: it is the
    customer's done / not yet / no-show menu, and a lead that misses it passing
    24h never gets it.

    Answers before touching Redis outside production, so no test or staging path
    pays for it. Unreadable Redis pauses: losing one of these sends is harmless,
    and sending blind on a possibly degraded number is the thing to avoid.
    """
    if not settings.is_production:
        return False
    # Deferred: get_whatsapp is defined in this package's __init__.
    from app.providers.whatsapp import get_whatsapp

    try:
        transmits = get_whatsapp().provider.transmits
        snapshot = await read_snapshot()
    except Exception as e:
        logger.warning(f"[WA Quality] snapshot unreadable, pausing sends: {e}")
        return True
    return _is_tightened(snapshot or {}, poll_status(snapshot, transmits))
