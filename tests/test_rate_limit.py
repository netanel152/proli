"""
PRO-21 — per-customer inbound rate-limit (sliding window) + daily AI/multimodal cap.

Unit tests for the new SecurityService primitives plus end-to-end wiring tests that
drive process_incoming_message and assert pros/admins are exempt and the AI engine is
never invoked once a cap trips.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

import app.services.workflow_service as workflow_service
from app.core.config import settings
from app.core.constants import UserStates, WorkerConstants
from app.core.messages import Messages
from app.services.security_service import SecurityService


# --------------------------------------------------------------------------- #
# Fake Redis with sorted-set + pipeline support (no fakeredis dependency)
# --------------------------------------------------------------------------- #
class _FakePipeline:
    def __init__(self, redis):
        self._redis = redis
        self._ops = []

    def zremrangebyscore(self, key, min_score, max_score):
        self._ops.append(("zrem", key, min_score, max_score))
        return self

    def zadd(self, key, mapping):
        self._ops.append(("zadd", key, mapping))
        return self

    def zcard(self, key):
        self._ops.append(("zcard", key))
        return self

    def expire(self, key, ttl):
        self._ops.append(("expire", key, ttl))
        return self

    async def execute(self):
        results = []
        for op in self._ops:
            kind = op[0]
            if kind == "zrem":
                _, key, lo, hi = op
                zset = self._redis.zsets.setdefault(key, {})
                stale = [m for m, s in zset.items() if lo <= s <= hi]
                for m in stale:
                    del zset[m]
                results.append(len(stale))
            elif kind == "zadd":
                _, key, mapping = op
                self._redis.zsets.setdefault(key, {}).update(mapping)
                results.append(len(mapping))
            elif kind == "zcard":
                _, key = op
                results.append(len(self._redis.zsets.get(key, {})))
            elif kind == "expire":
                results.append(True)
        return results


class _FakeRedis:
    def __init__(self):
        self.zsets = {}
        self.counters = {}
        self.expire_calls = []

    async def incr(self, key):
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    async def expire(self, key, ttl):
        self.expire_calls.append((key, ttl))
        return True

    def pipeline(self):
        return _FakePipeline(self)


def _patch_redis(redis):
    return patch(
        "app.services.security_service.get_redis_client",
        new_callable=AsyncMock,
        return_value=redis,
    )


# --------------------------------------------------------------------------- #
# check_sliding_window
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_sliding_window_allows_under_limit():
    redis = _FakeRedis()
    with _patch_redis(redis):
        for _ in range(WorkerConstants.INBOUND_RATE_LIMIT_MAX):
            assert (
                await SecurityService.check_sliding_window(
                    "c1@c.us", limit=20, window_seconds=60
                )
                is True
            )


@pytest.mark.asyncio
async def test_sliding_window_blocks_over_limit():
    redis = _FakeRedis()
    with _patch_redis(redis):
        results = [
            await SecurityService.check_sliding_window(
                "c1@c.us", limit=3, window_seconds=60
            )
            for _ in range(5)
        ]
    assert results[:3] == [True, True, True]
    assert results[3] is False and results[4] is False


@pytest.mark.asyncio
async def test_sliding_window_is_per_chat():
    redis = _FakeRedis()
    with _patch_redis(redis):
        for _ in range(3):
            await SecurityService.check_sliding_window(
                "c1@c.us", limit=3, window_seconds=60
            )
        # c1 is now full, but c2 is independent
        assert (
            await SecurityService.check_sliding_window(
                "c1@c.us", limit=3, window_seconds=60
            )
            is False
        )
        assert (
            await SecurityService.check_sliding_window(
                "c2@c.us", limit=3, window_seconds=60
            )
            is True
        )


@pytest.mark.asyncio
async def test_sliding_window_fails_open_on_redis_error():
    with patch(
        "app.services.security_service.get_redis_client",
        new_callable=AsyncMock,
        side_effect=Exception("Redis down"),
    ):
        assert (
            await SecurityService.check_sliding_window(
                "c1@c.us", limit=1, window_seconds=60
            )
            is True
        )


# --------------------------------------------------------------------------- #
# check_and_increment_daily_ai_cap
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_daily_cap_allows_then_blocks():
    redis = _FakeRedis()
    with _patch_redis(redis):
        results = [
            await SecurityService.check_and_increment_daily_ai_cap("c1@c.us", cap=2)
            for _ in range(3)
        ]
    assert results == [True, True, False]


@pytest.mark.asyncio
async def test_daily_cap_uses_israel_date_and_sets_ttl_once():
    redis = _FakeRedis()
    expected_date = datetime.now(ZoneInfo(settings.TIMEZONE)).date().isoformat()
    with _patch_redis(redis):
        await SecurityService.check_and_increment_daily_ai_cap("c1@c.us", cap=40)
        await SecurityService.check_and_increment_daily_ai_cap("c1@c.us", cap=40)

    key = f"ai:daily:c1@c.us:{expected_date}"
    assert redis.counters[key] == 2
    # EXPIRE set exactly once, on the first increment, to a full day
    assert redis.expire_calls == [(key, 86400)]


@pytest.mark.asyncio
async def test_daily_cap_fails_open_on_redis_error():
    with patch(
        "app.services.security_service.get_redis_client",
        new_callable=AsyncMock,
        side_effect=Exception("Redis down"),
    ):
        assert (
            await SecurityService.check_and_increment_daily_ai_cap("c1@c.us", cap=1)
            is True
        )


# --------------------------------------------------------------------------- #
# record_trip
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_record_trip_counts_and_expires_once():
    redis = _FakeRedis()
    with _patch_redis(redis):
        assert await SecurityService.record_trip("c1@c.us", window_seconds=60) == 1
        assert await SecurityService.record_trip("c1@c.us", window_seconds=60) == 2
    assert redis.expire_calls == [("rl:trips:c1@c.us", 60)]


@pytest.mark.asyncio
async def test_record_trip_fails_open_to_zero():
    with patch(
        "app.services.security_service.get_redis_client",
        new_callable=AsyncMock,
        side_effect=Exception("Redis down"),
    ):
        assert await SecurityService.record_trip("c1@c.us") == 0


# --------------------------------------------------------------------------- #
# End-to-end wiring through process_incoming_message
# --------------------------------------------------------------------------- #
@pytest.fixture
def wired_mocks(monkeypatch, mock_db):
    """Neutralize the Redis-backed lock + state so the inner handler runs cleanly."""
    monkeypatch.setattr(
        workflow_service, "acquire_chat_lock", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(workflow_service, "release_chat_lock", AsyncMock())
    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=UserStates.IDLE)
    mock_state.set_state = AsyncMock()
    mock_state.clear_state = AsyncMock()
    mock_state.get_metadata = AsyncMock(return_value={})
    monkeypatch.setattr(workflow_service, "StateManager", mock_state)
    return mock_state, mock_db


def _sent_texts(mock_wa):
    return [c.args[1] for c in mock_wa.send_message.call_args_list]


@pytest.mark.asyncio
async def test_customer_over_inbound_limit_is_throttled(wired_mocks):
    """A non-exempt customer over the inbound limit gets RATE_LIMITED and never hits AI."""
    monkeypatch_target = patch.object(
        workflow_service.SecurityService,
        "check_sliding_window",
        new=AsyncMock(return_value=False),
    )
    trip_target = patch.object(
        workflow_service.SecurityService,
        "record_trip",
        new=AsyncMock(return_value=1),
    )
    with monkeypatch_target, trip_target:
        await workflow_service.process_incoming_message(
            "972500000001@c.us", "שלום, יש לי נזילה"
        )

    assert Messages.Errors.RATE_LIMITED in _sent_texts(workflow_service.whatsapp)
    workflow_service.ai.analyze_conversation.assert_not_awaited()


@pytest.mark.asyncio
async def test_pro_is_exempt_from_inbound_limit(wired_mocks):
    """A pro (PRO_MODE) is never even checked against the inbound limiter."""
    mock_state, _ = wired_mocks
    mock_state.get_state = AsyncMock(return_value=UserStates.PRO_MODE)
    check = AsyncMock(return_value=False)
    with patch.object(
        workflow_service.SecurityService, "check_sliding_window", new=check
    ), patch.object(
        workflow_service, "_handle_pro_cmd", new=AsyncMock(return_value="ok")
    ):
        await workflow_service.process_incoming_message("972511111111@c.us", "תפריט")

    check.assert_not_awaited()
    assert Messages.Errors.RATE_LIMITED not in _sent_texts(workflow_service.whatsapp)
