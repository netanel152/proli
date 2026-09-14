"""Guards for the admin panel's Hebrew (RTL) rendering and its sidebar.

Every defect pinned here was found by measuring the real panel in a real
browser (`scripts/admin_panel_screenshots.py` over the layout preview), not by
reading the stylesheet — and several of them looked right in the source. The
tests are the cheap half of that loop: they cannot see a browser, but they can
stop a fix from being quietly reverted, and each one fails on the tree that
had the bug.

What is pinned, and what each one was:

* **the hamburger** opened a right-hand sidebar from the far left corner;
* **the sidebar's collapse button** sat on the screen edge instead of the
  content edge, the mirror image of where it belongs;
* **nav rows** were as wide as their text, so the selected pill had five
  different widths and the tap target was the words, not the row;
* **HTML tables** forced `text-align: left` on Hebrew, inside a box that was
  itself right-aligned;
* **the alert accent stripe** was `border-width: 0 0 0 4px` with no
  `border-style`, so it drew nothing at all — in either language;
* **`.chat-meta`** flipped twice (`direction: rtl` *and* `row-reverse`) and
  put the speaker icon back on the left;
* **the slider thumb** rendered ~400px outside its own track;
* **`STATUS_COLORS_DARK`** was never read by anything;
* **uppercase and letter-spacing** were applied to Hebrew, which has no case
  and is drawn to sit close.

`rtl.py` imports neither Streamlit nor anything under `app/`, so its half runs
with no `.env` and no database.
"""

from pathlib import Path

import pytest

from admin_panel.core.config import TRANS
from admin_panel.ui.components import (
    STATUS_COLORS,
    STATUS_COLORS_DARK,
    load_css,
    render_kanban_column,
)
from admin_panel.ui.responsive import TOUCH_TARGET_PX
from admin_panel.ui.rtl import rtl_css

REPO_ROOT = Path(__file__).resolve().parent.parent

HE = TRANS["HE"]
EN = TRANS["EN"]
LANGS = [HE, EN]


# --- Helpers ---------------------------------------------------------------


def _block_end(css, at):
    """Index just past the `}` closing the block whose header starts at `at`."""
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
    """The declarations of the first rule whose text starts with `selector`.

    Brace-matched rather than sliced to the next `}`, so a nested block (a
    media query, a `:has()` with braces in it) cannot truncate the body and
    turn a real assertion into a vacuous one.
    """
    assert selector in css, f"selector not found: {selector}"
    start = css.index(selector)
    end = _block_end(css, start)
    return css[css.index("{", start) + 1 : end - 1]


def _rendered_css(T, monkeypatch):
    """The `<style>` block `load_css` actually emits, for this language."""
    captured = []
    monkeypatch.setattr(
        "admin_panel.ui.components.st.markdown",
        lambda body, *a, **k: captured.append(body),
    )
    load_css("HE" if T["dir"] == "rtl" else "EN", T)
    styles = [c for c in captured if "<style>" in c]
    assert len(styles) == 1, f"expected one <style> block, got {len(styles)}"
    return styles[0]


def _declarations(body):
    """The `prop: value` pairs in a rule body, lower-cased, comments stripped."""
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


# --- rtl.py is a no-op in LTR, and says so ---------------------------------


def test_rtl_css_emits_no_declarations_in_ltr():
    """The module's central claim: in LTR there is nothing to correct.

    If this ever fails, a rule that is not actually direction-specific has
    been put in the wrong module — it belongs in `components.py`, where both
    languages get it.
    """
    css = rtl_css("ltr", "left")
    assert "{" not in css, f"rtl_css('ltr') emitted rules:\n{css}"
    assert "collapsedcontrol" not in css.lower()


def test_rtl_css_emits_rules_in_rtl():
    css = rtl_css("rtl", "right")
    assert css.count("{") >= 4, "expected every RTL correction"


# --- The sidebar hamburger and its collapse button -------------------------


def test_hamburger_moves_to_the_start_edge_in_hebrew(monkeypatch):
    """Streamlit pins it `left`, so Hebrew opened a right sidebar from the
    left corner — on top of the page title."""
    body = _rule_body(
        _rendered_css(HE, monkeypatch), '[data-testid="collapsedControl"]'
    )
    decls = _declarations(body)
    assert decls.get("left") == "auto !important"
    assert "right" in decls and decls["right"] != "auto !important"


def test_toolbar_swaps_sides_with_the_hamburger_in_hebrew(monkeypatch):
    """Both are pinned top-right by Streamlit. Moving only the hamburger put
    it exactly on top of the menu: on a 375px screen the toolbar ran
    x=275..371 and the hamburger x=334..368.
    """
    css = _rendered_css(HE, monkeypatch)
    burger = _declarations(_rule_body(css, '[data-testid="collapsedControl"]'))
    toolbar = _declarations(_rule_body(css, '[data-testid="stToolbar"]'))
    assert burger.get("left") == "auto !important"
    assert (
        toolbar.get("right") == "auto !important"
    ), "the hamburger cannot move to the right unless the toolbar leaves it"


def test_hamburger_is_left_alone_in_english(monkeypatch):
    css = _rendered_css(EN, monkeypatch)
    for selector in ('[data-testid="collapsedControl"]', '[data-testid="stToolbar"]'):
        assert selector not in css, (
            f"English needs no override for {selector}; Streamlit's own "
            "placement is already correct. An override that fires in both "
            "languages is a rule in the wrong module."
        )


def test_sidebar_collapse_button_moves_to_the_content_edge_in_hebrew(monkeypatch):
    """Its wrapper carries both `left` and `right`, pinning it to the
    sidebar's right edge: the content edge in LTR, the screen edge in RTL."""
    css = _rendered_css(HE, monkeypatch)
    body = _rule_body(css, 'div:has(> [data-testid="baseButton-header"])')
    decls = _declarations(body)
    assert decls.get("right") == "auto !important", (
        "without clearing `right`, the wrapper stays stretched between both "
        "edges and the button does not move"
    )
    assert "left" in decls and decls["left"] != "auto !important"


def test_sidebar_collapse_button_is_left_alone_in_english(monkeypatch):
    assert 'div:has(> [data-testid="baseButton-header"])' not in _rendered_css(
        EN, monkeypatch
    )


# --- Sidebar nav rows ------------------------------------------------------


@pytest.mark.parametrize("T", LANGS, ids=["rtl", "ltr"])
def test_sidebar_nav_rows_span_the_sidebar(T, monkeypatch):
    """A label shrinks to its text, so the selected pill ended where the words
    did — five different widths — and only the words were tappable."""
    css = _rendered_css(T, monkeypatch)
    body = _rule_body(css, 'section[data-testid="stSidebar"] .stRadio > div > label')
    decls = _declarations(body)
    assert decls.get("width") == "100%"
    assert decls.get("box-sizing") == "border-box", (
        "the row carries horizontal padding; without border-box a 100% width "
        "overflows the sidebar by exactly that padding"
    )
    assert decls.get("display") == "flex !important", (
        "the base `label` rule sets `display: block !important`; under block "
        "the radio marker wraps onto its own line above the text, which is "
        "how every desktop nav row came to be 61px tall instead of 39px"
    )
    assert decls.get("align-items") == "center"


@pytest.mark.parametrize("T", LANGS, ids=["rtl", "ltr"])
def test_nav_marker_does_not_absorb_the_new_free_space(T, monkeypatch):
    """A full-width flex row has space to distribute, and the radio marker is
    what stretches into an oval if nothing stops it."""
    css = _rendered_css(T, monkeypatch)
    body = _rule_body(
        css,
        'section[data-testid="stSidebar"] .stRadio > div > label > div:first-child',
    )
    assert _declarations(body).get("flex") == "0 0 auto"


# --- Text direction on our own markup --------------------------------------


def test_html_tables_follow_the_page_direction(monkeypatch):
    """Only the Glide grid is a deliberate LTR island. A markdown table has
    no column-misalignment problem and was merely left ragged."""
    for T in LANGS:
        css = _rendered_css(T, monkeypatch)
        for selector in ("thead tr th", "tbody tr td"):
            decls = _declarations(_rule_body(css, selector))
            assert decls.get("text-align") == f"{T['align']} !important", (
                f"{selector} should follow the page direction, "
                f"got {decls.get('text-align')!r} for dir={T['dir']}"
            )


def test_data_grid_stays_an_ltr_island(monkeypatch):
    """The exception that proves the rule above is still deliberate."""
    css = _rendered_css(HE, monkeypatch)
    decls = _declarations(_rule_body(css, 'div[data-testid="stDataFrame"] *'))
    assert decls.get("direction") == "ltr !important"
    assert decls.get("text-align") == "left !important"


@pytest.mark.parametrize("T", LANGS, ids=["rtl", "ltr"])
def test_chat_meta_does_not_flip_twice(T, monkeypatch):
    """`direction: rtl` already mirrors a flex row once. `row-reverse`
    mirrored it again and put the speaker icon back after the name."""
    decls = _declarations(_rule_body(_rendered_css(T, monkeypatch), ".chat-meta"))
    assert decls.get("flex-direction") == "row"


@pytest.mark.parametrize("T", LANGS, ids=["rtl", "ltr"])
def test_chat_bubbles_take_a_side_by_exactly_one_mechanism(T, monkeypatch):
    """`align-self` and an auto margin both claimed to place the bubble, and
    disagreed; the margin silently won. One mechanism, so the rendered side
    can be read off the rule."""
    css = _rendered_css(T, monkeypatch)
    for selector in (".user-msg", ".bot-msg"):
        decls = _declarations(_rule_body(css, selector))
        assert "align-self" not in decls, (
            f"{selector} sets align-self as well as an auto margin; the margin "
            "wins, so the align-self only misleads the next reader"
        )
        assert any(k.startswith("margin-") for k in decls)


# --- The alert accent ------------------------------------------------------


@pytest.mark.parametrize("T", LANGS, ids=["rtl", "ltr"])
def test_alert_rule_carries_no_dead_accent(T, monkeypatch):
    """`border-width` with no `border-style` computes to 0, so the stripe this
    rule claimed to draw was never visible in either language.

    It is not repaired, it is removed: Streamlit 1.31 distinguishes success
    from error only by a generated emotion class, so any stripe here would be
    one neutral colour on all four kinds — noise beside the tinted background
    that already carries the meaning. This guard is against the physical
    shorthand coming back, in any form.
    """
    decls = _declarations(_rule_body(_rendered_css(T, monkeypatch), ".stAlert"))
    assert "border-width" not in decls, (
        "a border-width with no border-style draws nothing; if an accent is "
        "wanted, it needs a style and a per-kind colour"
    )
    for prop in decls:
        assert not prop.startswith("border-left"), prop
        assert not prop.startswith("border-right"), prop


@pytest.mark.parametrize("T", LANGS, ids=["rtl", "ltr"])
def test_alert_styling_reaches_the_box_that_is_actually_visible(T, monkeypatch):
    """The outer `.stAlert` wrapper is transparent; the tinted box is the
    inner notification, so a radius set only on the wrapper rounds nothing."""
    css = _rendered_css(T, monkeypatch)
    head = css[css.index(".stAlert,") : css.index("{", css.index(".stAlert,"))]
    assert "stNotification" in head, head


# --- Dark mode -------------------------------------------------------------


def test_dark_status_palette_is_actually_emitted(monkeypatch):
    """`STATUS_COLORS_DARK` was declared in PRO-46 and read by nothing, so
    every pill and Kanban header stayed a pale pastel on a #0F172A page."""
    css = _rendered_css(HE, monkeypatch)
    start = css.index("@media (prefers-color-scheme: dark)", css.index("status-pill-"))
    dark_block = css[start : _block_end(css, start)]
    for status, colors in STATUS_COLORS_DARK.items():
        assert f".status-pill-{status}" in dark_block, status
        assert f".kanban-header--{status}" in dark_block, status
        assert colors["bg"].lower() in dark_block.lower(), status


def test_kanban_header_colours_are_a_class_not_an_inline_style():
    """An inline `style` attribute outranks every media query, so the dark
    palette could not have reached the header while the colours lived there."""
    html_out = render_kanban_column("booked", [], HE)
    assert "kanban-header--booked" in html_out
    assert (
        "background-color:" not in html_out
    ), "an inline background on the header re-breaks dark mode"


def test_unknown_status_still_gets_a_palette():
    """`colors` falls back to the `new` entry; the class has to fall back the
    same way or an unknown status renders with no rule at all."""
    html_out = render_kanban_column("not_a_real_status", [], HE)
    assert "kanban-header--new" in html_out


@pytest.mark.parametrize("status", sorted(STATUS_COLORS))
def test_every_light_status_has_a_dark_counterpart(status):
    assert (
        status in STATUS_COLORS_DARK
    ), f"'{status}' renders a pale palette on the dark theme with no override"


# --- Hebrew typography -----------------------------------------------------


def test_hebrew_drops_uppercase_and_tracking(monkeypatch):
    """Hebrew has no letter case, so `uppercase` is a no-op — but the tracking
    that goes with the small-caps look is not, and it pulls letterforms drawn
    to sit close into something that reads as spaced-out."""
    css = _rendered_css(HE, monkeypatch)
    for selector in (
        'label[data-testid="stWidgetLabel"] {',
        ".metric-tile-label",
        "thead tr th",
        ".kanban-header {",
    ):
        decls = _declarations(_rule_body(css, selector))
        assert decls.get("text-transform") == "none", selector
        assert decls.get("letter-spacing") == "normal", selector


def test_english_keeps_its_label_treatment(monkeypatch):
    """The Hebrew fix must not flatten the English design."""
    css = _rendered_css(EN, monkeypatch)
    for selector in (
        'label[data-testid="stWidgetLabel"] {',
        ".metric-tile-label",
        "thead tr th",
        ".kanban-header {",
    ):
        decls = _declarations(_rule_body(css, selector))
        assert decls.get("text-transform") == "uppercase", selector
        assert decls.get("letter-spacing", "").endswith("em"), selector


# --- Keyboard focus --------------------------------------------------------


@pytest.mark.parametrize("T", LANGS, ids=["rtl", "ltr"])
def test_interactive_chrome_has_a_visible_focus_ring(T, monkeypatch):
    """The sheet styled `:hover` on everything and `:focus` on nothing, so a
    keyboard user got the browser's own 1px ring in its own colour — and the
    sidebar nav rows, being labels, got nothing at all."""
    css = _rendered_css(T, monkeypatch)
    assert ":focus-visible" in css
    body = _rule_body(css, ".stButton button:focus-visible")
    decls = _declarations(body)
    assert "outline" in decls and "none" not in decls["outline"]
    # The rule has to reach the controls that are not `.stButton`.
    head = css[
        css.index(".stButton button:focus-visible") : css.index(
            "{", css.index(".stButton button:focus-visible")
        )
    ]
    for testid in ("stFormSubmitButton", "stSidebar"):
        assert testid in head, f"{testid} is not covered by the focus rule"


# --- Touch targets the first pass missed -----------------------------------


@pytest.mark.parametrize("T", LANGS, ids=["rtl", "ltr"])
def test_download_and_submit_buttons_reach_the_touch_target(T, monkeypatch):
    """Both measured 38px on a phone: the download button because the base
    sheet gives it a smaller padding of its own, the submit button because
    Streamlit files it under its own test id rather than `.stButton`."""
    css = _rendered_css(T, monkeypatch)
    body = _rule_body(
        css,
        '.stDownloadButton button,\n            [data-testid="stFormSubmitButton"] button',
    )
    assert _declarations(body).get("min-height") == f"{TOUCH_TARGET_PX}px"


@pytest.mark.parametrize("T", LANGS, ids=["rtl", "ltr"])
def test_mobile_drawer_leaves_the_page_visible(T, monkeypatch):
    """Streamlit's drawer is 336px of a 375px screen — a 39px sliver of page,
    too little to read as "the panel is behind this" or to tap to dismiss."""
    css = _rendered_css(T, monkeypatch)
    body = _rule_body(css, 'section[data-testid="stSidebar"][aria-expanded="true"]')
    decls = _declarations(body)
    assert "min(" in decls.get("width", ""), decls
    assert "vw" in decls["width"], "a fixed px cap does not scale down a phone"


# --- The label rule is scoped, so control rows keep their flex ------------


def _rules(css):
    """``[(selector, body), ...]`` for every rule in the sheet, media blocks
    descended into, so a rule can be judged by its own selector text."""
    out = []
    i = 0
    while True:
        j = css.find("{", i)
        if j < 0:
            return out
        head = css[max(css.rfind("}", 0, j), css.rfind("{", 0, j)) + 1 : j]
        while "/*" in head:  # the comment above a rule is not its selector
            before, _, rest = head.partition("/*")
            head = before + rest.partition("*/")[2]
        head = head.strip()
        if head.startswith("@"):
            i = j + 1  # a media query: keep walking into its rules
            continue
        end = _block_end(css, j)
        out.append((head, css[j + 1 : end - 1]))
        i = end


@pytest.mark.parametrize("T", LANGS, ids=["rtl", "ltr"])
def test_label_typography_is_scoped_to_widget_captions(T, monkeypatch):
    """`label {{ display: block !important }}` used to apply to every label —
    including the ones BaseWeb lays out as a *row*: the sidebar radio (which S6
    had to override with a second `!important`), and every checkbox and toggle,
    whose box and text then stacked one above the other (46px tall, measured,
    against 24px for the flex row). A caption rule belongs on captions only."""
    css = _rendered_css(T, monkeypatch)
    forcing = [
        sel
        for sel, body in _rules(css)
        if "label" in sel
        and _declarations(body).get("display", "").startswith("block")
        and "stWidgetLabel" not in sel
    ]
    assert not forcing, f"labels forced to block outside the caption rule: {forcing}"
    caption = _declarations(_rule_body(css, 'label[data-testid="stWidgetLabel"] {'))
    assert caption.get("display") == "block !important", caption


@pytest.mark.parametrize("T", LANGS, ids=["rtl", "ltr"])
def test_form_submit_buttons_wear_the_panels_own_primary(T, monkeypatch):
    """`st.form_submit_button` renders under `stFormSubmitButton` with
    `kind="primaryFormSubmit"`, so the login button — the first thing the
    operator sees — was the one primary in the panel in Streamlit's default
    red (`rgb(255, 75, 75)`, measured) while every other one was blue."""
    css = _rendered_css(T, monkeypatch)
    primary = [
        (sel, body)
        for sel, body in _rules(css)
        if 'button[kind="primary"]' in sel and ":hover" not in sel
    ]
    assert len(primary) == 1, [s for s, _ in primary]
    sel, body = primary[0]
    assert '[data-testid="stFormSubmitButton"] button[kind="primaryFormSubmit"]' in sel
    assert (
        _declarations(body)
        .get("background", "")
        .startswith("linear-gradient(135deg, var(--primary)")
    )
    base = [sel for sel, _ in _rules(css) if sel.startswith(".stButton button,")]
    assert base and '[data-testid="stFormSubmitButton"] button' in base[0], base
