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
        needs Sentry coverage, it should call `logger.critical(...)` or raise
        and let arq's top-level handler catch it.
      * No-op when SENTRY_DSN is unset. Tests, local dev, and the open-source
        checkout never touch the Sentry API.
    """
    if not settings.SENTRY_DSN:
        logger.info("Sentry disabled (SENTRY_DSN not set).")
        return

    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
    except ImportError:
        logger.warning(
            "SENTRY_DSN is set but sentry-sdk is not installed. "
            "Run `pip install -r requirements.txt`. Continuing without Sentry."
        )
        return

    # LoggingIntegration: breadcrumbs at INFO, but only CRITICAL creates issues.
    logging_integration = LoggingIntegration(
        level=logging.INFO,          # breadcrumb threshold
        event_level=logging.CRITICAL,  # issue-creation threshold
    )

    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.ENVIRONMENT,
        integrations=[logging_integration],
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        # Keep payloads small — no request bodies, no local vars.
        send_default_pii=False,
        attach_stacktrace=True,
    )
    sentry_sdk.set_tag("service", "proli-worker")
    logger.info(f"Sentry initialized (environment={settings.ENVIRONMENT}, CRITICAL-only).")


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
