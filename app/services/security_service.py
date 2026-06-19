import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.redis_client import get_redis_client
from app.core.logger import logger
from app.core.config import settings


class SecurityService:
    @staticmethod
    async def check_rate_limit(
        chat_id: str, limit: int = 10, window_seconds: int = 60
    ) -> bool:
        """
        Implements a Fixed Window rate limiter using Redis.
        Returns True if request is allowed, False if blocked.

        NOTE: This is the coarse webhook-layer backstop (DDoS shield). The precise
        per-customer limit with pro/admin exemptions lives in the worker via
        check_sliding_window — keep this generous so it never blocks a legit pro/admin.
        """
        try:
            redis = await get_redis_client()
            key = f"rate_limit:{chat_id}"

            # Increment the counter
            current_count = await redis.incr(key)

            # If this is the first request in the window, set expiration
            if current_count == 1:
                await redis.expire(key, window_seconds)

            if current_count > limit:
                return False

            return True

        except Exception as e:
            # Fail Open Policy: If Redis fails, allow the request to prevent downtime
            logger.error(f"Rate Limit Check Failed for {chat_id}: {e}")
            return True

    @staticmethod
    async def check_sliding_window(
        chat_id: str, limit: int, window_seconds: int
    ) -> bool:
        """
        True sliding-window rate limiter backed by a Redis sorted set (PRO-21).

        Drops timestamps older than the window, records this hit, then counts what
        remains. Returns True if the request is allowed, False if it should be throttled.
        Fail-open: any Redis error returns True.
        """
        try:
            redis = await get_redis_client()
            key = f"rl:inbound:{chat_id}"
            now = time.time()
            cutoff = now - window_seconds
            member = f"{now}:{uuid.uuid4().hex[:8]}"  # unique so identical timestamps don't collide

            pipe = redis.pipeline()
            pipe.zremrangebyscore(key, 0, cutoff)
            pipe.zadd(key, {member: now})
            pipe.zcard(key)
            pipe.expire(key, window_seconds)
            results = await pipe.execute()

            count = results[2]  # ZCARD result
            return count <= limit

        except Exception as e:
            logger.error(f"Sliding-window check failed for {chat_id}: {e}")
            return True

    @staticmethod
    async def check_and_increment_daily_ai_cap(chat_id: str, cap: int) -> bool:
        """
        Per-chat daily ceiling on AI/multimodal Gemini calls (PRO-21 cost cap).

        Increments the counter for today (Israel time, so the quota resets at local
        midnight) and returns True while the count is within the cap, False once it is
        exceeded. Fail-open: any Redis error returns True.
        """
        try:
            redis = await get_redis_client()
            today = datetime.now(ZoneInfo(settings.TIMEZONE)).date().isoformat()
            key = f"ai:daily:{chat_id}:{today}"

            count = await redis.incr(key)
            if count == 1:
                await redis.expire(key, 86400)

            return count <= cap

        except Exception as e:
            logger.error(f"Daily AI cap check failed for {chat_id}: {e}")
            return True

    @staticmethod
    async def record_trip(chat_id: str, window_seconds: int = 60) -> int:
        """
        Count how often a chat has tripped a limit recently so the caller can escalate
        repeated abuse to logger.error (→ Sentry). Returns the running trip count for the
        window. Fail-open: any Redis error returns 0 (no escalation).
        """
        try:
            redis = await get_redis_client()
            key = f"rl:trips:{chat_id}"
            count = await redis.incr(key)
            if count == 1:
                await redis.expire(key, window_seconds)
            return count
        except Exception as e:
            logger.error(f"Trip counter failed for {chat_id}: {e}")
            return 0
