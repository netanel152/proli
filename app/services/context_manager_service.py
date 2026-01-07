import json
from typing import List, Optional, Dict, Any
from app.core.redis_client import get_redis_client
from app.core.logger import logger

class ContextManager:
    TTL = 3600  # 1 hour expiration

    @classmethod
    async def get_history(cls, chat_id: str) -> Optional[List[Dict[str, Any]]]:
        """
        Returns list of messages from Redis key `context:{chat_id}`.
        Returns None if key doesn't exist or on error.
        """
        try:
            redis = await get_redis_client()
            key = f"context:{chat_id}"
            # Fetch all items from the list
            items = await redis.lrange(key, 0, -1)
            
            if not items:
                return None
            
            # Redis returns list of strings (bytes decoded if decode_responses=True)
            # We need to parse each JSON string back to dict
            history = [json.loads(item) for item in items]
            return history
        except Exception as e:
            logger.error(f"Redis get_history error for {chat_id}: {e}")
            return None

    @classmethod
    async def update_history(cls, chat_id: str, role: str, content: str):
        """
        Appends a new message dict to the Redis list and resets TTL.
        """
        try:
            redis = await get_redis_client()
            key = f"context:{chat_id}"
            
            # Construct the message object consistent with get_chat_history format
            msg = {"role": role, "parts": [content]}
            
            # Append to list (RPUSH)
            await redis.rpush(key, json.dumps(msg))
            
            # Reset Expiration
            await redis.expire(key, cls.TTL)
        except Exception as e:
            logger.error(f"Redis update_history error for {chat_id}: {e}")

    @classmethod
    async def set_history(cls, chat_id: str, messages: List[Dict[str, Any]]):
        """
        Sets the entire history for a chat_id. Used for cache warming.
        """
        try:
            redis = await get_redis_client()
            key = f"context:{chat_id}"
            
            # clear existing to be safe (though set_history implies overwrite)
            await redis.delete(key)
            
            if messages:
                # Convert all dicts to JSON strings
                dumped_msgs = [json.dumps(m) for m in messages]
                # RPUSH allows multiple values
                await redis.rpush(key, *dumped_msgs)
                await redis.expire(key, cls.TTL)
        except Exception as e:
            logger.error(f"Redis set_history error for {chat_id}: {e}")

    @classmethod
    async def clear_context(cls, chat_id: str):
        """
        Deletes the context key.
        """
        try:
            redis = await get_redis_client()
            await redis.delete(f"context:{chat_id}")
        except Exception as e:
            logger.error(f"Redis clear_context error for {chat_id}: {e}")
