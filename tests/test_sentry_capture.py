"""Exception-capture seams added around the shared Sentry init (PR-B):

* ``should_send`` — the in-process per-fingerprint throttle that keeps a
  perpetually failing scheduler job at ≤24 events/day.
* ``app.scheduler._on_job_error`` — the EVENT_JOB_ERROR listener (the only
  APScheduler seam that sees the exception object). Must never raise.
* ``process_message_task`` — PRO-134 context tags at task start: masked
  chat_id, provider, wamid. The raw phone number must never become a tag.

Import hygiene: no module-level sentry_sdk import (see test_sentry_init.py's
module docstring for why).
"""

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.core.sentry as sentry_module
from app.core.sentry import should_send


@pytest.fixture(autouse=True)
def _fresh_throttle(monkeypatch):
    monkeypatch.setattr(sentry_module, "_throttle", {})


class TestShouldSend:
    def test_first_send_passes_repeat_suppressed(self):
        assert should_send("apscheduler:job_x:ValueError") is True
        assert should_send("apscheduler:job_x:ValueError") is False

    def test_distinct_fingerprints_independent(self):
        assert should_send("apscheduler:job_x:ValueError") is True
        assert should_send("apscheduler:job_y:ValueError") is True
        assert should_send("apscheduler:job_x:KeyError") is True

    def test_resends_after_ttl(self, monkeypatch):
        clock = {"now": 1000.0}
        monkeypatch.setattr(sentry_module.time, "monotonic", lambda: clock["now"])
        assert should_send("fp", ttl_seconds=3600) is True
        clock["now"] += 3599
        assert should_send("fp", ttl_seconds=3600) is False
        clock["now"] += 2
        assert should_send("fp", ttl_seconds=3600) is True

    def test_size_cap_never_grows_unbounded(self):
        for i in range(sentry_module._THROTTLE_MAX_KEYS + 10):
            should_send(f"fp:{i}")
        assert len(sentry_module._throttle) <= sentry_module._THROTTLE_MAX_KEYS


class TestSchedulerJobErrorListener:
    def _event(self, job_id="stale_job_monitor", exc=None):
        return SimpleNamespace(job_id=job_id, exception=exc or ValueError("boom"))

    def _fake_sdk(self):
        sdk = MagicMock()
        scope = MagicMock()
        sdk.isolation_scope.return_value.__enter__ = MagicMock(return_value=scope)
        sdk.isolation_scope.return_value.__exit__ = MagicMock(return_value=False)
        return sdk, scope

    def test_captures_with_job_tag(self, monkeypatch):
        from app import scheduler as scheduler_module

        monkeypatch.setattr(sentry_module, "_active", True)
        sdk, scope = self._fake_sdk()
        monkeypatch.setitem(sys.modules, "sentry_sdk", sdk)

        event = self._event()
        scheduler_module._on_job_error(event)

        scope.set_tag.assert_called_once_with("scheduler_job_id", "stale_job_monitor")
        sdk.capture_exception.assert_called_once_with(event.exception)

    def test_throttles_repeat_failures(self, monkeypatch):
        from app import scheduler as scheduler_module

        monkeypatch.setattr(sentry_module, "_active", True)
        sdk, _ = self._fake_sdk()
        monkeypatch.setitem(sys.modules, "sentry_sdk", sdk)

        scheduler_module._on_job_error(self._event())
        scheduler_module._on_job_error(self._event())  # same job, same exc type

        assert sdk.capture_exception.call_count == 1

    def test_noop_when_sentry_inactive(self, monkeypatch):
        from app import scheduler as scheduler_module

        monkeypatch.setattr(sentry_module, "_active", False)
        sdk, _ = self._fake_sdk()
        monkeypatch.setitem(sys.modules, "sentry_sdk", sdk)

        scheduler_module._on_job_error(self._event())

        sdk.capture_exception.assert_not_called()

    def test_never_raises(self, monkeypatch):
        """Called synchronously from APScheduler's dispatch — an exception
        here would take the scheduler thread down with it."""
        from app import scheduler as scheduler_module

        monkeypatch.setattr(
            sentry_module, "sentry_active", MagicMock(side_effect=RuntimeError)
        )
        scheduler_module._on_job_error(self._event())  # must not raise

    def test_listener_registered(self, monkeypatch):
        """start_scheduler wires _on_job_error to EVENT_JOB_ERROR."""
        from apscheduler.events import EVENT_JOB_ERROR

        from app import scheduler as scheduler_module

        recorded = []

        class FakeScheduler:
            def __init__(self, timezone=None):
                pass

            def add_listener(self, callback, mask):
                recorded.append((callback, mask))

            def add_job(self, *a, **k):
                pass

            def start(self):
                pass

            def get_jobs(self):
                return []

        monkeypatch.setattr(scheduler_module, "AsyncIOScheduler", FakeScheduler)
        scheduler_module.start_scheduler()

        assert (scheduler_module._on_job_error, EVENT_JOB_ERROR) in recorded


class TestTaskContextTags:
    @pytest.mark.asyncio
    async def test_tags_masked_chat_id_provider_wamid(self, monkeypatch):
        from app.core import arq_worker

        monkeypatch.setattr(sentry_module, "_active", True)
        sdk = MagicMock()
        scope = MagicMock()
        sdk.get_isolation_scope.return_value = scope
        monkeypatch.setitem(sys.modules, "sentry_sdk", sdk)
        monkeypatch.setattr(
            arq_worker, "process_incoming_message", AsyncMock(return_value=None)
        )

        await arq_worker.process_message_task(
            {}, "972521234567@c.us", "hi", None, message_id="wamid.TAG1"
        )

        tags = {call.args[0]: call.args[1] for call in scope.set_tag.call_args_list}
        assert tags["chat_id"] == "97252****567@c.us"
        assert "972521234567" not in str(tags)  # raw phone never a tag
        assert tags["provider"] == arq_worker.settings.WHATSAPP_PROVIDER
        assert tags["wamid"] == "wamid.TAG1"

    @pytest.mark.asyncio
    async def test_message_id_optional_for_prequeued_jobs(self, monkeypatch):
        """Jobs enqueued before the kwarg existed must still deserialize
        and run — and no wamid tag is set."""
        from app.core import arq_worker

        monkeypatch.setattr(sentry_module, "_active", True)
        sdk = MagicMock()
        scope = MagicMock()
        sdk.get_isolation_scope.return_value = scope
        monkeypatch.setitem(sys.modules, "sentry_sdk", sdk)
        monkeypatch.setattr(
            arq_worker, "process_incoming_message", AsyncMock(return_value=None)
        )

        await arq_worker.process_message_task({}, "972521234567@c.us", "hi")

        tag_names = [call.args[0] for call in scope.set_tag.call_args_list]
        assert "wamid" not in tag_names

    @pytest.mark.asyncio
    async def test_no_sdk_touch_when_inactive(self, monkeypatch):
        from app.core import arq_worker

        monkeypatch.setattr(sentry_module, "_active", False)
        sdk = MagicMock()
        monkeypatch.setitem(sys.modules, "sentry_sdk", sdk)
        monkeypatch.setattr(
            arq_worker, "process_incoming_message", AsyncMock(return_value=None)
        )

        await arq_worker.process_message_task({}, "972521234567@c.us", "hi")

        sdk.get_isolation_scope.assert_not_called()
