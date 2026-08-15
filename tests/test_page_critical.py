"""PRO-113 — ``page_critical`` is the ONLY operator-paging primitive.

Sentry's ``LoggingIntegration`` only hooks *stdlib* ``logging`` records — a
loguru ``logger.critical`` never reached it (loguru emits no stdlib
``LogRecord`` on its own). ``page_critical`` closes that gap: it emits
through ``logging.getLogger("proli.paging").critical(...)``, which
``InterceptHandler`` (wired in ``setup_logging()``) routes back into loguru
so the stdout/file sinks still see it too.

Covers:
  1. a call produces a stdlib CRITICAL LogRecord on logger ``proli.paging``
  2. PII masking is applied inline (Sentry copies the record before loguru's
     sink-level ``_pii_filter`` ever runs, so the masking must happen before
     the record is emitted, not only at the sink)
  3. secret redaction is applied inline, same reasoning
  4. the record's level/name are exactly what Sentry's ``event_level``
     threshold and issue grouping key off
  5. ``stacklevel=2`` makes the record point at the caller, not ``logger.py``
"""

import logging
from pathlib import Path

import app.core.logger as logmod
from app.core.logger import page_critical


def test_page_critical_emits_stdlib_critical_record_on_proli_paging(caplog):
    with caplog.at_level(logging.CRITICAL, logger="proli.paging"):
        page_critical("system on fire")

    records = [r for r in caplog.records if r.name == "proli.paging"]
    assert len(records) == 1
    assert records[0].levelno == logging.CRITICAL
    assert "system on fire" in records[0].message


def test_page_critical_masks_pii_phone_number(caplog):
    with caplog.at_level(logging.CRITICAL, logger="proli.paging"):
        page_critical("customer 972521234567 reported an outage")

    record = next(r for r in caplog.records if r.name == "proli.paging")
    assert "972521234567" not in record.message
    assert "97252****567" in record.message


def test_page_critical_redacts_known_secret(monkeypatch, caplog):
    monkeypatch.setattr(logmod, "_SECRET_VALUES", ("hunter2",))

    with caplog.at_level(logging.CRITICAL, logger="proli.paging"):
        page_critical("mongo auth failed with password hunter2")

    record = next(r for r in caplog.records if r.name == "proli.paging")
    assert "hunter2" not in record.message
    assert "***REDACTED***" in record.message


def test_page_critical_record_levelname_and_logger_name(caplog):
    with caplog.at_level(logging.CRITICAL, logger="proli.paging"):
        page_critical("page me")

    record = next(r for r in caplog.records if r.name == "proli.paging")
    assert record.levelname == "CRITICAL"
    assert record.name == "proli.paging"


def test_page_critical_stacklevel_points_at_caller_not_logger_module(caplog):
    """stacklevel=2 in the ``_PAGING_LOGGER.critical(...)`` call makes the
    record's culprit the calling module (this test file), not
    app/core/logger.py — otherwise every Sentry issue would show the same
    unhelpful culprit regardless of which of the 12 call sites paged."""
    with caplog.at_level(logging.CRITICAL, logger="proli.paging"):
        page_critical("where did this come from")

    record = next(r for r in caplog.records if r.name == "proli.paging")
    assert not record.pathname.endswith("logger.py")
    assert record.pathname.endswith("test_page_critical.py")


def test_no_loguru_logger_critical_calls_under_app():
    """PRO-113 regression guard: ``page_critical`` is the ONLY way to page —
    a reintroduced loguru ``logger.critical(`` under app/ would silently stop
    reaching Sentry again (loguru emits no stdlib LogRecord, so
    LoggingIntegration never sees it) without any test failing to say so.

    Mirrors the repo's existing CI-guard philosophy (the vendor-domain guard
    and the outbound-egress guard in .github/workflows/tests.yml): a plain
    text scan, not an AST check, so it also catches the call syntax showing
    up in a comment/docstring — which is exactly the kind of copy-pasted
    "example" that later gets uncommented back into real code."""
    root = Path(__file__).resolve().parents[1]
    offenders = []
    for path in (root / "app").rglob("*.py"):
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if "logger.critical(" in line:
                offenders.append(f"{path.relative_to(root)}:{lineno}: {line.strip()}")

    assert not offenders, (
        "found loguru `logger.critical(` under app/ — loguru CRITICAL never "
        "reaches Sentry's LoggingIntegration (it emits no stdlib LogRecord). "
        "Use `page_critical(...)` from app.core.logger instead:\n"
        + "\n".join(offenders)
    )
