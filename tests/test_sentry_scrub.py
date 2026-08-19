"""``_scrub_event`` — the ``before_send`` hook in ``app/core/sentry.py``.

This is the payload-side half of the PII guarantee: ``_pii_filter`` protects
the loguru sinks and ``page_critical`` scrubs its own message inline, but
nothing else stands between an exception value / breadcrumb / request context
and the wire. Every string leaf of every outgoing event must pass through
``redact_secrets`` → ``mask_pii`` → structural URI-credential stripping.

Pure-python tests: no sentry_sdk import anywhere (the hook receives plain
dicts), so this file is collection-safe for
``tests/test_fire_test_page.py``'s "sentry_sdk was never imported" assertion.
"""

import pytest

import app.core.sentry as sentry_module
from app.core.sentry import _scrub_event, _scrub_string, _walk


PHONE = "972521234567"
MASKED = "97252****567"


def _event(**kwargs):
    return {"event_id": "abc", **kwargs}


class TestScrubString:
    def test_masks_israeli_phone(self):
        assert (
            _scrub_string(f"customer {PHONE} reported") == f"customer {MASKED} reported"
        )

    def test_strips_uri_userinfo_variants(self):
        """redact_secrets only knows the exact SecretStr values; a driver
        error echoing a percent-encoded or reordered variant must still lose
        its credentials structurally."""
        s = "mongodb+srv://proli_user:p%40ssw0rd@cluster0.mongodb.net/db failed"
        assert _scrub_string(s) == "mongodb+srv://***@cluster0.mongodb.net/db failed"

    def test_strips_redis_style_userinfo(self):
        s = "redis://:secretpass@redis.railway.internal:6379"
        assert _scrub_string(s) == "redis://***@redis.railway.internal:6379"

    def test_plain_url_untouched(self):
        s = "https://graph.facebook.com/v23.0/messages"
        assert _scrub_string(s) == s

    def test_secrets_redacted_before_pii_masking(self, monkeypatch):
        """A secret containing a 972… digit run must match redact_secrets
        first — mask_pii running first would mangle it out of recognition
        (same ordering contract as _pii_filter)."""
        secret = f"token-{PHONE}-suffix"
        monkeypatch.setattr(
            sentry_module,
            "redact_secrets",
            lambda m: m.replace(secret, "***REDACTED***"),
        )
        assert (
            _scrub_string(f"auth {secret} rejected") == "auth ***REDACTED*** rejected"
        )


class TestWalk:
    def test_scrubs_exception_values(self):
        event = _event(
            exception={
                "values": [
                    {
                        "type": "OperationFailure",
                        "value": f"bad auth for {PHONE} at mongodb://u:p@host/db",
                    }
                ]
            }
        )
        out = _scrub_event(event, None)
        value = out["exception"]["values"][0]["value"]
        assert PHONE not in value
        assert MASKED in value
        assert "mongodb://***@host/db" in value
        assert out["exception"]["values"][0]["type"] == "OperationFailure"

    def test_scrubs_breadcrumbs_and_nested_extra(self):
        event = _event(
            breadcrumbs={
                "values": [
                    {"message": f"sent to {PHONE}", "data": {"url": "https://x/y?u=a"}},
                ]
            },
            extra={"lead": {"chat": {"id": PHONE}}, "count": 3},
        )
        out = _scrub_event(event, None)
        assert out["breadcrumbs"]["values"][0]["message"] == f"sent to {MASKED}"
        assert out["extra"]["lead"]["chat"]["id"] == MASKED
        assert out["extra"]["count"] == 3  # non-string leaves untouched

    def test_lists_and_tuples_preserved(self):
        out = _walk({"a": [PHONE, ("x", PHONE)]})
        assert out["a"][0] == MASKED
        assert isinstance(out["a"][1], tuple)
        assert out["a"][1] == ("x", MASKED)

    def test_dict_keys_untouched(self):
        out = _walk({PHONE: "value"})
        assert PHONE in out  # keys pass through; only values are scrubbed

    def test_depth_cap_stops_recursion(self):
        deep = current = {}
        for _ in range(20):
            current["next"] = {}
            current = current["next"]
        current["leaf"] = PHONE
        out = _walk(deep)  # must not raise; the too-deep leaf is left as-is
        node = out
        for _ in range(20):
            node = node["next"]
        assert node["leaf"] == PHONE  # beyond cap — documented tradeoff

    def test_cycle_safety(self):
        event = _event(extra={})
        event["extra"]["self"] = event["extra"]
        out = _scrub_event(event, None)  # must not raise / hang
        assert out is not None

    def test_scrub_failure_replaces_leaf_with_placeholder(self, monkeypatch):
        def boom(value):
            raise RuntimeError("scrubber exploded")

        monkeypatch.setattr(sentry_module, "_scrub_string", boom)
        out = _walk({"message": PHONE})
        assert out["message"] == "[scrub-error]"


class TestScrubEventGuard:
    def test_walker_crash_drops_normal_event(self, monkeypatch):
        monkeypatch.setattr(
            sentry_module, "_walk", lambda *a, **k: (_ for _ in ()).throw(RuntimeError)
        )
        assert _scrub_event(_event(message="x"), None) is None

    def test_walker_crash_keeps_paging_event(self, monkeypatch):
        """A page must never be lost to its own safety net — page_critical
        already scrubbed the message inline before the record was built."""
        monkeypatch.setattr(
            sentry_module, "_walk", lambda *a, **k: (_ for _ in ()).throw(RuntimeError)
        )
        event = _event(logger="proli.paging", message="already-scrubbed")
        assert _scrub_event(event, None) is event
