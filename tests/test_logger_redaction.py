"""
Tests for app/core/logger.py log scrubbing:
  - mask_pii: Israeli phone masking (existing behavior, must stay intact)
  - redact_secrets (PRO-80): known secret values redacted wherever they appear
  - _pii_filter: the loguru sink filter applies both
"""

import app.core.logger as logmod
from app.core.logger import mask_pii, redact_secrets, _pii_filter


def test_mask_pii_unchanged():
    # Existing behavior must not regress: keep country code + 2, mask middle, keep last 3.
    assert mask_pii("972521234567") == "97252****567"


def test_redact_secrets_replaces_webhook_token_in_query(monkeypatch):
    # The exact leak PRO-80 fixes: uvicorn access log line with the token in the query string.
    monkeypatch.setattr(logmod, "_SECRET_VALUES", ["webhooktok456"])
    out = redact_secrets('POST /webhook?token=webhooktok456 HTTP/1.1" 200')
    assert "webhooktok456" not in out
    assert "***REDACTED***" in out


def test_redact_secrets_green_api_token_in_url_path(monkeypatch):
    # Value-based redaction also covers the Green API token sitting in a URL *path*
    # (e.g. an httpx exception string that reaches logger.error) — a query-only fix would miss it.
    monkeypatch.setattr(logmod, "_SECRET_VALUES", ["gtok789"])
    out = redact_secrets(
        "Failed to send: https://api.green-api.com/waInstance123/sendMessage/gtok789"
    )
    assert "gtok789" not in out
    assert "***REDACTED***" in out


def test_redact_secrets_noop_when_no_secrets(monkeypatch):
    monkeypatch.setattr(logmod, "_SECRET_VALUES", [])
    msg = "nothing sensitive here"
    assert redact_secrets(msg) == msg


def test_secret_values_has_no_empty_entries():
    # An unset/empty secret (e.g. WEBHOOK_TOKEN=None) must never enter the set —
    # otherwise "" would match every message and redact everything.
    assert all(v for v in logmod._SECRET_VALUES)


def test_pii_filter_masks_phone_and_redacts_secret(monkeypatch):
    # The real sink path: both scrubbers run, and the record is mutated in place.
    monkeypatch.setattr(logmod, "_SECRET_VALUES", ["seekret"])
    record = {"message": "call 972521234567 token=seekret"}
    assert _pii_filter(record) is True
    assert "seekret" not in record["message"]
    assert "***REDACTED***" in record["message"]
    assert "972521234567" not in record["message"]
    assert record["message"].startswith("call 97252****567")
