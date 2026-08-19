"""Shared Sentry init (``app/core/sentry.py``) — the single ``init_sentry``
that replaced the two hand-synced ``_init_sentry`` copies in ``app/main.py``
and ``app/worker.py`` (plus the third mirror in ``scripts/fire_test_page.py``).

Covers:
  1. ``init_sentry`` passes ``disabled_integrations=[LoguruIntegration()]`` —
     the resulting client has no ``loguru`` integration but does have
     ``logging`` (the one ``page_critical`` actually depends on).
  2. ``auto_enabling_integrations=False`` + ``before_send=_scrub_event`` are
     actually set on the client — the two options that close the sdk-2.x
     auto-enabled side doors (FastApi/Arq/PyMongo/Redis/Httpx events used to
     ship unscrubbed).
  3. Idempotency: a second ``init_sentry`` call is a no-op returning the
     first outcome (this is also the Streamlit rerun guard).
  4. The no-DSN path never imports sentry_sdk (constrains the lazy-import
     structure that ``tests/test_fire_test_page.py`` also depends on).
  5. ``_is_logging_machinery_frame`` — the frame-walk predicate
     ``InterceptHandler.emit`` uses to step over stdlib logging *and*
     sentry_sdk's ``LoggingIntegration`` frame.
  6. End-to-end: with a live Sentry client (dummy DSN, network-free) whose
     ``LoggingIntegration`` actually patches ``logging.Logger.callHandlers``,
     ``page_critical(...)`` renders with the true caller's name and PII
     stays masked.

Import hygiene: every ``sentry_sdk`` import in this file lives *inside* a
fixture or test function, never at module scope. pytest's collection phase
imports every test module up front, before any test body runs — a
module-level ``import sentry_sdk`` would land in ``sys.modules`` the moment
this file is collected, which would break
``tests/test_fire_test_page.py::test_main_no_dsn_returns_1_and_never_reaches_sentry_import``'s
"sentry_sdk was never imported" assertion regardless of which file's tests
*execute* first.

Global-state hygiene: sentry_sdk keeps one process-wide client behind a Hub.
Every test that calls a real ``sentry_sdk.init(dsn=...)`` is paired with an
autouse teardown that calls ``sentry_sdk.init(dsn=None)``, and
``app.core.sentry``'s module flags are reset around every test so one test's
init can't satisfy (or poison) the next test's assertion.
"""

import logging
import sys
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

import app.core.sentry as sentry_module
from app.core.logger import (
    _is_logging_machinery_frame,
    logger as loguru_logger,
    page_critical,
)

# init() succeeds fully offline against a syntactically valid DSN — nothing
# is transmitted unless something calls sentry_sdk.flush()/capture_event, and
# these tests never do either.
DUMMY_DSN = SecretStr("https://00000000000000000000000000000000@o0.ingest.sentry.io/0")


@pytest.fixture
def sentry_sdk():
    """Import sentry_sdk lazily, at fixture-setup time (i.e. only when a test
    that requests it actually runs) rather than at module collection time.
    Skips the test cleanly if sentry-sdk isn't installed."""
    return pytest.importorskip("sentry_sdk")


@pytest.fixture(autouse=True)
def _reset_sentry_state(monkeypatch):
    """Reset both the module idempotency flags (so each test exercises a
    fresh init) and, after the test, the process-wide sentry client."""
    monkeypatch.setattr(sentry_module, "_initialized", False)
    monkeypatch.setattr(sentry_module, "_active", False)
    yield
    # Only touch sentry_sdk if this test (or an earlier one in the same
    # process) actually imported it — importing it here ourselves would
    # defeat the point of deferring the import in the first place.
    module = sys.modules.get("sentry_sdk")
    if module is not None:
        module.init(dsn=None)


def test_init_sentry_disables_loguru_integration(monkeypatch, sentry_sdk):
    monkeypatch.setattr(sentry_module.settings, "SENTRY_DSN", DUMMY_DSN)

    assert sentry_module.init_sentry("proli-api") is True

    client = sentry_sdk.get_client()
    assert "loguru" not in client.integrations
    assert "logging" in client.integrations


def test_init_sentry_disables_auto_enabling_and_sets_scrubber(monkeypatch, sentry_sdk):
    """The PR-A core: auto-enabling off (no FastApi/Arq/PyMongo/Redis/Httpx
    side doors) and every outgoing event routed through _scrub_event."""
    monkeypatch.setattr(sentry_module.settings, "SENTRY_DSN", DUMMY_DSN)

    sentry_module.init_sentry("proli-worker")

    client = sentry_sdk.get_client()
    assert client.options["auto_enabling_integrations"] is False
    assert client.options["before_send"] is sentry_module._scrub_event
    assert client.options["include_local_variables"] is False
    assert client.options["send_default_pii"] is False
    # Allowlist is currently empty beyond LoggingIntegration: none of the
    # previously auto-enabled integrations may be present.
    for side_door in ("fastapi", "starlette", "arq", "pymongo", "redis", "httpx"):
        assert side_door not in client.integrations, side_door


def test_init_sentry_is_idempotent(monkeypatch, sentry_sdk):
    """Second call must not re-init (Streamlit reruns its script on every
    interaction; module state persists in the server process)."""
    monkeypatch.setattr(sentry_module.settings, "SENTRY_DSN", DUMMY_DSN)

    assert sentry_module.init_sentry("proli-admin") is True
    client_after_first = sentry_sdk.get_client()

    calls = []
    monkeypatch.setattr(sentry_sdk, "init", lambda *a, **k: calls.append(k))
    assert sentry_module.init_sentry("proli-admin") is True
    assert calls == []
    assert sentry_sdk.get_client() is client_after_first
    assert sentry_module.sentry_active() is True


def test_init_sentry_noop_without_dsn_never_imports_sdk(monkeypatch):
    """The no-op guard must short-circuit before touching sentry_sdk at all —
    the property tests/test_fire_test_page.py builds on."""
    monkeypatch.setattr(sentry_module.settings, "SENTRY_DSN", None)
    monkeypatch.delitem(sys.modules, "sentry_sdk", raising=False)

    assert sentry_module.init_sentry("proli-api") is False
    assert sentry_module.sentry_active() is False
    assert "sentry_sdk" not in sys.modules


class TestIsLoggingMachineryFrame:
    """No sentry_sdk import needed here at all — pure frame-filename check."""

    def _frame(self, filename: str):
        return SimpleNamespace(f_code=SimpleNamespace(co_filename=filename))

    def test_true_for_stdlib_logging_file(self):
        assert _is_logging_machinery_frame(self._frame(logging.__file__)) is True

    def test_true_for_sentry_logging_integration_forward_slash(self):
        frame = self._frame(
            "/home/app/venv/lib/python3.12/site-packages/sentry_sdk/integrations/logging.py"
        )
        assert _is_logging_machinery_frame(frame) is True

    def test_true_for_sentry_logging_integration_backslash(self):
        frame = self._frame(
            r"D:\Projects\proli\venv\Lib\site-packages\sentry_sdk\integrations\logging.py"
        )
        assert _is_logging_machinery_frame(frame) is True

    def test_false_for_app_file(self):
        frame = self._frame(r"D:\Projects\proli\app\services\pro_flow.py")
        assert _is_logging_machinery_frame(frame) is False

    def test_false_for_other_sentry_integration_file(self):
        """Only the logging integration's frame is stepped over — a frame
        from some other sentry_sdk integration must not be swallowed too."""
        frame = self._frame("/venv/lib/site-packages/sentry_sdk/integrations/stdlib.py")
        assert _is_logging_machinery_frame(frame) is False


def test_page_critical_renders_true_caller_not_sentry_patched_callhandlers(
    monkeypatch, sentry_sdk
):
    """The strongest check: with a live Sentry client whose LoggingIntegration
    is actually patching ``logging.Logger.callHandlers`` (not just configured
    — patched), a real ``page_critical`` call must still render with the
    calling function's name. Before PRO-113's frame-walk fix this rendered as
    ``sentry_patched_callhandlers`` for every single call site."""
    monkeypatch.setattr(sentry_module.settings, "SENTRY_DSN", DUMMY_DSN)
    assert sentry_module.init_sentry("proli-api") is True
    # Sanity: LoggingIntegration really is wired into this client, not just
    # requested — otherwise this test would pass for the wrong reason.
    assert "logging" in sentry_sdk.get_client().integrations

    captured = []
    sink_id = loguru_logger.add(
        lambda msg: captured.append(str(msg)), format="{function}", level=0
    )

    def a_uniquely_named_caller():
        page_critical("customer 972521234567 reported an outage")

    try:
        a_uniquely_named_caller()
    finally:
        loguru_logger.remove(sink_id)

    assert captured, "page_critical did not reach the loguru sink"
    rendered_functions = "".join(captured)
    assert "a_uniquely_named_caller" in rendered_functions
    assert "sentry_patched_callhandlers" not in rendered_functions
    assert "callHandlers" not in rendered_functions
    # PII masking still applies on the render path, Sentry client active or not.
    assert "972521234567" not in rendered_functions
