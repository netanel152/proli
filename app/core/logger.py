import sys
import os
import logging
import json
from datetime import datetime
from loguru import logger
from app.core.config import settings

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
            format="{message}", # message is pre-formatted in json_formatter if used as sink
            level=settings.LOG_LEVEL,
            serialize=True # Loguru built-in serialization is often sufficient, but custom can be better
        )
    else:
        # Human-readable for development
        logger.add(
            sys.stdout,
            format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
            level=settings.LOG_LEVEL,
            colorize=True
        )

    # File handler (always structured/archive)
    logger.add(
        os.path.join(log_dir, "proli.log"),
        rotation="10 MB",
        retention="10 days",
        level="DEBUG",
        compression="zip",
        enqueue=True,
        backtrace=True,
        diagnose=not is_production
    )

    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    
    for log_name in ["uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"]:
        logging_logger = logging.getLogger(log_name)
        logging_logger.handlers = [InterceptHandler()]
        logging_logger.propagate = False

setup_logging()
__all__ = ["logger", "setup_logging"]