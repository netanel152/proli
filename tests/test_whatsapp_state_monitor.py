"""
PRO-20 — Green API instance deauth monitor.

Tests cover three units:
  1. WhatsAppClient.get_state_instance()  — happy path + error swallow (2 tests)
  2. send_oncall_alert()                  — SMS-first routing, fallback, failure (5 tests)
  3. check_whatsapp_instance_state()      — all 8 FSM/Redis behavioral branches (8 tests)

Total: 15 tests.

Fake-Redis pattern lifted from tests/test_rate_limit.py: a hand-rolled async stub so we
have zero external dependencies and full nx/ex control without fakeredis.
"""

import time

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import app.services.monitor_service as monitor_module
import app.services.notification_service as notif_module
from app.core.constants import WorkerConstants
from app.core.messages import Messages
from app.services.whatsapp_client_service import WhatsAppClient


# ---------------------------------------------------------------------------
# Minimal async-Redis fake (get / set with nx,ex / delete)
# ---------------------------------------------------------------------------


class _FakeRedis:
    """Async Redis stub with just the operations used by check_whatsapp_instance_state.

    Key contract:
      * set(key, val, ex=N)          → always writes, returns True
      * set(key, val, ex=N, nx=True) → writes and returns True when key absent;
                                        returns None (falsy) when key already exists
      * delete(*keys)                → removes each key present, returns count
    """

    def __init__(self, initial: dict | None = None):
        self._store: dict[str, str] = dict(initial or {})

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def set(
        self,
        key: str,
        value: str,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool | None:
        if nx and key in self._store:
            return None  # nx guard: key already present — no-op
        self._store[key] = str(value)
        return True

    async def delete(self, *keys: str) -> int:
        deleted = 0
        for key in keys:
            if key in self._store:
                del self._store[key]
                deleted += 1
        return deleted

    async def expire(self, key: str, ttl: int) -> bool:
        """No-op TTL refresh — the fake has no expiry, but the monitor refreshes
        down_since's TTL on long outages, so the method must exist."""
        return key in self._store


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _ts_minutes_ago(minutes: float) -> str:
    """Return a stringified unix timestamp that is ``minutes`` minutes in the past."""
    return str(time.time() - minutes * 60)


def _redis_factory(redis_instance: _FakeRedis) -> AsyncMock:
    """Return an AsyncMock that, when awaited, yields ``redis_instance``."""
    return AsyncMock(return_value=redis_instance)


# Redis key constants (mirror monitor_service internals — kept in one place)
_DOWN_SINCE_KEY = "wa:instance:down_since"
_ALERTED_KEY = "wa:instance:alerted"
_LAST_ALERT_KEY = "wa:instance:last_alert"

# Convenience timestamps relative to the configured threshold
_ABOVE_THRESHOLD_TS = _ts_minutes_ago(
    WorkerConstants.WA_STATE_ALERT_THRESHOLD_MINUTES + 5
)
_BELOW_THRESHOLD_TS = _ts_minutes_ago(
    max(0, WorkerConstants.WA_STATE_ALERT_THRESHOLD_MINUTES - 2)
)


# ===========================================================================
# Section 1 — WhatsAppClient.get_state_instance()
# ===========================================================================


@pytest.mark.asyncio
async def test_get_state_instance_returns_state_value_from_json():
    """Happy path: JSON body has stateInstance → value returned verbatim."""
    wa = WhatsAppClient()

    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {"stateInstance": "authorized"}

    mock_http = AsyncMock()
    mock_http.get = AsyncMock(return_value=mock_resp)

    with patch.object(wa, "_get_client", new=AsyncMock(return_value=mock_http)):
        result = await wa.get_state_instance()

    assert result == "authorized"


@pytest.mark.asyncio
async def test_get_state_instance_returns_none_on_any_exception():
    """Network / HTTP errors are swallowed; None is returned (best-effort probe)."""
    wa = WhatsAppClient()

    with patch.object(
        wa, "_get_client", new=AsyncMock(side_effect=Exception("connection reset"))
    ):
        result = await wa.get_state_instance()

    assert result is None


# ===========================================================================
# Section 2 — send_oncall_alert()
# ===========================================================================


@pytest.mark.asyncio
async def test_send_oncall_alert_sms_configured_and_succeeds_skips_whatsapp(
    monkeypatch,
):
    """SMS-first: when SMS succeeds WhatsApp is never tried."""
    mock_sms = MagicMock()
    mock_sms.is_configured = True
    mock_sms.send_sms = AsyncMock(return_value=True)

    mock_wa = MagicMock()
    mock_wa.send_message = AsyncMock()

    monkeypatch.setattr(notif_module, "sms_client", mock_sms)
    monkeypatch.setattr(notif_module, "whatsapp", mock_wa)

    result = await notif_module.send_oncall_alert("system alert")

    assert result is True
    mock_sms.send_sms.assert_awaited_once()
    mock_wa.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_oncall_alert_sms_fails_falls_back_to_whatsapp(monkeypatch):
    """SMS configured but returns False → WhatsApp is the last resort."""
    mock_sms = MagicMock()
    mock_sms.is_configured = True
    mock_sms.send_sms = AsyncMock(return_value=False)

    mock_wa = MagicMock()
    mock_wa.send_message = AsyncMock()

    monkeypatch.setattr(notif_module, "sms_client", mock_sms)
    monkeypatch.setattr(notif_module, "whatsapp", mock_wa)

    result = await notif_module.send_oncall_alert("system alert")

    assert result is True
    mock_wa.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_oncall_alert_sms_not_configured_uses_whatsapp_directly(monkeypatch):
    """When SMS is not configured, WhatsApp is tried without calling SMS at all."""
    mock_sms = MagicMock()
    mock_sms.is_configured = False

    mock_wa = MagicMock()
    mock_wa.send_message = AsyncMock()

    monkeypatch.setattr(notif_module, "sms_client", mock_sms)
    monkeypatch.setattr(notif_module, "whatsapp", mock_wa)

    result = await notif_module.send_oncall_alert("system alert")

    assert result is True
    mock_wa.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_oncall_alert_all_channels_fail_returns_false(monkeypatch):
    """SMS not configured and WhatsApp raises → returns False without re-raising."""
    mock_sms = MagicMock()
    mock_sms.is_configured = False

    mock_wa = MagicMock()
    mock_wa.send_message = AsyncMock(side_effect=Exception("Green API unreachable"))

    monkeypatch.setattr(notif_module, "sms_client", mock_sms)
    monkeypatch.setattr(notif_module, "whatsapp", mock_wa)

    result = await notif_module.send_oncall_alert("system alert")

    assert result is False


@pytest.mark.asyncio
async def test_send_oncall_alert_appends_cus_suffix_to_bare_phone(monkeypatch):
    """Phone numbers without @c.us get the suffix appended before sending to WhatsApp."""
    mock_sms = MagicMock()
    mock_sms.is_configured = False

    mock_wa = MagicMock()
    mock_wa.send_message = AsyncMock()

    monkeypatch.setattr(notif_module, "sms_client", mock_sms)
    monkeypatch.setattr(notif_module, "whatsapp", mock_wa)

    await notif_module.send_oncall_alert("test")

    called_chat_id: str = mock_wa.send_message.call_args[0][0]
    assert called_chat_id.endswith(
        "@c.us"
    ), f"Expected @c.us suffix on WhatsApp chat_id, got: {called_chat_id!r}"


# ===========================================================================
# Section 3 — check_whatsapp_instance_state()
# ===========================================================================


@pytest.mark.asyncio
async def test_wa_monitor_authorized_with_prior_outage_sends_recovery_and_clears_keys(
    monkeypatch,
):
    """(a) authorized + down_since + alerted → WHATSAPP_RECOVERED alert sent; all 3 keys deleted."""
    redis = _FakeRedis(
        {
            _DOWN_SINCE_KEY: _ABOVE_THRESHOLD_TS,
            _ALERTED_KEY: "1",
            _LAST_ALERT_KEY: "1",
        }
    )
    mock_wa = MagicMock()
    mock_wa.get_state_instance = AsyncMock(return_value="authorized")
    mock_alert = AsyncMock(return_value=True)

    monkeypatch.setattr(monitor_module, "whatsapp", mock_wa)
    monkeypatch.setattr(monitor_module, "get_redis_client", _redis_factory(redis))
    monkeypatch.setattr(monitor_module, "send_oncall_alert", mock_alert)

    await monitor_module.check_whatsapp_instance_state()

    mock_alert.assert_awaited_once_with(Messages.Alerts.WHATSAPP_RECOVERED)
    assert _DOWN_SINCE_KEY not in redis._store
    assert _ALERTED_KEY not in redis._store
    assert _LAST_ALERT_KEY not in redis._store


@pytest.mark.asyncio
async def test_wa_monitor_authorized_no_prior_outage_no_recovery_alert(monkeypatch):
    """(b) authorized + no down_since in Redis → no alert (nothing to recover from)."""
    redis = _FakeRedis()
    mock_wa = MagicMock()
    mock_wa.get_state_instance = AsyncMock(return_value="authorized")
    mock_alert = AsyncMock()

    monkeypatch.setattr(monitor_module, "whatsapp", mock_wa)
    monkeypatch.setattr(monitor_module, "get_redis_client", _redis_factory(redis))
    monkeypatch.setattr(monitor_module, "send_oncall_alert", mock_alert)

    await monitor_module.check_whatsapp_instance_state()

    mock_alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_wa_monitor_not_authorized_first_detection_starts_timer_no_page(
    monkeypatch,
):
    """(c) not authorized + no down_since → sets down_since key, does NOT page."""
    redis = _FakeRedis()
    mock_wa = MagicMock()
    mock_wa.get_state_instance = AsyncMock(return_value="notAuthorized")
    mock_alert = AsyncMock()

    monkeypatch.setattr(monitor_module, "whatsapp", mock_wa)
    monkeypatch.setattr(monitor_module, "get_redis_client", _redis_factory(redis))
    monkeypatch.setattr(monitor_module, "send_oncall_alert", mock_alert)

    await monitor_module.check_whatsapp_instance_state()

    assert (
        _DOWN_SINCE_KEY in redis._store
    ), "Deauth timer key must be created on first detection"
    mock_alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_wa_monitor_not_authorized_below_threshold_no_page(monkeypatch):
    """(d) not authorized, downtime < WA_STATE_ALERT_THRESHOLD_MINUTES → no page yet."""
    redis = _FakeRedis({_DOWN_SINCE_KEY: _BELOW_THRESHOLD_TS})
    mock_wa = MagicMock()
    mock_wa.get_state_instance = AsyncMock(return_value="notAuthorized")
    mock_alert = AsyncMock()

    monkeypatch.setattr(monitor_module, "whatsapp", mock_wa)
    monkeypatch.setattr(monitor_module, "get_redis_client", _redis_factory(redis))
    monkeypatch.setattr(monitor_module, "send_oncall_alert", mock_alert)

    await monitor_module.check_whatsapp_instance_state()

    mock_alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_wa_monitor_not_authorized_above_threshold_first_page_sets_keys_and_critical(
    monkeypatch,
):
    """(e) downtime > threshold, last_alert absent → pages once, sets alerted + last_alert,
    emits logger.critical."""
    redis = _FakeRedis({_DOWN_SINCE_KEY: _ABOVE_THRESHOLD_TS})
    mock_wa = MagicMock()
    mock_wa.get_state_instance = AsyncMock(return_value="notAuthorized")
    mock_alert = AsyncMock(return_value=True)
    mock_logger = MagicMock()

    monkeypatch.setattr(monitor_module, "whatsapp", mock_wa)
    monkeypatch.setattr(monitor_module, "get_redis_client", _redis_factory(redis))
    monkeypatch.setattr(monitor_module, "send_oncall_alert", mock_alert)
    monkeypatch.setattr(monitor_module, "logger", mock_logger)

    await monitor_module.check_whatsapp_instance_state()

    # Exactly one page sent
    mock_alert.assert_awaited_once()
    alert_text: str = mock_alert.call_args[0][0]
    assert (
        "notAuthorized" in alert_text
    ), "Alert text must include the current state value"
    # Redis side effects
    assert redis._store.get(_ALERTED_KEY) == "1"
    assert redis._store.get(_LAST_ALERT_KEY) == "1"
    # logger.critical must be emitted (forwarded to Sentry in production)
    mock_logger.critical.assert_called_once()


@pytest.mark.asyncio
async def test_wa_monitor_not_authorized_above_threshold_dedup_prevents_repage(
    monkeypatch,
):
    """(f) last_alert already set (dedup key) → no re-page within the realert window."""
    redis = _FakeRedis(
        {
            _DOWN_SINCE_KEY: _ABOVE_THRESHOLD_TS,
            _LAST_ALERT_KEY: "1",  # still within WA_STATE_REALERT_MINUTES window
        }
    )
    mock_wa = MagicMock()
    mock_wa.get_state_instance = AsyncMock(return_value="notAuthorized")
    mock_alert = AsyncMock()

    monkeypatch.setattr(monitor_module, "whatsapp", mock_wa)
    monkeypatch.setattr(monitor_module, "get_redis_client", _redis_factory(redis))
    monkeypatch.setattr(monitor_module, "send_oncall_alert", mock_alert)

    await monitor_module.check_whatsapp_instance_state()

    mock_alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_wa_monitor_state_none_treated_as_not_authorized(monkeypatch):
    """(g) state=None (unreachable instance) is treated identically to not-authorized:
    deauth timer starts, no page on the first tick."""
    redis = _FakeRedis()
    mock_wa = MagicMock()
    mock_wa.get_state_instance = AsyncMock(return_value=None)
    mock_alert = AsyncMock()

    monkeypatch.setattr(monitor_module, "whatsapp", mock_wa)
    monkeypatch.setattr(monitor_module, "get_redis_client", _redis_factory(redis))
    monkeypatch.setattr(monitor_module, "send_oncall_alert", mock_alert)

    await monitor_module.check_whatsapp_instance_state()

    assert (
        _DOWN_SINCE_KEY in redis._store
    ), "down_since must be set even when state is None (unreachable treated as non-authorized)"
    mock_alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_wa_monitor_redis_error_fails_open_without_exception(monkeypatch):
    """(h) get_redis_client raises → function returns silently (fail-open). Worker must not crash."""
    mock_wa = MagicMock()
    mock_wa.get_state_instance = AsyncMock(return_value="notAuthorized")
    mock_alert = AsyncMock()

    monkeypatch.setattr(monitor_module, "whatsapp", mock_wa)
    monkeypatch.setattr(
        monitor_module,
        "get_redis_client",
        AsyncMock(side_effect=Exception("Redis connection refused")),
    )
    monkeypatch.setattr(monitor_module, "send_oncall_alert", mock_alert)

    # Must not raise — any exception here would kill the ARQ worker job
    await monitor_module.check_whatsapp_instance_state()

    mock_alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_wa_monitor_authorized_quiet_blip_no_recovery_alert_but_clears_keys(
    monkeypatch,
):
    """(a') authorized + down_since present but NEVER paged (no alerted key) → the
    'quiet blip' case: stay silent (no recovery notice) yet still clear the timer."""
    redis = _FakeRedis({_DOWN_SINCE_KEY: _BELOW_THRESHOLD_TS})  # no _ALERTED_KEY
    mock_wa = MagicMock()
    mock_wa.get_state_instance = AsyncMock(return_value="authorized")
    mock_alert = AsyncMock()

    monkeypatch.setattr(monitor_module, "whatsapp", mock_wa)
    monkeypatch.setattr(monitor_module, "get_redis_client", _redis_factory(redis))
    monkeypatch.setattr(monitor_module, "send_oncall_alert", mock_alert)

    await monitor_module.check_whatsapp_instance_state()

    mock_alert.assert_not_awaited()
    assert _DOWN_SINCE_KEY not in redis._store, "Timer must be cleared on recovery"


@pytest.mark.asyncio
async def test_wa_monitor_inner_exception_fails_open(monkeypatch):
    """(h') a failure AFTER Redis is obtained (e.g. redis.get raises) is caught by
    the inner guard — the worker job must never see the exception."""
    redis = _FakeRedis()
    redis.get = AsyncMock(side_effect=Exception("redis read timeout"))
    mock_wa = MagicMock()
    mock_wa.get_state_instance = AsyncMock(return_value="notAuthorized")
    mock_alert = AsyncMock()

    monkeypatch.setattr(monitor_module, "whatsapp", mock_wa)
    monkeypatch.setattr(monitor_module, "get_redis_client", _redis_factory(redis))
    monkeypatch.setattr(monitor_module, "send_oncall_alert", mock_alert)

    # Must not raise
    await monitor_module.check_whatsapp_instance_state()

    mock_alert.assert_not_awaited()


# ===========================================================================
# Section 4 — on-call routing (ONCALL_PHONE vs ADMIN_PHONE)
# ===========================================================================


@pytest.mark.asyncio
async def test_send_oncall_alert_routes_to_oncall_phone_when_set(monkeypatch):
    """ONCALL_PHONE, when set, is the number dialed — not ADMIN_PHONE."""
    monkeypatch.setattr(notif_module.settings, "ONCALL_PHONE", "972500000001")
    monkeypatch.setattr(notif_module.settings, "ADMIN_PHONE", "972524828796")

    mock_sms = MagicMock()
    mock_sms.is_configured = True
    mock_sms.send_sms = AsyncMock(return_value=True)
    monkeypatch.setattr(notif_module, "sms_client", mock_sms)

    await notif_module.send_oncall_alert("alert")

    dialed: str = mock_sms.send_sms.call_args[0][0]
    assert dialed == "972500000001"


@pytest.mark.asyncio
async def test_send_oncall_alert_falls_back_to_admin_phone_when_oncall_unset(
    monkeypatch,
):
    """When ONCALL_PHONE is unset, paging falls back to ADMIN_PHONE."""
    monkeypatch.setattr(notif_module.settings, "ONCALL_PHONE", None)
    monkeypatch.setattr(notif_module.settings, "ADMIN_PHONE", "972524828796")

    mock_sms = MagicMock()
    mock_sms.is_configured = True
    mock_sms.send_sms = AsyncMock(return_value=True)
    monkeypatch.setattr(notif_module, "sms_client", mock_sms)

    await notif_module.send_oncall_alert("alert")

    dialed: str = mock_sms.send_sms.call_args[0][0]
    assert dialed == "972524828796"
