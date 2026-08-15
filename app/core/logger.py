import sys
import os
import re
import logging
import json
from datetime import datetime
from loguru import logger
from app.core.config import settings

# PII masking pattern: Israeli phone numbers (972XXXXXXXXX)
# Keeps country code + first 2 digits + last 3 digits, masks the middle.
# Example: 972521234567 → 97252****567
_PHONE_PATTERN = re.compile(r"(972\d{2})(\d+)(\d{3})")


def mask_pii(message: str) -> str:
    """Mask Israeli phone numbers in log messages."""
    return _PHONE_PATTERN.sub(r"\1****\3", message)


# PRO-80: secret values that must never appear in logs, redacted wherever they
# occur — a URL query string (uvicorn access log: `/webhook?token=<WEBHOOK_TOKEN>`),
# a URL path, or an exception string. Built once at import from settings;
# empty/unset secrets are skipped so nothing over-redacts.
#
# PRO-94 replaced the hand-maintained tuple (WEBHOOK_TOKEN only, after PRO-86
# dropped the Green token) with every SecretStr field on Settings. The old list
# carried a standing "remember to append the next credential here" comment —
# exactly the kind of instruction that gets missed. Typing a new field
# SecretStr now enrolls it in redaction automatically.
#
# Second layer by design: SecretStr makes the *repr* leak impossible, this
# catches a value that reached a log line by some other route (an httpx
# exception echoing a URL, a uvicorn access line). Neither alone is enough.
_SECRET_VALUES = settings.iter_secret_values()


def redact_secrets(message: str) -> str:
    """Replace any known secret value with a placeholder. Complements PRO-79
    (which suppressed httpx INFO request logs at the source): this is
    defense-in-depth for any *other* path that echoes a secret — the uvicorn
    access log line, an httpx exception string reaching logger.error, etc."""
    for secret in _SECRET_VALUES:
        if secret in message:
            message = message.replace(secret, "***REDACTED***")
    return message


def _pii_filter(record):
    """Loguru sink filter: always mask PII and redact secrets before writing.

    Secrets first, then PII: a secret that happens to contain a ``972…`` digit
    run would otherwise be mangled by ``mask_pii`` and no longer match
    ``redact_secrets`` (same order as ``page_critical``)."""
    record["message"] = mask_pii(redact_secrets(record["message"]))
    return True


# PRO-113 — the ONLY way to page the operator. sentry_sdk's LoggingIntegration
# hooks the *stdlib* logging module; loguru emits no stdlib LogRecord, so a
# loguru `logger.critical` is stdout-only and has never created a Sentry
# issue. This primitive emits through stdlib (InterceptHandler routes the
# record back into loguru, so the stdout/file sinks still apply), which is
# what actually reaches Sentry's event_level=CRITICAL threshold.
_PAGING_LOGGER = logging.getLogger("proli.paging")


def page_critical(message: str) -> None:
    """Emit a CRITICAL that actually pages (Sentry issue → operator email).

    Scrubbing happens inline, not only at the sink: ``_pii_filter`` mutates
    loguru's own record dict and never touches the stdlib ``LogRecord`` that
    Sentry reads, so sink-side masking can never cover the Sentry payload —
    regardless of handler ordering. ``stacklevel=2`` makes the record (and
    the Sentry culprit) point at the caller, not this helper.

    Deliberately no ``exc_info``: a rendered exception *value* (e.g. a
    pymongo auth error echoing a credentialed URI) would reach Sentry as
    frames/vars that ``redact_secrets`` never sees. Callers interpolate the
    scrubbed ``str(exc)`` into the message instead.
    """
    safe = mask_pii(redact_secrets(message))
    try:
        _PAGING_LOGGER.critical(safe, stacklevel=2)
    except Exception:
        # Paging must never become the failure it reports: the loguru call
        # this replaced could not raise (sinks default to catch=True), and
        # several call sites are documented fail-open paths. `safe`, not
        # `message`: a bare test/aux sink without _pii_filter must never
        # see the unscrubbed text either.
        logger.opt(depth=1).critical(safe)


# Create logs directory if it doesn't exist
log_dir = os.path.join(os.getcwd(), "logs")
if not os.path.exists(log_dir):
    os.makedirs(log_dir)


def json_formatter(record):
    """
    Custom JSON formatter for Loguru.
    """
    subset = {
        "timestamp": record["time"].isoformat(),
        "level": record["level"].name,
        "message": record["message"],
        "module": record["module"],
        "function": record["function"],
        "line": record["line"],
    }

    # Include trace_id if available in extra context
    if "trace_id" in record["extra"]:
        subset["trace_id"] = record["extra"]["trace_id"]

    if record["exception"]:
        subset["exception"] = record["exception"]

    return json.dumps(subset) + "\n"


class InterceptHandler(logging.Handler):
    """
    Redirect standard logging to Loguru.
    """

    def emit(self, record):
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # On Python 3.12 logging.currentframe() returns *this* frame (not a
        # logging-internal one), so the original loguru recipe's loop never
        # ran and every intercepted line rendered as logging:callHandlers.
        # Force the first step, then walk out of the logging machinery.
        frame, depth = logging.currentframe(), 0
        while frame and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1
        # PRO-113: page_critical is a one-line shim in this module; skip it so
        # the rendered location is the real caller (matching stacklevel=2).
        # Identity check, not name — another function merely named
        # page_critical must not be skipped.
        if frame and frame.f_code is page_critical.__code__:
            frame, depth = frame.f_back, depth + 1
        if frame is None:  # walked off the top — depth is now meaningless
            depth = 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def setup_logging():
    """
    Configure Loguru and intercept standard logging.
    """
    logger.remove()

    # Determine format based on environment. PRO-34: staging is prod-like —
    # it gets the same structured JSON + PII filter + diagnose=False as
    # production, so staging logs are a faithful rehearsal of prod logs.
    is_prod_like = settings.is_prod_like

    if is_prod_like:
        # Structured JSON logging for staging/production
        logger.add(
            sys.stdout,
            format="{message}",
            level=settings.LOG_LEVEL,
            filter=_pii_filter,
            serialize=True,
        )
    else:
        # Human-readable for development
        logger.add(
            sys.stdout,
            format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
            level=settings.LOG_LEVEL,
            filter=_pii_filter,
            colorize=True,
        )

    # File handler (always structured/archive, always PII-masked)
    logger.add(
        os.path.join(log_dir, "proli.log"),
        filter=_pii_filter,
        rotation="10 MB",
        retention="10 days",
        level="DEBUG",
        compression="zip",
        enqueue=True,
        backtrace=True,
        diagnose=not is_prod_like,
    )

    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)

    for log_name in ["uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"]:
        logging_logger = logging.getLogger(log_name)
        logging_logger.handlers = [InterceptHandler()]
        logging_logger.propagate = False

    # PRO-79: httpx/httpcore log "HTTP Request: GET <url>" at INFO. Green API puts
    # the auth token in the URL path, so raise these to WARNING to keep the token
    # out of the logs entirely.
    for noisy in ["httpx", "httpcore"]:
        logging.getLogger(noisy).setLevel(logging.WARNING)


setup_logging()
__all__ = ["logger", "setup_logging", "page_critical", "mask_pii", "redact_secrets"]
