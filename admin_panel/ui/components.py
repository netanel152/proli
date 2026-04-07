import streamlit as st
import html

# Status color mapping for Kanban and pills
STATUS_COLORS = {
    "new": {"bg": "#EFF6FF", "text": "#1D4ED8", "border": "#BFDBFE", "icon": "fiber_new"},
    "contacted": {"bg": "#FFF7ED", "text": "#C2410C", "border": "#FED7AA", "icon": "call"},
    "booked": {"bg": "#F0FDF4", "text": "#15803D", "border": "#BBF7D0", "icon": "event_available"},
    "completed": {"bg": "#ECFDF5", "text": "#047857", "border": "#A7F3D0", "icon": "check_circle"},
    "rejected": {"bg": "#FEF2F2", "text": "#B91C1C", "border": "#FECACA", "icon": "cancel"},
    "closed": {"bg": "#F5F3FF", "text": "#6D28D9", "border": "#DDD6FE", "icon": "lock"},
    "cancelled": {"bg": "#FDF2F8", "text": "#BE185D", "border": "#FBCFE8", "icon": "block"},
}

# Dark mode status colors
STATUS_COLORS_DARK = {
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
    opp_align = "left" if align == "right" else "right"
    border_side = 'left' if direction == 'rtl' else 'right'
    opp_border = 'right' if direction == 'rtl' else 'left'

    # Import Google Fonts and Material Symbols
    st.markdown("""
        <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=Heebo:wght@300;400;500;600;700;800&display=swap">
        <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Material+Symbols+Rounded:opsz,wght,FILL,GRAD@24,400,1,0" />
    """, unsafe_allow_html=True)

    # Generate status pill CSS
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
        """

    st.markdown(f"""
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

        /* Form Labels */
        label {{
            font-weight: 500 !important;
            font-size: 0.85rem !important;
            color: var(--text-secondary) !important;
            text-transform: uppercase;
            letter-spacing: 0.03em;
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
            width: 280px !important;
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
        }}

        section[data-testid="stSidebar"] .stRadio > div > label:hover {{
            background-color: var(--bg-hover);
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
            text-transform: uppercase;
            letter-spacing: 0.05em;
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
        .stButton button {{
            border-radius: var(--radius-sm);
            font-family: 'Inter', 'Heebo', sans-serif !important;
            font-weight: 600;
            padding: 8px 20px;
            font-size: 0.875rem !important;
            transition: var(--transition);
            border: 1px solid transparent;
            cursor: pointer;
            letter-spacing: 0.01em;
        }}

        .stButton button[kind="primary"] {{
            background: linear-gradient(135deg, var(--primary) 0%, var(--primary-hover) 100%);
            color: white;
            border: none;
            box-shadow: 0 2px 4px rgb(37 99 235 / 0.3);
        }}

        .stButton button[kind="primary"]:hover {{
            box-shadow: 0 4px 8px rgb(37 99 235 / 0.4);
            transform: translateY(-1px);
        }}

        .stButton button[kind="secondary"] {{
            background-color: var(--bg-card);
            border: 1px solid var(--border-color);
            color: var(--text-main);
        }}

        .stButton button[kind="secondary"]:hover {{
            background-color: var(--bg-hover);
            border-color: var(--text-muted);
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

        /* Regular HTML tables (non-Glide, e.g. st.dataframe fallback) */
        thead tr th {{
            background-color: var(--bg-secondary) !important;
            color: var(--text-secondary) !important;
            font-weight: 600 !important;
            font-size: 0.8rem !important;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            padding: 12px 16px !important;
            text-align: left !important;
            border-bottom: 2px solid var(--border-color) !important;
        }}

        tbody tr td {{
            font-size: 0.9rem !important;
            color: var(--text-main) !important;
            padding: 12px 16px !important;
            background-color: var(--bg-card) !important;
            text-align: left !important;
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

        .user-msg {{
            background-color: var(--chat-user-bg);
            color: var(--chat-user-text);
            align-self: {'flex-start' if direction == 'rtl' else 'flex-end'};
            border-bottom-{'right' if direction == 'rtl' else 'left'}-radius: 4px;
            margin-{'right' if direction == 'rtl' else 'left'}: auto;
            box-shadow: var(--shadow-sm);
        }}

        .bot-msg {{
            background-color: var(--chat-bot-bg);
            color: var(--chat-bot-text);
            align-self: {'flex-end' if direction == 'rtl' else 'flex-start'};
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
            flex-direction: {'row' if direction == 'ltr' else 'row-reverse'};
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
            text-transform: uppercase;
            letter-spacing: 0.04em;
            direction: {direction};
        }}

        .kanban-count {{
            background-color: rgba(0,0,0,0.08);
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

        /* ===== STATUS PILLS ===== */
        {status_pill_css}

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
        .stAlert {{
            border-radius: var(--radius-sm) !important;
            font-size: 0.875rem !important;
            border-width: 0 0 0 4px !important;
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

        /* ===== CHECKBOX TOGGLE STYLE ===== */
        .stCheckbox label span {{
            text-transform: none !important;
            letter-spacing: normal !important;
            font-size: 0.9rem !important;
            color: var(--text-main) !important;
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
        }}

        @keyframes pulse {{
            0%, 100% {{ opacity: 1; }}
            50% {{ opacity: 0.4; }}
        }}

    </style>
    """, unsafe_allow_html=True)


def render_chat_bubble(text, role, timestamp, T):
    is_user = role == 'user'
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
    label = T.get(status, status.capitalize())
    icon = colors.get("icon", "circle")
    return f'<span class="status-pill-{status}"><span class="material-symbols-rounded" style="font-size:0.9rem">{icon}</span> {label}</span>'


def render_kanban_card(lead, T):
    """Render a single Kanban card as HTML."""
    client = lead.get("client", "?")
    details = lead.get("details_summary", "")
    pro = lead.get("professional", T.get("unknown_pro", "Unassigned"))
    date = lead.get("date")
    date_str = date.strftime("%d/%m %H:%M") if date else ""

    # Truncate details
    if len(details) > 80:
        details = details[:77] + "..."

    safe_details = html.escape(details)
    safe_client = html.escape(str(client))
    safe_pro = html.escape(str(pro))

    return f"""
    <div class="kanban-card">
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
                {date_str}
            </div>
        </div>
    </div>
    """


def render_kanban_column(status, leads, T):
    """Render a full Kanban column with header and cards."""
    colors = STATUS_COLORS.get(status, STATUS_COLORS["new"])
    label = T.get(status, status.capitalize())
    count = len(leads)

    cards_html = "".join(render_kanban_card(l, T) for l in leads)

    if not leads:
        cards_html = f"""
        <div class="empty-state" style="padding: 1.5rem 0.5rem;">
            <span class="material-symbols-rounded" style="font-size: 1.5rem;">inbox</span>
            <div style="font-size: 0.8rem;">—</div>
        </div>
        """

    return f"""
    <div class="kanban-column">
        <div class="kanban-header" style="background-color: {colors['bg']}; color: {colors['text']}; border: 1px solid {colors['border']};">
            <span class="material-symbols-rounded" style="font-size:1rem">{colors['icon']}</span>
            {label}
            <span class="kanban-count">{count}</span>
        </div>
        {cards_html}
    </div>
    """
