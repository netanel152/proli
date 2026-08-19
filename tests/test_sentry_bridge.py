"""The loguru→Sentry ERROR bridge (``app/core/sentry.py``).

Band contract: ERROR ≤ level < CRITICAL, loguru-native records only.
CRITICAL stays exclusively on the stdlib paging path (``page_critical``);
stdlib-origin records (``_stdlib=True``, bound by ``InterceptHandler``) and
explicit ``sentry_skip=True`` binds are excluded; per-site hourly throttle +
global daily cap bound the Sentry spend.

The sink is only *registered* by a successful ``init_sentry`` — these tests
attach it to a fresh sink id themselves, so no Sentry client (or sdk import)
is needed: ``capture_message`` is intercepted at the ``sys.modules`` seam.
"""

import sys
from unittest.mock import MagicMock

import pytest

import app.core.sentry as sentry_module
from app.core.logger import logger as loguru_logger
from app.core.sentry import _bridge_filter, _sentry_bridge_sink


@pytest.fixture(autouse=True)
def _fresh_bridge_state(monkeypatch):
    monkeypatch.setattr(sentry_module, "_throttle", {})
    monkeypatch.setattr(sentry_module, "_bridge_window_start", 0.0)
    monkeypatch.setattr(sentry_module, "_bridge_count", 0)


@pytest.fixture
def captured(monkeypatch):
    """Attach the real sink+filter to loguru and intercept capture_message."""
    sdk = MagicMock()
    scope = MagicMock()
    sdk.isolation_scope.return_value.__enter__ = MagicMock(return_value=scope)
    sdk.isolation_scope.return_value.__exit__ = MagicMock(return_value=False)
    monkeypatch.setitem(sys.modules, "sentry_sdk", sdk)
    sink_id = loguru_logger.add(
        _sentry_bridge_sink, level="ERROR", filter=_bridge_filter
    )
    try:
        yield sdk
    finally:
        loguru_logger.remove(sink_id)


def test_error_reaches_bridge_once(captured):
    loguru_logger.error("availability check failed")
    captured.capture_message.assert_called_once()
    args, kwargs = captured.capture_message.call_args
    assert args[0] == "availability check failed"
    assert kwargs["level"] == "error"


def test_warning_and_critical_excluded(captured):
    loguru_logger.warning("noise")
    loguru_logger.critical("paging is the stdlib path's job")
    captured.capture_message.assert_not_called()


def test_stdlib_origin_records_excluded(captured):
    """InterceptHandler binds _stdlib=True — uvicorn/arq/apscheduler ERRORs
    must not re-enter Sentry through the bridge."""
    loguru_logger.bind(_stdlib=True).error("uvicorn exploded")
    captured.capture_message.assert_not_called()


def test_sentry_skip_opt_out(captured):
    """The explicit opt-out for log-then-raise sites whose exception is
    captured elsewhere (ArqIntegration)."""
    loguru_logger.bind(sentry_skip=True).error("task failed, re-raising")
    captured.capture_message.assert_not_called()


def test_per_site_throttle_suppresses_repeats(captured):
    for _ in range(5):
        loguru_logger.error("same site, same line")
    assert captured.capture_message.call_count == 1


def test_daily_cap_bounds_total_spend(captured, monkeypatch):
    """With the per-site throttle bypassed, only the global daily cap stands
    between a noisy night and the Sentry quota."""
    monkeypatch.setattr(sentry_module, "_BRIDGE_DAILY_CAP", 3)
    monkeypatch.setattr(sentry_module, "should_send", lambda *a, **k: True)
    for i in range(6):
        loguru_logger.error(f"failure variant {i}")
    assert captured.capture_message.call_count == 3


def test_sink_failure_never_breaks_logging(captured):
    """A bridge crash must not take the logging path (often itself a
    fail-open error handler) down with it."""
    captured.isolation_scope.side_effect = RuntimeError("sentry hiccup")
    loguru_logger.error("still must not raise")  # must not raise


def test_tags_log_site(captured):
    loguru_logger.error("tagged failure")
    scope = captured.isolation_scope.return_value.__enter__.return_value
    tag_names = {call.args[0] for call in scope.set_tag.call_args_list}
    assert "log_site" in tag_names
    assert "via" in tag_names
