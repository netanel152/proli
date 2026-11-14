"""PRO-133 -- the inbound wamid must survive the hop from the ARQ job into
``process_incoming_message``.

It used to stop at the Sentry tag: the worker received ``message_id`` but never
passed it on, so nothing downstream could mark that message read. Mirrors the
setup of tests/test_arq_worker_trace_id.py.
"""

from unittest.mock import AsyncMock

import pytest

import app.core.arq_worker as arq_worker


@pytest.mark.asyncio
@pytest.mark.parametrize("message_id", ["wamid.IN1", None])
async def test_process_message_task_passes_the_wamid_to_the_turn(
    monkeypatch, message_id
):
    """None is a job enqueued before the kwarg existed: it must still run, with
    no wamid rather than a missing argument."""
    turn = AsyncMock(return_value=None)
    monkeypatch.setattr(arq_worker, "process_incoming_message", turn)

    await arq_worker.process_message_task(
        {}, "972500000001@c.us", "hi", None, message_id=message_id
    )

    turn.assert_awaited_once_with(
        "972500000001@c.us", "hi", None, message_id=message_id
    )
