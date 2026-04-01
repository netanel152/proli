"""
Tests for security_service.py: Redis-based rate limiting.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.security_service import SecurityService


@pytest.fixture
def mock_redis():
    """Create a mock Redis that simulates incr/expire behavior."""
    counters = {}

    async def mock_incr(key):
        counters[key] = counters.get(key, 0) + 1
        return counters[key]

    async def mock_expire(key, ttl):
        pass  # Just track that it was called

    redis = MagicMock()
    redis.incr = AsyncMock(side_effect=mock_incr)
    redis.expire = AsyncMock(side_effect=mock_expire)

    return redis, counters


@pytest.mark.asyncio
async def test_rate_limit_allows_under_limit(mock_redis):
    redis, _ = mock_redis

    with patch("app.services.security_service.get_redis_client", new_callable=AsyncMock, return_value=redis):
        for _ in range(5):
            result = await SecurityService.check_rate_limit("user1", limit=10, window_seconds=60)
            assert result is True


@pytest.mark.asyncio
async def test_rate_limit_blocks_over_limit(mock_redis):
    redis, _ = mock_redis

    with patch("app.services.security_service.get_redis_client", new_callable=AsyncMock, return_value=redis):
        results = []
        for _ in range(12):
            r = await SecurityService.check_rate_limit("user1", limit=10, window_seconds=60)
            results.append(r)

        # First 10 should be allowed
        assert all(results[:10])
        # 11th and 12th should be blocked
        assert not results[10]
        assert not results[11]


@pytest.mark.asyncio
async def test_rate_limit_different_users(mock_redis):
    redis, _ = mock_redis

    with patch("app.services.security_service.get_redis_client", new_callable=AsyncMock, return_value=redis):
        # Fill up user1's limit
        for _ in range(10):
            await SecurityService.check_rate_limit("user1", limit=10)

        # user1 should be blocked
        assert not await SecurityService.check_rate_limit("user1", limit=10)

        # user2 should still be allowed
        assert await SecurityService.check_rate_limit("user2", limit=10)


@pytest.mark.asyncio
async def test_rate_limit_expire_called_on_first():
    """Expire is set only on first request (count==1)."""
    redis = MagicMock()
    redis.incr = AsyncMock(return_value=1)
    redis.expire = AsyncMock()

    with patch("app.services.security_service.get_redis_client", new_callable=AsyncMock, return_value=redis):
        await SecurityService.check_rate_limit("user1", limit=10, window_seconds=120)

    redis.expire.assert_called_once_with("rate_limit:user1", 120)


@pytest.mark.asyncio
async def test_rate_limit_redis_failure_allows():
    """Redis failure -> fail-open (allow request)."""
    with patch("app.services.security_service.get_redis_client", new_callable=AsyncMock, side_effect=Exception("Redis down")):
        result = await SecurityService.check_rate_limit("user1", limit=10)
        assert result is True
