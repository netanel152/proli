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
    # Used by the metric grid's phone rule, where a two-up grid of long Hebrew
    # labels needs the value to stay on the tile.
    opp_align = "left" if align == "right" else "right"

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
               the Hebrew labels wrap to three lines. */
            .metric-grid {{
                grid-template-columns: repeat(3, 1fr);
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
            .stTextInput input,
            .stNumberInput input,
            .stTextArea textarea,
            .stDateInput input,
            .stTimeInput input,
            .stSelectbox div[data-baseweb="select"] {{
                font-size: {NO_ZOOM_FONT_PX}px !important;
            }}

            /* Touch targets. The sidebar nav and the tab strip are the two
               places an operator hits repeatedly on a phone. */
            .stButton button {{
                min-height: {TOUCH_TARGET_PX}px;
                padding: 10px 18px;
            }}

            section[data-testid="stSidebar"] .stRadio > div > label {{
                min-height: {TOUCH_TARGET_PX}px;
                display: flex;
                align-items: center;
            }}

            .stTabs [data-baseweb="tab"] {{
                min-height: {TOUCH_TARGET_PX}px;
            }}

            /* Two metric tiles per row, and a tighter tile: five stacked
               full-width cards used to push the first lead a screen and a
               half down the page. */
            .metric-grid {{
                grid-template-columns: repeat(2, 1fr);
                gap: 8px;
            }}

            .metric-tile {{
                padding: 12px 14px;
            }}

            .metric-tile-value {{
                font-size: 1.5rem !important;
            }}

            .metric-tile-label {{
                /* Long Hebrew labels get two lines rather than an ellipsis;
                   the tile grows instead of hiding which metric it is. */
                white-space: normal;
                text-align: {align};
                padding-{opp_align}: 0;
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
            }}

            /* An empty status is a header and a dash. On desktop it costs a
               column of a scroller nobody minds; stacked, it is a full screen
               of nothing between the operator and the next real lead. */
            .kanban-column--empty {{
                display: none;
            }}
        }}
"""
