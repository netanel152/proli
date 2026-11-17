"""The legacy ``POST /webhook`` route is gone, and stays gone.

It accepted the retired vendor's envelope, authenticated only by a ``?token=``
query parameter (``WEBHOOK_TOKEN``), and no live provider sent to it — it
survived as the e2e harness's ingress while being exposed to the internet in
production. Every ingress now goes through the Meta Cloud API route
(``POST /webhook/meta``, HMAC-authenticated). These tests pin the removal so
the old surface cannot come back by an accidental re-include or a revert of
the nginx block, while ``/webhook/meta`` keeps being served.
"""

import importlib.util
import re
from pathlib import Path

import httpx
import pytest

from app.core.constants import APIStatus
from app.main import app

REPO_ROOT = Path(__file__).resolve().parents[1]
NGINX_CONF = REPO_ROOT / "nginx" / "nginx.conf"

# The shape the retired vendor posted — what a stale integration or a scanner
# replaying it would still send.
_LEGACY_ENVELOPE = {
    "typeWebhook": "incomingMessageReceived",
    "idMessage": "LEGACY-1",
    "senderData": {"chatId": "972000000001@c.us", "sender": "972000000001@c.us"},
    "messageData": {
        "typeMessage": "textMessage",
        "textMessageData": {"textMessage": "hello"},
    },
}


async def _request(method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        return await client.request(method, path, **kwargs)


@pytest.mark.asyncio
async def test_post_legacy_webhook_returns_404():
    resp = await _request(
        "POST", "/webhook", params={"token": "anything"}, json=_LEGACY_ENVELOPE
    )
    assert resp.status_code == 404


def test_app_registers_no_bare_webhook_route_but_keeps_meta():
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/webhook" not in paths
    assert "/webhook/meta" in paths


def test_legacy_envelope_schema_package_is_gone():
    """``app/schemas/whatsapp.py`` modelled only the retired vendor's envelope;
    the Meta route parses its own payload in the provider layer."""
    # The module, not the package: a stale `app/schemas/__pycache__/` left in
    # an old checkout would still resolve `app.schemas` as a namespace package.
    assert not (REPO_ROOT / "app" / "schemas" / "whatsapp.py").exists()
    try:
        spec = importlib.util.find_spec("app.schemas.whatsapp")
    except ModuleNotFoundError:  # the parent package is gone too
        spec = None
    assert spec is None


def test_api_status_carries_only_the_meta_routes_statuses():
    legacy_only = {
        "RUNNING",
        "IGNORED_GROUP",
        "IGNORED_NO_DATA",
        "IGNORED_WRONG_INSTANCE",
        "IGNORED_RATE_LIMIT",
    }
    assert not legacy_only & set(vars(APIStatus))
    assert {"PROCESSING", "IGNORED_TYPE", "ERROR"} <= set(vars(APIStatus))


def _nginx_locations() -> dict[str, str]:
    """``location`` path → that block's body, for the simple non-nested blocks
    nginx.conf uses."""
    conf = NGINX_CONF.read_text(encoding="utf-8")
    return {
        m.group(1): m.group(2)
        for m in re.finditer(r"location\s+([^\s{]+)\s*\{([^}]*)\}", conf)
    }


def test_nginx_has_no_bare_webhook_location():
    """A ``location /webhook`` prefix block would proxy the removed route (and
    anything else under that prefix) straight to the api again."""
    assert "/webhook" not in _nginx_locations()


def test_nginx_still_proxies_the_meta_webhook():
    """``/webhook/meta`` used to be reached through the ``/webhook`` prefix
    block; with that gone it needs — and has — its own."""
    body = _nginx_locations().get("/webhook/meta")
    assert body is not None, "nginx.conf no longer routes /webhook/meta"
    assert re.search(r"proxy_pass\s+http://api:8000/webhook/meta\s*;", body)
    assert "limit_req zone=webhook_limit" in body
