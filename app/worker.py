import logging
import sys

from arq import run_worker

from app.core.arq_worker import WorkerSettings
from app.core.config import settings
from app.core.logger import logger


def _init_sentry() -> None:
    """
    Initialize Sentry if SENTRY_DSN is configured.

    Design choices (see SENTRY_SETUP.md for the full rationale):
      * Worker-only scope. The FastAPI webhook returns 200 OK immediately and
        does almost no business logic — all failures we actually care about
        (stuck leads, reassignment loops, SOS monitor crashes) surface here.
      * CRITICAL-only filter. Regular ERROR/WARNING noise stays in stdout and
        loguru. Sentry is reserved for operator-paging events. If a surface
        needs Sentry coverage, it must call `page_critical(...)` from
        app/core/logger.py (PRO-113: a plain loguru `logger.critical` emits
        no stdlib LogRecord and never reaches Sentry) or raise and let
        arq's top-level handler catch it.
      * No-op when SENTRY_DSN is unset. Tests, local dev, and the open-source
        checkout never touch the Sentry API.
    """
    if not settings.SENTRY_DSN:
        logger.info("Sentry disabled (SENTRY_DSN not set).")
        return

    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
        from sentry_sdk.integrations.loguru import LoguruIntegration
    except ImportError:
        logger.warning(
            "SENTRY_DSN is set but sentry-sdk is not installed. "
            "Run `pip install -r requirements.txt`. Continuing without Sentry."
        )
        return

    # LoggingIntegration: breadcrumbs at INFO, but only CRITICAL creates issues.
    logging_integration = LoggingIntegration(
        level=logging.INFO,  # breadcrumb threshold
        event_level=logging.CRITICAL,  # issue-creation threshold
    )

    sentry_sdk.init(
        dsn=settings.SENTRY_DSN.get_secret_value(),
        # PRO-34: validated + normalized to development|staging|production, so
        # staging reports into its own Sentry environment rather than
        # fragmenting across casing/typo variants of the same label.
        environment=settings.ENVIRONMENT,
        integrations=[logging_integration],
        # PRO-113 follow-up: sentry-sdk AUTO-ENABLES LoguruIntegration
        # (event_level=ERROR) when loguru is installed — an uncontrolled side
        # door that (a) duplicated every page as a second issue and (b) sent
        # loguru ERROR+ messages to Sentry UNSCRUBBED, bypassing _pii_filter
        # (sentry's own loguru sink has no filter). Paging is stdlib-only by
        # design (page_critical); loguru must not reach Sentry at all.
        disabled_integrations=[LoguruIntegration()],
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        send_default_pii=False,
        attach_stacktrace=True,
        # PRO-113: send_default_pii does NOT cover frame locals —
        # include_local_variables defaults to True and attach_stacktrace
        # would ship every frame's locals with each page (the unscrubbed
        # `message` argument, raw phone numbers, whole lead documents, a
        # Mongo exception carrying the URI). Locals stay off.
        include_local_variables=False,
    )
    sentry_sdk.set_tag("service", "proli-worker")
    logger.info(
        f"Sentry initialized (environment={settings.ENVIRONMENT}, CRITICAL-only)."
    )


def main():
    """
    Entry point for the ARQ worker process.
    """
    _init_sentry()
    logger.info("Initializing ARQ Worker...")
    try:
        run_worker(WorkerSettings)
    except Exception as e:
        logger.error(f"ARQ Worker crashed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("ARQ Worker stopped by user.")
