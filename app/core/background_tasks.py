"""Strong-referenced fire-and-forget tasks (PRO-143).

``asyncio.create_task`` returns a task the event loop only holds a **weak**
reference to. A caller that drops the return value hands the task to the
garbage collector, which is free to finalize it mid-await — the work simply
stops, with no error anywhere. A bare ``create_task`` also has no done
callback, so an exception raised inside it is retrieved by nobody and is
swallowed (at best it surfaces as a "Task exception was never retrieved"
warning during interpreter teardown, long after the request it belonged to).

``spawn_background_task`` is the canonical asyncio-docs fix: keep the task in
a module-level set for its lifetime, discard it in a done callback, and use
that same callback to log a failure at ERROR — which reaches Sentry via the
throttled loguru bridge in ``app/core/logger.py``.

Holding a strong reference has a cost the weak one did not: a coroutine that
never finishes is now pinned forever instead of being reaped. That is why
every task gets a timeout by default — detached work with no owner and no
deadline is how a stalled dependency turns into unbounded memory.

Use this for genuinely detached work whose result nobody awaits. A long-lived
task with an owner (the ARQ worker heartbeat, which lives in ``ctx`` and is
cancelled at shutdown) does not belong here — it already has a strong
reference and a lifecycle.
"""

import asyncio
from collections.abc import Coroutine
from typing import Any

from app.core.logger import logger

# Deadline applied to a spawned task unless the caller overrides it. Detached
# work is best-effort by definition, so finishing late is the same as not
# finishing — but hanging forever now costs a permanently pinned task.
DEFAULT_BACKGROUND_TASK_TIMEOUT_SECONDS = 30.0

# Strong references to in-flight background tasks. Without this the loop's
# weak reference is the only one, and the GC may collect a task mid-flight.
_background_tasks: set[asyncio.Task] = set()


def _on_task_done(task: asyncio.Task) -> None:
    """Release the strong reference, then surface any failure.

    The discard happens first and unconditionally: a task that somehow fails
    to report its own outcome must still not leak a reference forever.
    """
    _background_tasks.discard(task)

    if task.cancelled():
        # Cancellation is how shutdown is spelled — not a failure.
        logger.debug(f"Background task '{task.get_name()}' cancelled")
        return

    exc = task.exception()
    if exc is not None:
        try:
            detail = f"{type(exc).__name__}: {exc}"
        except Exception:  # pragma: no cover — pathological __str__
            # Rendering the value must not raise inside a done callback: that
            # goes to the loop exception handler, which the Sentry bridge
            # filters out as stdlib noise, losing the very report we are here
            # to make.
            detail = type(exc).__name__
        # ERROR (not warning): this is work that silently did not happen.
        # The loguru→Sentry bridge picks it up from here — and it fingerprints
        # on module:function:line, so every call site's failures share this one
        # hourly throttle slot and one Sentry issue. The task name in the
        # message is what tells them apart.
        logger.error(f"Background task '{task.get_name()}' failed: {detail}")


def spawn_background_task(
    coro: Coroutine[Any, Any, Any],
    *,
    name: str,
    timeout: float | None = DEFAULT_BACKGROUND_TASK_TIMEOUT_SECONDS,
) -> asyncio.Task:
    """Schedule ``coro`` detached, keeping it alive and its failures visible.

    Returns the task so a caller that later gains a reason to await or cancel
    it can, but the contract is fire-and-forget: this never blocks. It does
    raise ``RuntimeError`` when there is no running event loop — closing
    ``coro`` first, so a misuse doesn't also emit a bare "never awaited"
    warning.

    ``timeout`` bounds the task; on expiry it is cancelled and reported like
    any other failure. Pass ``None`` only for work that is genuinely unbounded
    and owned elsewhere.

    ``name`` shows up in the failure log, so keep it short and free of PII.
    A chat id belongs here only through ``phone.mask_chat_id`` — a raw
    ``chat_id[-4:]`` slice is both a leak risk and useless, since every id
    carries the same ``@c.us`` suffix.
    """
    wrapped = asyncio.wait_for(coro, timeout) if timeout is not None else coro
    try:
        task = asyncio.create_task(wrapped, name=name)
    except RuntimeError:
        # No running loop, or one already closing. Close the wrapper and the
        # original — closing the former does not close the coroutine it wraps.
        wrapped.close()
        coro.close()
        raise

    _background_tasks.add(task)
    task.add_done_callback(_on_task_done)
    return task


def pending_background_tasks() -> set[asyncio.Task]:
    """Snapshot of the tasks currently held. For tests and introspection."""
    return set(_background_tasks)
