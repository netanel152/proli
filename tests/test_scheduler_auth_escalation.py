"""PRO-112 — Mongo auth-failure escalation for scheduler jobs.

Covers:
  1. `_is_mongo_auth_failure` code/message classification (code 8000 no
     longer trips on its own — Atlas's generic policy code — only via text).
  2. `track_mongo_auth_failures` decorator behavior: OperationFailure AND
     ServerSelectionTimeoutError (SDAM-surfaced auth failures) are recorded
     when the message matches auth text; non-auth variants re-raise without
     recording; AutoReconnect is never caught; normal return passthrough.
  3. Escalation threshold + re-page throttle, paging via `page_critical`
     (PRO-113 — the only operator-paging primitive; it emits a stdlib
     CRITICAL on `proli.paging` so Sentry's LoggingIntegration actually
     sees it — loguru CRITICAL never reached it).
  4. Fail-open on a broken Redis client (loguru `logger.warning`).
  5. The tracking-failure guard: even if `_record_mongo_auth_failure` itself
     raises, the original exception still propagates.
  6. Atomic INCR+EXPIRE via a redis pipeline; ResponseError (corrupted
     counter) resets the key WITH a TTL.

Redis touches go through the autouse `fake_redis` fixture (PRO-78) — no
explicit patching needed except for the "Redis unavailable" case, which
patches `get_redis_client` directly (mirrors tests/test_scheduler.py's
PRO-111 backup-failure tests).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from pymongo.errors import AutoReconnect, OperationFailure, ServerSelectionTimeoutError

from app.scheduler import (
    _is_mongo_auth_failure,
    _record_mongo_auth_failure,
    track_mongo_auth_failures,
    MONGO_AUTH_FAILURE_COUNT_KEY,
    MONGO_AUTH_FAILURE_PAGED_KEY,
)
from app.core.constants import WorkerConstants
import app.scheduler as scheduler_module


def _patched_stdlib_critical(monkeypatch):
    """PRO-113: the escalation page goes through `page_critical`, the only
    operator-paging primitive (it emits a stdlib CRITICAL on `proli.paging`
    so Sentry's LoggingIntegration actually sees it — loguru CRITICAL never
    reached it). Patch it at the point of use, `app.scheduler.page_critical`."""
    mock_critical = MagicMock()
    monkeypatch.setattr(scheduler_module, "page_critical", mock_critical)
    return mock_critical


# --- 1. _is_mongo_auth_failure classification ------------------------------


def test_is_mongo_auth_failure_code_8000_with_auth_text_true():
    # 8000 is Atlas's generic policy code (also over-quota, disallowed
    # commands) — it only counts when the message itself says auth.
    exc = OperationFailure("bad auth : authentication failed", code=8000)
    assert _is_mongo_auth_failure(exc) is True


def test_is_mongo_auth_failure_code_8000_quota_message_false():
    exc = OperationFailure("you are over your space quota", code=8000)
    assert _is_mongo_auth_failure(exc) is False


def test_is_mongo_auth_failure_code_18_true():
    exc = OperationFailure("Authentication failed.", code=18)
    assert _is_mongo_auth_failure(exc) is True


def test_is_mongo_auth_failure_code_13_true():
    exc = OperationFailure("not authorized on proli_db to execute command", code=13)
    assert _is_mongo_auth_failure(exc) is True


def test_is_mongo_auth_failure_non_auth_code_false():
    exc = OperationFailure("interrupted at shutdown", code=11600)
    assert _is_mongo_auth_failure(exc) is False


def test_is_mongo_auth_failure_codeless_message_contains_auth_true():
    exc = OperationFailure("Authentication failed")
    assert exc.code is None
    assert _is_mongo_auth_failure(exc) is True


def test_is_mongo_auth_failure_bare_auth_substring_false():
    # Deliberately narrow text match: a bare "auth" substring (e.g. echoing a
    # field name like "author") must not false-positive.
    exc = OperationFailure("field 'author' is required", code=None)
    assert _is_mongo_auth_failure(exc) is False


def test_is_mongo_auth_failure_server_selection_timeout_with_auth_text_true():
    exc = ServerSelectionTimeoutError(
        "cluster0-shard-00-00.mongodb.net:27017: "
        "error message: bad auth : authentication failed"
    )
    assert _is_mongo_auth_failure(exc) is True


def test_is_mongo_auth_failure_server_selection_timeout_non_auth_false():
    exc = ServerSelectionTimeoutError("connection refused")
    assert _is_mongo_auth_failure(exc) is False


# --- 2. Decorator behavior --------------------------------------------------


@pytest.mark.asyncio
async def test_decorator_auth_operation_failure_reraises_and_records(fake_redis):
    exc = OperationFailure("bad auth : authentication failed", code=8000)

    @track_mongo_auth_failures
    async def flaky_job():
        raise exc

    with pytest.raises(OperationFailure):
        await flaky_job()

    assert await fake_redis.get(MONGO_AUTH_FAILURE_COUNT_KEY) == "1"


@pytest.mark.asyncio
async def test_decorator_non_auth_operation_failure_reraises_without_recording(
    fake_redis,
):
    exc = OperationFailure("interrupted at shutdown", code=11600)

    @track_mongo_auth_failures
    async def flaky_job():
        raise exc

    with pytest.raises(OperationFailure):
        await flaky_job()

    assert await fake_redis.get(MONGO_AUTH_FAILURE_COUNT_KEY) is None


@pytest.mark.asyncio
async def test_decorator_auth_server_selection_timeout_reraises_and_records(
    fake_redis,
):
    exc = ServerSelectionTimeoutError("error message: bad auth : authentication failed")

    @track_mongo_auth_failures
    async def flaky_job():
        raise exc

    with pytest.raises(ServerSelectionTimeoutError):
        await flaky_job()

    assert await fake_redis.get(MONGO_AUTH_FAILURE_COUNT_KEY) == "1"


@pytest.mark.asyncio
async def test_decorator_non_auth_server_selection_timeout_reraises_without_recording(
    fake_redis,
):
    exc = ServerSelectionTimeoutError("connection refused")

    @track_mongo_auth_failures
    async def flaky_job():
        raise exc

    with pytest.raises(ServerSelectionTimeoutError):
        await flaky_job()

    assert await fake_redis.get(MONGO_AUTH_FAILURE_COUNT_KEY) is None


@pytest.mark.asyncio
async def test_decorator_autoreconnect_not_caught_and_not_recorded(fake_redis):
    """AutoReconnect (broad connectivity collateral) is deliberately excluded
    from the except clause — it must propagate untouched, and never bump the
    auth-failure counter, even if its text happened to mention auth."""
    exc = AutoReconnect("bad auth : authentication failed")

    @track_mongo_auth_failures
    async def flaky_job():
        raise exc

    with pytest.raises(AutoReconnect):
        await flaky_job()

    assert await fake_redis.get(MONGO_AUTH_FAILURE_COUNT_KEY) is None


@pytest.mark.asyncio
async def test_decorator_normal_return_passes_through_untouched(fake_redis):
    @track_mongo_auth_failures
    async def healthy_job(x, y=2):
        return x + y

    result = await healthy_job(1, y=3)

    assert result == 4
    assert await fake_redis.get(MONGO_AUTH_FAILURE_COUNT_KEY) is None


@pytest.mark.asyncio
async def test_decorator_preserves_wrapped_function_name():
    @track_mongo_auth_failures
    async def some_named_job():
        return None

    assert some_named_job.__name__ == "some_named_job"


# --- 3. Escalation threshold + re-page throttle (stdlib CRITICAL) ----------


@pytest.mark.asyncio
async def test_escalation_first_two_failures_no_critical(monkeypatch, fake_redis):
    mock_critical = _patched_stdlib_critical(monkeypatch)
    exc = OperationFailure("bad auth : authentication failed", code=8000)

    await _record_mongo_auth_failure("run_sos_healer", exc)
    await _record_mongo_auth_failure("run_sos_healer", exc)

    assert await fake_redis.get(MONGO_AUTH_FAILURE_COUNT_KEY) == "2"
    assert WorkerConstants.SCHEDULER_MONGO_AUTH_TRIP_THRESHOLD == 3
    mock_critical.assert_not_called()


@pytest.mark.asyncio
async def test_escalation_third_failure_logs_critical_exactly_once(
    monkeypatch, fake_redis
):
    mock_critical = _patched_stdlib_critical(monkeypatch)
    exc = OperationFailure("bad auth : authentication failed", code=8000)

    await _record_mongo_auth_failure("run_sos_healer", exc)
    await _record_mongo_auth_failure("run_sos_healer", exc)
    await _record_mongo_auth_failure("run_sos_healer", exc)

    assert await fake_redis.get(MONGO_AUTH_FAILURE_COUNT_KEY) == "3"
    mock_critical.assert_called_once()
    assert await fake_redis.get(MONGO_AUTH_FAILURE_PAGED_KEY) == "1"


@pytest.mark.asyncio
async def test_escalation_fourth_failure_throttled_no_second_critical(
    monkeypatch, fake_redis
):
    mock_critical = _patched_stdlib_critical(monkeypatch)
    exc = OperationFailure("bad auth : authentication failed", code=8000)

    for _ in range(3):
        await _record_mongo_auth_failure("run_sos_healer", exc)
    assert mock_critical.call_count == 1

    mock_critical.reset_mock()

    # 4th failure — paged key still present (TTL = REALERT_SECONDS), so the
    # re-page throttle should suppress a second CRITICAL.
    await _record_mongo_auth_failure("run_sos_healer", exc)

    assert await fake_redis.get(MONGO_AUTH_FAILURE_COUNT_KEY) == "4"
    mock_critical.assert_not_called()


@pytest.mark.asyncio
async def test_escalation_rolling_window_ttl_refreshed_on_every_failure(fake_redis):
    exc = OperationFailure("bad auth : authentication failed", code=8000)

    await _record_mongo_auth_failure("run_sos_healer", exc)

    ttl = await fake_redis.ttl(MONGO_AUTH_FAILURE_COUNT_KEY)
    assert 0 < ttl <= WorkerConstants.SCHEDULER_MONGO_AUTH_WINDOW_SECONDS


# --- 4. Fail-open on broken Redis (loguru warning) --------------------------


@pytest.mark.asyncio
async def test_record_fails_open_when_get_redis_client_raises(monkeypatch):
    import app.core.redis_client as redis_client_module

    monkeypatch.setattr(
        redis_client_module,
        "get_redis_client",
        AsyncMock(side_effect=Exception("redis down")),
    )
    mock_logger = MagicMock()
    monkeypatch.setattr(scheduler_module, "logger", mock_logger)
    mock_critical = _patched_stdlib_critical(monkeypatch)
    exc = OperationFailure("bad auth : authentication failed", code=8000)

    # Must not raise.
    await _record_mongo_auth_failure("run_sos_healer", exc)

    mock_logger.warning.assert_called_once()
    mock_critical.assert_not_called()


@pytest.mark.asyncio
async def test_decorator_still_reraises_original_exception_when_redis_down(
    monkeypatch,
):
    import app.core.redis_client as redis_client_module

    monkeypatch.setattr(
        redis_client_module,
        "get_redis_client",
        AsyncMock(side_effect=Exception("redis down")),
    )
    exc = OperationFailure("bad auth : authentication failed", code=8000)

    @track_mongo_auth_failures
    async def flaky_job():
        raise exc

    with pytest.raises(OperationFailure) as excinfo:
        await flaky_job()

    assert excinfo.value is exc


# --- 5. Tracking-failure guard: _record_mongo_auth_failure itself raises ---


@pytest.mark.asyncio
async def test_decorator_reraises_original_even_if_recording_itself_raises(
    monkeypatch,
):
    mock_logger = MagicMock()
    monkeypatch.setattr(scheduler_module, "logger", mock_logger)
    monkeypatch.setattr(
        scheduler_module,
        "_record_mongo_auth_failure",
        AsyncMock(side_effect=RuntimeError("boom inside tracker")),
    )
    exc = OperationFailure("bad auth : authentication failed", code=8000)

    @track_mongo_auth_failures
    async def flaky_job():
        raise exc

    with pytest.raises(OperationFailure) as excinfo:
        await flaky_job()

    assert excinfo.value is exc
    mock_logger.warning.assert_called_once()
    assert "auth-failure tracking failed" in mock_logger.warning.call_args[0][0]


# --- 6. Atomic pipeline + ResponseError (corrupted counter) recovery -------


@pytest.mark.asyncio
async def test_response_error_on_incr_resets_counter_to_one_with_ttl(fake_redis):
    await fake_redis.set(MONGO_AUTH_FAILURE_COUNT_KEY, "not-a-number")
    exc = OperationFailure("bad auth : authentication failed", code=8000)

    # Must not raise despite the corrupted (non-integer) value.
    await _record_mongo_auth_failure("run_sos_healer", exc)

    assert await fake_redis.get(MONGO_AUTH_FAILURE_COUNT_KEY) == "1"
    ttl = await fake_redis.ttl(MONGO_AUTH_FAILURE_COUNT_KEY)
    assert 0 < ttl <= WorkerConstants.SCHEDULER_MONGO_AUTH_WINDOW_SECONDS


@pytest.mark.asyncio
async def test_response_error_recovery_then_reaches_threshold(monkeypatch, fake_redis):
    """After a corrupted-counter reset, the streak still correctly climbs to
    the trip threshold and pages — proving the reset doesn't permanently
    disable escalation."""
    mock_critical = _patched_stdlib_critical(monkeypatch)
    await fake_redis.set(MONGO_AUTH_FAILURE_COUNT_KEY, "not-a-number")
    exc = OperationFailure("bad auth : authentication failed", code=8000)

    await _record_mongo_auth_failure("run_sos_healer", exc)  # reset -> 1
    assert await fake_redis.get(MONGO_AUTH_FAILURE_COUNT_KEY) == "1"
    mock_critical.assert_not_called()

    await _record_mongo_auth_failure("run_sos_healer", exc)  # 2
    await _record_mongo_auth_failure("run_sos_healer", exc)  # 3 -> critical

    assert await fake_redis.get(MONGO_AUTH_FAILURE_COUNT_KEY) == "3"
    mock_critical.assert_called_once()
