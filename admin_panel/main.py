import sys
import os
import streamlit as st
from datetime import datetime, timedelta

# Add project root to path
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

from admin_panel.core.config import TRANS
from admin_panel.ui.components import load_css
import time
from admin_panel.core.auth import check_password, logout, get_manager

# Import new page views
from admin_panel.views.home import view_leads_dashboard
from admin_panel.views.professionals import view_professionals
from admin_panel.views.schedule import view_schedule_editor
from admin_panel.views.settings import view_system_settings

# --- Page Config ---
st.set_page_config(
    page_title="Fixi Admin",
    page_icon="🛠️",
    layout="wide",
    initial_sidebar_state="expanded"
)

cookie_manager = get_manager()
cookies = cookie_manager.get_all()

# --- Authentication ---
if not check_password(cookies):
    st.stop()

# --- Main App & Language Logic ---
# Use session state as the source of truth for the current run
if 'lang_code' not in st.session_state:
    st.session_state.lang_code = cookies.get("fixi_lang", "EN")

T = TRANS[st.session_state.lang_code]
load_css(st.session_state.lang_code, T)

with st.sidebar:
    st.title("Fixi Admin")
    
    # Language Selector
    lang_options = ["HE", "EN"]
    try:
        default_index = lang_options.index(st.session_state.lang_code)
    except ValueError:
        default_index = 0
    
    selected_lang = st.selectbox(
        "Language / שפה", 
        lang_options, 
        index=default_index
    )
    
    # If selection changes, update state, set cookie, and rerun
    if selected_lang != st.session_state.lang_code:
        st.session_state.lang_code = selected_lang
        expires = datetime.now() + timedelta(days=365)
        cookie_manager.set("fixi_lang", selected_lang, expires_at=expires)
        time.sleep(0.05) # Small delay to help ensure cookie is set
        st.rerun()

    st.divider()
    
    # --- Navigation ---
    page_options = [T["nav_dashboard"], T["nav_professionals"], T["nav_schedule"], T["nav_settings"]]
    
    # Initialize navigation state if not present or invalid
    if "current_page" not in st.session_state:
        st.session_state.current_page = page_options[0]
    
    # Ensure current page is valid (handle language switch)
    if st.session_state.current_page not in page_options:
         # Try to map by index if possible, else default to 0
         st.session_state.current_page = page_options[0]

    # Callback to update state
    def on_nav_change():
        st.session_state.current_page = st.session_state.nav_radio

    page = st.radio(
        T["nav_title"], 
        page_options, 
        index=page_options.index(st.session_state.current_page),
        key="nav_radio",
        on_change=on_nav_change
    )
    
    st.divider()
    logout(cookie_manager, T)

# --- Page Rendering ---
# Use session state or the widget value (they should be synced)
current_selection = st.session_state.get("current_page", page)

if current_selection == T["nav_dashboard"]:
    view_leads_dashboard(T)
elif current_selection == T["nav_professionals"]:
    view_professionals(T)
elif current_selection == T["nav_schedule"]:
    view_schedule_editor(T)
elif current_selection == T["nav_settings"]:
    view_system_settings(T)