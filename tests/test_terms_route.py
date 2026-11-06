"""GET /terms, the draft gate in front of it, the policy links in the consent and
onboarding messages, and the PUBLIC_BASE_ORIGIN validator."""

import re

import httpx
import pytest
from pydantic import ValidationError

from app.core import legal_links
from app.core.config import Settings, settings
from app.core.messages import Messages
from app.core.startup_checks import warn_if_terms_enabled_on_draft
from app.main import app

ORIGIN = "https://example.test"
_REQUIRED = dict(
    GEMINI_API_KEY="k",
    CLOUDINARY_CLOUD_NAME="c",
    CLOUDINARY_API_KEY="k2",
    CLOUDINARY_API_SECRET="s",
)


async def _get_terms() -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        return await client.get("/terms")


@pytest.fixture
def approved_text(monkeypatch):
    """Stands in for the owner having finished the text; the HTML stays the real file."""
    monkeypatch.setattr(legal_links, "terms_text_is_final", lambda: True)


# --- The gate ---


def test_shipped_terms_are_still_a_draft():
    """Delete this test in the PR that carries the owner-approved text."""
    assert legal_links.terms_text_is_final() is False


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.asyncio
async def test_draft_is_404_whatever_the_flag_says(monkeypatch, enabled):
    monkeypatch.setattr(legal_links, "terms_text_is_final", lambda: False)
    monkeypatch.setattr(settings, "TERMS_ENABLED", enabled)

    assert (await _get_terms()).status_code == 404


@pytest.mark.asyncio
async def test_approved_text_is_404_until_enabled(monkeypatch, approved_text):
    monkeypatch.setattr(settings, "TERMS_ENABLED", False)

    assert (await _get_terms()).status_code == 404


@pytest.mark.parametrize(
    "html, final",
    [
        ("<div data-terms-draft>x</div>", False),
        ("[להשלים: שם]", False),
        ("<h2>לבעלי מקצוע</h2>", True),
    ],
)
def test_any_marker_keeps_the_text_unfinished(monkeypatch, html, final):
    monkeypatch.setattr(legal_links, "TERMS_HTML", html)

    assert legal_links.terms_text_is_final() is final


def test_boot_check_reports_only_a_flag_set_on_a_draft(monkeypatch):
    monkeypatch.setattr("app.core.startup_checks.terms_text_is_final", lambda: False)
    monkeypatch.setattr(settings, "TERMS_ENABLED", True)
    assert warn_if_terms_enabled_on_draft(settings) is True

    monkeypatch.setattr(settings, "TERMS_ENABLED", False)
    assert warn_if_terms_enabled_on_draft(settings) is False


# --- The page, once published ---


@pytest.mark.asyncio
async def test_published_terms_page(monkeypatch, approved_text):
    monkeypatch.setattr(settings, "TERMS_ENABLED", True)

    resp = await _get_terms()

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert resp.headers["cache-control"] == "public, max-age=3600"
    assert "<h2>לבעלי מקצוע</h2>" in resp.text
    assert "<h2>ללקוחות</h2>" in resp.text
    assert "גרסת פיילוט" in resp.text
    assert 'href="/privacy"' in resp.text


@pytest.mark.parametrize("heading", ["לבעלי מקצוע", "ללקוחות"])
def test_each_section_is_under_400_words(heading):
    section = re.search(
        rf"<h2>{heading}</h2>(.*?)(?=<h2>|</main>)", legal_links.TERMS_HTML, re.DOTALL
    ).group(1)

    assert len(re.sub(r"<[^>]+>", " ", section).split()) < 400


# --- The messages ---


def _assert_valid_message(text: str):
    assert "None" not in text
    assert "localhost" not in text
    assert "{" not in text


def test_consent_without_origin_is_the_plain_request(monkeypatch, approved_text):
    """Even enabled terms cannot produce a link with nowhere to point."""
    monkeypatch.setattr(settings, "TERMS_ENABLED", True)
    monkeypatch.setattr(settings, "PUBLIC_BASE_ORIGIN", None)

    text = legal_links.consent_request_text()

    assert text == Messages.Consent.REQUEST
    assert "http" not in text
    _assert_valid_message(text)


def test_consent_links_privacy_only_while_terms_are_unpublished(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_BASE_ORIGIN", ORIGIN)

    text = legal_links.consent_request_text()

    assert text.endswith(f"המדיניות המלאה: {ORIGIN}/privacy")
    assert "/terms" not in text
    _assert_valid_message(text)


def test_consent_links_both_once_terms_are_published(monkeypatch, approved_text):
    monkeypatch.setattr(settings, "PUBLIC_BASE_ORIGIN", ORIGIN)
    monkeypatch.setattr(settings, "TERMS_ENABLED", True)

    text = legal_links.consent_request_text()

    assert f"המדיניות המלאה: {ORIGIN}/privacy\n" in text
    assert text.endswith(f"תנאי השימוש: {ORIGIN}/terms")
    _assert_valid_message(text)


def test_consent_menu_survives_the_links(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_BASE_ORIGIN", ORIGIN)

    text = legal_links.consent_request_text()

    assert text.startswith(Messages.Consent.REQUEST)
    assert "*כן* — להמשיך\n*לא* — לביטול" in text


@pytest.mark.parametrize(
    "origin, enabled",
    [(None, True), (ORIGIN, False)],
    ids=["no-origin", "not-enabled"],
)
def test_welcome_has_no_terms_line_without_published_terms(
    monkeypatch, approved_text, origin, enabled
):
    monkeypatch.setattr(settings, "PUBLIC_BASE_ORIGIN", origin)
    monkeypatch.setattr(settings, "TERMS_ENABLED", enabled)

    assert legal_links.onboarding_welcome_text() == Messages.Onboarding.WELCOME


def test_draft_terms_are_never_linked(monkeypatch):
    monkeypatch.setattr(legal_links, "terms_text_is_final", lambda: False)
    monkeypatch.setattr(settings, "PUBLIC_BASE_ORIGIN", ORIGIN)
    monkeypatch.setattr(settings, "TERMS_ENABLED", True)

    assert legal_links.onboarding_welcome_text() == Messages.Onboarding.WELCOME
    assert "/terms" not in legal_links.consent_request_text()


def test_welcome_links_terms_and_still_ends_on_its_question(monkeypatch, approved_text):
    monkeypatch.setattr(settings, "PUBLIC_BASE_ORIGIN", ORIGIN)
    monkeypatch.setattr(settings, "TERMS_ENABLED", True)

    text = legal_links.onboarding_welcome_text()

    assert f"בהרשמה את/ה מאשר/ת את תנאי השימוש: {ORIGIN}/terms\n" in text
    assert text.endswith(Messages.Onboarding.WELCOME.split("\n")[-1])
    _assert_valid_message(text)


# --- The setting ---


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("https://proli.example", "https://proli.example"),
        ("  https://proli.example/  ", "https://proli.example"),
        ("https://proli.example:8443", "https://proli.example:8443"),
        ("", None),
        ("   ", None),
    ],
)
def test_origin_is_normalized(raw, expected):
    assert Settings(**_REQUIRED, PUBLIC_BASE_ORIGIN=raw).PUBLIC_BASE_ORIGIN == expected


@pytest.mark.parametrize(
    "raw",
    [
        "proli.example",
        "http://proli.example",
        "https://",
        "http://localhost:8000",
        "https://localhost",
        "https://127.0.0.1:8000",
        "https://0.0.0.0",
        "https://[::1]",
        "https://proli.example/privacy",
        "https://proli.example?x=1",
        "https://user:pw@proli.example",
    ],
)
def test_origin_rejects_what_would_be_a_broken_link(raw):
    with pytest.raises(ValidationError, match="PUBLIC_BASE_ORIGIN"):
        Settings(**_REQUIRED, PUBLIC_BASE_ORIGIN=raw)
