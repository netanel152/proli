import pytest
from admin_panel.core import auth

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
