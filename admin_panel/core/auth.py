from functools import partial
import streamlit as st
import os
import time
import hashlib
import hmac
import secrets
import extra_streamlit_components as stx
import bcrypt
import redis as _sync_redis
from datetime import datetime, timedelta, timezone
from pymongo import MongoClient
from app.core.logger import logger
from app.core.config import settings
from admin_panel.core.audit_queries import write_audit_entry
from admin_panel.core.config import TRANS
from admin_panel.core.rbac import AdminRole
from admin_panel.ui.components import mark_login_page
import certifi

# --- Login brute-force lockout (sync Redis, fail-open) ---
# 5 failed attempts within 15 min → login locked for the remainder of that window.
# If Redis is unreachable we log and allow login (same fail-open policy as
# app/services/security_service.py) — admin panel availability beats lockout.
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 900  # 15 minutes

# PRO-267: the per-username counter is keyed on what the attacker types, so
# rotating usernames gave every guess a fresh counter. Every failure also counts
# against this one key, which nobody can vary. It is higher than the per-user
# limit so one mistyping operator never locks the others out, and it trips for
# everyone at once: during a spray an admin waits out the window too, which is
# the price of a key the attacker cannot rotate.
GLOBAL_LOCKOUT_IDENTIFIER = "__all_logins__"
GLOBAL_MAX_FAILED_ATTEMPTS = 30

try:
    _redis_url = (
        settings.REDIS_URL.get_secret_value()
        if settings.REDIS_URL
        else f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB}"
    )
    _rate_redis = _sync_redis.from_url(
        _redis_url, decode_responses=True, socket_connect_timeout=2
    )
except Exception as e:
    logger.warning(f"Admin rate limiter: Redis init failed, lockout disabled: {e}")
    _rate_redis = None


# PRO-252: every session expiry is aware UTC. The `admin_sessions.expiry` TTL
# index (expireAfterSeconds: 0) is evaluated by Mongo in UTC, and pymongo reads
# a stored datetime back as naive UTC; a naive local `datetime.now()` was right
# only while the container clock ran in UTC.
SESSION_LIFETIME = timedelta(days=7)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    """An expiry as aware UTC. A naive value is UTC: that is how pymongo
    returns every stored datetime, and a session written before PRO-252 was
    naive container time, which on Railway (no TZ set) is UTC too."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _session_live(session: dict | None, now: datetime | None = None) -> bool:
    return bool(session) and _as_utc(session["expiry"]) > (now or _utcnow())


def _lockout_key(identifier: str) -> str:
    return f"admin_lockout:{identifier}"


def _is_locked(identifier: str, limit: int = MAX_FAILED_ATTEMPTS) -> tuple[bool, int]:
    """Returns (locked, seconds_remaining). Fail-open on Redis errors."""
    if _rate_redis is None:
        return (False, 0)
    try:
        key = _lockout_key(identifier)
        count = _rate_redis.get(key)
        if count is None:
            return (False, 0)
        if int(count) >= limit:
            ttl = _rate_redis.ttl(key)
            return (True, max(0, int(ttl)))
        return (False, 0)
    except Exception as e:
        logger.warning(f"Admin lockout check failed (fail-open): {e}")
        return (False, 0)


def _record_failed_attempt(identifier: str) -> None:
    if _rate_redis is None:
        return
    try:
        key = _lockout_key(identifier)
        count = _rate_redis.incr(key)
        if count == 1:
            _rate_redis.expire(key, LOCKOUT_SECONDS)
    except Exception as e:
        logger.warning(f"Admin lockout increment failed: {e}")


def login_locked(identifier: str) -> tuple[bool, int]:
    """Locked out on the typed username's counter or on the global one (PRO-267)."""
    for key, limit in (
        (identifier, MAX_FAILED_ATTEMPTS),
        (GLOBAL_LOCKOUT_IDENTIFIER, GLOBAL_MAX_FAILED_ATTEMPTS),
    ):
        locked, seconds_left = _is_locked(key, limit)
        if locked:
            return (True, seconds_left)
    return (False, 0)


def record_failed_login(identifier: str) -> None:
    """Count a failure on the typed username and on the key nobody can vary."""
    _record_failed_attempt(identifier)
    _record_failed_attempt(GLOBAL_LOCKOUT_IDENTIFIER)


def _reset_attempts(identifier: str) -> None:
    if _rate_redis is None:
        return
    try:
        _rate_redis.delete(_lockout_key(identifier))
    except Exception as e:
        logger.warning(f"Admin lockout reset failed: {e}")


# MongoDB-backed session store — survives worker/Streamlit restarts
# Falls back to in-memory if DB is unavailable
_active_sessions: dict[str, dict] = {}  # local cache to avoid a DB hit on every rerun

# Sync MongoDB client for admin panel (Streamlit is synchronous)
_mongo_uri = settings.MONGO_URI.get_secret_value()  # PRO-94: SecretStr
_ca = certifi.where() if "+srv" in _mongo_uri else None
_kwargs = {"tlsCAFile": _ca} if _ca else {}
from pymongo import uri_parser as _uri_parser

_sync_client = MongoClient(_mongo_uri, **_kwargs)
_sync_db = _sync_client[_uri_parser.parse_uri(_mongo_uri).get("database") or "proli_db"]
_admins_col = _sync_db.admins
_audit_col = _sync_db.audit_log
_sessions_col = _sync_db.admin_sessions


def _log_audit_sync(username: str, action: str, details: dict | None = None):
    """Synchronous audit log for admin panel actions.

    The insert itself lives in ``admin_panel/core/audit_queries`` (PRO-142)
    so it can be tested against mongomock; this keeps the collection handle
    and the call-site-facing name. Fail-open behaviour is documented there.

    Returns whether the entry was written, so a call site that cares can
    distinguish "logged" from "lost". No current caller checks.
    """
    return write_audit_entry(_audit_col, username, action, details)


def log_audit(action: str, details: dict | None = None):
    """Log an audit event for the current session user."""
    username = st.session_state.get("admin_username", "unknown")
    _log_audit_sync(username, action, details)


def audit_as(username: str):
    """An audit writer bound to ``username``, for use during a send.

    ``log_audit`` reads ``st.session_state``, which is a yield point: with a rerun
    queued it raises before writing. Read the username first, outside the send.
    """
    return partial(_log_audit_sync, username)


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
    _admins_col.insert_one(
        {
            "username": username,
            "password_hash": make_hash(password),
            "role": role,
            "created_at": datetime.now(timezone.utc),
        }
    )
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
        {"username": username}, {"$set": {"role": new_role}}
    )
    return result.modified_count > 0


def _authenticate_admin(username: str, password: str) -> dict | None:
    """Authenticate against admins collection. Returns admin doc or None."""
    admin = _admins_col.find_one({"username": username})
    if admin and check_hash(password, admin["password_hash"]):
        return admin
    return None


def authenticate(username: str, password: str, has_db_admins: bool) -> dict | None:
    """Who these credentials log in as, or None.

    PRO-267: the env password is the bootstrap path and nothing else. Once the
    ``admins`` collection has anyone in it, only an account there can log in;
    before, a failed DB login fell through to the env password for *any*
    username, and a correct guess was owner.
    """
    if has_db_admins:
        return _authenticate_admin(username, password) if username else None
    return _authenticate_env(password)


def _authenticate_env(password: str) -> dict | None:
    """Bootstrap only: authenticate using env var while no DB admin exists."""
    admin_hash = os.getenv("ADMIN_PASSWORD_HASH")
    admin_plain = os.getenv("ADMIN_PASSWORD")

    if admin_hash:
        try:
            if bcrypt.checkpw(password.encode(), admin_hash.encode()):
                return {"username": "admin", "role": AdminRole.OWNER}
        except Exception:
            pass
    elif admin_plain and hmac.compare_digest(password.encode(), admin_plain.encode()):
        return {"username": "admin", "role": AdminRole.OWNER}

    return None


# --- Cookie Manager ---
def get_manager():
    if "cookie_manager" not in st.session_state:
        st.session_state.cookie_manager = stx.CookieManager(key="proli_auth_manager")
    return st.session_state.cookie_manager


def check_password(cookies):
    cookie_manager = get_manager()

    # PRO-61: Hebrew is the default — the operator is a Hebrew speaker, and
    # an EN default rendered the login screen and every fresh session LTR.
    saved_lang = cookies.get("proli_lang", "HE")
    T_auth = TRANS.get(saved_lang, TRANS["HE"])

    cookie_token = cookies.get("proli_auth_token")

    # Check if any admins exist in DB
    has_db_admins = _admins_col.count_documents({}) > 0

    admin_hash = os.getenv("ADMIN_PASSWORD_HASH")
    admin_plain = os.getenv("ADMIN_PASSWORD")

    if not has_db_admins and not admin_hash and not admin_plain:
        st.error(T_auth.get("fatal_no_admin", "FATAL: No admin accounts configured."))
        st.stop()

    if not has_db_admins and not admin_hash:
        st.warning(
            T_auth.get("plain_password_warning", "Using plain-text ADMIN_PASSWORD.")
        )

    # Check if we are in the process of logging out
    if st.session_state.get("logout_pending"):
        del st.session_state["logout_pending"]
        st.session_state["authenticated"] = False
    else:
        # 1. Session Cache Check
        if st.session_state.get("authenticated", False):
            return True

        # 2. Cookie Check — validate against persisted session tokens
        if cookie_token:
            _cleanup_expired_sessions()
            session = _active_sessions.get(cookie_token) or _load_session(cookie_token)
            if _session_live(session):
                _active_sessions[cookie_token] = session  # warm local cache
                st.session_state["authenticated"] = True
                st.session_state["admin_username"] = session["username"]
                st.session_state["admin_role"] = session["role"]
                return True

    # --- Login Screen ---
    # Centred by a marker plus one CSS rule rather than by a `[1, 2, 1]`
    # column row. Two reasons, both measured. Below 640px every column row
    # stacks, so the two empty side columns became empty *rows* and the form
    # was left at full width under a band of nothing. And the obvious
    # alternative — opening a `<div class="login-container">` in one
    # `st.markdown` and closing it in another — does not wrap anything:
    # Streamlit parses each markdown call on its own, so the browser closes
    # the div immediately and the widgets that follow are its siblings. The
    # div measured 400px wide, empty, with the form full-width beside it.
    mark_login_page()
    st.title("🔐 Proli Admin")
    st.markdown(f"### {T_auth['welcome_message']}")

    with st.form("login_form"):
        # Show username field if DB admins exist
        if has_db_admins:
            username = st.text_input(
                T_auth.get("admin_username_label", "Username"), placeholder="admin"
            )
        else:
            username = ""

        password = st.text_input(
            T_auth["admin_password_label"],
            type="password",
            placeholder=T_auth["admin_password_placeholder"],
        )
        remember_me = st.checkbox(T_auth["remember_me"])
        submitted = st.form_submit_button(T_auth["login_button"], type="primary")

        if submitted:
            identifier = username or "env"

            # Brute-force guard: block further attempts after MAX_FAILED_ATTEMPTS
            locked, seconds_left = login_locked(identifier)
            if locked:
                minutes_left = max(1, seconds_left // 60)
                logger.warning(
                    f"Admin login locked out: {identifier} ({seconds_left}s remaining)"
                )
                st.error(T_auth["login_locked"].replace("{minutes}", str(minutes_left)))
            else:
                auth_result = authenticate(username, password, has_db_admins)

                if auth_result:
                    admin_username = auth_result.get("username", "admin")
                    admin_role = auth_result.get("role", AdminRole.OWNER)

                    _reset_attempts(identifier)

                    logger.info(f"Admin login: {admin_username} (role: {admin_role})")
                    st.session_state["authenticated"] = True
                    st.session_state["admin_username"] = admin_username
                    st.session_state["admin_role"] = admin_role

                    _log_audit_sync(admin_username, "login")

                    if remember_me:
                        secure_token = secrets.token_hex(32)
                        expires = _utcnow() + SESSION_LIFETIME
                        session_data = {
                            "expiry": expires,
                            "username": admin_username,
                            "role": admin_role,
                        }
                        _active_sessions[secure_token] = session_data
                        _persist_session(secure_token, session_data)
                        # PRO-275: Secure, so the token never crosses plain
                        # HTTP. It cannot be HttpOnly — the component sets it
                        # from JavaScript — which is why the panel escapes
                        # every stored string it renders as HTML.
                        cookie_manager.set(
                            "proli_auth_token",
                            secure_token,
                            expires_at=expires,
                            secure=True,
                        )

                    # No `st.success` + `time.sleep(1)`: the rerun discards
                    # the message before it paints, so the second was pure
                    # latency on the one interaction every session starts with.
                    # The dashboard appearing is the confirmation.
                    st.rerun()
                else:
                    record_failed_login(identifier)
                    logger.warning(f"Failed admin login attempt (user: {identifier})")
                    st.error(T_auth["wrong_password"])

    return False


def logout(cookie_manager, T):
    # Plain `st.button`, not `st.sidebar.button`: `main.py` calls this inside
    # the sidebar's brand/logout column, and an explicit `st.sidebar.*` call
    # appends to the sidebar root instead — the column stayed empty and the
    # button rendered full-width under the brand.
    if st.button(T["disconnect"]):
        T_logout = TRANS.get(st.session_state.get("lang_code", "HE"), TRANS["HE"])
        st.toast(T_logout.get("disconnecting", "Disconnecting..."))

        username = st.session_state.get("admin_username", "unknown")
        _log_audit_sync(username, "logout")

        try:
            cookies = cookie_manager.get_all()
            token = cookies.get("proli_auth_token") if cookies else None
            if token:
                _active_sessions.pop(token, None)
                _delete_session(token)
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


def _token_key(token: str) -> str:
    """What ``admin_sessions._token`` holds: the token's SHA-256, never the token.

    PRO-275: the raw token is a bearer credential, so anyone who can read that
    collection, or a backup of it, could replay any live session. A hash still
    finds the session for the cookie that carries the token, and is useless
    without it. Sessions stored raw before this simply stop matching and expire.
    """
    return hashlib.sha256(token.encode()).hexdigest()


def _persist_session(token: str, session_data: dict):
    """Persist a session to MongoDB so it survives restarts."""
    key = _token_key(token)
    try:
        _sessions_col.update_one(
            {"_token": key}, {"$set": {"_token": key, **session_data}}, upsert=True
        )
    except Exception as e:
        logger.warning(f"Failed to persist admin session: {e}")


def _load_session(token: str) -> dict | None:
    """Load a session from MongoDB (used when local cache is cold after restart)."""
    try:
        doc = _sessions_col.find_one({"_token": _token_key(token)})
        if doc:
            return {
                "expiry": _as_utc(doc["expiry"]),
                "username": doc["username"],
                "role": doc["role"],
            }
    except Exception as e:
        logger.warning(f"Failed to load admin session: {e}")
    return None


def _delete_session(token: str):
    """Remove a session from MongoDB on logout."""
    try:
        _sessions_col.delete_one({"_token": _token_key(token)})
    except Exception as e:
        logger.warning(f"Failed to delete admin session: {e}")


def _cleanup_expired_sessions():
    now = _utcnow()
    # Clean local cache
    expired = [
        t for t, s in list(_active_sessions.items()) if not _session_live(s, now)
    ]
    for t in expired:
        del _active_sessions[t]
    # Clean MongoDB
    try:
        _sessions_col.delete_many({"expiry": {"$lte": now}})
    except Exception:
        pass
