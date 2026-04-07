import uuid
import time
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from app.core.config import settings
from app.core.constants import APIStatus
from contextlib import asynccontextmanager
from app.api.routes import webhook, health
from app.core.redis_client import close_redis_client, get_redis_client
from app.core.http_client import close_http_client as _close_shared_http_client
from app.core.database import client as mongo_client
from app.core.logger import logger
from scripts.create_indexes import create_all_indexes


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach a unique X-Request-ID to every request for log correlation."""
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 1)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time"] = f"{duration_ms}ms"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---- Startup ----
    # Verify MongoDB is reachable before accepting traffic
    try:
        await mongo_client.admin.command("ping")
        logger.info("✅ API connected to MongoDB.")
    except Exception as e:
        logger.critical(f"❌ API failed to connect to MongoDB on startup: {e}")
        raise

    # Verify Redis is reachable
    try:
        redis = await get_redis_client()
        await redis.ping()
        logger.info("✅ API connected to Redis.")
    except Exception as e:
        logger.critical(f"❌ API failed to connect to Redis on startup: {e}")
        raise

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
app.include_router(health.router)

if __name__ == "__main__":
    import os, uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port)
