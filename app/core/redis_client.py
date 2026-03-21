import asyncio
from redis.asyncio import Redis, from_url
from arq import create_pool
from arq.connections import RedisSettings
from app.core.config import settings
from app.core.logger import logger
from typing import Optional

_redis_client: Optional[Redis] = None
_arq_pool = None
_redis_lock = asyncio.Lock()
_arq_lock = asyncio.Lock()

async def get_redis_client() -> Redis:
    """
    Returns a singleton async Redis client instance.
    Uses asyncio.Lock to prevent race conditions during initialization.
    """
    global _redis_client
    if _redis_client is not None:
        return _redis_client

    async with _redis_lock:
        # Double-check after acquiring lock
        if _redis_client is not None:
            return _redis_client

        redis_url = settings.REDIS_URL or f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB}"
        try:
            client = from_url(
                redis_url,
                encoding="utf-8",
                decode_responses=True,
                max_connections=20
            )
            await client.ping()
            _redis_client = client
            logger.info("Connected to Redis successfully.")
        except Exception as e:
            logger.error(f"Could not connect to Redis: {e}")
            raise
    return _redis_client

async def get_arq_pool():
    """
    Returns a singleton ARQ Redis pool instance.
    Uses asyncio.Lock to prevent race conditions during initialization.
    """
    global _arq_pool
    if _arq_pool is not None:
        return _arq_pool

    async with _arq_lock:
        # Double-check after acquiring lock
        if _arq_pool is not None:
            return _arq_pool

        try:
            if settings.REDIS_URL:
                _arq_pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
            else:
                _arq_pool = await create_pool(
                    RedisSettings(
                        host=settings.REDIS_HOST,
                        port=settings.REDIS_PORT,
                        database=settings.REDIS_DB
                    )
                )
            logger.info("ARQ Redis pool created.")
        except Exception as e:
            logger.error(f"Could not create ARQ pool: {e}")
            raise
    return _arq_pool

async def close_redis_client():
    """Closes Redis and ARQ pool connections."""
    global _redis_client, _arq_pool
    if _redis_client:
        try:
            await _redis_client.close()
            _redis_client = None
            logger.info("Redis connection closed.")
        except Exception as e:
            logger.error(f"Error closing Redis client: {e}")

    if _arq_pool:
        try:
            await _arq_pool.close()
            _arq_pool = None
            logger.info("ARQ Redis pool closed.")
        except Exception as e:
            logger.error(f"Error closing ARQ pool: {e}")
