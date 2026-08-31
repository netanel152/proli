"""
Tests for app/core/logger.py log scrubbing:
  - mask_pii: Israeli phone masking (existing behavior, must stay intact)
  - redact_secrets (PRO-80): known secret values redacted wherever they appear
  - mask_address (PRO-174): Hebrew street-address masking, city left intact
  - _pii_filter: the loguru sink filter applies all three scrubbers
"""

import pytest

import app.core.logger as logmod
from app.core.logger import mask_pii, redact_secrets, mask_address, _pii_filter
from app.services.lead_manager_service import compose_full_address


def test_mask_pii_unchanged():
    # Existing behavior must not regress: keep country code + 2, mask middle, keep last 3.
    assert mask_pii("972521234567") == "97252****567"


def test_redact_secrets_replaces_webhook_token_in_query(monkeypatch):
    # The exact leak PRO-80 fixes: uvicorn access log line with the token in the query string.
    monkeypatch.setattr(logmod, "_SECRET_VALUES", ["webhooktok456"])
    out = redact_secrets('POST /webhook?token=webhooktok456 HTTP/1.1" 200')
    assert "webhooktok456" not in out
    assert "***REDACTED***" in out


def test_redact_secrets_provider_token_in_url_path(monkeypatch):
    # Value-based redaction also covers a provider token sitting in a URL *path*
    # (e.g. an httpx exception string that reaches logger.error) — a query-only fix
    # would miss it. PRO-86: the sample URL is provider-neutral now; PRO-89 must add
    # its Cloud API credential to _SECRET_VALUES for this to keep protecting anything.
    monkeypatch.setattr(logmod, "_SECRET_VALUES", ["gtok789"])
    out = redact_secrets(
        "Failed to send: https://graph.example.com/v20.0/1234/messages/gtok789"
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


# ---------------------------------------------------------------------------
# mask_address (PRO-174): Hebrew street-address masking
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        # Branch A: explicit street keyword + name + house number.
        "רחוב הרצל 15, תל אביב",
        # Branch B: bare "street number" form — what compose_full_address emits.
        "הרצל 15, תל אביב",
    ],
)
def test_mask_address_masks_street_and_number_but_keeps_city(message):
    out = mask_address(message)
    assert "***ADDRESS***" in out
    assert "הרצל" not in out
    assert "15" not in out
    # PRO-173's line: the city is genuine triage context and stays.
    assert out.endswith(", תל אביב")


def test_mask_address_masks_compose_full_address_output():
    # The exact shape lead_manager_service.compose_full_address builds —
    # "{street} {number}, {city}" — is what workflow_service logs as
    # full_address in several places (PRO-174's stated motivation).
    from types import SimpleNamespace

    extracted = SimpleNamespace(street="בן גוריון", street_number="42", city="חיפה")
    full_address = compose_full_address(extracted)

    out = mask_address(full_address)

    assert out == "***ADDRESS***, חיפה"


def test_mask_address_does_not_mask_appointment_time():
    # Guard: "מחר 10:00" is an appointment time on every lead, not a house
    # number — the `:` lookaround exists specifically so this line survives.
    message = "מחר 10:00"
    assert mask_address(message) == message


def test_mask_address_does_not_mask_already_masked_phone():
    # Guard: a phone already masked by mask_pii ("97252****567") must not be
    # re-mangled by the address pattern reading its trailing digits as a
    # house number — the `\d` lookaround exists specifically for this.
    message = "97252****567"
    assert mask_address(message) == message


def test_pii_filter_also_scrubs_address(monkeypatch):
    # PRO-174: _pii_filter now runs scrub() (secrets, then PII, then address),
    # not just mask_pii/redact_secrets — a record carrying all three must come
    # out clean of all three.
    monkeypatch.setattr(logmod, "_SECRET_VALUES", ["seekret"])
    record = {"message": "רחוב הרצל 15, תל אביב token=seekret 972521234567"}
    assert _pii_filter(record) is True
    assert "***ADDRESS***" in record["message"]
    assert "***REDACTED***" in record["message"]
    assert "97252****567" in record["message"]
    assert "הרצל" not in record["message"]
    assert "seekret" not in record["message"]
    assert "972521234567" not in record["message"]


def test_pii_filter_scrubs_string_extras_not_just_message():
    # PRO-174: _railway_json_sink (the prod sink) hoists record["extra"]
    # verbatim into the JSON line, and PRO-174 makes logger.contextualize the
    # house pattern for binding extras like chat_id — so an unscrubbed extra
    # would route a raw phone number past every scrubber in this module.
    # Bools (_stdlib, sentry_skip) must pass through untouched.
    record = {
        "message": "hello",
        "extra": {
            "chat_id": "call 972521234567 now",
            "trace_id": "abc123",
            "_stdlib": True,
        },
    }
    assert _pii_filter(record) is True
    assert "972521234567" not in record["extra"]["chat_id"]
    assert "97252****567" in record["extra"]["chat_id"]
    assert record["extra"]["trace_id"] == "abc123"
    assert record["extra"]["_stdlib"] is True
