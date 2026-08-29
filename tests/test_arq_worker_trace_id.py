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
# silently disable ARQ's max_tries=5 retry path (ChatLockBusyError) or the
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

    # Retry(defer=2) is the exact call in the source — same defer_score.
    assert exc_info.value.defer_score == Retry(defer=2).defer_score


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
