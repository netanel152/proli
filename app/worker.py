import sys
from arq import run_worker
from app.core.arq_worker import WorkerSettings
from app.core.logger import logger

def main():
    """
    Entry point for the ARQ worker process.
    """
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
