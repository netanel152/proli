import streamlit as st
from datetime import datetime, timedelta
from config import TRANS
from components import load_css
from views import view_dashboard, view_add_pro, view_settings, view_pros, view_schedule 
from auth import check_password, logout, get_manager
# --- הגדרות עמוד ---
st.set_page_config(
    page_title="Fixi Admin",
    page_icon="🛠️",
    layout="wide",
    initial_sidebar_state="expanded"
)

cookie_manager = get_manager()

# --- בדיקת אבטחה (לפני הכל) ---
if not check_password():
    st.stop()

# === מכאן והלאה: המשתמש מחובר ===

# --- סרגל צד ---
with st.sidebar:
    st.title("Fixi Admin")
    
    # טעינת שפה מקוקי
    cookies = cookie_manager.get_all(key="sidebar_cookies")
    default_lang_code = cookies.get("fixi_lang", "HE")
    lang_options = ["HE", "EN"]
    try:
        default_index = lang_options.index(default_lang_code)
    except ValueError:
        default_index = 0
        
    lang = st.selectbox("Language / שפה", lang_options, index=default_index)
    
    # שמירת שפה לקוקי אם השתנתה
    if lang != default_lang_code:
        expires = datetime.now() + timedelta(days=365)
        cookie_manager.set("fixi_lang", lang, expires_at=expires)
        st.rerun()

    T = TRANS[lang]
    load_css(lang, T)
    
    st.divider()    
    page = st.radio(T["nav_title"], [T["nav_dashboard"], T["nav_schedule"], T["nav_add_pro"], T["nav_settings"], T["nav_pros"]])
    st.divider()
    logout(cookie_manager, T)

# --- תוכן העמוד ---
if page == T["nav_dashboard"]: view_dashboard(T)
elif page == T["nav_schedule"]: view_schedule(T)
elif page == T["nav_add_pro"]: view_add_pro(T)
elif page == T["nav_settings"]: view_settings(T)
elif page == T["nav_pros"]: view_pros(T)