import asyncio
import functools
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


class ChatLockBusyError(Exception):
    """Raised when another worker is already processing a message for this chat_id."""
    def __init__(self, chat_id: str):
        self.chat_id = chat_id
        super().__init__(f"Chat lock held for {chat_id}")


async def acquire_chat_lock(chat_id: str, ttl: int = 10) -> bool:
    """
    Per-chat Redis SETNX lock to serialize concurrent ARQ tasks for the same user.
    Returns True if the lock was acquired (caller owns it and must release).
    On Redis connection error, returns True (degraded mode — proceed without lock
    rather than stall the pipeline).
    """
    try:
        redis = await get_redis_client()
        result = await redis.set(f"lock:chat:{chat_id}", "1", ex=ttl, nx=True)
        return bool(result)
    except Exception as e:
        logger.warning(f"acquire_chat_lock Redis error for {chat_id}: {e} — degrading")
        return True


async def release_chat_lock(chat_id: str) -> None:
    """Best-effort release. If Redis is down the TTL cleans up."""
    try:
        redis = await get_redis_client()
        await redis.delete(f"lock:chat:{chat_id}")
    except Exception as e:
        logger.debug(f"release_chat_lock swallow for {chat_id}: {e}")


def with_scheduler_lock(key: str, ttl: int):
    """
    Decorator for APScheduler jobs: ensures the wrapped coroutine runs on only
    one worker instance at a time (distributed lock via Redis SETNX).
    TTL should be slightly shorter than the job interval. Lock is released in a
    finally block so normal completion frees it immediately; TTL protects
    against crashes. On Redis error the job runs locally (degraded mode).
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            lock_key = f"lock:scheduler:{key}"
            redis = None
            try:
                redis = await get_redis_client()
            except Exception as e:
                logger.warning(f"with_scheduler_lock Redis unavailable for '{key}': {e} — running local only")
                return await func(*args, **kwargs)

            try:
                acquired = bool(await redis.set(lock_key, "1", ex=ttl, nx=True))
            except Exception as e:
                logger.warning(f"with_scheduler_lock SETNX failed for '{key}': {e} — running local only")
                return await func(*args, **kwargs)

            if not acquired:
                logger.debug(f"⏭️ Scheduler job '{key}' skipped — lock held by another worker")
                return

            try:
                return await func(*args, **kwargs)
            finally:
                try:
                    await redis.delete(lock_key)
                except Exception as e:
                    logger.debug(f"with_scheduler_lock release swallow for '{key}': {e}")
        return wrapper
    return decorator

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
