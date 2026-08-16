"""PRO-86 — blocking bridge for the synchronous admin panel.

Streamlit call sites used to reach for raw ``httpx.post`` against the legacy
WhatsApp vendor,
which is exactly how outbound traffic escaped the circuit breaker and helped
earn the yellowCard. They are synchronous, so they cannot ``await`` the facade;
this module is the one sanctioned way across that boundary.

It is a bridge, not a second egress: everything still goes through
:class:`~app.providers.whatsapp.facade.WhatsAppFacade`, breaker included.
"""

import asyncio
import threading
from typing import Any, Coroutine, TypeVar

from app.core.logger import logger
from app.providers.whatsapp import get_whatsapp

T = TypeVar("T")

# How long a single admin-panel send may block the Streamlit script run.
_SEND_TIMEOUT_SECONDS = 30.0

_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()


def _bridge_loop() -> asyncio.AbstractEventLoop:
    """One long-lived loop for the life of the process.

    Deliberately *not* ``asyncio.run`` per call. ``asyncio.run`` closes its loop
    on the way out, while ``app.core.redis_client`` caches its client in a
    process global — so the second call would reuse a connection pool bound to a
    dead loop, raise, and be swallowed by the breaker's fail-open handler. Every
    admin-panel send after the first would then go out *unguarded*: the same
    bypass this ticket exists to close, merely relocated. Sharing one loop keeps
    the loop and the cached Redis client on the same lifetime.
    """
    global _loop
    with _loop_lock:
        if _loop is None or _loop.is_closed():
            _loop = asyncio.new_event_loop()
            threading.Thread(
                target=_loop.run_forever,
                daemon=True,
                name="wa-sync-bridge",
            ).start()
        return _loop


def _run_blocking(coro: Coroutine[Any, Any, T], timeout: float) -> T:
    """Run ``coro`` on the bridge loop and wait for it.

    The timeout is load-bearing: an unbounded wait here hangs the Streamlit
    script run with no way out for the operator.
    """
    return asyncio.run_coroutine_threadsafe(coro, _bridge_loop()).result(timeout)


def send_text_sync(chat_id: str, text: str) -> bool:
    """Send one text message from sync code.

    Returns True only when the facade actually handed the message to a provider.
    A suppressed send — breaker engaged, kill switch set — returns False, so the
    admin panel can tell the operator "not sent" instead of showing a success
    toast for a message nobody received.

    Never raises: every caller is a best-effort notification hanging off a UI
    action, and a failed courtesy message must not abort the database mutation
    that preceded it.
    """
    try:
        result = _run_blocking(
            get_whatsapp().send_message(chat_id, text), _SEND_TIMEOUT_SECONDS
        )
    except Exception as e:
        logger.error(f"Admin-panel send to ...{chat_id[-4:]} failed: {e}")
        return False
    if result is None:
        logger.warning(
            f"Admin-panel send to ...{chat_id[-4:]} suppressed — outbound halted."
        )
        return False
    return True
