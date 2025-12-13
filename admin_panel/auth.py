import streamlit as st
import os
import time
import extra_streamlit_components as stx
from datetime import datetime, timedelta
import bcrypt
from config import TRANS

# --- Security: Bcrypt Hash ---
def make_hash(password):
    """Generates a salted bcrypt hash"""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def check_hash(password, hashed):
    """Verifies a password against a bcrypt hash"""
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False

# --- Cookie Manager ---
def get_manager():
    if "cookie_manager" not in st.session_state:
        st.session_state.cookie_manager = stx.CookieManager(key="fixi_auth_manager")
    return st.session_state.cookie_manager

def check_password(cookies):
    cookie_manager = get_manager()
    
    saved_lang = cookies.get("fixi_lang", "HE")
    T_auth = TRANS.get(saved_lang, TRANS["HE"])

    cookie_token = cookies.get("fixi_auth_token")
    
    # In production, ADMIN_PASSWORD should ideally be a stored hash.
    # Here we treat the Env var as the 'Source of Truth'.
    real_password = os.getenv("ADMIN_PASSWORD")

    if not real_password:
        st.error("FATAL: ADMIN_PASSWORD environment variable is not set. Application cannot start.")
        st.stop()
    
    # Check if we are in the process of logging out
    if st.session_state.get("logout_pending"):
        # Explicitly skip cookie check this run to show the login form
        # We perform the cleanup here to ensure next run acts normally
        del st.session_state["logout_pending"]
        
        # Ensure session authentication is false
        st.session_state["authenticated"] = False
    else:
        # 1. Session Cache Check
        if st.session_state.get("authenticated", False):
            return True

        # 2. Cookie Check (Secure)
        # The cookie contains a salted hash of the real password.
        if cookie_token and check_hash(real_password, cookie_token):
            st.session_state["authenticated"] = True
            return True

    # --- Login Screen ---
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("<br><br>", unsafe_allow_html=True)
        st.title("🔐 Fixi Admin")
        st.markdown(f"### {T_auth['welcome_message']}")
        
        with st.form("login_form"):
            password = st.text_input(T_auth["admin_password_label"], type="password", placeholder=T_auth["admin_password_placeholder"])
            remember_me = st.checkbox(T_auth["remember_me"])
            submitted = st.form_submit_button(T_auth["login_button"], type="primary")
            
            if submitted:
                # Direct comparison since Env is plain text
                if password == real_password:
                    st.session_state["authenticated"] = True
                    
                    if remember_me:
                        # Store a secure salted hash in the cookie
                        secure_token = make_hash(real_password)
                        expires = datetime.now() + timedelta(days=7)
                        cookie_manager.set("fixi_auth_token", secure_token, expires_at=expires)
                    
                    st.success("Connected!")
                    time.sleep(1)
                    st.rerun()
                else:
                    st.error(T_auth["wrong_password"])

    return False

def logout(cookie_manager, T):
    if st.sidebar.button(T["disconnect"]):
        st.toast("Disconnecting...", icon="👋")
        
        # Set flags to force logout state on next run
        st.session_state["authenticated"] = False
        st.session_state["logout_pending"] = True
        
        try:
            # Send command to frontend to delete cookie
            cookie_manager.delete("fixi_auth_token")
        except: pass
        
        time.sleep(0.5)
        st.rerun()
