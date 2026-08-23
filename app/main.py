import uuid
import time
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from app.core.config import settings
from app.core.constants import APIStatus
import asyncio
from contextlib import asynccontextmanager
from app.api.routes import webhook, meta_webhook, health, privacy
from app.core.redis_client import close_redis_client, get_redis_client
from app.core.http_client import close_http_client as _close_shared_http_client
from app.core.database import client as mongo_client
from app.core.logger import logger, page_critical
from app.core.sentry import init_sentry, sentry_active
from scripts.create_indexes import create_all_indexes


# One shared init for all three services (app/core/sentry.py): explicit
# integration allowlist + before_send scrubbing. service tag: proli-api.
init_sentry("proli-api")


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach a unique X-Request-ID to every request for log correlation."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        if sentry_active():
            # StarletteIntegration's ASGI wrapper creates the per-request
            # isolation scope outside all user middleware, so this tag lands
            # on every event/breadcrumb this request produces (PRO-134).
            import sentry_sdk

            sentry_sdk.get_isolation_scope().set_tag("request_id", request_id)
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 1)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time"] = f"{duration_ms}ms"
        return response


async def _connect_with_retry(name: str, probe, attempts: int = 6) -> None:
    """PRO-153 — bounded-backoff startup probe for a hard dependency.

    Railway does not order service restarts: an environment-wide redeploy can
    bring the api container up seconds before redis (it did, 2026-08-22, and
    /health answered 502 for 8 minutes while the dashboard said SUCCESS). A
    co-restart is transient, so retry — delays 1,2,4,8,8s (~23s of waiting,
    ~30s wall clock with probe timeouts) cover it without masking a genuinely
    dead dependency.

    Retries log at WARNING (visible under the production LOG_LEVEL=WARNING,
    unlike the INFO success lines). Exhausting the attempts keeps the old
    behaviour exactly: page_critical + raise — that path is proven to reach
    Sentry (issue PYTHON-5 is this very message from real failed boots).
    """
    for attempt in range(1, attempts + 1):
        try:
            await probe()
            logger.info(f"✅ API connected to {name}.")
            return
        except Exception as e:
            if attempt == attempts:
                page_critical(
                    f"❌ API failed to connect to {name} on startup "
                    f"after {attempts} attempts: {e}"
                )
                raise
            delay = min(2 ** (attempt - 1), 8)
            logger.warning(
                f"⚠️ {name} not reachable on startup "
                f"(attempt {attempt}/{attempts}): {e} — retrying in {delay}s"
            )
            await asyncio.sleep(delay)


async def _probe_mongo() -> None:
    await mongo_client.admin.command("ping")


async def _probe_redis() -> None:
    # get_redis_client only caches its singleton after a successful ping, so
    # a failed attempt leaves no poisoned client behind for the retry.
    redis = await get_redis_client()
    await redis.ping()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---- Startup ----
    # PRO-153: both probes retry with bounded backoff (see _connect_with_retry).
    # After the retries the decision is deliberate fail-closed: this API's one
    # job is enqueueing webhooks to Redis, so serving without it would 503
    # every webhook while the service looked "up" — refusing to boot (with the
    # page) is the honest failure once a co-restart window is exhausted.
    await _connect_with_retry("MongoDB", _probe_mongo)
    await _connect_with_retry("Redis", _probe_redis)

    # Ensure all MongoDB indexes exist (idempotent — safe to run on every startup)
    try:
        await create_all_indexes(silent=True)
        logger.info("✅ MongoDB indexes verified.")
    except Exception as e:
        logger.warning(f"⚠️ Index creation failed (non-fatal): {e}")

    yield

    # ---- Shutdown ----
    await close_redis_client()
    await _close_shared_http_client()
    logger.info("API shut down cleanly.")


app = FastAPI(title=settings.PROJECT_NAME, lifespan=lifespan)

# --- Middleware ---
app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# --- Routers ---
app.include_router(webhook.router)
app.include_router(meta_webhook.router)  # PRO-89 — Meta Cloud API inbound
app.include_router(privacy.router)  # PRO-87 — public privacy policy page
app.include_router(health.router)

if __name__ == "__main__":
    import os, uvicorn

    port = int(os.getenv("PORT", 8000))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port)
