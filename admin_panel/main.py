import sys
import os
import streamlit as st
from datetime import datetime, timedelta
import time

# Add project root to path
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

from admin_panel.core.config import TRANS
from admin_panel.ui.components import load_css
from admin_panel.core.auth import check_password, logout, get_manager

from admin_panel.views.home import view_leads_dashboard
from admin_panel.views.professionals import view_professionals
from admin_panel.views.schedule import view_schedule_editor
from admin_panel.views.settings import view_system_settings
from admin_panel.views.analytics import view_analytics

# --- Page Config ---
st.set_page_config(
    page_title="Proli Admin",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

cookie_manager = get_manager()
cookies = cookie_manager.get_all()

# --- Authentication ---
if not check_password(cookies):
    st.stop()

# --- Language Logic ---
if 'lang_code' not in st.session_state:
    st.session_state.lang_code = cookies.get("proli_lang", "EN")

T = TRANS[st.session_state.lang_code]
load_css(st.session_state.lang_code, T)

# --- Sidebar ---
with st.sidebar:
    # Top row: Branding + Logout
    c_brand, c_logout = st.columns([3, 1])
    with c_brand:
        st.title("⚡ Proli")
    with c_logout:
        st.markdown("")
        logout(cookie_manager, T)

    # User info
    username = st.session_state.get("admin_username", "")
    role = st.session_state.get("admin_role", "")
    if username:
        st.caption(f"{username} · {role}")

    st.markdown("")

    # Language Selector (compact)
    lang_options = ["HE", "EN"]
    try:
        default_index = lang_options.index(st.session_state.lang_code)
    except ValueError:
        default_index = 0

    selected_lang = st.selectbox(
        T.get("lang_label", "Language / שפה"),
        lang_options,
        index=default_index,
        key="lang_select"
    )

    if selected_lang != st.session_state.lang_code:
        st.session_state.lang_code = selected_lang
        expires = datetime.now() + timedelta(days=365)
        cookie_manager.set("proli_lang", selected_lang, expires_at=expires)
        time.sleep(0.05)
        st.rerun()

    st.divider()

    # --- Navigation ---
    page_options = [
        T["nav_dashboard"],
        T["nav_professionals"],
        T["nav_schedule"],
        T.get("nav_analytics", "Analytics"),
        T["nav_settings"]
    ]

    if "current_page" not in st.session_state:
        st.session_state.current_page = page_options[0]

    if st.session_state.current_page not in page_options:
        st.session_state.current_page = page_options[0]

    def on_nav_change():
        if "nav_radio" in st.session_state:
            st.session_state.current_page = st.session_state.nav_radio

    page = st.radio(
        T["nav_title"],
        page_options,
        index=page_options.index(st.session_state.current_page),
        key="nav_radio",
        on_change=on_nav_change,
        label_visibility="collapsed"
    )

    st.divider()

    # --- Auto-Refresh Toggle ---
    auto_refresh = st.toggle(
        T.get("auto_refresh", "Auto-refresh"),
        value=st.session_state.get("auto_refresh", False),
        key="auto_refresh_toggle"
    )

    if auto_refresh:
        refresh_interval = st.select_slider(
            T.get("refresh_interval", "Interval"),
            options=[15, 30, 60, 120],
            value=st.session_state.get("refresh_interval_val", 30),
            format_func=lambda x: f"{x}s",
            key="refresh_interval_slider"
        )
        st.session_state["refresh_interval_val"] = refresh_interval

        # Show indicator
        st.markdown("""
            <div class="refresh-indicator">
                <div class="refresh-dot"></div>
                <span>Live</span>
            </div>
        """, unsafe_allow_html=True)



# --- Page Rendering ---
current_selection = st.session_state.get("current_page", page)

if current_selection == T["nav_dashboard"]:
    view_leads_dashboard(T)
elif current_selection == T["nav_professionals"]:
    view_professionals(T)
elif current_selection == T["nav_schedule"]:
    view_schedule_editor(T)
elif current_selection == T.get("nav_analytics", "Analytics"):
    view_analytics(T)
elif current_selection == T["nav_settings"]:
    view_system_settings(T)

# --- Auto-Refresh Logic ---
if st.session_state.get("auto_refresh_toggle", False):
    interval = st.session_state.get("refresh_interval_val", 30)
    time.sleep(interval)
    st.rerun()
