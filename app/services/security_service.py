from app.core.redis_client import get_redis_client
from app.core.logger import logger

class SecurityService:
    @staticmethod
    async def check_rate_limit(chat_id: str, limit: int = 10, window_seconds: int = 60) -> bool:
        """
        Implements a Fixed Window rate limiter using Redis.
        Returns True if request is allowed, False if blocked.
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
