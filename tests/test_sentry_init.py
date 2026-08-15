"""PRO-113 follow-up — sentry-sdk 2.x auto-enables ``LoguruIntegration`` at
``event_level=ERROR`` the moment loguru is importable, regardless of whether
the app ever asked for it. That auto-enabled integration (a) duplicated every
page as a second Sentry issue and (b) shipped raw, unscrubbed loguru text to
Sentry — bypassing ``app/core/logger.py``'s ``_pii_filter``, since Sentry's
own loguru sink has no filter of its own.

Covers:
  1. ``app.main._init_sentry`` / ``app.worker._init_sentry`` both pass
     ``disabled_integrations=[LoguruIntegration()]`` — the resulting client
     has no ``loguru`` integration but does have ``logging`` (the one
     ``page_critical`` actually depends on).
  2. ``_is_logging_machinery_frame`` — the frame-walk predicate
     ``InterceptHandler.emit`` uses to step over stdlib logging *and*
     sentry_sdk's ``LoggingIntegration`` frame (``sentry_sdk.integrations.
     logging.sentry_patched_callhandlers`` sits mid-walk once Sentry is
     active and would otherwise become the rendered "caller").
  3. End-to-end: with a live Sentry client (dummy DSN, network-free) and its
     ``LoggingIntegration`` actually patching ``logging.Logger.callHandlers``,
     a ``page_critical(...)`` call made from a named function renders with
     that function's name — not ``sentry_patched_callhandlers`` — and PII
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
autouse teardown that calls ``sentry_sdk.init(dsn=None)`` — a client with no
DSN gets no transport, so nothing can transmit even if some other code calls
``sentry_sdk.init()`` again unexpectedly, and later tests never inherit a
live client.
"""

import logging
import sys
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

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
def _reset_sentry_client():
    yield
    # Only touch sentry_sdk if this test (or an earlier one in the same
    # process) actually imported it — importing it here ourselves would
    # defeat the point of deferring the import in the first place.
    module = sys.modules.get("sentry_sdk")
    if module is not None:
        module.init(dsn=None)


def test_main_init_sentry_disables_loguru_integration(monkeypatch, sentry_sdk):
    import app.main as main_module

    monkeypatch.setattr(main_module.settings, "SENTRY_DSN", DUMMY_DSN)

    main_module._init_sentry()

    client = sentry_sdk.get_client()
    assert "loguru" not in client.integrations
    assert "logging" in client.integrations


def test_worker_init_sentry_disables_loguru_integration(monkeypatch, sentry_sdk):
    import app.worker as worker_module

    monkeypatch.setattr(worker_module.settings, "SENTRY_DSN", DUMMY_DSN)

    worker_module._init_sentry()

    client = sentry_sdk.get_client()
    assert "loguru" not in client.integrations
    assert "logging" in client.integrations


def test_main_init_sentry_noop_without_dsn(monkeypatch, sentry_sdk):
    """The existing no-op guard must still short-circuit before touching
    sentry_sdk at all — this is the same branch app/main.py has always had;
    pinning it here guards against the disabled_integrations change having
    moved the DSN check."""
    import app.main as main_module

    monkeypatch.setattr(main_module.settings, "SENTRY_DSN", None)
    before = sentry_sdk.get_client()

    main_module._init_sentry()

    assert sentry_sdk.get_client() is before


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
    from sentry_sdk.integrations.logging import LoggingIntegration
    from sentry_sdk.integrations.loguru import LoguruIntegration

    import app.main as main_module

    monkeypatch.setattr(main_module.settings, "SENTRY_DSN", DUMMY_DSN)
    sentry_sdk.init(
        dsn=DUMMY_DSN.get_secret_value(),
        integrations=[
            LoggingIntegration(level=logging.INFO, event_level=logging.CRITICAL)
        ],
        disabled_integrations=[LoguruIntegration()],
        traces_sample_rate=0.0,
        send_default_pii=False,
        attach_stacktrace=True,
        include_local_variables=False,
    )
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
