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
# a URL path (the Green API token in `/waInstance<id>/sendMessage/<token>`), or an
# exception string. Built once at import from settings; empty/unset secrets are
# skipped so nothing over-redacts (e.g. WEBHOOK_TOKEN is optional).
_SECRET_VALUES = [v for v in (settings.GREEN_API_TOKEN, settings.WEBHOOK_TOKEN) if v]


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
    """Loguru sink filter: always mask PII and redact secrets before writing."""
    record["message"] = redact_secrets(mask_pii(record["message"]))
    return True


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

        frame, depth = logging.currentframe(), 2
        while frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def setup_logging():
    """
    Configure Loguru and intercept standard logging.
    """
    logger.remove()

    # Determine format based on environment
    is_production = settings.ENVIRONMENT.lower() == "production"

    if is_production:
        # Structured JSON logging for production
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
        diagnose=not is_production,
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
__all__ = ["logger", "setup_logging"]
