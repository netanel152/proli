"""
PRO-78: guard tests for the autouse `fake_redis` fixture in conftest.py.

These tests fail loudly if the isolation fixture is removed or broken —
i.e. if `get_redis_client()` starts returning a real Redis connection again,
or if the fake store stops being reset fresh for every test.
"""

import pytest
import fakeredis.aioredis

from app.core.redis_client import get_redis_client


@pytest.mark.asyncio
async def test_get_redis_client_returns_fakeredis_instance():
    client = await get_redis_client()
    assert isinstance(client, fakeredis.aioredis.FakeRedis)


@pytest.mark.asyncio
async def test_fresh_store_a_writes_sentinel_after_confirming_empty():
    client = await get_redis_client()

    # No bleed from any other test that may have written this same key.
    assert await client.dbsize() == 0
    assert await client.get("pro78:sentinel") is None

    await client.set("pro78:sentinel", "1")
    assert await client.get("pro78:sentinel") == "1"


@pytest.mark.asyncio
async def test_fresh_store_b_writes_sentinel_after_confirming_empty():
    client = await get_redis_client()

    # Order-independent: regardless of whether this runs before or after the
    # sibling test above (pytest-randomly), the store must start empty because
    # each test gets its own fresh FakeRedis instance.
    assert await client.dbsize() == 0
    assert await client.get("pro78:sentinel") is None

    await client.set("pro78:sentinel", "1")
    assert await client.get("pro78:sentinel") == "1"
