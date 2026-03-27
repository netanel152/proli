import asyncio
import time
from arq.connections import RedisSettings
from app.core.config import settings
from app.services.workflow_service import process_incoming_message, whatsapp
from app.core.logger import logger
from app.core.database import client
from app.core.http_client import close_http_client
from app.core.redis_client import get_redis_client
from app.core.messages import Messages
from app.scheduler import start_scheduler

# Redis configuration for ARQ
if settings.REDIS_URL:
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
else:
    redis_settings = RedisSettings(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        database=settings.REDIS_DB
    )

async def startup(ctx):
    """
    Called when the worker starts.
    """
    logger.info("ARQ Worker starting...")
    
    # 1. Verify DB Connection
    try:
        await client.admin.command('ping')
        logger.info("✅ Worker connected to MongoDB.")
    except Exception as e:
        logger.critical(f"❌ Worker failed to connect to MongoDB: {e}")
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

async def process_message_task(ctx, chat_id: str, user_text: str, media_url: str = None):
    """
    ARQ Task wrapper for process_incoming_message.
    Sends a user-friendly error message if processing fails.
    """
    logger.info(f"Task started: processing message for {chat_id}")
    try:
        await process_incoming_message(chat_id, user_text, media_url)
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
    job_timeout = 300  # 5 minutes
    max_tries = 3  # Retry failed jobs up to 3 times
