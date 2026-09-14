"""Guards for S3/S4: action rows, the login screen, and the tab strip.

The defect S3 exists to fix is narrow and easy to state. Below 640px Streamlit
puts ``min-width: calc(100% - 22.5px)`` on every ``[data-testid="column"]``,
so **every** row wraps — and nothing in the DOM distinguishes a row of text
inputs, which should wrap, from a Yes/No pair, which must not. A confirm
therefore rendered as two stacked full-width buttons with the destructive one
on top, which is the single most dangerous shape a phone layout can take.

`mark_row_inline()` is the handle that was missing. What this file pins:

* the marker reaches the row it marks (a ``+`` sibling rule, so the marker has
  to be *immediately* before the row — the one thing a stylesheet cannot check
  for itself, and the reason for the source-level test below);
* the marker costs no space at **any** width, not only on a phone — a
  zero-height flex item still earns the vertical block's ``gap``, and desktop
  being unchanged is this work's own acceptance criterion;
* the rule clears ``min-width`` and leaves ``flex-basis`` alone, so a row keeps
  the weights it was declared with (``[2, 2, 1]`` stays 2:2:1);
* every action row in the panel is marked, and no row of *inputs* is;
* the login page is capped by a marker plus one container rule, because the
  obvious alternative — opening a ``<div>`` in one ``st.markdown`` and closing
  it in another — wraps nothing at all.
"""

import ast
from pathlib import Path

import pytest

from admin_panel.core.config import TRANS
from admin_panel.ui.components import (
    LOGIN_PAGE_MARKER,
    ROW_INLINE_MARKER,
    load_css,
)
from admin_panel.ui.responsive import MOBILE_MAX

REPO_ROOT = Path(__file__).resolve().parent.parent
PANEL = REPO_ROOT / "admin_panel"

HE = TRANS["HE"]
EN = TRANS["EN"]
LANGS = [HE, EN]

PHONE = f"@media (max-width: {MOBILE_MAX}px)"

# Every row `mark_row_inline()` is applied to, as (file, the names it binds).
# Listing them by name rather than counting them means a row that loses its
# marker fails here with the row's own name, not as an arithmetic mismatch.
MARKED_ROWS = {
    "views/professionals.py": 3,  # delete confirm, reject confirm, geo save row
    "views/schedule.py": 2,  # generate/clear, clear-day confirm
    "views/home.py": 1,  # delete-lead confirm
    "views/settings.py": 1,  # audit pagination
}


# --- Helpers ---------------------------------------------------------------


def _block_end(css, at):
    i = css.index("{", at) + 1
    depth = 1
    while i < len(css) and depth:
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
        i += 1
    return i


def _rule_body(css, selector):
    assert selector in css, f"selector not found: {selector}"
    start = css.index(selector)
    return css[css.index("{", start) + 1 : _block_end(css, start) - 1]


def _media_body(css, query):
    assert query in css, f"media query not found: {query}"
    start = css.index(query)
    return css[css.index("{", start) + 1 : _block_end(css, start) - 1]


def _outside_media(css):
    out = []
    i = 0
    while i < len(css):
        if css.startswith("@media", i):
            i = _block_end(css, i)
            continue
        out.append(css[i])
        i += 1
    return "".join(out)


def _declarations(body):
    out = {}
    while "/*" in body:
        head, _, rest = body.partition("/*")
        _, _, tail = rest.partition("*/")
        body = head + tail
    for decl in body.split(";"):
        if ":" not in decl:
            continue
        prop, _, value = decl.partition(":")
        out[prop.strip().lower()] = value.strip().lower()
    return out


def _rendered_css(T, monkeypatch):
    captured = []
    monkeypatch.setattr(
        "admin_panel.ui.components.st.markdown",
        lambda body, *a, **k: captured.append(body),
    )
    load_css("HE" if T["dir"] == "rtl" else "EN", T)
    styles = [c for c in captured if "<style>" in c]
    assert len(styles) == 1
    return styles[0]


def _source(relpath):
    return (PANEL / relpath).read_text(encoding="utf-8")


# --- The marker reaches the row, and only because it is adjacent -----------


@pytest.mark.parametrize("relpath", sorted(MARKED_ROWS))
def test_every_marker_is_immediately_followed_by_its_row(relpath):
    """The CSS uses `+`, so an element between the marker and the row breaks
    it silently — the row simply stacks again and nothing reports it.

    This is the half of the contract a stylesheet cannot check, so it is
    checked in the source: a `mark_row_inline()` call must be followed by an
    `st.columns(...)` assignment as the very next statement.
    """
    tree = ast.parse(_source(relpath))
    found = 0
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        for i, stmt in enumerate(body):
            if not (
                isinstance(stmt, ast.Expr)
                and isinstance(stmt.value, ast.Call)
                and isinstance(stmt.value.func, ast.Name)
                and stmt.value.func.id == "mark_row_inline"
            ):
                continue
            found += 1
            assert i + 1 < len(body), (
                f"{relpath}:{stmt.lineno} mark_row_inline() is the last "
                "statement in its block; it marks nothing"
            )
            nxt = body[i + 1]
            call = getattr(nxt, "value", None)
            is_columns = (
                isinstance(nxt, ast.Assign)
                and isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "columns"
            )
            assert is_columns, (
                f"{relpath}:{stmt.lineno} mark_row_inline() must be "
                f"immediately followed by an st.columns(...) assignment, "
                f"got {ast.dump(nxt)[:80]}"
            )
    assert (
        found == MARKED_ROWS[relpath]
    ), f"{relpath}: expected {MARKED_ROWS[relpath]} marked rows, found {found}"


def test_no_confirm_row_still_carries_a_spacer_column():
    """The confirm rows were `[1, 1, 4]` — two buttons and a wide spacer that
    only existed to stop them stretching on desktop. The marker does that job
    at every width, and on a phone the spacer became a third empty row.
    """
    for relpath in sorted(MARKED_ROWS):
        source = _source(relpath)
        assert (
            "st.columns([1, 1, 4])" not in source
        ), f"{relpath} still builds a confirm row with a spacer column"


def test_marker_constants_are_hidden_spans():
    """Hidden, so neither marker can ever render as a stray character if the
    CSS that hides its container is lost."""
    for marker in (ROW_INLINE_MARKER, LOGIN_PAGE_MARKER):
        assert marker.startswith("<span")
        assert " hidden" in marker
        assert marker.endswith("</span>")


# --- What the rules actually say -------------------------------------------


@pytest.mark.parametrize("T", LANGS, ids=["rtl", "ltr"])
def test_marked_row_clears_min_width_on_a_phone(T, monkeypatch):
    """`min-width` is the whole stacking mechanism: Streamlit sets
    `calc(100% - 22.5px)` on every column below 640px."""
    phone = _media_body(_rendered_css(T, monkeypatch), PHONE)
    selector = '[data-testid="element-container"]:has(.row-inline)'
    assert selector in phone
    body = _rule_body(phone, selector)
    assert _declarations(body).get("min-width") == "0 !important"


@pytest.mark.parametrize("T", LANGS, ids=["rtl", "ltr"])
def test_marked_row_keeps_its_declared_weights(T, monkeypatch):
    """`flex: 1 1 0` would make every column equal and quietly flatten
    `[2, 2, 1]` into thirds — Cancel would stop being the narrow one."""
    phone = _media_body(_rendered_css(T, monkeypatch), PHONE)
    body = _rule_body(phone, '[data-testid="element-container"]:has(.row-inline)')
    decls = _declarations(body)
    assert "flex" not in decls, (
        "setting flex here overrides Streamlit's flex-basis, which is what "
        "carries the column weights"
    )
    assert "flex-basis" not in decls


@pytest.mark.parametrize("T", LANGS, ids=["rtl", "ltr"])
def test_markers_are_invisible_at_every_width(T, monkeypatch):
    """A zero-height flex item still earns the vertical block's `gap`.

    Measured: with this rule scoped to the phone breakpoint, the marked rows
    on a 1440px screen sat 15px, 30px and 45px lower than without it — a
    desktop change, which this work is not allowed to make.
    """
    css = _rendered_css(T, monkeypatch)
    outside = _outside_media(css)
    for cls in (".row-inline", ".login-page"):
        selector = f'[data-testid="element-container"]:has({cls})'
        assert selector in outside, (
            f"{cls}'s container is hidden only inside a media query; it will "
            "contribute a gap at every other width"
        )
    body = _rule_body(outside, '[data-testid="element-container"]:has(.row-inline)')
    assert _declarations(body).get("display") == "none !important"


# --- The login page --------------------------------------------------------


@pytest.mark.parametrize("T", LANGS, ids=["rtl", "ltr"])
def test_login_page_is_capped_on_the_container(T, monkeypatch):
    """Not inside a media query: 400px is wider than a phone, so the cap is
    inert there and the ordinary container padding gives a full-width form."""
    outside = _outside_media(_rendered_css(T, monkeypatch))
    decls = _declarations(_rule_body(outside, ".block-container:has(.login-page)"))
    assert decls.get("max-width") == "400px !important"
    assert decls.get("margin-inline") == "auto !important"


def test_login_no_longer_centres_with_a_column_row():
    """`[1, 2, 1]` stacks below 640px like every other row, so the two empty
    side columns became empty rows and the form was left full width."""
    source = (PANEL / "core" / "auth.py").read_text(encoding="utf-8")
    assert "st.columns([1, 2, 1])" not in source
    assert "mark_login_page()" in source


def test_login_does_not_try_to_wrap_widgets_in_a_markdown_div():
    """Measured, because it is the obvious thing to reach for and it does not
    work: an unclosed `<div>` in one `st.markdown` is closed by the browser
    immediately, so the widgets that follow are siblings. The container
    rendered 400px wide, empty (`children.length == 0`), with the form
    full-width beside it.
    """
    source = (PANEL / "core" / "auth.py").read_text(encoding="utf-8")
    # The class name appears in the comment that explains why it is not used,
    # so look for the call rather than the word.
    assert 'st.markdown(\'<div class="login-container">' not in source, (
        "a markdown-opened div wraps nothing; the page cap is a marker plus "
        "`.block-container:has(.login-page)`"
    )


# --- S4: the tab strip -----------------------------------------------------


@pytest.mark.parametrize("T", LANGS, ids=["rtl", "ltr"])
def test_tab_strip_scrolls_rather_than_wrapping_on_a_phone(T, monkeypatch):
    """Analytics has six tabs. Wrapped, the selected one can land on a second
    row with nothing to say there is one; the strip is 613px of content in a
    343px box."""
    phone = _media_body(_rendered_css(T, monkeypatch), PHONE)
    strip = _declarations(_rule_body(phone, '.stTabs [data-baseweb="tab-list"]'))
    assert strip.get("overflow-x") == "auto"
    assert strip.get("flex-wrap") == "nowrap"

    tab = _declarations(_rule_body(phone, '.stTabs [data-baseweb="tab"]'))
    assert (
        tab.get("white-space") == "nowrap"
    ), "a wrapping label re-introduces the tall strip that nowrap avoids"
    assert tab.get("flex") == "0 0 auto", (
        "without this the tabs shrink to fit instead of overflowing, and the "
        "strip never scrolls"
    )


def test_analytics_still_has_the_six_tabs_the_rule_is_for():
    """If the strip ever drops to two tabs the scroll rule is harmless, but
    this is the count that made it necessary — worth failing loudly if the
    view is restructured so the reason can be re-checked."""
    source = _source("views/analytics.py")
    tree = ast.parse(source)
    counts = [
        len(node.args[0].elts)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "tabs"
        and node.args
        and isinstance(node.args[0], ast.List)
    ]
    # Counting `T.get(` misses `T["tab_finops"]`, which is the sixth: the list
    # mixes both lookup forms, so it has to be parsed rather than scanned.
    assert counts == [6], f"expected one six-tab strip, found {counts}"


# --- S4: the data grid stays a grid ----------------------------------------


def test_leads_editor_takes_no_explicit_height():
    """The plan called for capping it at 60vh. Measured, that rule would never
    fire: `st.data_editor` self-caps at 402px whether it is given 30 rows or
    200, which is 49vh on a 375x812 phone. A cap that can never apply is the
    dead-rule shape this project has been bitten by before, so there is none —
    and this test records why, so the idea is not reintroduced blind.
    """
    source = _source("views/home.py")
    editor = source[source.index("edited_df = st.data_editor(") :]
    editor = editor[: editor.index("\n        )")]
    assert "height=" not in editor, (
        "if a height is added here, re-measure first: the default is already "
        "under 60vh on a phone"
    )
