import asyncio
import time
from arq.connections import RedisSettings
from arq.worker import Retry
from app.core.config import settings
from app.services.workflow_service import process_incoming_message, whatsapp
from app.core.logger import logger, mask_pii, page_critical
from app.core.sentry import sentry_active
from app.core.database import client
from app.core.http_client import close_http_client
from app.core.redis_client import get_redis_client, ChatLockBusyError
from app.core.messages import Messages
from app.providers.whatsapp.cloud_api import META_MEDIA_SCHEME, fetch_meta_media
from app.services.cloudinary_client_service import upload_media_bytes
from app.scheduler import start_scheduler

# Redis configuration for ARQ
if settings.REDIS_URL:
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL.get_secret_value())
else:
    redis_settings = RedisSettings(
        host=settings.REDIS_HOST, port=settings.REDIS_PORT, database=settings.REDIS_DB
    )


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
    logger.info("⏳ Starting Scheduler within Worker...")
    ctx["scheduler"] = start_scheduler()

    # 3. Start heartbeat loop
    async def _heartbeat_loop():
        while True:
            try:
                redis = await get_redis_client()
                await redis.set("worker:heartbeat", str(time.time()), ex=120)
            except Exception:
                pass
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


async def _resolve_inbound_media(media_url: str | None) -> str | None:
    """PRO-89: turn a ``meta-media://<id>`` marker into a permanent URL.

    Meta webhooks carry a media *id*; the real CDN URL needs an authorized
    fetch and expires within minutes, so the route enqueues the marker and the
    worker re-hosts the bytes on Cloudinary here — downstream code (lead
    ``media_urls``, the pro's offer message, the AI engine) then sees an
    ordinary public URL, exactly as it always has. Failure degrades to
    text-only processing (returns ``None``), never to a crashed task.
    """
    if not media_url or not media_url.startswith(META_MEDIA_SCHEME):
        return media_url
    media_id = media_url[len(META_MEDIA_SCHEME) :]
    data, _mime = await fetch_meta_media(media_id)
    if data is None:
        logger.warning(f"Could not fetch Meta media {media_id} — processing text only.")
        return None
    hosted_url = await asyncio.to_thread(upload_media_bytes, data)
    if hosted_url is None:
        logger.warning(
            f"Could not re-host Meta media {media_id} — processing text only."
        )
    return hosted_url


async def process_message_task(
    ctx, chat_id: str, user_text: str, media_url: str = None, message_id: str = None
):
    """
    ARQ Task wrapper for process_incoming_message.
    Sends a user-friendly error message if processing fails.

    ``message_id`` (the provider's wamid) is optional so jobs enqueued
    before the kwarg existed still deserialize.
    """
    logger.info(f"Task started: processing message for {chat_id}")
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
        media_url = await _resolve_inbound_media(media_url)
        await process_incoming_message(chat_id, user_text, media_url)
    except ChatLockBusyError:
        # Another worker is mid-flight for this chat_id — defer so we preserve
        # message order without duplicate-processing.
        logger.info(f"Chat lock busy for {chat_id} — requeuing with 2s defer")
        raise Retry(defer=2)
    except Exception as e:
        logger.error(f"Error in process_message_task for {chat_id}: {e}", exc_info=True)
        # Send user-friendly fallback message
        try:
            await whatsapp.send_message(chat_id, Messages.Errors.AI_OVERLOAD)
        except Exception:
            logger.error(f"Failed to send error message to {chat_id}")
        raise


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
    max_tries = 5  # Retry transient failures up to 5 times
    retry_jobs = True
    # Keep results in Redis for 7 days so failed jobs are visible for debugging
    keep_result = 604800  # 7 days in seconds
    keep_result_forever = False
