"""Screenshot the admin panel at every viewport the responsive work targets.

Slice S5 of `docs/ADMIN_PANEL_RESPONSIVE_PLAN.md`. The unit suite pins the CSS's
*structure*; this answers the question it cannot — what the page actually looks
like at 375px in Hebrew — and produces the evidence a reviewer can check without
a phone.

Two targets:

* **the layout preview** (default) — `scripts/admin_panel_layout_preview.py`,
  real Streamlit and real CSS with fake data, so it needs no database, no
  password and no cookie. This is what CI or a reviewer can run.
* **the live panel** — pass `--url`. Needs the panel running against a real
  Mongo/Redis, and an operator already logged in (or `--password`).

    # preview, both languages, every width  (starts Streamlit itself)
    python scripts/admin_panel_screenshots.py

    # a live panel you already have open
    python scripts/admin_panel_screenshots.py --url http://localhost:8501

Output lands in `artifacts/admin-panel/<section>-<lang>-<width>x<height>.png`,
which is gitignored — these are review aids, not fixtures. `--scheme dark`
re-shoots the same grid on the dark palette, which has bugs of its own that
the light one cannot show.

Playwright is a dev-only dependency and deliberately not in `requirements.txt`;
the script says so and exits cleanly rather than failing obscurely when it is
absent. It never downloads a browser: it uses the one already on the machine,
falling back to Playwright's own resolution when no pinned path is set.
"""

import argparse
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "artifacts" / "admin-panel"

# The four the plan's acceptance criteria name, and why each is in the list.
VIEWPORTS = [
    (375, 812, "phone"),  # iPhone-class portrait: the operator in the field
    (768, 1024, "tablet-portrait"),  # also a half-tiled desktop window
    (1024, 768, "tablet-landscape"),  # the top of the tablet band
    (1440, 900, "desktop"),  # must be unchanged from before the work
]

LANGS = ["HE", "EN"]

# Every page the preview can render. `widgets` exists because every RTL defect
# that survived the first pass was in chrome the Dashboard happens not to show;
# `forms` is the S3 surface (the action rows that must not stack) and `login`
# is its own page because that is how the login screen really renders — alone,
# with the page cap doing the centring.
SECTIONS = ["dashboard", "widgets", "forms", "login"]

# Google's own class rule for the icon font, so a locally supplied file renders
# exactly as the stylesheet the panel links to. Needed because an environment
# whose browser cannot reach fonts.googleapis.com (a sandboxed session behind
# an egress proxy, an offline laptop) shoots every icon as its ligature *name*
# — "support_agent", "handyman" — and those words are wide enough to wrap a
# kanban header onto three lines and push its count badge out of the column.
# That reads as a layout bug and is not one: with the glyphs in place every
# header fits. `--icon-font` makes the screenshots show the real thing.
ICON_FONT_FAMILY = "Material Symbols Rounded"
ICON_FONT_CLASS_CSS = """
.material-symbols-rounded {
  font-family: 'Material Symbols Rounded';
  font-weight: normal;
  font-style: normal;
  font-size: 24px;
  line-height: 1;
  letter-spacing: normal;
  text-transform: none;
  display: inline-block;
  white-space: nowrap;
  word-wrap: normal;
  direction: ltr;
  -webkit-font-smoothing: antialiased;
}
"""

# Set by the environment this repo's sessions run in; when present it is the
# browser to use, and downloading another would be both slow and wrong.
PINNED_CHROMIUM = "/opt/pw-browsers/chromium"


def _free_port():
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _wait_for_http(url, timeout=60):
    """Poll until the server answers, so a slow boot is not a flaky failure."""
    import urllib.error
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    return False


def _start_preview(lang, section):
    """Run the fake-data preview app; returns (process, url)."""
    port = _free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(REPO_ROOT / "scripts" / "admin_panel_layout_preview.py"),
            "--server.port",
            str(port),
            "--server.headless",
            "true",
            "--browser.gatherUsageStats",
            "false",
            "--",
            "--lang",
            lang,
            "--section",
            section,
        ],
        cwd=REPO_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    url = f"http://localhost:{port}"
    if not _wait_for_http(url):
        proc.terminate()
        raise RuntimeError(f"preview app did not come up on {url}")
    return proc, url


def _icon_font_css(path):
    """An `@font-face` carrying the woff2 at `path` inline, plus the class rule.

    Inline as a data URI rather than served: Streamlit's static route cannot be
    pointed at an arbitrary file, and a `file://` URL is blocked as mixed
    content from an http page.
    """
    import base64

    data = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    return (
        "@font-face {"
        f"font-family: '{ICON_FONT_FAMILY}'; font-style: normal; font-weight: 400;"
        f" font-display: block; src: url(data:font/woff2;base64,{data}) format('woff2');"
        "}" + ICON_FONT_CLASS_CSS
    )


def _shoot(page, url, width, height, label, lang, section, extra_css=None):
    page.set_viewport_size({"width": width, "height": height})
    page.goto(url, wait_until="networkidle")
    if extra_css:
        page.add_style_tag(content=extra_css)
    # Streamlit renders its script asynchronously after the socket connects;
    # waiting for a widget rather than a fixed sleep keeps this from being
    # flaky on a slow machine and fast on a quick one.
    try:
        page.wait_for_selector('[data-testid="stAppViewContainer"]', timeout=30_000)
    except Exception:
        pass
    page.wait_for_timeout(1_500)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{section}-{lang}-{width}x{height}-{label}.png"
    page.screenshot(path=str(path), full_page=True)
    print(f"  {path.relative_to(REPO_ROOT)}")
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--url",
        help="screenshot a panel already running here instead of the preview app",
    )
    ap.add_argument(
        "--lang",
        choices=LANGS,
        action="append",
        help="limit to one language (repeatable); default is both",
    )
    ap.add_argument(
        "--section",
        choices=SECTIONS,
        action="append",
        help="limit to one preview page (repeatable); default is both",
    )
    ap.add_argument(
        "--icon-font",
        metavar="WOFF2",
        help=(
            "a local Material Symbols Rounded .woff2 to embed, for a browser "
            "that cannot fetch it from Google (icons otherwise render as words)"
        ),
    )
    ap.add_argument(
        "--scheme",
        choices=["light", "dark"],
        default="light",
        help="colour scheme to emulate; the dark palette has its own bugs",
    )
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "playwright is not installed. It is a dev-only dependency, kept out "
            "of requirements.txt on purpose:\n"
            "    pip install playwright\n"
            "The browser is expected to be on the machine already; this script "
            "never downloads one.",
            file=sys.stderr,
        )
        return 2

    langs = args.lang or LANGS
    sections = args.section or SECTIONS
    extra_css = _icon_font_css(args.icon_font) if args.icon_font else None
    exe = PINNED_CHROMIUM if Path(PINNED_CHROMIUM).exists() else None
    if exe is None and not shutil.which("chromium"):
        print(
            f"no browser at {PINNED_CHROMIUM}; falling back to Playwright's own "
            "resolution, which may fail if none is installed.",
            file=sys.stderr,
        )

    written = []
    with sync_playwright() as p:
        launch_kwargs = {"executable_path": exe} if exe else {}
        browser = p.chromium.launch(**launch_kwargs)
        try:
            for lang in langs:
                for section in sections:
                    proc = None
                    try:
                        if args.url:
                            url = args.url
                        else:
                            proc, url = _start_preview(lang, section)

                        print(f"{lang} / {section} -> {url}")
                        # A fresh context per run: Streamlit caches the
                        # language in session state, and a reused context
                        # would quietly shoot the previous one.
                        context = browser.new_context(
                            locale="he-IL" if lang == "HE" else "en-US",
                            device_scale_factor=2,
                            color_scheme=args.scheme,
                        )
                        page = context.new_page()
                        for width, height, label in VIEWPORTS:
                            written.append(
                                _shoot(
                                    page,
                                    url,
                                    width,
                                    height,
                                    label,
                                    lang,
                                    section,
                                    extra_css,
                                )
                            )
                        context.close()
                    finally:
                        if proc:
                            proc.terminate()
                            proc.wait(timeout=15)
                    if args.url:
                        # A live panel is one page; shooting it twice would
                        # only produce identical files under two names.
                        break
        finally:
            browser.close()

    print(f"\n{len(written)} screenshots in {OUT_DIR.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
