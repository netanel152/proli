"""PRO-214: one chat's messages run in the order they arrived.

Each waiting job used to retry on its own growing backoff, so the message that
had waited longest retried least often: with a slow turn holding the chat, a
correction sent second could run before the message it corrected.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from arq.worker import Retry

from app.core import arq_worker, chat_order

CHAT = "972500000077@c.us"
T0 = datetime(2026, 11, 17, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def turns(monkeypatch):
    ran = []

    async def turn(chat_id, text, media_url=None, message_id=None):
        ran.append(text)

    monkeypatch.setattr(arq_worker, "process_incoming_message", turn)
    monkeypatch.setattr(arq_worker, "whatsapp", AsyncMock())
    return ran


async def _try(job_id, text, enqueued_at, job_try=1):
    """One arq try of a job. True if it ran (or finished), False if deferred."""
    ctx = {"job_id": job_id, "job_try": job_try, "enqueue_time": enqueued_at}
    try:
        await arq_worker.process_message_task(ctx, CHAT, text)
    except Retry:
        return False
    return True


@pytest.mark.asyncio
async def test_a_slow_turn_and_two_queued_followers_run_in_arrival_order(
    turns, fake_redis
):
    # The slow turn: at the head of the queue and running.
    assert await arq_worker._my_turn(CHAT, "slow", T0) is True

    # Message 1 at second 1, message 2 at second 30. Message 2 happens to
    # retry first once the slow turn is done — the case that used to invert.
    first, second = T0 + timedelta(seconds=1), T0 + timedelta(seconds=30)
    assert await _try("m1", "address: Herzl 1", first) is False
    assert await _try("m2", "sorry, Herzl 10", second) is False

    await arq_worker._leave_queue(CHAT, "slow")

    assert await _try("m2", "sorry, Herzl 10", second, job_try=2) is False
    assert await _try("m1", "address: Herzl 1", first, job_try=2) is True
    assert await _try("m2", "sorry, Herzl 10", second, job_try=3) is True

    assert turns == ["address: Herzl 1", "sorry, Herzl 10"]
    assert await fake_redis.zcard(chat_order.order_key(CHAT)) == 0


@pytest.mark.asyncio
async def test_a_job_that_stopped_retrying_does_not_hold_the_chat(turns, fake_redis):
    first, second = T0, T0 + timedelta(seconds=5)
    assert await _try("lost", "first", first, job_try=1) is True  # runs and leaves
    # A job that joined and then vanished (its presence key expired).
    await fake_redis.zadd(chat_order.order_key(CHAT), {"ghost": first.timestamp()})

    assert await _try("m2", "second", second) is True
    assert turns == ["first", "second"]


@pytest.mark.asyncio
async def test_a_failed_turn_leaves_the_queue(monkeypatch, fake_redis):
    monkeypatch.setattr(
        arq_worker, "process_incoming_message", AsyncMock(side_effect=ValueError())
    )
    monkeypatch.setattr(arq_worker, "whatsapp", AsyncMock())

    with pytest.raises(ValueError):
        await arq_worker.process_message_task(
            {"job_id": "bad", "job_try": 1, "enqueue_time": T0}, CHAT, "x"
        )

    assert await fake_redis.zcard(chat_order.order_key(CHAT)) == 0


@pytest.mark.asyncio
async def test_the_last_try_of_a_waiting_job_drops_it_and_leaves(turns, fake_redis):
    assert await arq_worker._my_turn(CHAT, "slow", T0) is True

    await _try(
        "m1",
        "hi",
        T0 + timedelta(seconds=1),
        job_try=arq_worker.WorkerSettings.max_tries,
    )

    assert turns == []
    members = await fake_redis.zrange(chat_order.order_key(CHAT), 0, -1)
    assert members == ["slow"]


@pytest.mark.asyncio
async def test_a_job_without_an_enqueue_time_runs_unordered(turns, fake_redis):
    assert await arq_worker._my_turn(CHAT, "slow", T0) is True

    await arq_worker.process_message_task({"job_id": "old", "job_try": 1}, CHAT, "hi")

    assert turns == ["hi"]


@pytest.mark.asyncio
async def test_the_webhook_joins_the_order_at_enqueue_under_the_job_id(
    fake_redis, monkeypatch
):
    """Joining at enqueue fixes the order before any worker picks a job up, so
    two messages milliseconds apart taken by two slots at once cannot swap."""
    import json

    import httpx

    from app.api.routes import meta_webhook
    from app.core.config import settings
    from app.main import app

    jobs = []

    class Pool:
        async def enqueue_job(self, name, *args, _job_id=None, **kwargs):
            jobs.append(_job_id)

    async def pool():
        return Pool()

    monkeypatch.setattr(meta_webhook, "get_arq_pool", pool)
    monkeypatch.setattr(settings, "META_APP_SECRET", None)
    sender = "972500000088"

    def payload(wamid, text):
        return {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messages": [
                                    {
                                        "from": sender,
                                        "id": wamid,
                                        "type": "text",
                                        "text": {"body": text},
                                    }
                                ]
                            },
                        }
                    ]
                }
            ],
        }

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        for n, text in enumerate(("first", "second")):
            await client.post(
                "/webhook/meta", content=json.dumps(payload(f"wamid.O{n}", text))
            )

    members = await fake_redis.zrange(chat_order.order_key(f"{sender}@c.us"), 0, -1)
    assert len(jobs) == 2 and all(jobs)
    assert members == jobs
