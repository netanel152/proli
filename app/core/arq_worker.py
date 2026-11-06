import asyncio
import time
from arq.connections import RedisSettings
from arq.worker import Retry
from app.core.config import settings
from app.core.startup_checks import (
    warn_if_admin_phone_unconfigured,
    warn_if_public_origin_malformed,
    warn_if_waba_id_missing,
    warn_if_terms_enabled_on_draft,
)
from app.services.workflow_service import (
    page_emergency_turn,
    process_incoming_message,
    whatsapp,
)
from app.core.logger import logger, mask_pii, new_trace_id, page_critical
from app.core.phone import mask_chat_id
from app.core.sentry import sentry_active
from app.core.database import client
from app.core.http_client import close_http_client
from app.core.redis_client import get_redis_client, ChatLockBusyError
from app.core.messages import Messages
from app.core.text_matching import is_emergency_text
from app.scheduler import start_scheduler

# Redis configuration for ARQ
if settings.REDIS_URL:
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL.get_secret_value())
else:
    redis_settings = RedisSettings(
        host=settings.REDIS_HOST, port=settings.REDIS_PORT, database=settings.REDIS_DB
    )


# How many times a job may really start. Tries spent waiting on the chat lock
# don't count, so this is separate from WorkerSettings.max_tries.
MAX_REAL_STARTS = 5
# Longer than a job can live: max_tries of backoff plus the turns ahead of it.
LOCK_BUSY_COUNT_TTL_SECONDS = 3600
RETRY_NOTICE_TTL_SECONDS = 60


async def startup(ctx):
    """
    Called when the worker starts.
    """
    logger.info("ARQ Worker starting...")

    # 1. Verify DB Connection
    try:
        await client.admin.command("ping")
        logger.info("✅ Worker connected to MongoDB.")
    except Exception as e:
        page_critical(f"❌ Worker failed to connect to MongoDB: {e}")
        raise e  # Stop startup if DB is down

    # 2. Start Scheduler
    warn_if_admin_phone_unconfigured(settings)  # PRO-48
    warn_if_terms_enabled_on_draft(settings)
    warn_if_public_origin_malformed(settings)
    warn_if_waba_id_missing(settings)

    logger.info("⏳ Starting Scheduler within Worker...")
    ctx["scheduler"] = start_scheduler()

    # 3. Start heartbeat loop
    async def _heartbeat_loop():
        while True:
            try:
                redis = await get_redis_client()
                await redis.set("worker:heartbeat", str(time.time()), ex=120)
            except Exception as e:
                # WARNING, deliberately not ERROR: the bridge must not spend
                # Sentry budget on this — a dead heartbeat already surfaces
                # as worker_alive=false on /health, which is the real signal.
                logger.warning(f"💓 Heartbeat write failed: {e}")
            await asyncio.sleep(60)

    ctx["heartbeat_task"] = asyncio.create_task(_heartbeat_loop())
    logger.info("💓 Worker heartbeat started.")


async def shutdown(ctx):
    """
    Called when the worker shuts down.
    """
    logger.info("ARQ Worker shutting down...")

    # Cancel heartbeat
    if "heartbeat_task" in ctx:
        ctx["heartbeat_task"].cancel()

    # Shutdown Scheduler
    if "scheduler" in ctx:
        ctx["scheduler"].shutdown()
        logger.info("Scheduler shut down.")

    # Close shared HTTP client
    await close_http_client()


async def process_message_task(
    ctx,
    chat_id: str,
    user_text: str,
    media_url: str = None,
    message_id: str = None,
    trace_id: str = None,
    **_ignored,
):
    """
    ARQ Task wrapper for process_incoming_message.
    Sends a user-friendly error message if processing fails.

    ``message_id`` (the provider's wamid) and ``trace_id`` (PRO-174) are both
    optional so jobs enqueued before either kwarg existed still deserialize.
    ``**_ignored`` covers the other direction, which is the one that actually
    loses messages: api and worker are separate Railway services and do not
    restart together, so for the length of a deploy a *new* api enqueues a
    kwarg an *old* worker has no parameter for — a TypeError inside arq's
    `run_job`, and the message is gone rather than deferred. It does not
    retroactively protect this deploy (the worker running then is the old
    one), but it means the next kwarg added here costs nothing.
    """
    # PRO-174: the correlation id the webhook minted for this turn, rebound
    # here so every line the worker emits — and everything the flows log
    # beneath it — carries the same id as the API's. A locally minted id is
    # the fallback for a job enqueued before this kwarg existed: it cannot
    # match the api's half of that turn, but it still groups the worker's,
    # which is what makes those lines readable at all.
    with logger.contextualize(trace_id=trace_id or new_trace_id()):
        await _process_message(ctx, chat_id, user_text, media_url, message_id)


async def _process_message(
    ctx, chat_id: str, user_text: str, media_url: str = None, message_id: str = None
):
    """The task body, split out so all of it — the ``except`` arms included —
    runs inside the ``trace_id`` binding."""
    # These two lines are the only way to tie arq's own WARNINGs, which name just
    # the job id, to a chat. Its INFO lines carry the message, so they are off.
    job_id = ctx.get("job_id")
    job_try = ctx.get("job_try") or 1
    started = time.monotonic()
    logger.info(f"Task started: job {job_id} try {job_try} for {mask_chat_id(chat_id)}")
    try:
        if await _ran_too_many_times(job_id, job_try):
            await _drop_job(
                chat_id,
                user_text,
                f"re-run too many times: job {job_id}, try {job_try}",
            )
            return
        await _run_message(chat_id, user_text, media_url, message_id, job_id, job_try)
    finally:
        logger.info(
            f"Task ended: job {job_id} try {job_try} for {mask_chat_id(chat_id)} "
            f"after {time.monotonic() - started:.2f}s"
        )


async def _ran_too_many_times(job_id, job_try: int) -> bool:
    """`max_tries` is sized for waiting on the lock, which runs nothing. A job that
    keeps starting and dying repeats its AI call and reply, so real starts keep a
    lower cap. If Redis cannot say how many tries were waits, the job runs."""
    if job_try < MAX_REAL_STARTS:
        return False
    try:
        redis = await get_redis_client()
        busy = int(await redis.get(f"lock_busy:{job_id}") or 0)
    except Exception as e:
        logger.warning(f"Could not read the lock-wait count of job {job_id}: {e}")
        return False
    return job_try - busy >= MAX_REAL_STARTS


async def _count_lock_wait(job_id) -> None:
    try:
        redis = await get_redis_client()
        pipe = redis.pipeline()
        pipe.incr(f"lock_busy:{job_id}")
        pipe.expire(f"lock_busy:{job_id}", LOCK_BUSY_COUNT_TTL_SECONDS)
        await pipe.execute()
    except Exception as e:
        logger.warning(f"Could not count a lock wait for job {job_id}: {e}")


async def _drop_job(chat_id: str, user_text: str, reason: str) -> None:
    """arq would delete the job with no trace of whose it was. The message is lost
    either way; at least the customer and the operator know."""
    logger.error(f"Message from {mask_chat_id(chat_id)} dropped, it is lost ({reason})")
    if is_emergency_text((user_text or "").strip().lower()):
        await page_emergency_turn(chat_id, "was dropped by the worker")
    try:
        redis = await get_redis_client()
        # Followers queued behind the same turn run out together.
        first = await redis.set(
            f"retry_notice:{chat_id}", "1", ex=RETRY_NOTICE_TTL_SECONDS, nx=True
        )
    except Exception:
        first = True
    if first:
        await _tell_customer_to_retry(chat_id)


async def _run_message(chat_id, user_text, media_url, message_id, job_id, job_try):
    if sentry_active():
        # PRO-134: tags set at task start (not in an except) so they ride
        # every event and breadcrumb this job produces. ArqIntegration
        # isolates scope per job. chat_id is masked — never the raw phone.
        import sentry_sdk

        scope = sentry_sdk.get_isolation_scope()
        scope.set_tag("chat_id", mask_pii(chat_id))
        scope.set_tag("provider", settings.WHATSAPP_PROVIDER)
        if message_id:
            scope.set_tag("wamid", message_id)
    try:
        await process_incoming_message(chat_id, user_text, media_url)
    except ChatLockBusyError:
        # Another worker is mid-flight for this chat_id — defer so we preserve
        # message order without duplicate-processing.
        if job_try >= WorkerSettings.max_tries:
            await _drop_job(
                chat_id, user_text, f"chat lock busy on the last try: job {job_id}"
            )
            return
        await _count_lock_wait(job_id)
        defer = 2 * job_try
        logger.info(
            f"Chat lock busy for {mask_chat_id(chat_id)} — requeuing with {defer}s defer"
        )
        raise Retry(defer=defer)
    except Exception as e:
        # sentry_skip: the re-raise below propagates to ArqIntegration, which
        # captures the full exception — the bridge reporting this log line
        # too would double-count every task failure.
        logger.bind(sentry_skip=True).error(
            f"Error in process_message_task for {mask_chat_id(chat_id)}: {e}",
            exc_info=True,
        )
        await _tell_customer_to_retry(chat_id)
        raise


async def _tell_customer_to_retry(chat_id: str) -> None:
    """Send user-friendly fallback message."""
    try:
        await whatsapp.send_message(chat_id, Messages.Errors.AI_OVERLOAD)
    except Exception:
        logger.error(f"Failed to send error message to {mask_chat_id(chat_id)}")


class WorkerSettings:
    """
    Configuration for the ARQ worker.
    """

    functions = [process_message_task]
    redis_settings = redis_settings
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 10
    job_timeout = 300  # 5 minutes per job
    # arq re-runs a job only on Retry (a busy chat lock) or CancelledError (a worker
    # shutdown); any other exception fails it for good. With the lock backoff of
    # 2s, 4s, 6s... the last try lands 306s in, past job_timeout, so a follower
    # outlives even the slowest single turn ahead of it. MAX_REAL_STARTS caps the
    # re-runs that actually do something.
    max_tries = 18
    retry_jobs = True
    # A result stores the job's args, and the args are the customer's message, so
    # keeping them would hold text from someone who declined consent. Nothing
    # reads results. An exception reaches Sentry and the logs; a job arq drops at
    # max_tries is only a WARNING naming its id, traced through "Task started".
    keep_result = 0
    keep_result_forever = False
