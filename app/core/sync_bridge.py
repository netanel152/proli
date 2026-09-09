"""One sync→async bridge for the whole process.

The Streamlit admin panel is synchronous, so it cannot ``await`` the async
services (the WhatsApp facade, geocoding). Everything that crosses that
boundary goes through :func:`run_blocking`, on one long-lived event loop.

Why one loop, and why it is shared
----------------------------------
``asyncio.run`` per call closes its loop on the way out, while
``app.core.redis_client`` caches its client in a process global. The second
call would then reuse a connection pool bound to a dead loop and raise. For
the WhatsApp bridge (PRO-86) that raise was swallowed by the breaker's
fail-open handler, so every admin-panel send after the first went out
*unguarded* — the exact bypass PRO-86 exists to close, merely relocated.

The same cached client is why there must be exactly one bridge loop, not one
per caller: a second loop (say, geocoding on its own) would hand the cached
Redis pool to a loop it was not created on. Extracted from
``app.providers.whatsapp.sync`` when the pro-approval geocode check needed the
same crossing.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Coroutine, TypeVar

T = TypeVar("T")

_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()


def bridge_loop() -> asyncio.AbstractEventLoop:
    """The process's one long-lived bridge loop, started on first use."""
    global _loop
    with _loop_lock:
        if _loop is None or _loop.is_closed():
            _loop = asyncio.new_event_loop()
            threading.Thread(
                target=_loop.run_forever,
                daemon=True,
                name="sync-bridge",
            ).start()
        return _loop


def run_blocking(coro: Coroutine[Any, Any, T], timeout: float) -> T:
    """Run ``coro`` on the bridge loop and wait for it.

    The timeout is load-bearing: an unbounded wait here hangs the Streamlit
    script run with no way out for the operator.
    """
    return asyncio.run_coroutine_threadsafe(coro, bridge_loop()).result(timeout)
