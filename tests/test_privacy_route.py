"""PRO-87 — GET /privacy: the public privacy policy page.

Static page (app/static/privacy.html) served by app/api/routes/privacy.py.
No auth, no DB, no Redis — a plain httpx.ASGITransport client is enough (see
tests/test_meta_webhook_route.py for the pattern used elsewhere in this repo).
"""

import re

import httpx
import pytest

from app.main import app


async def _get_privacy() -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        return await client.get("/privacy")


@pytest.mark.asyncio
async def test_get_privacy_returns_200_html_with_cache_control():
    resp = await _get_privacy()

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert resp.headers["cache-control"] == "public, max-age=3600"


@pytest.mark.asyncio
async def test_get_privacy_no_auth_required():
    """The page must stay reachable with zero credentials/token — Meta's
    reviewer hits this URL cold."""
    resp = await _get_privacy()

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_privacy_body_contains_hebrew_and_english_titles():
    resp = await _get_privacy()

    assert "מדיניות פרטיות" in resp.text
    assert "Privacy Policy" in resp.text


@pytest.mark.asyncio
async def test_get_privacy_body_states_90_day_retention():
    """This wording must stay in sync with the messages-collection TTL index
    in scripts/create_indexes.py (expireAfterSeconds=7776000, i.e. 90 days).
    If that TTL ever changes, this page's retention statement (and this
    assertion) must change with it, in both languages."""
    resp = await _get_privacy()

    assert "90 יום" in resp.text
    assert "90 days" in resp.text


@pytest.mark.asyncio
async def test_get_privacy_body_contains_contact_mailto_link():
    resp = await _get_privacy()

    assert 'href="mailto:' in resp.text


@pytest.mark.asyncio
async def test_get_privacy_page_is_self_contained_no_external_resources():
    """No <link>/<script>/<img> may point at an external http(s) resource —
    Meta's reviewer must never see a broken page because a CDN was down.
    mailto: links and plain-text URLs in the body copy are fine; this only
    guards actual resource-loading tags."""
    resp = await _get_privacy()

    external_resource_tags = re.findall(
        r"<(?:link|script|img)[^>]*\s(?:href|src)=[\"']https?://[^\"']*[\"'][^>]*>",
        resp.text,
        flags=re.IGNORECASE,
    )
    assert external_resource_tags == []
