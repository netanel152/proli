"""GET /terms, the draft gate in front of it, the policy links in the consent and
onboarding messages, and the PUBLIC_BASE_ORIGIN validator."""

import re
from pathlib import Path

import httpx
import pytest
from loguru import logger as loguru_logger

from app.core import legal_links
from app.core.config import Settings, settings
from app.core.messages import Messages
from app.core.startup_checks import (
    warn_if_public_origin_malformed,
    warn_if_terms_enabled_on_draft,
)
from app.main import app

REPO_ROOT = Path(__file__).resolve().parents[1]
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


def test_shipped_terms_are_the_approved_text():
    """The owner approved the wording and the draft banner is gone, so the only thing
    keeping /terms unpublished is TERMS_ENABLED, which is the owner's Railway change."""
    assert legal_links.terms_text_is_final() is True
    assert "data-terms-draft" not in legal_links.TERMS_HTML


def test_approving_the_text_does_not_publish_it_by_itself():
    """TERMS_ENABLED stays off by default: /terms answers 404 until the owner sets it."""
    assert Settings(**_REQUIRED).TERMS_ENABLED is False


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


@pytest.mark.parametrize("heading", ["לבעלי מקצוע", "ללקוחות", "לכל המשתמשים"])
def test_each_section_is_under_400_words(heading):
    section = re.search(
        rf"<h2>{heading}</h2>(.*?)(?=<h2>|</main>)", legal_links.TERMS_HTML, re.DOTALL
    ).group(1)

    assert len(re.sub(r"<[^>]+>", " ", section).split()) < 400


# --- The operator's details ---


def _terms_text() -> str:
    """The page as a reader sees it: tags stripped, whitespace collapsed."""
    return " ".join(re.sub(r"<[^>]+>", " ", legal_links.TERMS_HTML).split())


def test_no_placeholder_or_draft_marker_is_left():
    for marker in ("[להשלים", "data-terms-draft", "טיוטה"):
        assert marker not in legal_links.TERMS_HTML, marker


def test_terms_name_the_operator_and_how_to_reach_them():
    text = _terms_text()

    assert "נתנאל אקנין" in text
    assert "אדם פרטי" not in text
    assert 'href="mailto:info@proli.me"' in legal_links.TERMS_HTML
    assert "תל אביב-יפו" in text
    assert "הדין הישראלי" in text


def test_terms_state_an_effective_date_in_the_house_format():
    assert re.search(
        r"בתוקף מתאריך \d{1,2} ב[א-ת]+ 20\d\d", _terms_text()
    ), "the 'effective from' line must carry a real date"


def test_terms_carry_no_business_number_or_postal_address():
    """There is no registered business yet; the clauses are removed, not blanked."""
    text = _terms_text()

    for gone in ('ח"פ', "עוסק", "כתובת למשלוח דואר"):
        assert gone not in text


@pytest.mark.parametrize(
    "clause",
    [
        "אנחנו לא שירות חירום",
        "לבני 18 ומעלה",
        "נכתבות על ידי בינה מלאכותית",
        "איננו אחראים לעבודה עצמה",
    ],
)
def test_terms_carry_the_four_pilot_clauses(clause):
    assert clause in _terms_text()


def test_terms_follow_the_copy_style_guide():
    """docs/COPY_STYLE_GUIDE.md: no em dash, no emoji, no slashed gender form, and
    the 'we' voice, never Proli speaking about itself in the third person."""
    text = _terms_text()

    assert "—" not in legal_links.TERMS_HTML
    assert not re.search(r"[א-ת]/[א-ת]", text)
    assert not re.search(r"[\U0001F000-\U0001FFFF☀-➿]", text)
    assert "פרולי מתווכת" not in text
    assert "פרולי שומרת" not in text


@pytest.mark.asyncio
async def test_published_terms_page_names_the_operator(monkeypatch, approved_text):
    monkeypatch.setattr(settings, "TERMS_ENABLED", True)

    resp = await _get_terms()

    assert "נתנאל אקנין" in resp.text
    assert "info@proli.me" in resp.text


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
        ("https://proli.example:65535", "https://proli.example:65535"),
        ("https://www.xn--4dbrk0ce.example", "https://www.xn--4dbrk0ce.example"),
        ("https://8.8.8.8", "https://8.8.8.8"),
        ("", None),
        ("   ", None),
    ],
)
def test_origin_is_normalized(raw, expected):
    s = Settings(**_REQUIRED, PUBLIC_BASE_ORIGIN=raw)

    assert s.PUBLIC_BASE_ORIGIN == expected
    assert s.public_base_origin_malformed is False
    assert warn_if_public_origin_malformed(s) is False


def test_unset_origin_stays_silent():
    s = Settings(**_REQUIRED)

    assert s.PUBLIC_BASE_ORIGIN is None
    assert warn_if_public_origin_malformed(s) is False


MALFORMED_ORIGINS = [
    "proli.example",
    "http://proli.example",
    "https://",
    "http://localhost:8000",
    "https://localhost",
    "https://localhost.",
    "https://foo.localhost",
    "https://printer.local",
    "https://foo.railway.internal",
    "https://api",
    "https://127.0.0.1:8000",
    "https://127.0.0.2",
    "https://127.1",
    "https://0x7f.1",
    "https://2130706433",
    "https://0.0.0.0",
    "https://10.0.0.5",
    "https://192.168.1.1",
    "https://169.254.169.254",
    "https://[::1]",
    "https://proli.example/privacy",
    "https://proli.example?x=1",
    "https://p.example#",
    "https://p.example?",
    "https://p.example:",
    "https://p.example:abc",
    "https://p.example:0",
    "https://p.example:99999",
    "https://p .example",
    "https://.",
    "https://-",
    "https://-p.example",
    "https://p.example\\",
    "https://p.example\n.evil",
    "https://user:pw@proli.example",
]


@pytest.mark.parametrize("raw", MALFORMED_ORIGINS)
def test_malformed_origin_boots_with_no_link(monkeypatch, raw):
    s = Settings(**_REQUIRED, PUBLIC_BASE_ORIGIN=raw)
    monkeypatch.setattr(settings, "PUBLIC_BASE_ORIGIN", s.PUBLIC_BASE_ORIGIN)

    assert s.public_base_origin_malformed is True
    assert legal_links.consent_request_text() == Messages.Consent.REQUEST


def test_malformed_origin_is_reported_without_its_value():
    s = Settings(**_REQUIRED, PUBLIC_BASE_ORIGIN="https://user:hunter2@p.example")

    records = []
    sink_id = loguru_logger.add(records.append, level="ERROR")
    try:
        assert warn_if_public_origin_malformed(s) is True
    finally:
        loguru_logger.remove(sink_id)

    assert len(records) == 1
    assert "PUBLIC_BASE_ORIGIN" in records[0].record["message"]
    assert "hunter2" not in records[0].record["message"]
    assert "hunter2" not in repr(s)


def test_both_boot_paths_report_a_malformed_origin():
    for module in ("app/main.py", "app/core/arq_worker.py"):
        source = (REPO_ROOT / module).read_text(encoding="utf-8")
        assert "warn_if_public_origin_malformed(settings)" in source, module


@pytest.mark.parametrize("blank", ["", " "])
def test_blank_terms_enabled_is_off(blank):
    assert Settings(**_REQUIRED, TERMS_ENABLED=blank).TERMS_ENABLED is False


@pytest.mark.parametrize("value, expected", [("true", True), ("false", False)])
def test_terms_enabled_still_parses_a_real_value(value, expected):
    assert Settings(**_REQUIRED, TERMS_ENABLED=value).TERMS_ENABLED is expected
