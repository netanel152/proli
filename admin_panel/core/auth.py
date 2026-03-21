import streamlit as st
import os
import time
import secrets
import extra_streamlit_components as stx
import bcrypt
from datetime import datetime, timedelta
from app.core.logger import logger
from admin_panel.core.config import TRANS

# In-memory session token store: {token: expiry_datetime}
_active_sessions: dict[str, datetime] = {}

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
        st.session_state.cookie_manager = stx.CookieManager(key="proli_auth_manager")
    return st.session_state.cookie_manager

def check_password(cookies):
    cookie_manager = get_manager()
    
    saved_lang = cookies.get("proli_lang", "EN")
    T_auth = TRANS.get(saved_lang, TRANS["EN"])

    cookie_token = cookies.get("proli_auth_token")
    
    # Secure Auth: Check for Hash first, then fallback to Legacy Plain Text
    admin_hash = os.getenv("ADMIN_PASSWORD_HASH")
    admin_plain = os.getenv("ADMIN_PASSWORD")

    if not admin_hash and not admin_plain:
        st.error("FATAL: Neither ADMIN_PASSWORD_HASH nor ADMIN_PASSWORD is set. Application cannot start.")
        st.stop()
    
    if not admin_hash:
        st.warning("⚠️ SECURITY WARNING: Using plain-text ADMIN_PASSWORD. Please generate a hash using 'scripts/generate_admin_hash.py' and set ADMIN_PASSWORD_HASH.")
    
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

        # 2. Cookie Check — validate against active session tokens
        if cookie_token:
            _cleanup_expired_sessions()
            if cookie_token in _active_sessions and _active_sessions[cookie_token] > datetime.now():
                st.session_state["authenticated"] = True
                return True

    # --- Login Screen ---
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("<br><br>", unsafe_allow_html=True)
        st.title("🔐 Proli Admin")
        st.markdown(f"### {T_auth['welcome_message']}")
        
        with st.form("login_form"):
            password = st.text_input(T_auth["admin_password_label"], type="password", placeholder=T_auth["admin_password_placeholder"])
            remember_me = st.checkbox(T_auth["remember_me"])
            submitted = st.form_submit_button(T_auth["login_button"], type="primary")
            
            if submitted:
                # Authenticate
                auth_success = False
                
                if admin_hash:
                    # Verify input against stored hash
                    try:
                        if bcrypt.checkpw(password.encode(), admin_hash.encode()):
                            auth_success = True
                    except Exception as e:
                        st.error(f"Error verifying password hash: {e}")
                elif admin_plain:
                    # Legacy plain text check
                    if password == admin_plain:
                        auth_success = True
                
                if auth_success:
                    logger.info(f"Admin login successful from IP (Streamlit session)")
                    st.session_state["authenticated"] = True
                    
                    if remember_me:
                        # Generate a random session token
                        secure_token = secrets.token_hex(32)
                        expires = datetime.now() + timedelta(days=7)
                        _active_sessions[secure_token] = expires
                        cookie_manager.set("proli_auth_token", secure_token, expires_at=expires)
                    
                    st.success("Connected!")
                    time.sleep(1)
                    st.rerun()
                else:
                    logger.warning("Failed admin login attempt.")
                    st.error(T_auth["wrong_password"])

    return False

def logout(cookie_manager, T):
    if st.sidebar.button(T["disconnect"]):
        st.toast("Disconnecting...", icon="👋")

        # Invalidate server-side session token
        try:
            cookies = cookie_manager.get_all()
            token = cookies.get("proli_auth_token") if cookies else None
            if token and token in _active_sessions:
                del _active_sessions[token]
        except:
            pass

        # Set flags to force logout state on next run
        st.session_state["authenticated"] = False
        st.session_state["logout_pending"] = True

        try:
            cookie_manager.delete("proli_auth_token")
        except:
            pass

        time.sleep(0.5)
        st.rerun()


def _cleanup_expired_sessions():
    """Remove expired tokens from the session store."""
    now = datetime.now()
    expired = [t for t, exp in _active_sessions.items() if exp <= now]
    for t in expired:
        del _active_sessions[t]
