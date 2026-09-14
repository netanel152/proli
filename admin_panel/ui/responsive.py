"""Responsive layout rules for the Streamlit admin panel.

Every rule the panel has for narrow viewports lives here, in one function, and
is appended **last** inside `load_css`'s single `<style>` block. Last is the
whole point: the desktop rules above it already carry `!important` in places,
and appending means these win on specificity ties without a second arms race
of `!important`.

Nothing in this module imports Streamlit or anything from `app/`. It is pure
string construction, so `tests/test_admin_responsive.py` can assert on the real
CSS without a running panel, a database or a `.env` — the same reason
`render_kanban_column` is a pure function.

**Selectors this depends on are Streamlit internals, pinned to 1.31.1**
(`requirements.txt`). A Streamlit upgrade reviews this list first:

* ``section[data-testid="stSidebar"]`` — the sidebar container
* ``.block-container`` — the main content column (and the sidebar's own)
* ``.stButton button`` — every button
* ``.stTextInput input`` / ``.stNumberInput input`` / ``.stTextArea textarea``
  / ``.stDateInput input`` / ``.stTimeInput input`` — text-entry controls
* ``.stSelectbox div[data-baseweb="select"]`` — the select control
* ``section[data-testid="stSidebar"] .stRadio > div > label`` — nav items

Breakpoints are named constants rather than repeated literals so the CSS and
the tests cannot drift apart.
"""

# Phones in portrait (375 / 390 / 430 CSS px). 640 is deliberate: it is also
# where Streamlit itself stops laying `st.columns` out side by side and stacks
# them, so our rules and its rules flip at the same width instead of fighting
# across a 40px band.
MOBILE_MAX = 640

# Tablets, phones in landscape, and a narrow desktop window.
TABLET_MAX = 1024

# Streamlit's own sidebar breakpoint: at or below this it renders the sidebar
# as an overlay with its own width, so our fixed width must not apply here.
SIDEBAR_OVERLAY_MAX = 768

# Apple's Human Interface Guidelines minimum, and the reason inputs are 16px
# below: iOS Safari zooms the whole page when focusing an input under 16px.
TOUCH_TARGET_PX = 44
NO_ZOOM_FONT_PX = 16


def responsive_css(direction, align):
    """Return the responsive block for `load_css`, already interpolated.

    `direction` / `align` come from the language dict exactly as they do in
    `load_css`, so RTL stays the default rather than an afterthought.
    """
    return f"""
        /* =========================================================
           RESPONSIVE — admin_panel/ui/responsive.py
           Appended last on purpose; see that module's docstring.
           ========================================================= */

        /* ---------- Desktop only ---------- */
        /* The fixed sidebar width lives here rather than on the base rule:
           below this width Streamlit renders the sidebar as an overlay and
           sizes it itself, and a 280px !important fought that sizing — the
           sidebar covered the whole phone screen on load. */
        @media (min-width: {SIDEBAR_OVERLAY_MAX + 1}px) {{
            section[data-testid="stSidebar"] {{
                width: 280px !important;
            }}
        }}

        /* ---------- Tablet and below ---------- */
        @media (max-width: {TABLET_MAX}px) {{
            .block-container {{
                padding-left: 1.25rem !important;
                padding-right: 1.25rem !important;
            }}

            /* Three metric tiles per row: five would each be under 150px and
               the Hebrew labels wrap to three lines.
               `!important` because `render_metric_grid` may put an explicit
               desktop track list in a style attribute (to preserve a row's
               original column weighting), and an inline declaration outranks
               any selector without it. */
            .metric-grid {{
                grid-template-columns: repeat(3, 1fr) !important;
            }}

            /* The board wraps to two columns instead of scrolling sideways.
               DOM order is untouched, so pending_admin_review still leads
               (PRO-46). */
            .kanban-board {{
                flex-wrap: wrap;
                overflow-x: visible;
            }}

            .kanban-column {{
                flex: 1 1 calc(50% - 12px);
                min-width: 0;
            }}

            /* An empty column packs four-up instead of taking half a row.
               Two-up would turn a board with two active statuses into four
               rows, six of them a dash — worse than the desktop scroller it
               replaces, and this band is also where a half-tiled desktop
               browser lands. The phone hides them; here they still carry the
               "this queue is clear" signal cheaply. */
            .kanban-column--empty {{
                flex: 0 1 calc(25% - 12px);
                min-height: 0;
            }}
        }}

        /* ---------- Phone ---------- */
        @media (max-width: {MOBILE_MAX}px) {{
            .block-container {{
                padding-top: 1rem !important;
                padding-bottom: 2rem !important;
                padding-left: 1rem !important;
                padding-right: 1rem !important;
            }}

            h1 {{
                font-size: 1.5rem !important;
            }}

            h2 {{
                font-size: 1.2rem !important;
            }}

            h3 {{
                font-size: 1.05rem !important;
            }}

            /* 16px exactly. Anything smaller and iOS Safari zooms the page on
               focus, which leaves the operator scrolled sideways on a form
               they were only trying to type into. */
            /* The select needs all three: the base sheet sets a size on
               the inner `span` directly, and BaseWeb sets one on its own
               `input` from theme typography — neither inherits from the
               container, so styling the container alone leaves the visible
               text at 15px and the zoom still fires. */
            .stTextInput input,
            .stNumberInput input,
            .stTextArea textarea,
            .stDateInput input,
            .stTimeInput input,
            .stSelectbox div[data-baseweb="select"],
            .stSelectbox div[data-baseweb="select"] span,
            .stSelectbox div[data-baseweb="select"] input {{
                font-size: {NO_ZOOM_FONT_PX}px !important;
            }}

            /* Touch targets. The sidebar nav and the tab strip are the two
               places an operator hits repeatedly on a phone. */
            .stButton button {{
                min-height: {TOUCH_TARGET_PX}px;
                padding: 10px 18px;
            }}

            /* `!important` is not decoration here: the base sheet sets
               `label {{ display: block !important }}`, and an !important
               declaration beats a plain one whatever the source order — so
               without it the row is 44px tall with its text stuck at the top,
               which is the half-fix that looks done. */
            section[data-testid="stSidebar"] .stRadio > div > label {{
                min-height: {TOUCH_TARGET_PX}px;
                display: flex !important;
                align-items: center;
            }}

            .stTabs [data-baseweb="tab"] {{
                min-height: {TOUCH_TARGET_PX}px;
            }}

            /* The one control the button rule misses, and the most-tapped one
               on the phone path: the pending-review assign picker. BaseWeb
               renders it ~40px. */
            .stSelectbox div[data-baseweb="select"] > div {{
                min-height: {TOUCH_TARGET_PX}px;
            }}

            /* Two metric tiles per row, and a tighter tile: five stacked
               full-width cards used to push the first lead a screen and a
               half down the page. */
            /* !important for the same reason as the tablet rule above. */
            .metric-grid {{
                grid-template-columns: repeat(2, 1fr) !important;
                gap: 8px;
            }}

            .metric-tile {{
                padding: 12px 14px;
            }}

            .metric-tile-value {{
                font-size: 1.5rem !important;
            }}

            .metric-tile-label {{
                /* Two-up on a phone is where the long Hebrew labels get
                   tight, so the label keeps its own alignment rather than
                   inheriting the tile's centring. The tracking goes: it buys
                   ~11px on "ממתין לבדיקת מנהל" for no legibility gain, since
                   Hebrew has no case for the uppercase transform to act on
                   either. */
                text-align: {align};
                letter-spacing: 0;
            }}

            /* The board becomes one stacked column. Sideways scrolling on a
               phone hides the columns that matter behind a gesture nobody
               makes. */
            .kanban-board {{
                flex-direction: column;
                flex-wrap: nowrap;
                overflow-x: visible;
            }}

            .kanban-column {{
                flex: none;
                min-width: 0;
                width: 100%;
                /* The base rule reserves 200px so columns line up side by
                   side. Stacked, a column holding one card reserves ~80px of
                   nothing, and four of them is an extra screen of scrolling. */
                min-height: 0;
            }}

            /* An empty status is a header and a dash. On desktop it costs a
               column of a scroller nobody minds; stacked, it is a full screen
               of nothing between the operator and the next real lead. */
            .kanban-column--empty {{
                display: none;
            }}
        }}
"""
