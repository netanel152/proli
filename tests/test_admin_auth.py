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
