import httpx
from typing import Optional

from app.core.logger import logger

_http_client: Optional[httpx.AsyncClient] = None


async def get_http_client() -> httpx.AsyncClient:
    """Returns a shared httpx.AsyncClient for media downloads and general HTTP calls."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=30.0)
    return _http_client


async def close_http_client():
    """Closes the shared HTTP client. Never raises (PRO-115).

    httpx's async close is ``aclose()`` — the old ``close()`` call raised
    AttributeError on every graceful shutdown of both services, failing the
    lifespan/worker teardown (the API never logged its clean-shutdown line).
    A failure to close a client the process is about to drop anyway is
    log-worthy, never fatal.
    """
    global _http_client
    client, _http_client = _http_client, None
    if client is None or client.is_closed:
        return
    try:
        await client.aclose()
    except Exception as e:
        logger.warning(f"HTTP client close failed (ignored during shutdown): {e}")
