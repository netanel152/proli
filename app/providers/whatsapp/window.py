"""PRO-89 — the Meta 24-hour customer-service window, tracked in Redis.

Meta delivers free-form messages only inside the 24 hours following the
recipient's most recent inbound message; outside that window only a
pre-approved template goes through. The window is **per recipient, not per
conversation** (see docs/WHATSAPP_TEMPLATE_CATALOG.md — it is the finding that
shaped PRO-88), so the key is the chat_id and nothing else.

``open_service_window`` is called by the Meta webhook route on every inbound
message; key-present therefore means window-open by construction, and the TTL
is Meta's 24h, not a tunable.

Both functions fail **open** on a Redis error, deliberately mirroring the
facade's Redis-error posture (PRO-82): a monitoring/bookkeeping dependency
going down must not take the send path with it. The backstop is real — a send
Meta rejects for a closed window comes back as a ``failed`` delivery status
with error 131047, which the status handler routes through the template-retry
path. Failing *closed* here would instead silently convert a Redis blip into
dropped customer replies, which is the exact failure mode PRO-89 forbids.
"""

from datetime import datetime, timezone

from app.core.constants import WorkerConstants
from app.core.logger import logger
from app.core.redis_client import get_redis_client

_WINDOW_KEY_PREFIX = "wa:window:"


def _window_key(chat_id: str) -> str:
    return f"{_WINDOW_KEY_PREFIX}{chat_id}"


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
