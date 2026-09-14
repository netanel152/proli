"""Guards for the admin panel's responsive layout (S1 + S2 of the mobile pass).

The panel renders through Streamlit, so nothing here can assert on a real
browser at a real width. What it *can* pin is everything that made the panel
desktop-only in the first place, and every one of these assertions fails on the
pre-change tree:

* the three breakpoints exist, and the responsive block is appended **last**
  inside `load_css`'s single `<style>` — appended-last is what lets it win the
  cascade against the desktop rules without a second round of `!important`;
* no fixed pixel width survives outside a media query (the sidebar's
  `width: 280px !important` at every width is the bug that put the sidebar over
  the whole phone screen on load);
* the metric grid and the Kanban board render the classes the breakpoints
  target, in the DOM order PRO-46 requires.

`responsive.py` imports neither Streamlit nor anything under `app/`, so its half
of this runs with no `.env` and no database.
"""

import re
from pathlib import Path

import pytest

from admin_panel.core.config import TRANS
from admin_panel.ui.components import (
    load_css,
    render_kanban_column,
    render_metric_grid,
)
from admin_panel.ui.responsive import (
    MOBILE_MAX,
    SIDEBAR_OVERLAY_MAX,
    TABLET_MAX,
    responsive_css,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# The real language dicts, both of them: every rule interpolates
# direction/align, RTL is the default rather than the variant, and a stub dict
# would not catch a renderer reaching for a key only Hebrew defines.
LANGS = [TRANS["HE"], TRANS["EN"]]

PHONE = f"@media (max-width: {MOBILE_MAX}px)"
TABLET = f"@media (max-width: {TABLET_MAX}px)"
DESKTOP = f"@media (min-width: {SIDEBAR_OVERLAY_MAX + 1}px)"


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


def _media_body(css, query):
    """Return the body of one `@media` block, matched by braces.

    Slicing "from this query to the next one" quietly swallows a neighbouring
    block the day the order changes, and the test then passes for the wrong
    reason. Brace matching cannot.
    """
    assert query in css, f"media query not found: {query}"
    start = css.index(query)
    end = _block_end(css, start)
    return css[css.index("{", start) + 1 : end - 1]


def _outside_media(css):
    """Everything in the sheet that is *not* inside an `@media` block."""
    out = []
    i = 0
    while i < len(css):
        if css.startswith("@media", i):
            i = _block_end(css, i)
            continue
        out.append(css[i])
        i += 1
    return "".join(out)


def _rendered_css(T, monkeypatch):
    """Return the `<style>` block `load_css` actually emits.

    `load_css` writes through `st.markdown`; capturing it is the only way to
    assert on the composed sheet rather than on its pieces.
    """
    captured = []

    monkeypatch.setattr(
        "admin_panel.ui.components.st.markdown",
        lambda body, *a, **k: captured.append(body),
    )
    load_css("HE" if T["dir"] == "rtl" else "EN", T)

    styles = [c for c in captured if "<style>" in c]
    assert len(styles) == 1, f"expected one <style> block, got {len(styles)}"
    return styles[0]


def _source(*parts):
    return REPO_ROOT.joinpath(*parts).read_text(encoding="utf-8")


# --- The breakpoints themselves --------------------------------------------


def test_breakpoints_are_ordered_and_distinct():
    """The constants must describe three widening bands, not overlap."""
    assert MOBILE_MAX < SIDEBAR_OVERLAY_MAX < TABLET_MAX


@pytest.mark.parametrize("T", LANGS, ids=["rtl", "ltr"])
def test_responsive_css_declares_all_three_breakpoints(T):
    css = responsive_css(T["dir"], T["align"])

    assert PHONE in css
    assert TABLET in css
    # The sidebar width is a *min*-width rule: it applies above Streamlit's own
    # overlay breakpoint and nowhere else.
    assert DESKTOP in css


@pytest.mark.parametrize("T", LANGS, ids=["rtl", "ltr"])
def test_responsive_block_is_appended_last(T, monkeypatch):
    """It has to be the final thing in the sheet, or it loses the cascade."""
    css = _rendered_css(T, monkeypatch)
    marker = "RESPONSIVE — admin_panel/ui/responsive.py"

    assert marker in css, "responsive block missing from load_css output"

    after = css[css.index(marker) :]
    assert (
        "@keyframes pulse" not in after
    ), "a desktop rule follows the responsive block"
    assert after.rstrip().endswith("</style>")


# --- The bug that started this ---------------------------------------------


@pytest.mark.parametrize("T", LANGS, ids=["rtl", "ltr"])
def test_no_fixed_pixel_width_outside_a_media_query(T, monkeypatch):
    """A hard `width: NNNpx !important` at every width is the sidebar bug.

    Pinned generally rather than for the sidebar alone: the same shape from any
    future rule breaks the same way, and it is invisible on a desktop screen.
    """
    css = _rendered_css(T, monkeypatch)

    offenders = re.findall(r"width:\s*\d+px\s*!important", _outside_media(css))
    assert not offenders, (
        "fixed pixel width(s) outside a media query — these apply on phones too "
        f"and fight Streamlit's own sizing: {offenders}"
    )


@pytest.mark.parametrize("T", LANGS, ids=["rtl", "ltr"])
def test_sidebar_width_applies_only_above_the_overlay_breakpoint(T):
    css = responsive_css(T["dir"], T["align"])

    assert "width: 280px !important" in css, "the desktop sidebar width is gone"
    assert "width: 280px !important" in _media_body(css, DESKTOP)


def test_page_config_lets_streamlit_choose_the_sidebar_state():
    """`expanded` opened the sidebar over the whole phone screen on load."""
    source = _source("admin_panel", "main.py")

    assert 'initial_sidebar_state="auto"' in source
    assert 'initial_sidebar_state="expanded"' not in source


# --- Metric grid -----------------------------------------------------------


@pytest.mark.parametrize("T", LANGS, ids=["rtl", "ltr"])
def test_metric_grid_renders_one_tile_per_item_in_order(T):
    items = [("Total", 12), ("Needs Review", 3), ("New", 7)]
    html_out = render_metric_grid(items, T)

    assert html_out.count('class="metric-tile"') == len(items)
    assert f'dir="{T["dir"]}"' in html_out

    # Order is the contract: the tiles must read in the order given.
    positions = [html_out.index(str(label)) for label, _ in items]
    assert positions == sorted(positions)

    for label, value in items:
        assert f">{label}<" in html_out
        assert f">{value}<" in html_out


@pytest.mark.parametrize("T", LANGS, ids=["rtl", "ltr"])
def test_metric_grid_escapes_labels_and_values(T):
    html_out = render_metric_grid([("<script>x</script>", "<b>1</b>")], T)

    assert "<script>" not in html_out
    assert "&lt;script&gt;" in html_out
    assert "&lt;b&gt;1&lt;/b&gt;" in html_out


@pytest.mark.parametrize("T", LANGS, ids=["rtl", "ltr"])
def test_metric_grid_is_empty_but_valid_with_no_items(T):
    html_out = render_metric_grid([], T)

    assert 'class="metric-grid"' in html_out
    assert "metric-tile" not in html_out


@pytest.mark.parametrize("T", LANGS, ids=["rtl", "ltr"])
def test_metric_grid_reflows_at_both_breakpoints(T):
    """Five up on desktop, three on tablet, two on a phone."""
    css = responsive_css(T["dir"], T["align"])

    assert "repeat(3, 1fr)" in _media_body(css, TABLET)
    assert "repeat(2, 1fr)" in _media_body(css, PHONE)


def test_views_render_the_grid_rather_than_five_stacked_columns():
    """The 5-wide `st.columns` metric rows are what stacked on a phone."""
    home = _source("admin_panel", "views", "home.py")
    analytics = _source("admin_panel", "views", "analytics.py")

    assert home.count("render_metric_grid(") == 2, "both home.py rows must convert"
    assert "render_metric_grid(" in analytics

    assert "c1, c2, c3, c4, c5 = st.columns" not in home
    assert "c1, c2, c3, c4, c5 = st.columns" not in analytics


# --- Kanban board ----------------------------------------------------------


@pytest.mark.parametrize("T", LANGS, ids=["rtl", "ltr"])
def test_empty_kanban_column_is_marked(T):
    empty = render_kanban_column("new", [], T)
    assert "kanban-column--empty" in empty

    filled = render_kanban_column("new", [{"id": "1", "client": "A"}], T)
    assert "kanban-column--empty" not in filled
    assert 'class="kanban-column"' in filled


@pytest.mark.parametrize("T", LANGS, ids=["rtl", "ltr"])
def test_empty_column_is_hidden_only_on_a_phone(T):
    """It still renders on desktop — an empty status is information there."""
    css = responsive_css(T["dir"], T["align"])

    assert ".kanban-column--empty" in _media_body(css, PHONE)
    assert ".kanban-column--empty" not in _media_body(css, TABLET)
    assert ".kanban-column--empty" not in _outside_media(css)


def test_board_wrapper_is_a_class_with_an_explicit_dir():
    """PRO-46: reading order must not depend on an ancestor's direction."""
    home = _source("admin_panel", "views", "home.py")

    assert 'class="kanban-board"' in home
    assert "dir=\"{T['dir']}\"" in home
    # The old inline flex wrapper must be gone, or nothing can re-flow it.
    assert "display: flex; gap: 12px; overflow-x: auto" not in home


def test_pending_review_stays_the_leading_column():
    """The breakpoints re-flow the board; they must not reorder it."""
    import admin_panel.views.home as home

    assert home.KANBAN_STATUSES[0] == "pending_admin_review"


@pytest.mark.parametrize("T", LANGS, ids=["rtl", "ltr"])
def test_board_stacks_on_phone_and_wraps_on_tablet(T):
    css = responsive_css(T["dir"], T["align"])

    assert "flex-wrap: wrap" in _media_body(css, TABLET)
    assert "flex-direction: column" in _media_body(css, PHONE)


# --- Touch targets and the iOS zoom ----------------------------------------


@pytest.mark.parametrize("T", LANGS, ids=["rtl", "ltr"])
def test_phone_block_sets_touch_targets_and_16px_inputs(T):
    phone = _media_body(responsive_css(T["dir"], T["align"]), PHONE)

    assert "min-height: 44px" in phone
    # Exactly 16px: iOS Safari zooms the page on focus below this.
    assert "font-size: 16px !important" in phone
    assert ".stTextInput input" in phone
    assert ".stButton button" in phone


@pytest.mark.parametrize("T", LANGS, ids=["rtl", "ltr"])
def test_direction_is_threaded_through_rather_than_hardcoded(T):
    """A rule that hardcodes one side breaks the other language silently."""
    css = responsive_css(T["dir"], T["align"])

    assert f"text-align: {T['align']}" in css
    opposite = "left" if T["align"] == "right" else "right"
    assert f"text-align: {opposite}" not in css
