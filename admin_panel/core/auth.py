import streamlit as st
import os
import time
import secrets
import extra_streamlit_components as stx
import bcrypt
from datetime import datetime, timedelta
from pymongo import MongoClient
from app.core.logger import logger
from app.core.config import settings
from admin_panel.core.config import TRANS
from admin_panel.core.rbac import AdminRole
import certifi

# In-memory session token store: {token: {"expiry": datetime, "username": str, "role": str}}
_active_sessions: dict[str, dict] = {}

# Sync MongoDB client for admin panel (Streamlit is synchronous)
_ca = certifi.where() if "+srv" in settings.MONGO_URI else None
_kwargs = {"tlsCAFile": _ca} if _ca else {}
_sync_client = MongoClient(settings.MONGO_URI, **_kwargs)
_sync_db = _sync_client.proli_db
_admins_col = _sync_db.admins
_audit_col = _sync_db.audit_log


def _log_audit_sync(username: str, action: str, details: dict | None = None):
    """Synchronous audit log for admin panel actions."""
    try:
        _audit_col.insert_one({
            "admin_user": username,
            "action": action,
            "details": details or {},
            "timestamp": datetime.utcnow(),
        })
    except Exception as e:
        logger.error(f"Audit log write failed: {e}")


def log_audit(action: str, details: dict | None = None):
    """Log an audit event for the current session user."""
    username = st.session_state.get("admin_username", "unknown")
    _log_audit_sync(username, action, details)


def get_current_role() -> str:
    """Get the role of the currently authenticated admin."""
    return st.session_state.get("admin_role", AdminRole.VIEWER)


def get_current_username() -> str:
    """Get the username of the currently authenticated admin."""
    return st.session_state.get("admin_username", "unknown")


# --- Security: Bcrypt Hash ---
def make_hash(password):
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def check_hash(password, hashed):
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


# --- Admin CRUD (sync for Streamlit) ---

def create_admin(username: str, password: str, role: str) -> bool:
    """Create a new admin user."""
    if _admins_col.find_one({"username": username}):
        return False
    _admins_col.insert_one({
        "username": username,
        "password_hash": make_hash(password),
        "role": role,
        "created_at": datetime.utcnow(),
    })
    return True


def delete_admin(username: str) -> bool:
    """Delete an admin user."""
    result = _admins_col.delete_one({"username": username})
    return result.deleted_count > 0


def list_admins() -> list:
    """List all admin users (without password hashes)."""
    return list(_admins_col.find({}, {"password_hash": 0}))


def update_admin_role(username: str, new_role: str) -> bool:
    """Update an admin's role."""
    result = _admins_col.update_one(
        {"username": username},
        {"$set": {"role": new_role}}
    )
    return result.modified_count > 0


def _authenticate_admin(username: str, password: str) -> dict | None:
    """Authenticate against admins collection. Returns admin doc or None."""
    admin = _admins_col.find_one({"username": username})
    if admin and check_hash(password, admin["password_hash"]):
        return admin
    return None


def _authenticate_env(password: str) -> dict | None:
    """Fallback: authenticate using env var (bootstrap mode)."""
    admin_hash = os.getenv("ADMIN_PASSWORD_HASH")
    admin_plain = os.getenv("ADMIN_PASSWORD")

    if admin_hash:
        try:
            if bcrypt.checkpw(password.encode(), admin_hash.encode()):
                return {"username": "admin", "role": AdminRole.OWNER}
        except Exception:
            pass
    elif admin_plain and password == admin_plain:
        return {"username": "admin", "role": AdminRole.OWNER}

    return None


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

    # Check if any admins exist in DB
    has_db_admins = _admins_col.count_documents({}) > 0

    admin_hash = os.getenv("ADMIN_PASSWORD_HASH")
    admin_plain = os.getenv("ADMIN_PASSWORD")

    if not has_db_admins and not admin_hash and not admin_plain:
        st.error("FATAL: No admin accounts configured. Set ADMIN_PASSWORD_HASH or create admins in database.")
        st.stop()

    if not has_db_admins and not admin_hash:
        st.warning("⚠️ Using plain-text ADMIN_PASSWORD. Generate a hash with 'scripts/generate_admin_hash.py'.")

    # Check if we are in the process of logging out
    if st.session_state.get("logout_pending"):
        del st.session_state["logout_pending"]
        st.session_state["authenticated"] = False
    else:
        # 1. Session Cache Check
        if st.session_state.get("authenticated", False):
            return True

        # 2. Cookie Check — validate against active session tokens
        if cookie_token:
            _cleanup_expired_sessions()
            session = _active_sessions.get(cookie_token)
            if session and session["expiry"] > datetime.now():
                st.session_state["authenticated"] = True
                st.session_state["admin_username"] = session["username"]
                st.session_state["admin_role"] = session["role"]
                return True

    # --- Login Screen ---
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("<br><br>", unsafe_allow_html=True)
        st.title("🔐 Proli Admin")
        st.markdown(f"### {T_auth['welcome_message']}")

        with st.form("login_form"):
            # Show username field if DB admins exist
            if has_db_admins:
                username = st.text_input(T_auth.get("admin_username_label", "Username"), placeholder="admin")
            else:
                username = ""

            password = st.text_input(T_auth["admin_password_label"], type="password", placeholder=T_auth["admin_password_placeholder"])
            remember_me = st.checkbox(T_auth["remember_me"])
            submitted = st.form_submit_button(T_auth["login_button"], type="primary")

            if submitted:
                auth_result = None

                # Try DB auth first if admins exist
                if has_db_admins and username:
                    auth_result = _authenticate_admin(username, password)

                # Fallback to env var auth
                if not auth_result:
                    auth_result = _authenticate_env(password)

                if auth_result:
                    admin_username = auth_result.get("username", "admin")
                    admin_role = auth_result.get("role", AdminRole.OWNER)

                    logger.info(f"Admin login: {admin_username} (role: {admin_role})")
                    st.session_state["authenticated"] = True
                    st.session_state["admin_username"] = admin_username
                    st.session_state["admin_role"] = admin_role

                    _log_audit_sync(admin_username, "login")

                    if remember_me:
                        secure_token = secrets.token_hex(32)
                        expires = datetime.now() + timedelta(days=7)
                        _active_sessions[secure_token] = {
                            "expiry": expires,
                            "username": admin_username,
                            "role": admin_role,
                        }
                        cookie_manager.set("proli_auth_token", secure_token, expires_at=expires)

                    st.success("Connected!")
                    time.sleep(1)
                    st.rerun()
                else:
                    logger.warning(f"Failed admin login attempt (user: {username or 'env'})")
                    st.error(T_auth["wrong_password"])

    return False


def logout(cookie_manager, T):
    if st.sidebar.button(T["disconnect"]):
        st.toast("Disconnecting...", icon="👋")

        username = st.session_state.get("admin_username", "unknown")
        _log_audit_sync(username, "logout")

        try:
            cookies = cookie_manager.get_all()
            token = cookies.get("proli_auth_token") if cookies else None
            if token and token in _active_sessions:
                del _active_sessions[token]
        except Exception:
            pass

        st.session_state["authenticated"] = False
        st.session_state["admin_username"] = None
        st.session_state["admin_role"] = None
        st.session_state["logout_pending"] = True

        try:
            cookie_manager.delete("proli_auth_token")
        except Exception:
            pass

        time.sleep(0.5)
        st.rerun()


def _cleanup_expired_sessions():
    now = datetime.now()
    expired = [t for t, s in _active_sessions.items() if s["expiry"] <= now]
    for t in expired:
        del _active_sessions[t]
