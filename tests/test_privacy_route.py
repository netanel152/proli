"""PRO-87 — GET /privacy: the public privacy policy page.

Static page (app/static/privacy.html) served by app/api/routes/privacy.py.
No auth, no DB, no Redis — a plain httpx.ASGITransport client is enough (see
tests/test_meta_webhook_route.py for the pattern used elsewhere in this repo).
"""

import calendar
import re

import httpx
import pytest

from app.core.constants import WorkerConstants
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
    in app/db/indexes.py (expireAfterSeconds=7776000, i.e. 90 days).
    If that TTL ever changes, this page's retention statement (and this
    assertion) must change with it, in both languages."""
    resp = await _get_privacy()

    assert "90 יום" in resp.text
    assert "90 days" in resp.text


@pytest.mark.asyncio
async def test_get_privacy_body_states_the_pre_consent_hold():
    """The policy says one hour because the hold's TTL is one hour. Changing the
    constant must change this text with it, in both languages."""
    assert WorkerConstants.CONSENT_HOLD_TTL_SECONDS == 3600
    resp = await _get_privacy()
    assert "<strong>שעה</strong> מההודעה האחרונה" in resp.text
    assert "<strong>one hour</strong> after your last message" in resp.text


@pytest.mark.asyncio
async def test_get_privacy_body_contains_contact_mailto_link():
    resp = await _get_privacy()

    assert 'href="mailto:' in resp.text


@pytest.mark.asyncio
async def test_get_privacy_body_names_the_operator_next_to_the_contact_line():
    """Amendment 13 to the Privacy Protection Law: the notice names the database
    controller and how to reach them. The name sits in the Contact section of both
    language parts, before the mailto line. It must stay in step with
    app/static/terms.html, which names the same operator."""
    resp = await _get_privacy()

    for name, heading in (("נתנאל אקנין", "יצירת קשר"), ("Netanel Aknin", "Contact")):
        section = re.search(
            rf"<h2>{heading}</h2>(.*?)(?=<!--|</section>|</main>)", resp.text, re.DOTALL
        ).group(1)
        assert name in section, heading
        assert section.index(name) < section.index('href="mailto:info@proli.me"')


@pytest.mark.asyncio
async def test_get_privacy_body_uses_the_statutory_term_for_the_controller():
    resp = await _get_privacy()

    assert "בעל השליטה במאגר המידע: נתנאל אקנין" in resp.text
    assert "ובעל מאגר המידע" not in resp.text


@pytest.mark.asyncio
async def test_get_privacy_last_updated_date_agrees_in_both_languages():
    """The two parts are one policy: bumping one date and not the other is a bug."""
    months = {
        "ינואר": 1, "פברואר": 2, "מרץ": 3, "אפריל": 4, "מאי": 5, "יוני": 6,
        "יולי": 7, "אוגוסט": 8, "ספטמבר": 9, "אוקטובר": 10, "נובמבר": 11,
        "דצמבר": 12,
    }  # fmt: skip
    english = {name: i for i, name in enumerate(calendar.month_name) if name}
    resp = await _get_privacy()

    he = re.search(r"עדכון אחרון: (\d{1,2}) ב(\S+) (\d{4})", resp.text)
    en = re.search(r"Last updated: (\w+) (\d{1,2}), (\d{4})", resp.text)

    assert he and en
    assert (int(he[3]), months[he[2]], int(he[1])) == (
        int(en[3]),
        english[en[1]],
        int(en[2]),
    )


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
