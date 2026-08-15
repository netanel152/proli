"""Fire one test operator page and verify it reaches Sentry (PRO-113, AC4).

The PRO-113 evidence run: after deploying the page_critical migration, run
this once per service with that service's environment so the real DSN, the
LoggingIntegration wiring, and the Sentry alert rule are exercised end-to-end:

    railway run python scripts/fire_test_page.py --service worker
    railway run python scripts/fire_test_page.py --service api

Then confirm in Sentry that one issue per service was created (level=fatal,
logger=proli.paging) and attach screenshots to PRO-113. The event is clearly
marked as a test and safe to resolve immediately.
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings  # noqa: E402
from app.core.logger import logger, page_critical  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fire one clearly-marked test CRITICAL through the "
        "page_critical → Sentry paging path (PRO-113 verification)."
    )
    parser.add_argument(
        "--service",
        choices=["api", "worker"],
        required=True,
        help="Which service tag to stamp on the event (proli-api / proli-worker).",
    )
    args = parser.parse_args()

    if not settings.SENTRY_DSN:
        logger.error(
            "SENTRY_DSN is not set — there is no Sentry to verify against. "
            "Run inside the service's environment (e.g. `railway run …`)."
        )
        return 1

    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
    except ImportError:
        logger.error(
            "sentry-sdk is not installed; run `pip install -r requirements.txt`."
        )
        return 1

    # Mirror the services' init (app/main.py / app/worker.py _init_sentry):
    # CRITICAL-only issue creation, INFO breadcrumbs, no PII, no frame locals.
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN.get_secret_value(),
        environment=settings.ENVIRONMENT,
        integrations=[
            LoggingIntegration(level=logging.INFO, event_level=logging.CRITICAL)
        ],
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        send_default_pii=False,
        attach_stacktrace=True,
        include_local_variables=False,
    )
    sentry_sdk.set_tag("service", f"proli-{args.service}")

    page_critical(
        f"🧪 [PRO-113] Test operator page from service 'proli-{args.service}' "
        f"(environment={settings.ENVIRONMENT}). If you can read this in "
        "Sentry, the stdlib paging path works end-to-end. Safe to resolve."
    )
    sentry_sdk.flush(timeout=10)
    logger.info(
        "Test page emitted and flushed. Now confirm the issue exists in "
        "Sentry (level=fatal, logger=proli.paging) and screenshot it for "
        "PRO-113."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
