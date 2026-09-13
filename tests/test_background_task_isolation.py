"""Tests for the PRO-187 test-harness fix in tests/conftest.py.

``app.core.background_tasks._background_tasks`` is module state that outlives
the per-test event loop ``asyncio_mode = strict`` tears down after every test.
The autouse ``_isolate_background_tasks`` fixture (conftest.py) clears the
registry on setup and evicts-without-cancelling on teardown, quarantining any
still-pending task in the module-level ``_evicted_background_tasks`` list so
it keeps a strong reference (PRO-143's own guarantee) instead of being
garbage-collected mid-await.

These tests pin the harness itself, not ``spawn_background_task`` -- see
tests/test_background_tasks.py for that.
"""

import asyncio
import re
from pathlib import Path

import pytest

# `conftest`, not `tests.conftest`: tests/ has no __init__.py, so pytest
# registers the file under the bare name and `import tests.conftest` would
# execute it a *second* time through the namespace package -- a separate
# module object with its own _evicted_background_tasks, which is not the one
# the fixture mutates. Asserting against that copy would pass by accident.
import conftest
from app.core.background_tasks import pending_background_tasks, spawn_background_task


@pytest.mark.asyncio
async def test_registry_is_empty_on_entry():
    """The setup half of the fixture: whatever a previous test spawned and
    never awaited must not be visible here. Asserted as an entry invariant
    (rather than depending on run order relative to a specific leaking test)
    per the task's own guidance on order-dependent assertions."""
    assert pending_background_tasks() == set()


@pytest.mark.asyncio
async def test_leaked_task_does_not_leave_registry_for_next_test():
    """A test that spawns detached work and returns without awaiting it must
    not hand that task to whichever test runs next."""

    async def work():
        await asyncio.sleep(10)

    spawn_background_task(work(), name="pro-187-leaker", timeout=None)
    assert pending_background_tasks() != set()  # sanity: it is actually here
    # No await, no cleanup -- deliberately leaked, same as the real bug.


@pytest.mark.asyncio
async def test_registry_is_still_empty_after_a_leaker_ran():
    """Companion to the two tests above: run right after a leaking test (by
    file order) and the registry must already be clear again, proving the
    fixture -- its teardown half -- is what isolates it, not luck of
    timing."""
    assert pending_background_tasks() == set()


async def _spawn_leftover_task_and_run_real_teardown(name: str) -> asyncio.Task:
    """Spawn a task that is deliberately left pending, then run the *actual*
    teardown code the fixture runs -- ``conftest.evict_leftover_background_tasks``
    -- and hand back the task.

    The fixture's teardown lives in that named helper precisely so a test can
    reach it: pytest refuses a direct call to a fixture function, and a test
    that hand-copies the teardown body pins its own copy rather than the code
    that actually ships.
    """

    async def work():
        await asyncio.sleep(10)

    task = spawn_background_task(work(), name=name, timeout=None)
    assert task in pending_background_tasks()  # sanity before the code under test
    conftest.evict_leftover_background_tasks()
    return task


@pytest.mark.asyncio
async def test_teardown_retains_leftover_task_without_cancelling_it():
    """Both halves of the teardown contract, pinned against the real fixture
    function rather than a copy of its logic:

    - retains a strong reference to a still-pending task in
      ``conftest._evicted_background_tasks`` instead of dropping it, so
      PRO-143's "no GC mid-await" guarantee holds even after eviction;
    - does not cancel it -- a cancel can never be delivered once the test's
      own loop has stopped running, so it would only orphan the coroutine
      for a 'never awaited' warning. ``Task.cancelling()`` is checked (not
      just ``cancelled()``) because a cancellation *request* is recorded
      synchronously the moment ``cancel()`` is called, before the loop ever
      gets a chance to actually deliver it -- so if a future change made
      teardown call ``task.cancel()``, ``cancelling()`` would already read 1
      immediately after ``next(gen, None)`` returns, while ``cancelled()``
      would still (misleadingly) read False until the loop ran the task
      again, which in this harness it never will.
    """
    before = len(conftest._evicted_background_tasks)
    task = await _spawn_leftover_task_and_run_real_teardown("pro-187-retained")

    assert task not in pending_background_tasks()
    assert task in conftest._evicted_background_tasks
    assert len(conftest._evicted_background_tasks) == before + 1
    assert task.cancelling() == 0
    assert not task.cancelled()

    # Clean up: this task is otherwise unbounded and would stay pending for
    # the rest of the suite.
    task.cancel()
    conftest._evicted_background_tasks.remove(task)
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.parametrize(
    "filename",
    [
        "test_workflow_orchestrator.py",
        "test_ai_parsing.py",
    ],
)
def test_narrowed_readers_no_longer_drain_the_whole_registry(filename):
    """PRO-187 regression guard on the two call sites that used to do

        for t in list(pending_background_tasks()):
            await t

    (test_ai_parsing.py) or the same shape via a module-qualified call

        for t in list(background_tasks_module.pending_background_tasks()):
            await t

    (test_workflow_orchestrator.py, on ``origin/dev`` before PRO-187) --
    which awaits whatever any earlier test left behind, a task bound to a
    now-closed loop, raising 'attached to a different loop'. Both real
    spellings wrap the call in ``list(...)`` and one adds a module-attribute
    prefix, so the pattern below makes the wrapper and the prefix optional --
    a naive regex requiring a bare ``pending_background_tasks()`` matches
    neither line that actually existed, which would make this guard inert
    against the exact regression it exists to catch. This is a source-level
    check rather than a run: the failure is order-dependent and isn't
    reliably reproducible in-process within a single suite run."""
    path = Path(__file__).parent / filename
    assert path.exists(), f"{filename} was renamed -- update this guard"
    contents = path.read_text(encoding="utf-8")

    drain_pattern = re.compile(
        r"for\s+\w+\s+in\s+"
        # The wrapper and the module prefix are both optional: the two
        # spellings that actually existed were `list(pending_background_tasks())`
        # and `list(background_tasks_module.pending_background_tasks())`, but a
        # bare `pending_background_tasks()` is the same defect and must also trip.
        r"(?:(?:list|set|tuple)\(\s*)?"
        r"(?:\w+\.)?pending_background_tasks\(\)"
        r"[\s)]*:\s*\n\s*await\s+\w+"
    )
    # The other realistic re-regression: gathering the registry rather than
    # looping it. Same defect, so the same guard has to see it.
    gather_pattern = re.compile(
        r"gather\(\s*\*\s*(?:\w+\.)?pending_background_tasks\(\)"
    )
    for pattern in (drain_pattern, gather_pattern):
        assert not pattern.search(contents), (
            f"{path} appears to drain the whole background-task registry "
            "again; await only the tasks your own spy captured (see PRO-187)."
        )
