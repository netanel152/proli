import sys
import os
from loguru import logger

# Create logs directory if it doesn't exist
log_dir = os.path.join(os.getcwd(), "logs")
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

# Configure logger
logger.remove()  # Remove default handler

# Console handler (Human readable)
logger.add(
    sys.stderr,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    level="INFO"
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

# Export logger
__all__ = ["logger"]
