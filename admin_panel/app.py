import streamlit as st
from config import TRANS
from components import load_css
from views import view_dashboard, view_add_pro, view_settings, view_pros

st.set_page_config(page_title="Fixi Admin", page_icon="🛠️", layout="wide", initial_sidebar_state="expanded")

with st.sidebar:
    st.title("Fixi Admin")
    lang = st.selectbox("Language / שפה", ["HE", "EN"])
    T = TRANS[lang]
    load_css(lang, T)
    st.divider()
    page = st.radio(T["nav_title"], [T["nav_dashboard"], T["nav_add_pro"], T["nav_settings"], T["nav_pros"]])

if page == T["nav_dashboard"]: view_dashboard(T)
elif page == T["nav_add_pro"]: view_add_pro(T)
elif page == T["nav_settings"]: view_settings(T)
elif page == T["nav_pros"]: view_pros(T)