"""The public one-pager under site/: static, self-contained, and deployable as written."""

import base64
import hashlib
import re
import tomllib
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
PAGES = sorted(SITE.glob("*.html"))
API = "https://api-production-d95db.up.railway.app"
TRACKERS = re.compile(
    r"googletagmanager|google-analytics|gtag\(|fbq\(|facebook\.net|hotjar|clarity\.ms"
    r"|plausible|segment\.(?:com|io)|mixpanel|posthog",
    re.I,
)


class Page(HTMLParser):
    def __init__(self, path: Path):
        super().__init__()
        self.tags: list[tuple[str, dict]] = []
        self.feed(path.read_text(encoding="utf-8"))

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def hrefs(self):
        return [a["href"] for t, a in self.tags if t == "a" and "href" in a]

    def ids(self):
        return {a["id"] for _, a in self.tags if "id" in a}


def inline_styles(path: Path) -> list[bytes]:
    # Raw bytes: the browser hashes exactly what is served, so no newline normalising.
    return re.findall(rb"<style>(.*?)</style>", path.read_bytes(), re.S)


def redirects() -> dict[str, tuple[str, str]]:
    rules = {}
    for line in (SITE / "_redirects").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            source, target, status = line.split()
            rules[source] = (target, status)
    return rules


def netlify() -> dict:
    return tomllib.loads((ROOT / "netlify.toml").read_text(encoding="utf-8"))


def site_headers() -> dict[str, str]:
    block = next(h for h in netlify()["headers"] if h["for"] == "/*")
    return block["values"]


def test_the_pages_exist():
    assert {p.name for p in PAGES} == {"index.html", "404.html"}


@pytest.mark.parametrize("path", PAGES, ids=lambda p: p.name)
def test_page_is_hebrew_rtl(path):
    html = next(a for t, a in Page(path).tags if t == "html")
    assert html.get("lang") == "he"
    assert html.get("dir") == "rtl"


@pytest.mark.parametrize("path", PAGES, ids=lambda p: p.name)
def test_page_loads_nothing_from_anywhere_else(path):
    for tag, attrs in Page(path).tags:
        assert tag != "script", "no scripts: the CSP would block them anyway"
        assert "style" not in attrs, f"inline style attribute on <{tag}> breaks the CSP"
        if tag == "link" and attrs.get("rel") != "canonical":
            assert attrs["href"].startswith("data:"), attrs
        assert "src" not in attrs, f"<{tag}> loads an external resource"
    for css in inline_styles(path):
        assert not re.search(rb"@import|@font-face|url\(", css)
    assert not TRACKERS.search(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", PAGES, ids=lambda p: p.name)
def test_every_internal_link_resolves(path):
    page = Page(path)
    for href in page.hrefs():
        if href.startswith("#"):
            assert href[1:] in page.ids(), href
        elif href == "/":
            assert (SITE / "index.html").is_file()
        elif href.startswith("/"):
            assert href in redirects() or (SITE / href[1:]).is_file(), href


@pytest.mark.parametrize("source", ["/privacy", "/terms"])
def test_legal_pages_are_proxied_not_copied(source):
    target, status = redirects()[source]
    assert target == f"{API}{source}"
    assert status == "200"
    # A file of the same name would shadow the proxy and fork the text.
    assert not (SITE / f"{source[1:]}.html").exists()


def test_netlify_publishes_site_without_a_build():
    build = netlify()["build"]
    assert "command" not in build
    assert (ROOT / build.get("base", "") / build["publish"]).resolve() == SITE


def test_security_headers_are_present():
    headers = site_headers()
    for name in (
        "Strict-Transport-Security",
        "X-Content-Type-Options",
        "Referrer-Policy",
        "Content-Security-Policy",
        "X-Frame-Options",
        "Permissions-Policy",
    ):
        assert headers.get(name), name
    assert "preload" not in headers["Strict-Transport-Security"]


def test_csp_allows_exactly_the_styles_the_pages_ship():
    csp = site_headers()["Content-Security-Policy"]
    directives = dict(d.strip().split(" ", 1) for d in csp.split(";") if d.strip())
    expected = {
        "'sha256-" + base64.b64encode(hashlib.sha256(css).digest()).decode() + "'"
        for path in PAGES
        for css in inline_styles(path)
    }
    assert set(directives["style-src"].split()) == expected
    assert directives["default-src"] == "'none'"
    assert directives["frame-ancestors"] == "'none'"
    assert "unsafe" not in csp


@pytest.mark.parametrize("entry", ["site/", "netlify.toml"])
def test_the_api_image_does_not_carry_the_site(entry):
    lines = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert entry in lines
