"""PRO-174 — app.core.arq_worker.process_message_task binds a correlation
id (trace_id) for the whole task body via loguru's contextualize().

Captured with a temporary loguru sink rather than mocking contextualize
directly: contextualize is a context manager returning a plain
contextvars-backed token, so the sink is the simplest way to observe what
actually landed in record["extra"]["trace_id"] for a real log call made
inside the block.
"""

import asyncio
import re
from unittest.mock import AsyncMock

import pytest
from arq.worker import Retry
from loguru import logger

import app.core.arq_worker as arq_worker
from app.core.phone import mask_chat_id
from app.core.redis_client import ChatLockBusyError

_HEX12 = re.compile(r"^[0-9a-f]{12}$")


@pytest.fixture
def captured_trace_ids():
    """Install a temporary sink that records trace_id (if any) for every
    line emitted while the fixture is active, then tear it down."""
    seen = []

    def _sink(message):
        record = message.record
        seen.append(record["extra"].get("trace_id"))

    sink_id = logger.add(_sink, level="INFO")
    try:
        yield seen
    finally:
        logger.remove(sink_id)


@pytest.mark.asyncio
async def test_process_message_task_reuses_the_given_trace_id(
    monkeypatch, captured_trace_ids
):
    monkeypatch.setattr(
        arq_worker, "process_incoming_message", AsyncMock(return_value=None)
    )

    await arq_worker.process_message_task(
        {},
        "972500000001@c.us",
        "hi",
        message_id="wamid.ABC",
        trace_id="given-trace-id",
    )

    assert captured_trace_ids
    assert all(tid == "given-trace-id" for tid in captured_trace_ids)


@pytest.mark.asyncio
async def test_process_message_task_binds_a_fresh_trace_id_when_omitted(
    monkeypatch, captured_trace_ids
):
    # Simulates a job enqueued before the trace_id kwarg existed: the worker
    # must still bind *an* id (12 lowercase hex chars) on every line, and —
    # since new_trace_id() is unkeyed random now, not a reproducible hash —
    # two independent runs of this fallback must not collide either.
    monkeypatch.setattr(
        arq_worker, "process_incoming_message", AsyncMock(return_value=None)
    )

    await arq_worker.process_message_task(
        {}, "972500000002@c.us", "hi", message_id="wamid.LEGACY"
    )

    assert captured_trace_ids
    first_run_id = captured_trace_ids[0]
    assert _HEX12.match(first_run_id)
    assert all(tid == first_run_id for tid in captured_trace_ids)

    captured_trace_ids.clear()
    await arq_worker.process_message_task(
        {}, "972500000002@c.us", "hi", message_id="wamid.LEGACY"
    )
    second_run_id = captured_trace_ids[0]
    assert second_run_id != first_run_id


# ---------------------------------------------------------------------------
# The `with logger.contextualize(...)` block must not change control flow —
# only add a binding. A context manager that swallowed either escape would
# silently disable ARQ's max_tries retry path (ChatLockBusyError) or the
# task-failure reporting path (any other exception).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_message_task_chat_lock_busy_still_escapes_as_retry(
    monkeypatch,
):
    monkeypatch.setattr(
        arq_worker,
        "process_incoming_message",
        AsyncMock(side_effect=ChatLockBusyError("972500000003@c.us")),
    )

    with pytest.raises(Retry) as exc_info:
        await arq_worker.process_message_task(
            {}, "972500000003@c.us", "hi", trace_id="t1"
        )

    assert exc_info.value.defer_score == Retry(defer=2).defer_score


@pytest.mark.asyncio
async def test_the_lock_backoff_grows_with_the_try(monkeypatch):
    monkeypatch.setattr(
        arq_worker,
        "process_incoming_message",
        AsyncMock(side_effect=ChatLockBusyError("972500000003@c.us")),
    )

    with pytest.raises(Retry) as exc_info:
        await arq_worker.process_message_task(
            {"job_try": 3}, "972500000003@c.us", "hi", trace_id="t1"
        )

    assert exc_info.value.defer_score == Retry(defer=6).defer_score


def test_the_total_backoff_outlasts_the_slowest_turn():
    last_try = arq_worker.WorkerSettings.max_tries
    assert sum(2 * n for n in range(1, last_try)) > (
        arq_worker.WorkerSettings.job_timeout
    )


@pytest.mark.asyncio
async def test_the_last_busy_try_says_so_and_tells_the_customer(
    monkeypatch, fake_redis
):
    chat_id = "972500000003@c.us"
    monkeypatch.setattr(
        arq_worker,
        "process_incoming_message",
        AsyncMock(side_effect=ChatLockBusyError(chat_id)),
    )
    fake_whatsapp = AsyncMock()
    monkeypatch.setattr(arq_worker, "whatsapp", fake_whatsapp)
    errors = []
    last_try = arq_worker.WorkerSettings.max_tries
    await fake_redis.set("lock_busy:j1", last_try - 1)
    sink = logger.add(lambda m: errors.append(str(m)), level="ERROR")
    try:
        await arq_worker.process_message_task(
            {"job_try": last_try, "job_id": "j1"},
            chat_id,
            "secret words",
            trace_id="t1",
        )
    finally:
        logger.remove(sink)

    fake_whatsapp.send_message.assert_awaited_once()
    assert len(errors) == 1
    assert "j1" in errors[0] and mask_chat_id(chat_id) in errors[0]
    assert "secret words" not in errors[0]


@pytest.fixture
def worker_turn(monkeypatch):
    turn = AsyncMock()
    fake_whatsapp = AsyncMock()
    monkeypatch.setattr(arq_worker, "process_incoming_message", turn)
    monkeypatch.setattr(arq_worker, "whatsapp", fake_whatsapp)
    return turn, fake_whatsapp


@pytest.mark.asyncio
async def test_a_crash_loop_is_capped_on_real_starts(worker_turn):
    turn, fake_whatsapp = worker_turn
    errors = []
    sink = logger.add(lambda m: errors.append(str(m)), level="ERROR")
    try:
        await arq_worker.process_message_task(
            {"job_try": arq_worker.MAX_REAL_STARTS, "job_id": "j1"},
            "972500000003@c.us",
            "secret words",
        )
    finally:
        logger.remove(sink)

    turn.assert_not_awaited()
    fake_whatsapp.send_message.assert_awaited_once()
    assert mask_chat_id("972500000003@c.us") in errors[0]
    assert "secret words" not in errors[0]


@pytest.mark.asyncio
async def test_tries_spent_waiting_on_the_lock_do_not_count(worker_turn, fake_redis):
    turn, _ = worker_turn
    await fake_redis.set("lock_busy:j1", 13)

    await arq_worker.process_message_task(
        {"job_try": 17, "job_id": "j1"}, "972500000003@c.us", "hi"
    )

    turn.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_busy_try_is_counted_with_a_ttl(monkeypatch, fake_redis):
    monkeypatch.setattr(
        arq_worker,
        "process_incoming_message",
        AsyncMock(side_effect=ChatLockBusyError("972500000003@c.us")),
    )

    with pytest.raises(Retry):
        await arq_worker.process_message_task(
            {"job_try": 1, "job_id": "j1"}, "972500000003@c.us", "hi"
        )

    assert await fake_redis.get("lock_busy:j1") == "1"
    assert 0 < await fake_redis.ttl("lock_busy:j1")


@pytest.mark.asyncio
async def test_redis_down_does_not_drop_a_job(monkeypatch, worker_turn):
    turn, _ = worker_turn
    monkeypatch.setattr(
        arq_worker, "get_redis_client", AsyncMock(side_effect=ConnectionError)
    )

    await arq_worker.process_message_task(
        {"job_try": 17, "job_id": "j1"}, "972500000003@c.us", "hi"
    )

    turn.assert_awaited_once()


@pytest.mark.asyncio
async def test_followers_running_out_together_notify_once(monkeypatch, fake_redis):
    monkeypatch.setattr(
        arq_worker,
        "process_incoming_message",
        AsyncMock(side_effect=ChatLockBusyError("972500000003@c.us")),
    )
    fake_whatsapp = AsyncMock()
    monkeypatch.setattr(arq_worker, "whatsapp", fake_whatsapp)
    last_try = arq_worker.WorkerSettings.max_tries
    for job_id in ("j1", "j2"):
        await fake_redis.set(f"lock_busy:{job_id}", last_try - 1)
        await arq_worker.process_message_task(
            {"job_try": last_try, "job_id": job_id}, "972500000003@c.us", "hi"
        )

    fake_whatsapp.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_dropped_emergency_pages_and_an_ordinary_one_does_not(
    monkeypatch, worker_turn
):
    page = AsyncMock()
    monkeypatch.setattr(arq_worker, "page_emergency_turn", page)
    ctx = {"job_try": arq_worker.MAX_REAL_STARTS, "job_id": "j1"}

    await arq_worker.process_message_task(ctx, "972500000003@c.us", "שלום")
    page.assert_not_awaited()

    await arq_worker.process_message_task(ctx, "972500000003@c.us", "יש שריפה")
    page.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_and_end_lines_name_the_job_and_the_masked_chat(monkeypatch):
    chat_id = "972500000003@c.us"
    monkeypatch.setattr(arq_worker, "process_incoming_message", AsyncMock())
    lines = []
    sink = logger.add(lambda m: lines.append(str(m)), level="INFO")
    try:
        await arq_worker.process_message_task(
            {"job_try": 2, "job_id": "j1"}, chat_id, "secret words", trace_id="t1"
        )
    finally:
        logger.remove(sink)

    for prefix in ("Task started", "Task ended"):
        line = next(line for line in lines if prefix in line)
        assert "j1" in line and "try 2" in line and mask_chat_id(chat_id) in line
    assert not [line for line in lines if "secret words" in line]


@pytest.mark.asyncio
async def test_process_message_task_generic_exception_propagates_after_fallback_sent(
    monkeypatch,
):
    monkeypatch.setattr(
        arq_worker,
        "process_incoming_message",
        AsyncMock(side_effect=Exception("boom")),
    )
    fake_whatsapp = AsyncMock()
    monkeypatch.setattr(arq_worker, "whatsapp", fake_whatsapp)

    with pytest.raises(Exception, match="boom"):
        await arq_worker.process_message_task(
            {}, "972500000004@c.us", "hi", trace_id="t2"
        )

    fake_whatsapp.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_message_task_trace_id_does_not_leak_across_concurrent_tasks(
    monkeypatch,
):
    # No cross-task bleed: contextvars are per-Task, so two concurrent turns
    # bound inside asyncio.gather must never see each other's trace_id, and
    # the binding must not survive past the `with` block into the caller.
    seen: list[tuple[str, str | None]] = []

    def _sink(message):
        record = message.record
        seen.append((record["message"], record["extra"].get("trace_id")))

    sink_id = logger.add(_sink, level="INFO")

    async def _interleaving_process_incoming_message(
        chat_id, user_text, media_url=None
    ):
        logger.info(f"loop1 {chat_id}")
        await asyncio.sleep(0)
        logger.info(f"loop2 {chat_id}")
        await asyncio.sleep(0)
        logger.info(f"loop3 {chat_id}")

    monkeypatch.setattr(
        arq_worker, "process_incoming_message", _interleaving_process_incoming_message
    )

    try:
        await asyncio.gather(
            arq_worker.process_message_task({}, "chat-alpha", "hi", trace_id="trace-A"),
            arq_worker.process_message_task({}, "chat-beta", "hi", trace_id="trace-B"),
        )
        logger.info("after gather")
    finally:
        logger.remove(sink_id)

    alpha_lines = [(msg, tid) for msg, tid in seen if "chat-alpha" in msg]
    beta_lines = [(msg, tid) for msg, tid in seen if "chat-beta" in msg]
    # Sanity: the interleaving actually happened (both chats logged multiple
    # lines while the other was also in flight), otherwise this test could
    # pass by accident with no real concurrency.
    assert len(alpha_lines) >= 3
    assert len(beta_lines) >= 3

    assert all(tid == "trace-A" for _, tid in alpha_lines)
    assert all(tid == "trace-B" for _, tid in beta_lines)

    after_lines = [tid for msg, tid in seen if msg == "after gather"]
    assert after_lines == [None]
