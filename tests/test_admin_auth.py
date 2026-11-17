import pytest
from unittest.mock import MagicMock
from admin_panel.core import auth


# --- Password hashing / verification ---


def test_password_hashing():
    """
    Test that make_hash returns a valid bcrypt hash different from input.
    """
    password = "password123"
    hashed = auth.make_hash(password)

    assert isinstance(hashed, str)
    assert len(hashed) > 0
    assert hashed != password
    # Bcrypt hashes usually start with $2b$ or $2a$
    assert hashed.startswith("$2")


def test_password_verification_success():
    """
    Test that check_hash correctly verifies a valid password.
    """
    password = "secure_password"
    hashed = auth.make_hash(password)

    assert auth.check_hash(password, hashed) is True


def test_password_verification_failure():
    """
    Test that check_hash rejects an incorrect password.
    """
    password = "secure_password"
    hashed = auth.make_hash(password)

    assert auth.check_hash("wrong_password", hashed) is False


def test_check_hash_invalid_format():
    """
    Test edge case: checking against a non-hash string should return False (handled by exception).
    """
    assert auth.check_hash("password", "not_a_hash") is False


# --- Login brute-force lockout ---


class FakeRedis:
    """In-memory stand-in for sync redis with TTL bookkeeping."""

    def __init__(self):
        self.store: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    def get(self, key):
        val = self.store.get(key)
        return str(val) if val is not None else None

    def incr(self, key):
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    def expire(self, key, seconds):
        self.ttls[key] = seconds

    def ttl(self, key):
        return self.ttls.get(key, -1)

    def delete(self, key):
        self.store.pop(key, None)
        self.ttls.pop(key, None)


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(auth, "_rate_redis", fake)
    return fake


def test_lockout_not_triggered_below_threshold(fake_redis):
    for _ in range(auth.MAX_FAILED_ATTEMPTS - 1):
        auth._record_failed_attempt("alice")
    locked, _ = auth._is_locked("alice")
    assert locked is False


def test_lockout_triggered_at_threshold(fake_redis):
    for _ in range(auth.MAX_FAILED_ATTEMPTS):
        auth._record_failed_attempt("alice")
    locked, seconds_left = auth._is_locked("alice")
    assert locked is True
    assert seconds_left == auth.LOCKOUT_SECONDS


def test_lockout_ttl_set_on_first_attempt_only(fake_redis):
    auth._record_failed_attempt("alice")
    assert fake_redis.ttls["admin_lockout:alice"] == auth.LOCKOUT_SECONDS
    # Subsequent attempts must not reset the TTL — lockout is from the first failure
    fake_redis.ttls["admin_lockout:alice"] = 100  # simulate time passing
    auth._record_failed_attempt("alice")
    assert fake_redis.ttls["admin_lockout:alice"] == 100


def test_reset_attempts_clears_lockout(fake_redis):
    for _ in range(auth.MAX_FAILED_ATTEMPTS):
        auth._record_failed_attempt("alice")
    assert auth._is_locked("alice")[0] is True
    auth._reset_attempts("alice")
    assert auth._is_locked("alice") == (False, 0)


def test_lockout_fails_open_when_redis_unavailable(monkeypatch):
    monkeypatch.setattr(auth, "_rate_redis", None)
    # All helpers should no-op without raising
    auth._record_failed_attempt("bob")
    auth._reset_attempts("bob")
    assert auth._is_locked("bob") == (False, 0)


def test_lockout_fails_open_on_redis_exception(monkeypatch):
    broken = MagicMock()
    broken.get.side_effect = RuntimeError("redis down")
    broken.incr.side_effect = RuntimeError("redis down")
    broken.delete.side_effect = RuntimeError("redis down")
    monkeypatch.setattr(auth, "_rate_redis", broken)

    # Must not propagate; _is_locked returns unlocked
    assert auth._is_locked("bob") == (False, 0)
    auth._record_failed_attempt("bob")
    auth._reset_attempts("bob")


# --- PRO-252: session expiry is UTC, whatever the container clock says ---

import time as _time  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

import mongomock  # noqa: E402


@pytest.fixture
def israel_clock(monkeypatch):
    """Run the test with the process in Israel time (UTC+2/+3), the clock a
    non-Railway host would have. ``datetime.now()`` follows it; Mongo's TTL
    monitor does not."""
    monkeypatch.setenv("TZ", "Asia/Jerusalem")
    _time.tzset()
    assert datetime.now().hour != datetime.now(timezone.utc).replace(tzinfo=None).hour
    yield
    monkeypatch.undo()
    _time.tzset()


@pytest.fixture
def sessions(monkeypatch):
    col = mongomock.MongoClient().proli_test.admin_sessions
    monkeypatch.setattr(auth, "_sessions_col", col)
    monkeypatch.setattr(auth, "_active_sessions", {})
    return col


def test_new_session_expiry_is_seven_days_of_utc(israel_clock):
    expires = auth._utcnow() + auth.SESSION_LIFETIME
    assert expires.tzinfo is not None
    delta = expires - datetime.now(timezone.utc)
    assert timedelta(days=7) - timedelta(seconds=5) < delta <= timedelta(days=7)


def test_persisted_session_round_trips_through_mongo_as_utc(israel_clock, sessions):
    """Written aware, read back naive (pymongo's shape), compared aware: a
    session with an hour left is live, not two-three hours expired."""
    expiry = auth._utcnow() + timedelta(hours=1)
    auth._persist_session("tok", {"expiry": expiry, "username": "a", "role": "owner"})

    stored = sessions.find_one({"_token": auth._token_key("tok")})["expiry"]
    assert abs(stored.replace(tzinfo=timezone.utc) - expiry) < timedelta(seconds=1)

    loaded = auth._load_session("tok")
    assert loaded["expiry"].tzinfo is not None
    assert auth._session_live(loaded) is True


def test_a_naive_utc_expiry_from_mongo_is_judged_in_utc(israel_clock):
    """The bug: the old check compared Mongo's naive-UTC expiry against naive
    *local* now(), so in Israel time a session with an hour left read as
    already expired."""
    naive_utc_expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(
        tzinfo=None
    )
    assert not naive_utc_expiry > datetime.now()  # what the old code concluded
    assert auth._session_live({"expiry": naive_utc_expiry}) is True
    assert (
        auth._session_live({"expiry": naive_utc_expiry - timedelta(hours=2)}) is False
    )


def test_cleanup_drops_expired_and_keeps_live_sessions_in_utc(israel_clock, sessions):
    now = auth._utcnow()
    live = {"expiry": now + timedelta(minutes=30), "username": "a", "role": "owner"}
    dead = {"expiry": now - timedelta(minutes=30), "username": "b", "role": "owner"}
    legacy = {  # a pre-PRO-252 naive document, still within its lifetime
        "expiry": (now + timedelta(days=1)).replace(tzinfo=None),
        "username": "c",
        "role": "owner",
    }
    for token, data in (("live", live), ("dead", dead), ("legacy", legacy)):
        auth._active_sessions[token] = data
        auth._persist_session(token, data)

    auth._cleanup_expired_sessions()

    assert set(auth._active_sessions) == {"live", "legacy"}
    assert {d["_token"] for d in sessions.find()} == {
        auth._token_key("live"),
        auth._token_key("legacy"),
    }


def test_session_check_never_compares_against_naive_local_time():
    """Source pin: no argument-less ``datetime.now()`` call left in auth.py."""
    import ast
    import inspect

    bare = [
        node.lineno
        for node in ast.walk(ast.parse(inspect.getsource(auth)))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "now"
        and not node.args
        and not node.keywords
    ]
    assert bare == []


# --- PRO-267: the env password is bootstrap-only, and rotating names locks out ---


@pytest.fixture
def env_password(monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)
    monkeypatch.setenv("ADMIN_PASSWORD", "hunter2")
    monkeypatch.setattr(auth, "_authenticate_admin", lambda username, password: None)


def test_env_password_logs_in_while_no_db_admin_exists(env_password):
    assert auth.authenticate("", "hunter2", has_db_admins=False) == {
        "username": "admin",
        "role": auth.AdminRole.OWNER,
    }


def test_env_password_no_longer_works_once_db_admins_exist(env_password):
    for username in ("", "admin", "anything"):
        assert auth.authenticate(username, "hunter2", has_db_admins=True) is None


def test_db_admins_still_log_in_with_their_own_password(monkeypatch):
    doc = {"username": "dana", "role": "editor"}
    monkeypatch.setattr(
        auth,
        "_authenticate_admin",
        lambda username, password: doc if password == "right" else None,
    )

    assert auth.authenticate("dana", "right", has_db_admins=True) is doc
    assert auth.authenticate("dana", "wrong", has_db_admins=True) is None


def test_rotating_usernames_is_locked_out_by_the_global_counter(fake_redis):
    for n in range(auth.GLOBAL_MAX_FAILED_ATTEMPTS):
        assert auth.login_locked(f"a{n}") == (False, 0)
        auth.record_failed_login(f"a{n}")

    locked, _ = auth.login_locked("a-fresh-name")
    assert locked is True


def test_one_users_failures_do_not_lock_the_others_out(fake_redis):
    for _ in range(auth.MAX_FAILED_ATTEMPTS):
        auth.record_failed_login("dana")

    assert auth.login_locked("dana")[0] is True
    assert auth.login_locked("yossi") == (False, 0)


def test_plaintext_env_compare_is_constant_time():
    import inspect

    source = inspect.getsource(auth._authenticate_env)
    assert "compare_digest" in source
    assert "password == admin_plain" not in source


# --- PRO-275: tokens stored hashed, a Secure cookie, escaped pro text ---


def test_a_session_is_stored_under_the_tokens_hash_never_the_token(sessions):
    token = "a" * 64
    data = {
        "expiry": auth._utcnow() + timedelta(hours=1),
        "username": "a",
        "role": "owner",
    }

    auth._persist_session(token, data)

    stored = [d["_token"] for d in sessions.find()]
    assert stored == [auth._token_key(token)]
    assert token not in stored
    assert auth._load_session(token)["username"] == "a"
    # The stored value replayed as a cookie finds nothing.
    assert auth._load_session(stored[0]) is None

    auth._delete_session(token)
    assert sessions.count_documents({}) == 0


def test_the_auth_cookie_is_set_secure():
    import inspect

    source = inspect.getsource(auth.check_password)
    call = source[source.index("cookie_manager.set(") :]
    call = call[: call.index(")") + 1]
    assert "secure=True" in call


def test_pro_supplied_text_is_escaped_before_an_html_render():
    from pathlib import Path

    source = Path("admin_panel/views/professionals.py").read_text(encoding="utf-8")
    assert "html.escape(image_url" in source
    assert (
        source.count('html.escape(", ".join(map(str, p.get("service_areas"')
        + source.count("html.escape(', '.join(map(str, p.get('service_areas'")
        == 2
    )
