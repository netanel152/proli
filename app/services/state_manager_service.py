import json
from app.core.redis_client import get_redis_client
from app.core.logger import logger
from app.core.phone import mask_chat_id
from app.core.constants import UserStates, WorkerConstants
from typing import Dict, Any


class StateManager:
    TTL = 14400  # 4 hours expiration (allows longer conversations)

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
            logger.error(f"Error getting state for {mask_chat_id(chat_id)}: {e}")
            return UserStates.IDLE

    @classmethod
    async def set_state(cls, chat_id: str, state_value: str, ttl: int = None):
        """
        Sets key state:{chat_id} with TTL.
        Optional ttl overrides the default (e.g. 7200s for PAUSED_FOR_HUMAN).
        """
        try:
            redis = await get_redis_client()
            effective_ttl = ttl if ttl is not None else cls.TTL
            prev = await redis.get(f"state:{chat_id}")
            await redis.set(f"state:{chat_id}", state_value, ex=effective_ttl)
            logger.info(
                f"🔄 FSM {mask_chat_id(chat_id)}: {prev or UserStates.IDLE} → {state_value} (ttl={effective_ttl}s)"
            )
        except Exception as e:
            logger.error(f"Error setting state for {mask_chat_id(chat_id)}: {e}")

    @classmethod
    async def clear_state(cls, chat_id: str):
        """
        Deletes state:{chat_id}, state_meta:{chat_id} and consent_hold:{chat_id},
        so a decline or a reset drops whatever was held before consent.
        """
        try:
            redis = await get_redis_client()
            prev = await redis.get(f"state:{chat_id}")
            await redis.delete(
                f"state:{chat_id}", f"state_meta:{chat_id}", f"consent_hold:{chat_id}"
            )
            if prev:
                logger.info(f"🔄 FSM {mask_chat_id(chat_id)}: {prev} → IDLE (cleared)")
        except Exception as e:
            logger.error(f"Error clearing state for {mask_chat_id(chat_id)}: {e}")

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
            logger.error(f"Error getting metadata for {mask_chat_id(chat_id)}: {e}")
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
            logger.error(f"Error setting metadata for {mask_chat_id(chat_id)}: {e}")

    # Not state_meta: that is one shared dict with a fixed 4h TTL, rewritten
    # whole by read-modify-write, so it cannot give "exactly one taker".
    #
    # None of the error logs below may carry `e`: this key holds message text
    # from someone who has not consented, and a client error can echo a command.
    @classmethod
    async def hold_message(cls, chat_id: str, text: str, media_url: str = None):
        """Queue a pre-consent message in Redis until the customer answers."""
        try:
            redis = await get_redis_client()
            key = f"consent_hold:{chat_id}"
            # One transaction, so the list can never exist without its TTL.
            pipe = redis.pipeline()
            pipe.rpush(key, json.dumps({"text": text, "media_url": media_url}))
            pipe.ltrim(key, 0, WorkerConstants.CONSENT_HOLD_MAX_MESSAGES - 1)
            pipe.expire(key, WorkerConstants.CONSENT_HOLD_TTL_SECONDS)
            await pipe.execute()
        except Exception as e:
            logger.error(
                f"Could not hold a message for {mask_chat_id(chat_id)}: "
                f"{type(e).__name__}"
            )

    @classmethod
    async def take_held_messages(cls, chat_id: str) -> Dict[str, Any] | None:
        """Atomically remove what was held and return it as one inbound turn.

        Atomic so that of two accepts, only one gets it. A turn takes one media
        file, so the first held one wins and any others are dropped.
        """
        try:
            redis = await get_redis_client()
            key = f"consent_hold:{chat_id}"
            pipe = redis.pipeline()
            pipe.lrange(key, 0, -1)
            pipe.delete(key)
            items, _ = await pipe.execute()
            held = [json.loads(item) for item in items]
        except Exception as e:
            logger.error(
                f"Could not read held messages for {mask_chat_id(chat_id)}: "
                f"{type(e).__name__}"
            )
            return None
        if not held:
            return None
        texts = []
        for entry in held:
            # Consecutive repeats are a retried job or a redelivery, not new words.
            if entry["text"] and (not texts or texts[-1] != entry["text"]):
                texts.append(entry["text"])
        media_urls = [entry["media_url"] for entry in held if entry["media_url"]]
        return {
            "text": "\n".join(texts),
            "media_url": media_urls[0] if media_urls else None,
        }
