"""A slow turn and the job queued behind it, through a real arq worker.

Real seconds on purpose: what is being tested is the race between the chat lock's
TTL and the retry backoff, and scaling either one down would test a different race.
"""

import asyncio
import time
from unittest.mock import AsyncMock

import fakeredis.aioredis
import pytest
from arq.connections import ArqRedis
from arq.worker import Worker, func
from fakeredis import FakeServer
from loguru import logger
from redis.asyncio.client import Pipeline
from redis.exceptions import WatchError

from app.core import redis_client
from app.core.arq_worker import WorkerSettings, process_message_task
from app.services import media_handler
from app.core.redis_client import (
    acquire_chat_lock,
    keep_chat_lock,
    release_chat_lock,
)
from tests.e2e.ai_replay import reply

# Longer than the 10s lock TTL, and longer than the 9s a follower used to survive.
SLOW_UPLOAD_SECONDS = 12
FOLLOWER = "וזה קרה גם אתמול בלילה"


@pytest.mark.asyncio
async def test_a_follower_outlives_an_upload_longer_than_the_lock(world, monkeypatch):
    await world.grant_consent()

    async def _no_server_info(*_args, **_kwargs):
        return None  # fakeredis has no INFO command

    monkeypatch.setattr("arq.worker.log_redis_info", _no_server_info)
    monkeypatch.setattr(
        media_handler,
        "fetch_meta_media",
        AsyncMock(return_value=(b"bytes", "image/jpeg")),
    )

    def _slow_upload(_data):
        time.sleep(SLOW_UPLOAD_SECONDS)
        return "https://res.cloudinary.com/proli/hosted/image"

    monkeypatch.setattr(media_handler, "upload_media_bytes", _slow_upload)
    world.ai.script(reply("ראיתי את התמונה."), reply("הבנתי."))

    queue = ArqRedis(
        connection_pool=fakeredis.aioredis.FakeRedis(
            server=FakeServer()
        ).connection_pool
    )
    await queue.enqueue_job(
        "process_message_task", world.customer, "תראו", "meta-media://M1"
    )
    # Deferred only so the upload is certainly holding the lock when this one runs.
    await queue.enqueue_job(
        "process_message_task", world.customer, FOLLOWER, _defer_by=0.5
    )
    worker = Worker(
        functions=[func(process_message_task, name="process_message_task")],
        redis_pool=queue,
        burst=True,
        handle_signals=False,
        poll_delay=0.05,
        max_tries=WorkerSettings.max_tries,
        keep_result=WorkerSettings.keep_result,
        retry_jobs=WorkerSettings.retry_jobs,
    )
    run = asyncio.create_task(worker.main())
    try:
        # Past the 10s TTL, still inside the upload: only renewal can explain this.
        await asyncio.sleep(11)
        assert await world.redis.get(f"lock:chat:{world.customer}") is not None
        await run
    finally:
        run.cancel()

    rows = await world.db.messages.find({"role": "user"}).to_list(length=10)
    assert FOLLOWER in [row["text"] for row in rows], (
        f"follower lost: {worker.jobs_complete} complete, "
        f"{worker.jobs_failed} failed, {worker.jobs_retried} retried"
    )
    assert worker.jobs_failed == 0


@pytest.mark.asyncio
async def test_a_late_finisher_does_not_free_the_next_jobs_lock(world):
    assert await acquire_chat_lock(world.customer, token="first")
    await world.redis.delete(f"lock:chat:{world.customer}")  # its TTL lapsed
    assert await acquire_chat_lock(world.customer, token="second")

    await release_chat_lock(world.customer, "first")

    assert await world.redis.get(f"lock:chat:{world.customer}") == "second"
    await release_chat_lock(world.customer, "second")
    assert await world.redis.get(f"lock:chat:{world.customer}") is None


@pytest.mark.asyncio
async def test_the_lock_is_renewed_during_a_turn_and_stops_when_lost(world):
    key = f"lock:chat:{world.customer}"
    assert await acquire_chat_lock(world.customer, ttl=1, token="mine")
    renewal = asyncio.create_task(keep_chat_lock(world.customer, "mine", ttl=1))
    try:
        await asyncio.sleep(1.5)
        assert await world.redis.get(key) == "mine"

        await world.redis.set(key, "theirs", ex=5)
        await _until(renewal.done)
        assert await world.redis.get(key) == "theirs"
    finally:
        renewal.cancel()


async def _until(condition, deadline_seconds=3):
    deadline = time.monotonic() + deadline_seconds
    while not condition():
        assert time.monotonic() < deadline, "condition not met before the deadline"
        await asyncio.sleep(0.02)


def _change_the_key_during_watch(monkeypatch, world, key):
    """Another owner takes the lock between the GET and the EXEC."""
    real_get = Pipeline.get

    async def _get(self, name):
        value = await real_get(self, name)
        await world.redis.set(key, "theirs", ex=5)
        return value

    monkeypatch.setattr(Pipeline, "get", _get, raising=False)


@pytest.mark.asyncio
async def test_a_key_changed_during_watch_is_not_released(world, monkeypatch):
    key = f"lock:chat:{world.customer}"
    assert await acquire_chat_lock(world.customer, token="mine")
    _change_the_key_during_watch(monkeypatch, world, key)

    await release_chat_lock(world.customer, "mine")

    assert await world.redis.get(key) == "theirs"


@pytest.mark.asyncio
async def test_a_watch_error_does_not_stop_renewal(world, monkeypatch):
    """redis-py raises it for a dropped connection, which is not a lost lock."""
    key = f"lock:chat:{world.customer}"
    assert await acquire_chat_lock(world.customer, ttl=1, token="mine")
    real = redis_client._if_chat_lock_owner
    attempts = []

    async def _drops_once(*args):
        attempts.append(args)
        if len(attempts) == 1:
            raise WatchError("A ConnectionError occurred while watching")
        return await real(*args)

    monkeypatch.setattr(redis_client, "_if_chat_lock_owner", _drops_once)
    renewal = asyncio.create_task(keep_chat_lock(world.customer, "mine", ttl=1))
    try:
        await _until(lambda: len(attempts) >= 2)
        assert not renewal.done()
        assert await world.redis.get(key) == "mine"
    finally:
        renewal.cancel()


@pytest.mark.asyncio
async def test_a_real_loss_during_watch_is_noticed_on_the_next_tick(world, monkeypatch):
    key = f"lock:chat:{world.customer}"
    assert await acquire_chat_lock(world.customer, ttl=1, token="mine")
    _change_the_key_during_watch(monkeypatch, world, key)
    errors = []
    sink = logger.add(lambda m: errors.append(str(m)), level="ERROR")
    renewal = asyncio.create_task(keep_chat_lock(world.customer, "mine", ttl=1))
    try:
        await _until(renewal.done)
    finally:
        renewal.cancel()
        logger.remove(sink)

    assert await world.redis.get(key) == "theirs"
    assert [line for line in errors if "lost mid-turn" in line]
