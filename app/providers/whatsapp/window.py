"""PRO-89 — the Meta 24-hour customer-service window, tracked in Redis.

Meta delivers free-form messages only inside the 24 hours following the
recipient's most recent inbound message; outside that window only a
pre-approved template goes through. The window is **per recipient, not per
conversation** (see docs/WHATSAPP_TEMPLATE_CATALOG.md — it is the finding that
shaped PRO-88), so the key is the chat_id and nothing else.

``open_service_window`` is called by the Meta webhook route on every inbound
message; key-present therefore means window-open by construction, and the TTL
is Meta's 24h, not a tunable.

``open_service_window`` and ``is_service_window_open`` fail **open** on a
Redis error, deliberately mirroring the facade's Redis-error posture (PRO-82):
a monitoring/bookkeeping dependency going down must not take the send path
with it. The backstop is real — a send Meta rejects for a closed window comes
back as a ``failed`` delivery status with error 131047, which the status
handler routes through the template-retry path. Failing *closed* here would
instead silently convert a Redis blip into dropped customer replies, which is
the exact failure mode PRO-89 forbids.

``claim_window_page`` is the deliberate exception: it guards an *alert*, not a
send, so it fails **loud** rather than open — silence is the worse failure for
a pager. See its own docstring for why that is bounded rather than
unconditional.
"""

import time
from datetime import datetime, timezone

from app.core.constants import WorkerConstants
from app.core.logger import logger
from app.core.redis_client import get_redis_client

_WINDOW_KEY_PREFIX = "wa:window:"

# PRO-162/PRO-172: the "window closed and no approved fallback template"
# condition is detected in two places — before the send (``_window_fallback``)
# and again asynchronously when Meta rejects one with error 131047
# (``delivery._retry_as_template``). Both page through ``claim_window_page``
# against this one key, so a recipient is paged once a day for the condition
# rather than once per detection path per occurrence.
_PAGE_KEY_PREFIX = "wa:window:page:"

# A paging-policy knob, not Meta's rule — it equals SERVICE_WINDOW_TTL_SECONDS
# by coincidence, not derivation, so it is named separately.
_PAGE_TTL_SECONDS = 86400  # one page per recipient per day

# Best-effort fallback claims for when Redis cannot arbitrate (see
# ``claim_window_page``). Per-process and non-authoritative: two replicas may
# each page once. Bounded so a long outage cannot grow it without limit.
_LOCAL_PAGE_CLAIMS: dict[str, float] = {}
_LOCAL_PAGE_CLAIMS_MAX = 1000


def _window_key(chat_id: str) -> str:
    return f"{_WINDOW_KEY_PREFIX}{chat_id}"


def _page_key(chat_id: str) -> str:
    return f"{_PAGE_KEY_PREFIX}{chat_id}"


def _claim_locally(chat_id: str) -> bool:
    """In-process stand-in for the Redis claim, used when Redis cannot answer.

    Not a substitute for the real claim — it is per-process, so N replicas may
    each page once — but it converts "page on every occurrence" into "page
    once per recipient per process per period", which is the property that
    matters when the alternative is a flood.
    """
    now = time.monotonic()
    previous = _LOCAL_PAGE_CLAIMS.get(chat_id)
    # An explicit "never claimed" check, not a sentinel default: `time.monotonic()`
    # counts from an arbitrary epoch (process or boot start on most platforms), so
    # a `0.0` default would make `now - previous` smaller than the TTL on any host
    # up less than a day — denying the *first* page and, because nothing is then
    # recorded, every page after it. Silent suppression is the one outcome worse
    # than the flood this function exists to bound.
    if previous is not None and now - previous < _PAGE_TTL_SECONDS:
        return False
    if len(_LOCAL_PAGE_CLAIMS) > _LOCAL_PAGE_CLAIMS_MAX:
        _LOCAL_PAGE_CLAIMS.clear()
    _LOCAL_PAGE_CLAIMS[chat_id] = now
    return True


async def claim_window_page(chat_id: str) -> bool:
    """True at most once per recipient per 24h — the caller may page CRITICAL.

    With every registry entry still DRAFT, a closed window is the *expected*
    state for business-initiated sends, so paging on each occurrence would bury
    the one guaranteed operator channel (PRO-75/PRO-113) under a standing
    condition. A caller that gets ``False`` still reports the drop, at ERROR —
    losing the claim never means going quiet.

    Fail-**loud**, unlike the rest of this module: silence is the worse failure
    for an alert. But "loud" is bounded rather than unconditional, because the
    two postures interact badly — a Redis outage makes
    ``is_service_window_open`` fail *open*, so every business-initiated send is
    transmitted and rejected by Meta, and every rejection lands here. Page
    volume would peak exactly when the dedup key is unavailable. The
    in-process fallback keeps that bounded without ever swallowing the alert.

    Both detection paths (pre-send ``_window_fallback`` and the asynchronous
    131047 handler) share one budget **on purpose**: they report the same
    operator-facing condition. Note the asymmetry that creates — a pre-send
    block ("not sent", the caller still gets a `ServiceWindowClosedError`) can
    consume the day's budget and demote a later post-send loss ("accepted by
    Meta, then LOST") to ERROR. Accepted: one condition, one page.
    """
    if not chat_id:
        # No per-recipient key to claim. Collapsing every unattributable loss
        # into the bare prefix would hide all but the first for a whole day,
        # and these are the least diagnosable losses there are — so fall back
        # to the same bounded local claim an unreadable Redis gets.
        return _claim_locally("")
    try:
        redis = await get_redis_client()
        return bool(
            await redis.set(_page_key(chat_id), "1", ex=_PAGE_TTL_SECONDS, nx=True)
        )
    except Exception:
        return _claim_locally(chat_id)


async def open_service_window(chat_id: str) -> None:
    """Mark ``chat_id``'s service window open for the next 24 hours.

    The value is the inbound timestamp (UTC ISO) rather than a bare ``1`` so an
    operator inspecting Redis can see *when* the window opened; nothing reads
    the value programmatically — presence is the signal.
    """
    if not chat_id:
        return
    try:
        redis = await get_redis_client()
        await redis.set(
            _window_key(chat_id),
            datetime.now(timezone.utc).isoformat(),
            ex=WorkerConstants.SERVICE_WINDOW_TTL_SECONDS,
        )
    except Exception as e:
        logger.warning(f"Could not record service window for ...{chat_id[-4:]}: {e}")


async def is_service_window_open(chat_id: str) -> bool:
    """True when a free-form send to ``chat_id`` is deliverable.

    Fail-open: an unreadable Redis reports the window as open and lets Meta be
    the judge (131047 → template-retry path), per the module docstring.
    """
    try:
        redis = await get_redis_client()
        return bool(await redis.exists(_window_key(chat_id)))
    except Exception as e:
        logger.warning(
            f"Service-window check failed for ...{chat_id[-4:]} — "
            f"assuming open (fail-open): {e}"
        )
        return True
