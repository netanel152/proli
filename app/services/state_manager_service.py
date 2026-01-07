import json
from app.core.redis_client import get_redis_client
from app.core.logger import logger
from app.core.constants import UserStates
from typing import Optional, Dict, Any

class StateManager:
    TTL = 3600  # 1 hour expiration

    @classmethod
    async def get_state(cls, chat_id: str) -> str:
        """
        Returns the state string. Defaults to UserStates.IDLE if key missing or Redis error.
        """
        try:
            redis = await get_redis_client()
            state = await redis.get(f"state:{chat_id}")
            if state:
                return state
            return UserStates.IDLE
        except Exception as e:
            logger.error(f"Error getting state for {chat_id}: {e}")
            return UserStates.IDLE

    @classmethod
    async def set_state(cls, chat_id: str, state_value: str):
        """
        Sets key state:{chat_id} with TTL.
        """
        try:
            redis = await get_redis_client()
            await redis.set(f"state:{chat_id}", state_value, ex=cls.TTL)
        except Exception as e:
             logger.error(f"Error setting state for {chat_id}: {e}")

    @classmethod
    async def clear_state(cls, chat_id: str):
        """
        Deletes state:{chat_id} and state_meta:{chat_id}.
        """
        try:
            redis = await get_redis_client()
            await redis.delete(f"state:{chat_id}", f"state_meta:{chat_id}")
        except Exception as e:
            logger.error(f"Error clearing state for {chat_id}: {e}")

    @classmethod
    async def get_metadata(cls, chat_id: str) -> Dict[str, Any]:
        """
        Returns dict from state_meta:{chat_id} (JSON load).
        """
        try:
            redis = await get_redis_client()
            data = await redis.get(f"state_meta:{chat_id}")
            if data:
                return json.loads(data)
            return {}
        except Exception as e:
            logger.error(f"Error getting metadata for {chat_id}: {e}")
            return {}

    @classmethod
    async def set_metadata(cls, chat_id: str, data: Dict[str, Any]):
        """
        Stores dict in state_meta:{chat_id} (JSON dump) with TTL.
        """
        try:
            redis = await get_redis_client()
            await redis.set(f"state_meta:{chat_id}", json.dumps(data), ex=cls.TTL)
        except Exception as e:
            logger.error(f"Error setting metadata for {chat_id}: {e}")
