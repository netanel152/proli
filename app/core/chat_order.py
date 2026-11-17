"""PRO-214 — one chat's messages run in the order they arrived.

A per-chat Redis sorted set, ``chat_order:{chat_id}``, holds the job ids of that
chat's queued messages scored by arrival time; the worker runs a job only from
the head (``app/core/arq_worker.py``). The webhook joins each job at *enqueue*,
under the id it hands arq, so the order is fixed before any worker can pick a
job up: two messages a few milliseconds apart, taken by two worker slots at
once, cannot swap. A job's presence key says it is still alive; a head whose key
has expired is pruned so a lost job cannot hold the chat.
"""

#: How long a queued job's presence lasts without a refresh. The worker refreshes
#: it on every try (the longest lock backoff between tries is well under this),
#: and extends it to cover a whole turn while the job runs.
WAITING_TTL_SECONDS = 60
#: The sorted set itself, refreshed on every join.
KEY_TTL_SECONDS = 3600


def order_key(chat_id: str) -> str:
    return f"chat_order:{chat_id}"


def presence_key(job_id) -> str:
    return f"chat_order_seen:{job_id}"


async def join(redis, chat_id: str, job_id: str, score: float) -> None:
    """Put ``job_id`` in the chat's queue at ``score`` (first join wins)."""
    pipe = redis.pipeline()
    pipe.zadd(order_key(chat_id), {job_id: score}, nx=True)
    pipe.expire(order_key(chat_id), KEY_TTL_SECONDS)
    pipe.set(presence_key(job_id), "1", ex=WAITING_TTL_SECONDS)
    await pipe.execute()


async def leave(redis, chat_id: str, job_id: str) -> None:
    pipe = redis.pipeline()
    pipe.zrem(order_key(chat_id), job_id)
    pipe.delete(presence_key(job_id))
    await pipe.execute()
