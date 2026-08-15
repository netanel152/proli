"""PRO-113 AC4 — scripts/fire_test_page.py's no-DSN guard.

The script is a manual verification tool (run via `railway run ...` against a
real environment), so the only branch worth pinning in the test suite is the
one that's safe and deterministic offline: no ``SENTRY_DSN`` configured means
the script must bail before ever touching ``sentry_sdk`` — that's the branch
that keeps a bare `pytest` run (no Sentry, no network) from being able to
explode here.

Import follows the precedent in tests/test_backup_script.py: `scripts/` has
no `__init__.py`, but pytest's rootdir insertion makes `import scripts.<mod>`
work as an implicit namespace package.
"""

import builtins

import pytest
from unittest.mock import MagicMock

import scripts.fire_test_page as fire_test_page_module


class _SentrySdkImportAttempted(Exception):
    """Sentinel distinct from ImportError. main()'s own `except ImportError`
    clause would otherwise swallow a real ImportError and let the test pass
    for the wrong reason (a caught-and-handled import still means the import
    statement executed) — this exception type is never caught by main(), so
    if it propagates, the test fails loudly instead of silently."""


def test_main_no_dsn_returns_1_and_never_reaches_sentry_import(monkeypatch):
    """Order-independent version: rather than asserting on `sys.modules`
    (which lands `sentry_sdk` process-wide the moment ANY test — in this
    file, in a sibling file, in this process — imports it, regardless of run
    order), patch `builtins.__import__` to blow up on any `sentry_sdk`/
    `sentry_sdk.*` import and prove the guarded import statement was never
    reached at all, however many other tests already loaded sentry_sdk."""
    monkeypatch.setattr(fire_test_page_module.settings, "SENTRY_DSN", None)
    monkeypatch.setattr(
        fire_test_page_module.sys, "argv", ["fire_test_page.py", "--service", "worker"]
    )
    mock_logger = MagicMock()
    monkeypatch.setattr(fire_test_page_module, "logger", mock_logger)
    # page_critical must never fire on the no-DSN early-return path — there's
    # nothing to verify against.
    mock_page_critical = MagicMock()
    monkeypatch.setattr(fire_test_page_module, "page_critical", mock_page_critical)

    real_import = builtins.__import__

    def _guarded_import(name, *args, **kwargs):
        if name == "sentry_sdk" or name.startswith("sentry_sdk."):
            raise _SentrySdkImportAttempted(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _guarded_import)

    # If main() ever reaches `import sentry_sdk` (or the later
    # `from sentry_sdk.integrations.loguru import LoguruIntegration`),
    # _SentrySdkImportAttempted propagates uncaught (main() only catches
    # ImportError) and fails this test with a clear traceback rather than
    # returning 1 for the wrong reason.
    result = fire_test_page_module.main()

    assert result == 1
    mock_page_critical.assert_not_called()
    # Pin the *specific* early return (no DSN) rather than the sibling
    # ImportError branch a few lines later — both return 1, so the log text
    # is what proves the sentry_sdk import was never attempted.
    mock_logger.error.assert_called_once()
    logged_message = mock_logger.error.call_args[0][0]
    assert "SENTRY_DSN is not set" in logged_message


def test_main_no_dsn_requires_service_argument(monkeypatch):
    """--service is `required=True`; omitting it must fail argparse before
    ever reaching the DSN check, not silently default to something."""
    monkeypatch.setattr(fire_test_page_module.sys, "argv", ["fire_test_page.py"])

    with pytest.raises(SystemExit) as exc_info:
        fire_test_page_module.main()

    assert exc_info.value.code == 2
