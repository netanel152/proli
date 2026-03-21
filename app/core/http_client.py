import httpx
from typing import Optional

_http_client: Optional[httpx.AsyncClient] = None

async def get_http_client() -> httpx.AsyncClient:
    """Returns a shared httpx.AsyncClient for media downloads and general HTTP calls."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=30.0)
    return _http_client

async def close_http_client():
    """Closes the shared HTTP client."""
    global _http_client
    if _http_client and not _http_client.is_closed:
        await _http_client.close()
        _http_client = None
