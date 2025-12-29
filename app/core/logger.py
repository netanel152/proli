import sys
import os
import logging
from loguru import logger

# Create logs directory if it doesn't exist
log_dir = os.path.join(os.getcwd(), "logs")
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

class InterceptHandler(logging.Handler):
    """
    Redirect standard logging to Loguru.
    """
    def emit(self, record):
        # Get corresponding Loguru level if it exists
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find caller from where originated the logged message
        frame, depth = logging.currentframe(), 2
        while frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )

def setup_logging():
    """
    Configure Loguru and intercept standard logging (Uvicorn, etc).
    """
    # Remove default Loguru handler
    logger.remove()

    # Console handler (Human readable for Docker logs)
    # Using sys.stdout for Docker usually works better for ensuring visibility
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level="INFO",
        colorize=True
    )

    # File handler (Structured/Archive)
    logger.add(
        os.path.join(log_dir, "fixi.log"),
        rotation="10 MB",
        retention="10 days",
        level="DEBUG",
        compression="zip",
        enqueue=True, # Thread-safe
        backtrace=True,
        diagnose=True
    )

    # Intercept standard logging
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    
    # Explicitly set Uvicorn loggers to use our handler
    for log_name in ["uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"]:
        logging_logger = logging.getLogger(log_name)
        logging_logger.handlers = [InterceptHandler()]
        logging_logger.propagate = False

# Initialize logging immediately on import
setup_logging()

# Export logger
__all__ = ["logger", "setup_logging"]
