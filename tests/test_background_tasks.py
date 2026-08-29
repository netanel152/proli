"""
Tests for app/core/background_tasks.py (PRO-143).

spawn_background_task exists specifically to fix a GC bug: a bare
asyncio.create_task() with no held reference can be collected mid-await
because the event loop itself only holds a *weak* reference. These tests
prove the strong-ref set actually prevents that, drains once the task
finishes, and that the done-callback tells cancellation (not a failure)
apart from a genuine exception when logging.
"""

import asyncio
import gc

import pytest
from unittest.mock import MagicMock

import app.core.background_tasks as background_tasks_module
from app.core.background_tasks import pending_background_tasks, spawn_background_task


@pytest.mark.asyncio
async def test_spawned_task_survives_gc_with_no_local_reference():
    """The actual bug this module fixes: dropping the caller's reference to
    the Task must not let a GC pass collect it before it finishes running.
    """
    ran = asyncio.Event()

    async def work():
        await asyncio.sleep(0.01)
        ran.set()

    # No local variable holds the returned Task -- spawn_background_task's
    # own module-level set is the only thing keeping it alive.
    spawn_background_task(work(), name="gc-survival-test")
    gc.collect()

    assert any(t.get_name() == "gc-survival-test" for t in pending_background_tasks())

    await asyncio.wait_for(ran.wait(), timeout=2)
    # Let the done-callback run so the set drains before the next test.
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_pending_set_drains_once_task_completes():
    async def work():
        return None

    task = spawn_background_task(work(), name="drain-test")
    await task

    assert task not in pending_background_tasks()


@pytest.mark.asyncio
async def test_failed_task_logs_error_with_name_and_exception_type_and_is_discarded(
    monkeypatch,
):
    mock_logger = MagicMock()
    monkeypatch.setattr(background_tasks_module, "logger", mock_logger)

    async def work():
        raise ValueError("boom")

    task = spawn_background_task(work(), name="failing-task")

    with pytest.raises(ValueError):
        await task
    # add_done_callback was registered before this await's own wakeup
    # callback, so it has already run by now -- but yield once more to be
    # robust to loop-implementation callback ordering.
    await asyncio.sleep(0)

    mock_logger.error.assert_called_once()
    (msg,), _ = mock_logger.error.call_args
    assert "failing-task" in msg
    assert "ValueError" in msg
    assert "boom" in msg
    assert task not in pending_background_tasks()


@pytest.mark.asyncio
async def test_cancelled_task_does_not_log_error_and_is_discarded(monkeypatch):
    mock_logger = MagicMock()
    monkeypatch.setattr(background_tasks_module, "logger", mock_logger)

    started = asyncio.Event()

    async def work():
        started.set()
        await asyncio.sleep(10)

    task = spawn_background_task(work(), name="cancelled-task")
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)

    mock_logger.error.assert_not_called()
    assert task not in pending_background_tasks()


@pytest.mark.asyncio
async def test_task_exceeding_timeout_logs_error_and_is_discarded(monkeypatch):
    """wait_for's cancellation-on-expiry raises TimeoutError into the task,
    which is a genuine failure -- not the debug-level cancellation path a
    caller-initiated task.cancel() takes (see the test above)."""
    mock_logger = MagicMock()
    monkeypatch.setattr(background_tasks_module, "logger", mock_logger)

    async def work():
        await asyncio.sleep(10)

    task = spawn_background_task(work(), name="timeout-task", timeout=0.05)

    with pytest.raises(TimeoutError):
        await task
    await asyncio.sleep(0)

    mock_logger.error.assert_called_once()
    (msg,), _ = mock_logger.error.call_args
    assert "timeout-task" in msg
    assert task not in pending_background_tasks()


@pytest.mark.asyncio
async def test_timeout_none_leaves_slow_task_to_finish_normally(monkeypatch):
    """The opt-out: timeout=None must not wrap the coroutine in wait_for, so
    a task that would exceed the default deadline still completes and logs
    nothing."""
    mock_logger = MagicMock()
    monkeypatch.setattr(background_tasks_module, "logger", mock_logger)

    async def work():
        await asyncio.sleep(0.05)
        return "done"

    task = spawn_background_task(work(), name="unbounded-task", timeout=None)
    result = await task
    await asyncio.sleep(0)

    assert result == "done"
    mock_logger.error.assert_not_called()
    assert task not in pending_background_tasks()


def test_spawn_without_running_loop_raises_and_closes_the_coroutine():
    """asyncio.create_task requires a running loop. Outside one,
    spawn_background_task must still close the coroutine it was handed --
    otherwise the RuntimeError is followed by a bare "coroutine was never
    awaited" RuntimeWarning at some later, unrelated point (GC or
    interpreter teardown)."""

    async def work():
        pass

    coro = work()

    with pytest.raises(RuntimeError):
        spawn_background_task(coro, name="no-loop-task")

    # A closed coroutine has no frame left to resume -- this is the honest
    # signal that close() ran, as opposed to the coroutine merely never
    # having started.
    assert coro.cr_frame is None
