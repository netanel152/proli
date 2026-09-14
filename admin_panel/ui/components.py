import streamlit as st
import html

from admin_panel.core.labels import lead_status_label
from admin_panel.ui.responsive import responsive_css
from admin_panel.ui.rtl import rtl_css

# Status color mapping for Kanban and pills
STATUS_COLORS = {
    # Amber "needs a human" — deliberately distinct from contacted's orange and
    # rejected's red so the human-intervention queue reads as attention, not error.
    "pending_admin_review": {
        "bg": "#FFFBEB",
        "text": "#B45309",
        "border": "#F59E0B",
        "icon": "support_agent",
    },
    "new": {
        "bg": "#EFF6FF",
        "text": "#1D4ED8",
        "border": "#BFDBFE",
        "icon": "fiber_new",
    },
    "contacted": {
        "bg": "#FFF7ED",
        "text": "#C2410C",
        "border": "#FED7AA",
        "icon": "call",
    },
    "booked": {
        "bg": "#F0FDF4",
        "text": "#15803D",
        "border": "#BBF7D0",
        "icon": "event_available",
    },
    "completed": {
        "bg": "#ECFDF5",
        "text": "#047857",
        "border": "#A7F3D0",
        "icon": "check_circle",
    },
    "rejected": {
        "bg": "#FEF2F2",
        "text": "#B91C1C",
        "border": "#FECACA",
        "icon": "cancel",
    },
    "closed": {"bg": "#F5F3FF", "text": "#6D28D9", "border": "#DDD6FE", "icon": "lock"},
    "cancelled": {
        "bg": "#FDF2F8",
        "text": "#BE185D",
        "border": "#FBCFE8",
        "icon": "block",
    },
}

# Dark mode status colors
STATUS_COLORS_DARK = {
    "pending_admin_review": {"bg": "#422006", "text": "#FCD34D", "border": "#B45309"},
    "new": {"bg": "#1E3A5F", "text": "#93C5FD", "border": "#2563EB"},
    "contacted": {"bg": "#451A03", "text": "#FDBA74", "border": "#C2410C"},
    "booked": {"bg": "#052E16", "text": "#86EFAC", "border": "#15803D"},
    "completed": {"bg": "#022C22", "text": "#6EE7B7", "border": "#047857"},
    "rejected": {"bg": "#450A0A", "text": "#FCA5A5", "border": "#B91C1C"},
    "closed": {"bg": "#2E1065", "text": "#C4B5FD", "border": "#6D28D9"},
    "cancelled": {"bg": "#500724", "text": "#F9A8D4", "border": "#BE185D"},
}


def load_css(lang_code, T):
    direction = T["dir"]
    align = T["align"]
    border_side = "left" if direction == "rtl" else "right"
    opp_border = "right" if direction == "rtl" else "left"

    # Hebrew has no letter case, so `text-transform: uppercase` is a no-op on
    # it — but the tracking that goes with the small-caps look is not. Hebrew
    # letterforms are drawn to sit close, and 0.03–0.05em pulls them apart
    # into something that reads as spaced-out rather than as a label.
    caps = "none" if direction == "rtl" else "uppercase"

    def tracking(em):
        """The rule's own LTR tracking, or none of it in Hebrew."""
        return "normal" if direction == "rtl" else em

    # Import Google Fonts and Material Symbols
    st.markdown(
        """
        <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=Heebo:wght@300;400;500;600;700;800&display=swap">
        <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Material+Symbols+Rounded:opsz,wght,FILL,GRAD@24,400,1,0" />
    """,
        unsafe_allow_html=True,
    )

    # Status colours, both palettes, as classes.
    #
    # `STATUS_COLORS_DARK` existed from PRO-46 and was never read by anything:
    # both render helpers took the light dict, so on the dark palette every
    # pill and every Kanban header stayed a pale pastel on a #0F172A page.
    # Python cannot see `prefers-color-scheme` — only CSS can — so the fix is
    # to emit both palettes here and let the media query choose, which is
    # also why the header colours move out of an inline `style` attribute in
    # `render_kanban_column`: an inline declaration outranks any media query.
    status_pill_css = ""
    for status, colors in STATUS_COLORS.items():
        status_pill_css += f"""
        .status-pill-{status} {{
            background-color: {colors['bg']};
            color: {colors['text']};
            border: 1px solid {colors['border']};
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 0.8rem;
            font-weight: 600;
            display: inline-flex;
            align-items: center;
            gap: 4px;
            white-space: nowrap;
        }}

        .kanban-header--{status} {{
            background-color: {colors['bg']};
            color: {colors['text']};
            border: 1px solid {colors['border']};
        }}
        """

    status_dark_css = ""
    for status, colors in STATUS_COLORS_DARK.items():
        status_dark_css += f"""
            .status-pill-{status},
            .kanban-header--{status} {{
                background-color: {colors['bg']};
                color: {colors['text']};
                border-color: {colors['border']};
            }}
        """
    status_dark_css = (
        "@media (prefers-color-scheme: dark) {\n" + status_dark_css + "\n        }\n"
    )

    st.markdown(
        f"""
    <style>
        :root {{
            /* --- Light Mode Design Tokens --- */
            --primary: #2563EB;
            --primary-light: #3B82F6;
            --primary-hover: #1D4ED8;
            --primary-bg: #EFF6FF;

            --success: #059669;
            --success-bg: #ECFDF5;
            --warning: #D97706;
            --warning-bg: #FFFBEB;
            --danger: #DC2626;
            --danger-bg: #FEF2F2;

            --bg-app: #F1F5F9;
            --bg-card: #FFFFFF;
            --bg-secondary: #F8FAFC;
            --bg-hover: #F1F5F9;

            --text-main: #0F172A;
            --text-secondary: #64748B;
            --text-muted: #94A3B8;
            --text-inverse: #FFFFFF;

            --border-color: #E2E8F0;
            --border-light: #F1F5F9;

            /* Chat */
            --chat-bg: #F8FAFC;
            --chat-user-bg: #2563EB;
            --chat-user-text: #FFFFFF;
            --chat-bot-bg: #FFFFFF;
            --chat-bot-text: #334155;

            --shadow-xs: 0 1px 2px 0 rgb(0 0 0 / 0.03);
            --shadow-sm: 0 1px 3px 0 rgb(0 0 0 / 0.06), 0 1px 2px -1px rgb(0 0 0 / 0.06);
            --shadow-md: 0 4px 6px -1px rgb(0 0 0 / 0.07), 0 2px 4px -2px rgb(0 0 0 / 0.05);
            --shadow-lg: 0 10px 15px -3px rgb(0 0 0 / 0.08), 0 4px 6px -4px rgb(0 0 0 / 0.04);

            --radius-sm: 8px;
            --radius-md: 12px;
            --radius-lg: 16px;
            --radius-xl: 20px;

            --transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
        }}

        @media (prefers-color-scheme: dark) {{
            :root {{
                --primary: #3B82F6;
                --primary-light: #60A5FA;
                --primary-hover: #60A5FA;
                --primary-bg: #1E3A5F;

                --success: #34D399;
                --success-bg: #022C22;
                --warning: #FBBF24;
                --warning-bg: #451A03;
                --danger: #F87171;
                --danger-bg: #450A0A;

                --bg-app: #0F172A;
                --bg-card: #1E293B;
                --bg-secondary: #1E293B;
                --bg-hover: #334155;

                --text-main: #E2E8F0;
                --text-secondary: #94A3B8;
                --text-muted: #64748B;
                --text-inverse: #0F172A;

                --border-color: #334155;
                --border-light: #1E293B;

                --chat-bg: #0F172A;
                --chat-user-bg: #1D4ED8;
                --chat-user-text: #E2E8F0;
                --chat-bot-bg: #1E293B;
                --chat-bot-text: #E2E8F0;

                --shadow-xs: 0 1px 2px 0 rgb(0 0 0 / 0.2);
                --shadow-sm: 0 1px 3px 0 rgb(0 0 0 / 0.3);
                --shadow-md: 0 4px 6px -1px rgb(0 0 0 / 0.4);
                --shadow-lg: 0 10px 15px -3px rgb(0 0 0 / 0.4);
            }}
        }}

        /* ===== GLOBAL RESET & TYPOGRAPHY ===== */
        html, body, [class*="css"] {{
            font-family: 'Inter', 'Heebo', -apple-system, BlinkMacSystemFont, sans-serif !important;
            color: var(--text-main);
            font-size: 15px !important;
            line-height: 1.6;
            direction: {direction} !important;
            text-align: {align} !important;
        }}

        .stApp {{
            background-color: var(--bg-app);
        }}

        h1, h2, h3, h4, h5, h6 {{
            color: var(--text-main) !important;
            font-family: 'Inter', 'Heebo', sans-serif !important;
            font-weight: 700 !important;
            letter-spacing: -0.02em;
            text-align: {align} !important;
        }}

        h1 {{
            font-size: 1.875rem !important;
            margin-bottom: 0.25rem !important;
            font-weight: 800 !important;
        }}
        h2 {{
            font-size: 1.375rem !important;
            margin-top: 1rem !important;
            font-weight: 700 !important;
        }}
        h3 {{
            font-size: 1.125rem !important;
            color: var(--text-secondary) !important;
            font-weight: 600 !important;
        }}

        p, div, span, label, li {{
            color: var(--text-main);
            font-size: 0.9375rem;
            text-align: {align};
        }}

        /* Form labels — the caption above a text input, select, date picker.
           Scoped to Streamlit's widget-label element on purpose: an unscoped
           `label {{ display: block !important }}` also reached the labels
           BaseWeb uses as *rows* — the sidebar radio (S6 had to beat it
           with a second `!important`) and every checkbox and toggle, whose
           box and text then stacked one above the other, 46px tall instead
           of 24, with the checkbox text in uppercase that a later `span`
           rule could not undo because the text lives in a `p`. A checkbox
           label is a control, not a caption; it keeps BaseWeb's flex row. */
        label[data-testid="stWidgetLabel"] {{
            font-weight: 500 !important;
            font-size: 0.85rem !important;
            color: var(--text-secondary) !important;
            text-transform: {caps};
            letter-spacing: {tracking('0.03em')};
            display: block !important;
            text-align: {align} !important;
            direction: {direction} !important;
            margin-bottom: 2px !important;
        }}

        /* Page description captions */
        .stCaption, div[data-testid="stCaptionContainer"] {{
            font-size: 0.9rem !important;
            color: var(--text-secondary) !important;
            margin-bottom: 1.5rem !important;
        }}

        /* ===== MAIN CONTAINER ===== */
        .block-container {{
            padding-top: 2rem !important;
            padding-bottom: 3rem !important;
            max-width: 100% !important;
            padding-left: 2rem !important;
            padding-right: 2rem !important;
        }}

        /* ===== SIDEBAR ===== */
        section[data-testid="stSidebar"] {{
            background: linear-gradient(180deg, var(--bg-card) 0%, var(--bg-secondary) 100%);
            border-{border_side}: none;
            box-shadow: var(--shadow-md);
            /* The 280px width is NOT here: it applies only above Streamlit's
               own sidebar-overlay breakpoint, in `responsive.py`. Pinned at
               every width it fought the mobile overlay and the sidebar
               covered the whole phone screen on load. */
        }}

        section[data-testid="stSidebar"] .block-container {{
            padding-top: 1rem;
            padding-left: 1rem !important;
            padding-right: 1rem !important;
            direction: {direction} !important;
            text-align: {align} !important;
        }}

        /* Sidebar title */
        section[data-testid="stSidebar"] h1 {{
            font-size: 1.4rem !important;
            background: linear-gradient(135deg, var(--primary) 0%, var(--primary-light) 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            margin-bottom: 0.5rem !important;
        }}

        /* Sidebar navigation radio */
        section[data-testid="stSidebar"] .stRadio > div {{
            direction: {direction};
            text-align: {align};
        }}

        section[data-testid="stSidebar"] .stRadio > div > label {{
            background-color: transparent;
            border-radius: var(--radius-sm);
            padding: 8px 12px !important;
            margin: 2px 0 !important;
            cursor: pointer;
            transition: var(--transition);
            text-transform: none;
            font-size: 0.9rem !important;
            font-weight: 500 !important;
            color: var(--text-main) !important;
            letter-spacing: normal;
            /* The row, not the words. A label shrinks to its text by
               default, so the selected pill ended at the end of the label —
               five ragged widths down the sidebar — and the tap target was
               the text rather than the row the operator aims at. */
            width: 100%;
            box-sizing: border-box;
            /* Kept `!important` as belt-and-braces. The caption rule above
               is scoped to `stWidgetLabel` now, so nothing forces this label
               to `block` any more — but when it did (S6), the radio marker
               fell onto its own line above the text and every nav row
               measured 61px instead of 39px at every width but the phone. */
            display: flex !important;
            align-items: center;
            gap: 8px;
        }}

        section[data-testid="stSidebar"] .stRadio > div > label:hover {{
            background-color: var(--bg-hover);
        }}

        /* A full-width label gives the flex row free space to distribute;
           without this the radio marker is the item that absorbs it and
           deforms into an oval. */
        section[data-testid="stSidebar"] .stRadio > div > label > div:first-child {{
            flex: 0 0 auto;
        }}

        section[data-testid="stSidebar"] .stRadio > div > label[data-checked="true"],
        section[data-testid="stSidebar"] .stRadio > div > label:has(input:checked) {{
            background-color: var(--primary-bg) !important;
            color: var(--primary) !important;
            font-weight: 600 !important;
        }}

        section[data-testid="stSidebar"] .stSelectbox div {{
            direction: {direction};
            text-align: {align};
        }}

        /* ===== METRICS CARDS ===== */
        div[data-testid="stMetric"] {{
            background-color: var(--bg-card);
            border: 1px solid var(--border-color);
            padding: 20px 24px;
            border-radius: var(--radius-lg);
            box-shadow: var(--shadow-sm);
            transition: var(--transition);
            height: 100%;
            display: flex;
            flex-direction: column;
            justify-content: center;
            direction: {direction};
            text-align: {align};
            position: relative;
            overflow: hidden;
        }}

        div[data-testid="stMetric"]:hover {{
            box-shadow: var(--shadow-md);
            transform: translateY(-1px);
        }}

        div[data-testid="stMetric"]::before {{
            content: '';
            position: absolute;
            top: 0;
            {opp_border}: 0;
            {border_side}: 0;
            width: 100%;
            height: 3px;
            background: linear-gradient(90deg, var(--primary) 0%, var(--primary-light) 100%);
        }}

        div[data-testid="stMetricLabel"] {{
            width: 100%;
            text-align: {align} !important;
        }}

        div[data-testid="stMetricLabel"] p {{
            font-size: 0.8rem !important;
            text-transform: {caps};
            letter-spacing: {tracking('0.05em')};
            color: var(--text-secondary) !important;
            font-weight: 600 !important;
        }}

        div[data-testid="stMetricValue"] {{
            width: 100%;
            text-align: {align} !important;
        }}

        div[data-testid="stMetricValue"] div {{
            font-size: 2rem !important;
            font-weight: 800 !important;
            color: var(--text-main) !important;
        }}

        /* ===== METRIC GRID (render_metric_grid) =====
           A CSS grid rather than `st.columns` + `st.metric`, because
           Streamlit stacks every column to full width below 640px and five
           tall cards then push the first lead a screen and a half down. One
           markup, re-flowed by the breakpoints in `responsive.py`: five up on
           desktop, three on tablet, two on a phone, DOM order untouched.
           Styled to match `stMetric` above, which is still used wherever a
           delta is shown. */
        .metric-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 12px;
        }}

        .metric-tile {{
            background-color: var(--bg-card);
            border: 1px solid var(--border-color);
            padding: 20px 24px;
            border-radius: var(--radius-lg);
            box-shadow: var(--shadow-sm);
            transition: var(--transition);
            display: flex;
            flex-direction: column;
            justify-content: center;
            text-align: {align};
            position: relative;
            overflow: hidden;
        }}

        .metric-tile:hover {{
            box-shadow: var(--shadow-md);
            transform: translateY(-1px);
        }}

        .metric-tile::before {{
            content: '';
            position: absolute;
            top: 0;
            {opp_border}: 0;
            {border_side}: 0;
            width: 100%;
            height: 3px;
            background: linear-gradient(90deg, var(--primary) 0%, var(--primary-light) 100%);
        }}

        .metric-tile-label {{
            font-size: 0.8rem !important;
            text-transform: {caps};
            letter-spacing: {tracking('0.05em')};
            color: var(--text-secondary) !important;
            font-weight: 600 !important;
            text-align: {align};
        }}

        .metric-tile-value {{
            font-size: 2rem !important;
            font-weight: 800 !important;
            color: var(--text-main) !important;
            text-align: {align};
            line-height: 1.2;
        }}

        /* ===== TABS ===== */
        .stTabs [data-baseweb="tab-list"] {{
            gap: 4px;
            background-color: var(--bg-secondary);
            border-radius: var(--radius-md);
            padding: 4px;
            border: 1px solid var(--border-color);
        }}

        .stTabs [data-baseweb="tab"] {{
            border-radius: var(--radius-sm);
            padding: 8px 16px;
            font-size: 0.875rem;
            font-weight: 500;
            color: var(--text-secondary);
            border: none;
            background: transparent;
            transition: var(--transition);
        }}

        .stTabs [data-baseweb="tab"]:hover {{
            color: var(--text-main);
            background-color: var(--bg-hover);
        }}

        .stTabs [aria-selected="true"] {{
            background-color: var(--bg-card) !important;
            color: var(--primary) !important;
            font-weight: 600 !important;
            box-shadow: var(--shadow-sm);
        }}

        /* Hide tab underline */
        .stTabs [data-baseweb="tab-highlight"] {{
            display: none;
        }}

        .stTabs [data-baseweb="tab-border"] {{
            display: none;
        }}

        /* ===== EXPANDERS ===== */
        .streamlit-expanderHeader {{
            background-color: var(--bg-card) !important;
            border: 1px solid var(--border-color) !important;
            border-radius: var(--radius-md);
            color: var(--text-main) !important;
            font-family: 'Inter', 'Heebo', sans-serif !important;
            font-weight: 500;
            font-size: 0.95rem;
            padding: 12px 16px;
            margin-bottom: 0;
            transition: var(--transition);
            direction: {direction} !important;
            text-align: {align} !important;
        }}

        .streamlit-expanderHeader:hover {{
            background-color: var(--bg-hover) !important;
        }}

        .streamlit-expanderHeader p {{
            margin: 0 !important;
            flex-grow: 1;
            text-align: {align} !important;
        }}

        .streamlit-expanderContent {{
            background-color: var(--bg-card) !important;
            border: 1px solid var(--border-color) !important;
            border-top: none !important;
            border-bottom-left-radius: var(--radius-md);
            border-bottom-right-radius: var(--radius-md);
            padding: 16px;
            margin-top: 0;
            margin-bottom: 1rem;
            box-shadow: var(--shadow-xs);
            direction: {direction} !important;
            text-align: {align} !important;
        }}

        /* ===== BUTTONS ===== */
        /* `st.form_submit_button` carries `kind="primaryFormSubmit"` rather
           than `kind="primary"`, so the `[kind]` variants below missed it and
           the login button was the one primary in the panel wearing
           Streamlit's default red (#FF4B4B, measured). On 1.31.1 its wrapper
           still has the `stButton` class, so naming `stFormSubmitButton` on
           the base rule is forward-compat for versions that drop it. */
        .stButton button,
        [data-testid="stFormSubmitButton"] button {{
            /* Pinned, not inherited: the per-language arrow in
               T["back_to_list"] relies on the label's base direction. */
            direction: {direction};
            border-radius: var(--radius-sm);
            font-family: 'Inter', 'Heebo', sans-serif !important;
            font-weight: 600;
            padding: 8px 20px;
            font-size: 0.875rem !important;
            transition: var(--transition);
            border: 1px solid transparent;
            cursor: pointer;
            letter-spacing: 0.01em;
            /* Primary and secondary measured 39px and 41px in the same row,
               because only one of them carries a border. `border-box` and a
               floor make a row of buttons line up whatever kind they are. */
            box-sizing: border-box;
            min-height: 40px;
        }}

        .stButton button[kind="primary"],
        [data-testid="stFormSubmitButton"] button[kind="primaryFormSubmit"] {{
            background: linear-gradient(135deg, var(--primary) 0%, var(--primary-hover) 100%);
            color: white;
            border: none;
            box-shadow: 0 2px 4px rgb(37 99 235 / 0.3);
        }}

        .stButton button[kind="primary"]:hover,
        [data-testid="stFormSubmitButton"] button[kind="primaryFormSubmit"]:hover {{
            box-shadow: 0 4px 8px rgb(37 99 235 / 0.4);
            transform: translateY(-1px);
        }}

        .stButton button[kind="secondary"],
        [data-testid="stFormSubmitButton"] button[kind="secondaryFormSubmit"] {{
            background-color: var(--bg-card);
            border: 1px solid var(--border-color);
            color: var(--text-main);
        }}

        .stButton button[kind="secondary"]:hover,
        [data-testid="stFormSubmitButton"] button[kind="secondaryFormSubmit"]:hover {{
            background-color: var(--bg-hover);
            border-color: var(--text-muted);
        }}

        /* Keyboard focus. The sheet styled `:hover` on everything and
           `:focus` on nothing, so a keyboard user got only the browser's own
           1px ring in its own colour — legible on a white page, much less so
           against the primary-blue and card surfaces here, and absent from
           the sidebar nav rows, which are labels rather than buttons.
           `:focus-visible` (plus `:focus-within` for the nav row, whose real
           focus lands on the input inside it) shows a 2px ring in the
           panel's own primary for keyboard users, and does not draw one on
           every mouse click.

           Measuring this needs care: `.stButton button` carries
           `transition: all 0.2s`, and Chromium animates `outline`. Sampled
           ~100ms after Tab it reads "1px" in a colour part-way to the target
           — which looks exactly like the rule not applying. */
        .stButton button:focus-visible,
        .stDownloadButton button:focus-visible,
        [data-testid="stFormSubmitButton"] button:focus-visible,
        section[data-testid="stSidebar"] .stRadio > div > label:focus-within,
        .stTabs [data-baseweb="tab"]:focus-visible {{
            outline: 2px solid var(--primary) !important;
            outline-offset: 2px !important;
        }}

        /* The `mark_row_inline()` marker. It must occupy nothing at *every*
           width, not only on a phone: a zero-height flex item still earns the
           vertical block's `gap`, which would push each marked row ~1rem down
           on desktop — and desktop being unchanged is this work's own
           acceptance criterion. `display: none` removes it from flex layout
           entirely, and a `display: none` element is still matched by `+`, so
           the phone rule that reads it still works. */
        [data-testid="element-container"]:has(.row-inline),
        [data-testid="element-container"]:has(.login-page) {{
            display: none !important;
        }}

        /* The login screen is the whole page when it renders at all, so
           the cap goes on the container rather than on each element. */
        .block-container:has(.login-page) {{
            max-width: 400px !important;
            margin-inline: auto !important;
        }}

        /* ===== TABLES & DATA EDITOR ===== */
        div[data-testid="stDataFrame"] {{
            border: 1px solid var(--border-color);
            border-radius: var(--radius-md);
            overflow: hidden;
            box-shadow: var(--shadow-sm);
            background-color: var(--bg-card);
            /* Force LTR for Glide Data Grid - it breaks in RTL */
            direction: ltr !important;
        }}

        /* Force LTR on data grid internals to prevent column misalignment */
        div[data-testid="stDataFrame"] * {{
            direction: ltr !important;
            text-align: left !important;
        }}

        div[data-testid="stDataFrame"] canvas {{
            direction: ltr !important;
        }}

        /* Glide Data Grid container */
        .dvn-scroller {{
            direction: ltr !important;
        }}

        /* Data editor cell overlay (edit mode) */
        .gdg-style {{
            direction: ltr !important;
        }}

        /* Column header text */
        div[data-testid="stDataFrame"] [role="columnheader"] {{
            direction: ltr !important;
            text-align: left !important;
        }}

        /* Cell content */
        div[data-testid="stDataFrame"] [role="gridcell"] {{
            direction: ltr !important;
            text-align: left !important;
        }}

        /* Data editor input overlay */
        div[data-testid="stDataFrame"] input,
        div[data-testid="stDataFrame"] textarea {{
            direction: ltr !important;
            text-align: left !important;
        }}

        /* Selectbox inside data editor */
        div[data-testid="stDataFrame"] div[data-baseweb="select"] {{
            direction: ltr !important;
        }}

        /* Regular HTML tables (non-Glide: st.table, and markdown tables).
           These follow the page direction. Only the Glide grid above is an
           LTR island, and for a specific reason — its columns misalign in
           RTL. A markdown table has no such problem, and hard-coding
           `text-align: left` here left every Hebrew table ragged against the
           wrong edge of its own right-aligned box. */
        thead tr th {{
            background-color: var(--bg-secondary) !important;
            color: var(--text-secondary) !important;
            font-weight: 600 !important;
            font-size: 0.8rem !important;
            text-transform: {caps};
            letter-spacing: {tracking('0.04em')};
            padding: 12px 16px !important;
            text-align: {align} !important;
            border-bottom: 2px solid var(--border-color) !important;
        }}

        tbody tr td {{
            font-size: 0.9rem !important;
            color: var(--text-main) !important;
            padding: 12px 16px !important;
            background-color: var(--bg-card) !important;
            text-align: {align} !important;
            border-bottom: 1px solid var(--border-light) !important;
        }}

        tbody tr:hover td {{
            background-color: var(--bg-hover) !important;
        }}

        /* Arrow / dataframe toolbar stays LTR */
        div[data-testid="stDataFrameResizable"] {{
            direction: ltr !important;
        }}

        /* ===== FORM INPUTS ===== */
        .stTextInput input, .stNumberInput input, .stTextArea textarea, .stDateInput input, .stTimeInput input {{
            background-color: var(--bg-card) !important;
            border: 1.5px solid var(--border-color) !important;
            border-radius: var(--radius-sm);
            color: var(--text-main) !important;
            padding: 10px 14px;
            font-size: 0.9375rem;
            font-family: 'Inter', 'Heebo', sans-serif !important;
            direction: {direction} !important;
            text-align: {align} !important;
            transition: var(--transition);
        }}

        .stTextInput input:focus, .stNumberInput input:focus, .stTextArea textarea:focus {{
            border-color: var(--primary) !important;
            box-shadow: 0 0 0 3px var(--primary-bg) !important;
        }}

        /* ===== SELECTBOX ===== */
        div[data-baseweb="select"] > div {{
            background-color: var(--bg-card) !important;
            border-color: var(--border-color) !important;
            color: var(--text-main) !important;
            border-radius: var(--radius-sm) !important;
            direction: {direction} !important;
            border-width: 1.5px !important;
        }}

        div[data-baseweb="select"] > div:focus-within {{
            border-color: var(--primary) !important;
            box-shadow: 0 0 0 3px var(--primary-bg) !important;
        }}

        div[data-baseweb="select"] span {{
            color: var(--text-main) !important;
            font-size: 0.9375rem;
            font-family: 'Inter', 'Heebo', sans-serif !important;
            text-align: {align} !important;
        }}

        li[role="option"] {{
            direction: {direction} !important;
            text-align: {align} !important;
            font-size: 0.9375rem !important;
            padding: 10px 14px !important;
        }}

        li[role="option"]:hover {{
            background-color: var(--primary-bg) !important;
        }}

        /* ===== CONTAINERS WITH BORDER ===== */
        div[data-testid="stVerticalBlock"] > div[data-testid="stVerticalBlockBorderWrapper"] {{
            border-radius: var(--radius-md) !important;
            border-color: var(--border-color) !important;
            background-color: var(--bg-card);
            box-shadow: var(--shadow-xs);
            transition: var(--transition);
        }}

        div[data-testid="stVerticalBlock"] > div[data-testid="stVerticalBlockBorderWrapper"]:hover {{
            box-shadow: var(--shadow-sm);
        }}

        /* ===== DIVIDERS ===== */
        hr {{
            border-color: var(--border-light) !important;
            margin: 1.5rem 0 !important;
        }}

        /* ===== CHAT BUBBLES ===== */
        .chat-container {{
            display: flex;
            flex-direction: column;
            gap: 8px;
            padding: 16px;
            background-color: var(--chat-bg);
            border-radius: var(--radius-lg);
            max-height: 450px;
            overflow-y: auto;
            border: 1px solid var(--border-color);
            direction: {direction};
        }}

        .chat-bubble {{
            padding: 10px 16px;
            border-radius: 16px;
            max-width: 75%;
            font-size: 0.9rem;
            line-height: 1.5;
            position: relative;
            font-family: 'Inter', 'Heebo', sans-serif !important;
            display: inline-block;
            text-align: {align};
        }}

        /* Side is carried by the auto margin alone. `align-self` was also
           set here and was inert: an auto margin consumes the free space
           before alignment ever runs, so the two could disagree (and did)
           without the disagreement being visible. One mechanism, so the
           side a bubble takes can be read off one line. */
        .user-msg {{
            background-color: var(--chat-user-bg);
            color: var(--chat-user-text);
            border-bottom-{'right' if direction == 'rtl' else 'left'}-radius: 4px;
            margin-{'right' if direction == 'rtl' else 'left'}: auto;
            box-shadow: var(--shadow-sm);
        }}

        .bot-msg {{
            background-color: var(--chat-bot-bg);
            color: var(--chat-bot-text);
            border-bottom-{'left' if direction == 'rtl' else 'right'}-radius: 4px;
            margin-{'left' if direction == 'rtl' else 'right'}: auto;
            border: 1px solid var(--border-color);
        }}

        .chat-meta {{
            font-size: 0.7rem;
            font-weight: 600;
            margin-bottom: 2px;
            display: flex;
            align-items: center;
            opacity: 0.6;
            /* Plain `row`, both languages. A flex row already lays out along
               the inline axis, so under `direction: rtl` it is mirrored
               once; `row-reverse` mirrored it a second time and put the
               speaker icon back on the left, after the name. */
            flex-direction: row;
            gap: 4px;
        }}

        /* ===== PROFILE IMAGE ===== */
        .pro-circle-img {{
            width: 72px;
            height: 72px;
            border-radius: 50%;
            object-fit: cover;
            border: 3px solid var(--border-color);
            box-shadow: var(--shadow-sm);
        }}

        .material-symbols-rounded {{
            font-size: 1.1em;
            vertical-align: middle;
        }}

        /* ===== KANBAN BOARD ===== */
        /* The wrapper was an inline style on the markup in `home.py`; it is a
           class so `responsive.py` can re-flow it (two-up on tablet, one
           stacked column on a phone). The `dir` attribute stays inline on the
           element — PRO-46: the reading order must not depend on an
           ancestor's direction. */
        .kanban-board {{
            display: flex;
            gap: 12px;
            overflow-x: auto;
            padding-bottom: 12px;
        }}

        .kanban-column {{
            background-color: var(--bg-secondary);
            border-radius: var(--radius-md);
            padding: 12px;
            min-height: 200px;
            min-width: 180px;
            flex: 1 0 180px;
            border: 1px solid var(--border-color);
        }}

        .kanban-header {{
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 8px 12px;
            border-radius: var(--radius-sm);
            margin-bottom: 12px;
            font-weight: 700;
            font-size: 0.8rem;
            text-transform: {caps};
            letter-spacing: {tracking('0.04em')};
            direction: {direction};
        }}

        .kanban-count {{
            /* Was `rgba(0,0,0,0.08)`: a black wash over whatever the header
               is wearing, which reads as a chip on a pale header and as
               nothing at all on a dark one. An outline in the header's own
               `currentColor` is legible on both palettes without this rule
               having to know which one is in play. */
            background-color: transparent;
            border: 1px solid currentColor;
            color: inherit;
            padding: 2px 8px;
            border-radius: 10px;
            font-size: 0.75rem;
            font-weight: 700;
        }}

        .kanban-card {{
            background-color: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: var(--radius-sm);
            padding: 12px;
            margin-bottom: 8px;
            box-shadow: var(--shadow-xs);
            transition: var(--transition);
            cursor: default;
            direction: {direction};
            text-align: {align};
        }}

        .kanban-card:hover {{
            box-shadow: var(--shadow-sm);
            transform: translateY(-1px);
        }}

        .kanban-card-title {{
            font-weight: 600;
            font-size: 0.85rem;
            color: var(--text-main);
            margin-bottom: 4px;
            display: flex;
            align-items: center;
            gap: 6px;
        }}

        .kanban-card-meta {{
            font-size: 0.75rem;
            color: var(--text-secondary);
            display: flex;
            flex-direction: column;
            gap: 2px;
        }}

        .kanban-card-detail {{
            display: flex;
            align-items: center;
            gap: 4px;
        }}

        /* ===== STATUS PILLS & KANBAN HEADERS ===== */
        {status_pill_css}
        {status_dark_css}

        /* ===== SECTION HEADER ===== */
        .section-header {{
            display: flex;
            align-items: center;
            gap: 10px;
            margin-bottom: 16px;
            padding-bottom: 12px;
            border-bottom: 2px solid var(--border-light);
            direction: {direction};
        }}

        .section-header h2 {{
            margin: 0 !important;
            padding: 0 !important;
        }}

        /* ===== LOGIN PAGE ===== */
        .login-container {{
            max-width: 400px;
            margin: 4rem auto;
            padding: 2.5rem;
            background: var(--bg-card);
            border-radius: var(--radius-xl);
            box-shadow: var(--shadow-lg);
            border: 1px solid var(--border-color);
        }}

        .login-logo {{
            text-align: center;
            margin-bottom: 1.5rem;
        }}

        .login-logo h1 {{
            font-size: 2rem !important;
            margin: 0 !important;
            text-align: center !important;
        }}

        .login-logo p {{
            color: var(--text-secondary);
            font-size: 0.95rem;
            text-align: center !important;
        }}

        /* ===== TOAST / ALERTS ===== */
        /* This rule used to carry `border-width: 0 0 0 4px`, an accent stripe
           that never drew: `border-style` was never set, so the computed
           width was 0 — in both languages, for as long as it existed.
           It is gone rather than repaired, for two reasons. The side was
           wrong (a left literal puts it on the far edge of a Hebrew alert),
           and, more decisively, it cannot be made to mean anything:
           Streamlit 1.31 tells success from error only by a generated
           emotion class (`st-al` vs `st-bf`) that changes between builds, so
           a stripe here would be the same neutral colour on all four kinds.
           The tinted background Streamlit already gives each kind carries
           that signal; a uniform bar beside it would only add noise.

           The selector also gains the inner notification, because the outer
           `.stAlert` wrapper is transparent and zero-bordered — the radius
           and font size were landing on a box nobody sees. */
        .stAlert,
        div[data-testid="stAlert"],
        div[data-testid="stNotification"] {{
            border-radius: var(--radius-sm) !important;
            font-size: 0.875rem !important;
        }}

        /* ===== DOWNLOAD BUTTON ===== */
        .stDownloadButton button {{
            background-color: var(--bg-card) !important;
            border: 1.5px solid var(--border-color) !important;
            color: var(--text-main) !important;
            border-radius: var(--radius-sm) !important;
            font-size: 0.85rem !important;
            padding: 6px 14px !important;
        }}

        .stDownloadButton button:hover {{
            background-color: var(--bg-hover) !important;
            border-color: var(--text-muted) !important;
        }}

        /* ===== FORMS ===== */
        [data-testid="stForm"] {{
            background-color: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: var(--radius-md);
            padding: 24px;
            box-shadow: var(--shadow-xs);
        }}

        /* ===== EMPTY STATE ===== */
        .empty-state {{
            text-align: center;
            padding: 3rem 1rem;
            color: var(--text-muted);
        }}

        .empty-state .material-symbols-rounded {{
            font-size: 3rem;
            margin-bottom: 0.5rem;
            display: block;
        }}

        /* ===== RTL / DIRECTION OVERRIDES ===== */
        /* Apply direction globally but exclude data grids which must stay LTR */
        .stMarkdown, .stMarkdown p, .stMarkdown span,
        .stTextInput, .stTextArea, .stSelectbox,
        .stRadio, .stCheckbox {{
            direction: {direction} !important;
            text-align: {align} !important;
        }}

        /* Ensure data grids are NOT affected by RTL overrides */
        div[data-testid="stDataFrame"],
        div[data-testid="stDataFrame"] *,
        div[data-testid="stDataFrameResizable"],
        div[data-testid="stDataFrameResizable"] * {{
            direction: ltr !important;
            text-align: left !important;
        }}

        /* ===== AUTO-REFRESH INDICATOR ===== */
        .refresh-indicator {{
            display: flex;
            align-items: center;
            gap: 6px;
            font-size: 0.75rem;
            color: var(--text-muted);
            padding: 4px 0;
        }}

        .refresh-dot {{
            width: 6px;
            height: 6px;
            border-radius: 50%;
            background-color: var(--success);
            animation: pulse 2s infinite;
            /* The paused label is far longer than "Live"; without this the
               flex row squashes the dot into an oval in a narrow sidebar. */
            flex-shrink: 0;
        }}

        /* PRO-141: auto-refresh is suspended while a table has unsaved
           edits, so the same row has to be able to say "paused" rather
           than "live". Colour is not the only carrier — the label beside
           it changes too. */
        .refresh-dot--paused {{
            background-color: var(--warning);
            animation: none;
        }}

        @keyframes pulse {{
            0%, 100% {{ opacity: 1; }}
            50% {{ opacity: 0.4; }}
        }}
{rtl_css(direction, align)}
{responsive_css(direction, align)}
    </style>
    """,
        unsafe_allow_html=True,
    )


ROW_INLINE_MARKER = '<span class="row-inline" hidden></span>'
LOGIN_PAGE_MARKER = '<span class="login-page" hidden></span>'


def mark_login_page():
    """Mark the page as the login screen, so the CSS can narrow it.

    The same marker mechanism as `mark_row_inline()` and for the same reason —
    Streamlit gives no handle on a container — but scoped to the *page*: the
    rule caps `.block-container` itself, which carries the title, the welcome
    line and the form together. On a phone the cap is wider than the screen
    and the ordinary container padding applies, so the form is full width.
    """
    st.markdown(LOGIN_PAGE_MARKER, unsafe_allow_html=True)


def mark_row_inline():
    """Emit the marker that keeps the *next* `st.columns` row horizontal.

    Call it immediately before a row of **actions** — a Yes/No confirm, a
    Save/Cancel, a Prev/Next — and never before a row of inputs, which should
    stack on a phone.

    Why a marker at all: below 640px Streamlit puts
    `min-width: calc(100% - 22.5px)` on every `[data-testid="column"]`, which
    is what makes a row wrap. Nothing distinguishes a row of buttons from a
    row of text inputs in the DOM, and Streamlit 1.31 takes no `key` on
    `st.columns`, so there is no handle to style one row and not another. The
    marker is that handle.

    It sits *before* the row rather than inside its first cell — the shape the
    plan originally sketched — because a marker inside a cell adds the
    vertical block's `1rem` gap to that cell alone, and the two buttons of a
    confirm pair then sit a line apart. As a preceding sibling the CSS reaches
    the row with `+`, and the marker's own container is `display: none`, so it
    occupies nothing and contributes no gap.

    Returns nothing and renders nothing visible; it is `st.markdown` because
    that is the only way to put a class into Streamlit's DOM.
    """
    st.markdown(ROW_INLINE_MARKER, unsafe_allow_html=True)


def set_flash(key, message, level="success"):
    """Stash a confirmation to be shown on the *next* run (PRO-61).

    ``st.success`` followed by ``st.rerun()`` is discarded with the element
    tree before the browser paints it, so a mutation that reruns to refresh
    its view had no readable confirmation. Same pattern as ``leads_flash``
    (PRO-161) and ``sch_flash`` (PRO-158): set it right before the
    ``st.rerun()``, ``render_flash`` it at the top of the view.

    ``level`` is ``"success"``, ``"warning"`` or ``"error"``. Not every mutation
    that completes is good news — approving a pro whose service areas could not
    all be geocoded succeeds *and* needs the operator to come back to it —
    and a green tick over that sentence reads as "nothing to do here". ``error``
    is the third case PRO-188 needed: "assigned, now go phone them" and "the
    assignment failed" are different problems, and one amber box for both
    collapses exactly the distinction the message is trying to draw.
    """
    st.session_state[key] = (level, message)


def render_flash(key):
    """Render and clear the confirmation stashed by ``set_flash``."""
    flash = st.session_state.pop(key, None)
    if not flash:
        return
    # Tolerates a bare string: a caller that wrote the key directly, and any
    # message stashed by the previous build still sitting in a live session.
    level, msg = flash if isinstance(flash, tuple) else ("success", flash)
    if level == "error":
        st.toast(msg, icon="🚨")
        st.error(msg)
    elif level == "warning":
        st.toast(msg, icon="⚠️")
        st.warning(msg)
    else:
        st.toast(msg, icon="✅")
        st.success(msg)


def render_chat_bubble(text, role, timestamp, T):
    is_user = role == "user"
    cls = "user-msg" if is_user else "bot-msg"
    name = T["role_user"] if is_user else T["role_bot"]

    icon_name = "person" if is_user else "smart_toy"
    icon_html = f'<span class="material-symbols-rounded">{icon_name}</span>'

    time_str = timestamp.strftime("%H:%M") if timestamp else ""
    safe_text = html.escape(text)

    return f"<div class='chat-bubble {cls}'><span class='chat-meta'>{icon_html} {name} • {time_str}</span>{safe_text}</div>"


def render_status_pill(status, T):
    """Render an HTML status pill badge."""
    colors = STATUS_COLORS.get(status, STATUS_COLORS["new"])
    label = lead_status_label(T, status)
    icon = colors.get("icon", "circle")
    return f'<span class="status-pill-{status}"><span class="material-symbols-rounded" style="font-size:0.9rem">{icon}</span> {label}</span>'


def render_kanban_card(lead, T):
    """Render a single Kanban card as HTML."""
    client = lead.get("client", "?")
    details = lead.get("details_summary", "")
    pro = lead.get("professional", T["unknown_pro"])
    date = lead.get("date")
    date_str = date.strftime("%d/%m %H:%M") if date else ""

    # Truncate details
    if len(details) > 80:
        details = details[:77] + "..."

    safe_details = html.escape(details)
    safe_client = html.escape(str(client))
    safe_pro = html.escape(str(pro))

    return f"""<div class="kanban-card">
    <div class="kanban-card-title">
        <span class="material-symbols-rounded" style="font-size:1rem; color:var(--text-secondary)">person</span>
        {safe_client}
    </div>
    <div class="kanban-card-meta">
        <div class="kanban-card-detail">
            <span class="material-symbols-rounded" style="font-size:0.85rem">handyman</span>
            {safe_details or '—'}
        </div>
        <div class="kanban-card-detail">
            <span class="material-symbols-rounded" style="font-size:0.85rem">engineering</span>
            {safe_pro}
        </div>
        <div class="kanban-card-detail">
            <span class="material-symbols-rounded" style="font-size:0.85rem">schedule</span>
            <span dir="ltr" style="unicode-bidi:isolate">{date_str}</span>
        </div>
    </div>
</div>"""


def render_kanban_column(status, leads, T):
    """Render a full Kanban column with header and cards."""
    colors = STATUS_COLORS.get(status, STATUS_COLORS["new"])
    # The class has to fall back the same way `colors` does, or an unknown
    # status would render a header with no rule at all — worse than the
    # "new" palette it used to borrow.
    palette = status if status in STATUS_COLORS else "new"
    label = lead_status_label(T, status)
    count = len(leads)

    cards_html = "".join(render_kanban_card(lead, T) for lead in leads)

    # A column with nothing in it is marked rather than omitted: on desktop it
    # still renders (an empty status is information — the operator can see the
    # queue is clear), and only the phone breakpoint hides it, where a stacked
    # empty column is a screen of nothing between two real ones. Dropping it
    # here instead would change the desktop board too.
    empty_class = "" if leads else " kanban-column--empty"

    if not leads:
        cards_html = """<div class="empty-state" style="padding: 1.5rem 0.5rem;">
    <span class="material-symbols-rounded" style="font-size: 1.5rem;">inbox</span>
    <div style="font-size: 0.8rem;">—</div>
</div>"""

    # Colours come from `.kanban-header--<status>` in the sheet rather than
    # an inline style, so the dark-mode media query can reach them at all.
    return f"""<div class="kanban-column{empty_class}">
    <div class="kanban-header kanban-header--{palette}">
        <span class="material-symbols-rounded" style="font-size:1rem">{colors['icon']}</span>
        {label}
        <span class="kanban-count">{count}</span>
    </div>
    {cards_html}
</div>"""


def render_metric_grid(items, T, weights=None):
    """Render a row of metric tiles as one CSS grid.

    `items` is a sequence of ``(label, value)`` pairs, rendered in order.
    Below desktop the grid re-flows itself — three up on tablet, two on a
    phone — so no caller has to keep a column count in sync with the number
    of tiles.

    `weights` optionally gives the desktop column proportions, one number per
    item, and means exactly what the same list meant to `st.columns`:
    ``[1, 1.4, 1, 1, 1]`` gives the second tile 1.4x the width of its
    neighbours. Omit it and every tile is equal. It exists so converting a
    weighted `st.columns` row does not silently re-proportion the desktop
    layout, which is meant to come through this work unchanged.

    Scope note, because the plan this came from got it wrong: it claimed
    `st.metric` was kept "wherever a delta is shown". No view passes `delta=`
    at all. What is actually left on `st.metric` is `professionals.py`'s row,
    which is two metrics beside a *button* rather than a metric row, and the
    single FinOps tile — neither is a grid of tiles.
    """
    direction = T.get("dir", "ltr")
    items = list(items)

    tiles = "".join(
        f'<div class="metric-tile">'
        f'<div class="metric-tile-label">{html.escape(str(label))}</div>'
        f'<div class="metric-tile-value">{html.escape(str(value))}</div>'
        f"</div>"
        for label, value in items
    )

    style = ""
    if weights:
        if len(weights) != len(items):
            raise ValueError(
                f"weights has {len(weights)} entries for {len(items)} items"
            )
        # Built from floats rather than interpolated as a caller-supplied CSS
        # string: there is then no way for a track list to carry anything but
        # numbers, whatever a future caller derives them from. The narrower
        # breakpoints override this with `!important`, which they need because
        # an inline declaration outranks any selector.
        tracks = " ".join(f"{float(w):g}fr" for w in weights)
        style = f' style="grid-template-columns: {tracks};"'

    # `dir` is on the element rather than inherited, for the same reason the
    # Kanban board sets it (PRO-46): tile order must not depend on an
    # ancestor's direction.
    return f'<div class="metric-grid" dir="{direction}"{style}>{tiles}</div>'
