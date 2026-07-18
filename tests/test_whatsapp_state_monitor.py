"""
PRO-20 — Green API instance deauth monitor.

Tests cover four units:
  1. WhatsAppClient.get_state_instance()  — happy path + error swallow (2 tests)
  2. send_oncall_alert()                  — state-guarded WhatsApp-only delivery (PRO-75:
                                             SMS removed entirely; alert is only sent over
                                             WhatsApp when the instance is itself authorized)
  3. check_whatsapp_instance_state()      — every FSM/Redis behavioral branch
  4. on-call routing                      — ONCALL_PHONE vs ADMIN_PHONE

Time anchors are computed per-test, never at module scope — see _below_threshold_ts
(PRO-68).

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
    """Return a stringified unix timestamp that is ``minutes`` minutes in the past.

    Call this *inside* a test, never at module scope: the monitor compares the anchor
    against the wall clock at assertion time, so an anchor frozen at import drifts by
    however long the suite takes to reach the test (PRO-68).
    """
    return str(time.time() - minutes * 60)


def _redis_factory(redis_instance: _FakeRedis) -> AsyncMock:
    """Return an AsyncMock that, when awaited, yields ``redis_instance``."""
    return AsyncMock(return_value=redis_instance)


# Redis key constants (mirror monitor_service internals — kept in one place)
_DOWN_SINCE_KEY = "wa:instance:down_since"
_ALERTED_KEY = "wa:instance:alerted"
_LAST_ALERT_KEY = "wa:instance:last_alert"

# Convenience anchors relative to the configured threshold. These are functions, not
# module constants: as constants they were evaluated once at import, and a suite run
# long enough to drift past the threshold turned the below-threshold anchor into an
# above-threshold one, spuriously failing the no-page assertion (PRO-68).


def _above_threshold_ts() -> str:
    """An outage that started well before the paging threshold — the monitor should page.

    The fixed ``+ 5`` is safe here in a way it would not be below: drift only ever grows
    the measured downtime, which pushes this anchor further into the paging branch the
    callers assert on. Erring later is erring in the assertion's favour.
    """
    return _ts_minutes_ago(WorkerConstants.WA_STATE_ALERT_THRESHOLD_MINUTES + 5)


def _below_threshold_ts() -> str:
    """A fresh outage that has not yet earned a page.

    Anchored at half the threshold rather than "threshold minus 2 minutes": the margin
    is now a fraction of the threshold instead of a fixed constant, so shortening
    WA_STATE_ALERT_THRESHOLD_MINUTES can't quietly squeeze it to zero.
    """
    assert (
        WorkerConstants.WA_STATE_ALERT_THRESHOLD_MINUTES > 0
    ), "A zero/negative paging threshold has no 'below threshold' case to anchor."
    return _ts_minutes_ago(WorkerConstants.WA_STATE_ALERT_THRESHOLD_MINUTES / 2)


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
#
# PRO-75: SMS is gone. send_oncall_alert is now state-guarded and WhatsApp-only:
#   * probes whatsapp.get_state_instance() first
#   * state != "authorized" → NEVER calls whatsapp.send_message; logs
#     logger.critical (the Sentry-forwarded out-of-band page) and returns False
#   * state == "authorized" → sends via whatsapp.send_message, returns True;
#     an exception during send is caught, logs logger.critical, returns False
# ===========================================================================


@pytest.mark.asyncio
async def test_send_oncall_alert_authorized_sends_via_whatsapp(monkeypatch):
    """Instance authorized → alert is delivered over WhatsApp; returns True."""
    mock_wa = MagicMock()
    mock_wa.get_state_instance = AsyncMock(return_value="authorized")
    mock_wa.send_message = AsyncMock()

    monkeypatch.setattr(notif_module, "whatsapp", mock_wa)

    result = await notif_module.send_oncall_alert("system alert")

    assert result is True
    mock_wa.send_message.assert_awaited_once()
    assert mock_wa.send_message.call_args.args[1] == "system alert"


@pytest.mark.parametrize(
    "state", ["notAuthorized", "yellowCard", "starting", "blocked", None]
)
@pytest.mark.asyncio
async def test_send_oncall_alert_not_authorized_never_sends_whatsapp(
    monkeypatch, state
):
    """Instance not authorized (any non-'authorized' state, including unreachable
    → None) → NO WhatsApp send is attempted; logger.critical is emitted instead;
    returns False."""
    mock_wa = MagicMock()
    mock_wa.get_state_instance = AsyncMock(return_value=state)
    mock_wa.send_message = AsyncMock()
    mock_logger = MagicMock()

    monkeypatch.setattr(notif_module, "whatsapp", mock_wa)
    monkeypatch.setattr(notif_module, "logger", mock_logger)

    result = await notif_module.send_oncall_alert("system alert")

    assert result is False
    mock_wa.send_message.assert_not_awaited()
    mock_logger.critical.assert_called_once()


@pytest.mark.asyncio
async def test_send_oncall_alert_authorized_but_send_raises_returns_false(
    monkeypatch,
):
    """Instance reports authorized but the send itself blows up → caught,
    logger.critical emitted, returns False (no re-raise, no SMS to fall back to)."""
    mock_wa = MagicMock()
    mock_wa.get_state_instance = AsyncMock(return_value="authorized")
    mock_wa.send_message = AsyncMock(side_effect=Exception("Green API unreachable"))
    mock_logger = MagicMock()

    monkeypatch.setattr(notif_module, "whatsapp", mock_wa)
    monkeypatch.setattr(notif_module, "logger", mock_logger)

    result = await notif_module.send_oncall_alert("system alert")

    assert result is False
    mock_logger.critical.assert_called_once()


@pytest.mark.asyncio
async def test_send_oncall_alert_appends_cus_suffix_to_bare_phone(monkeypatch):
    """Phone numbers without @c.us get the suffix appended before sending to
    WhatsApp — only reachable on the authorized path since that's the only one
    that calls send_message at all."""
    mock_wa = MagicMock()
    mock_wa.get_state_instance = AsyncMock(return_value="authorized")
    mock_wa.send_message = AsyncMock()

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
            _DOWN_SINCE_KEY: _above_threshold_ts(),
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

    mock_alert.assert_awaited_once_with(
        Messages.Alerts.WHATSAPP_RECOVERED, assume_authorized=True
    )
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
    redis = _FakeRedis({_DOWN_SINCE_KEY: _below_threshold_ts()})
    mock_wa = MagicMock()
    mock_wa.get_state_instance = AsyncMock(return_value="notAuthorized")
    mock_alert = AsyncMock()

    monkeypatch.setattr(monitor_module, "whatsapp", mock_wa)
    monkeypatch.setattr(monitor_module, "get_redis_client", _redis_factory(redis))
    monkeypatch.setattr(monitor_module, "send_oncall_alert", mock_alert)

    down_since_before = redis._store[_DOWN_SINCE_KEY]

    await monitor_module.check_whatsapp_instance_state()

    mock_alert.assert_not_awaited()
    # Pin the branch. "No page" is also what first-detection (test c) does, so without
    # this the two tests are indistinguishable and a down_since key rename would quietly
    # turn this into a duplicate of (c) — green, but no longer covering the timer.
    assert (
        redis._store[_DOWN_SINCE_KEY] == down_since_before
    ), "Existing deauth timer must be left intact, not restarted as a first detection"


@pytest.mark.asyncio
async def test_wa_monitor_not_authorized_above_threshold_first_page_sets_keys_and_critical(
    monkeypatch,
):
    """(e) downtime > threshold, last_alert absent → threshold crossed: sets alerted +
    last_alert, emits logger.critical. PRO-75: does NOT call send_oncall_alert on the
    down path — paging an outage about WhatsApp over WhatsApp would amplify it, so the
    down-path page is logger.critical (Sentry) only."""
    redis = _FakeRedis({_DOWN_SINCE_KEY: _above_threshold_ts()})
    mock_wa = MagicMock()
    mock_wa.get_state_instance = AsyncMock(return_value="notAuthorized")
    mock_alert = AsyncMock(return_value=True)
    mock_logger = MagicMock()

    monkeypatch.setattr(monitor_module, "whatsapp", mock_wa)
    monkeypatch.setattr(monitor_module, "get_redis_client", _redis_factory(redis))
    monkeypatch.setattr(monitor_module, "send_oncall_alert", mock_alert)
    monkeypatch.setattr(monitor_module, "logger", mock_logger)

    await monitor_module.check_whatsapp_instance_state()

    # No WhatsApp-borne page on the down path
    mock_alert.assert_not_awaited()
    # Redis side effects still happen (threshold crossed, dedup window opened)
    assert redis._store.get(_ALERTED_KEY) == "1"
    assert redis._store.get(_LAST_ALERT_KEY) == "1"
    # logger.critical must be emitted (forwarded to Sentry in production) and
    # must include the current state for an actionable page.
    mock_logger.critical.assert_called_once()
    critical_text = mock_logger.critical.call_args[0][0]
    assert "notAuthorized" in critical_text


@pytest.mark.asyncio
async def test_wa_monitor_not_authorized_above_threshold_dedup_prevents_repage(
    monkeypatch,
):
    """(f) last_alert already set (dedup key) → no re-page within the realert window."""
    redis = _FakeRedis(
        {
            _DOWN_SINCE_KEY: _above_threshold_ts(),
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
    redis = _FakeRedis({_DOWN_SINCE_KEY: _below_threshold_ts()})  # no _ALERTED_KEY
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

    mock_wa = MagicMock()
    mock_wa.get_state_instance = AsyncMock(return_value="authorized")
    mock_wa.send_message = AsyncMock()
    monkeypatch.setattr(notif_module, "whatsapp", mock_wa)

    await notif_module.send_oncall_alert("alert")

    dialed: str = mock_wa.send_message.call_args[0][0]
    assert dialed == "972500000001@c.us"


@pytest.mark.asyncio
async def test_send_oncall_alert_falls_back_to_admin_phone_when_oncall_unset(
    monkeypatch,
):
    """When ONCALL_PHONE is unset, paging falls back to ADMIN_PHONE."""
    monkeypatch.setattr(notif_module.settings, "ONCALL_PHONE", None)
    monkeypatch.setattr(notif_module.settings, "ADMIN_PHONE", "972524828796")

    mock_wa = MagicMock()
    mock_wa.get_state_instance = AsyncMock(return_value="authorized")
    mock_wa.send_message = AsyncMock()
    monkeypatch.setattr(notif_module, "whatsapp", mock_wa)

    await notif_module.send_oncall_alert("alert")

    dialed: str = mock_wa.send_message.call_args[0][0]
    assert dialed == "972524828796@c.us"
