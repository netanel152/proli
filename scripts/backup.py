"""
Proli Database Backup Script

Performs MongoDB backup using mongodump with gzip compression.
Optionally uploads to S3 if AWS credentials are configured.
Manages retention: keeps last 7 daily + 4 weekly backups.

Usage:
    python scripts/backup.py                  # Local backup only
    python scripts/backup.py --upload-s3      # Local + S3 upload
    python scripts/backup.py --cleanup        # Run retention cleanup
"""

import os
import sys
import subprocess
import glob
from datetime import datetime, timedelta
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings
from app.core.logger import logger

BACKUP_DIR = Path(__file__).parent.parent / "backups"
DAILY_RETENTION = 7
WEEKLY_RETENTION = 4


def run_mongodump() -> Path | None:
    """Run mongodump and return the archive path."""
    BACKUP_DIR.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    weekday = datetime.now().strftime("%A").lower()
    archive_name = f"proli_{timestamp}_{weekday}.gz"
    archive_path = BACKUP_DIR / archive_name

    mongo_uri = settings.MONGO_URI
    cmd = [
        "mongodump",
        f"--uri={mongo_uri}",
        f"--archive={archive_path}",
        "--gzip",
    ]

    logger.info(f"Starting MongoDB backup -> {archive_path}")

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300
        )
        if result.returncode != 0:
            logger.error(f"mongodump failed: {result.stderr}")
            return None

        size_mb = archive_path.stat().st_size / (1024 * 1024)
        logger.info(f"Backup complete: {archive_name} ({size_mb:.1f} MB)")
        return archive_path

    except FileNotFoundError:
        logger.error("mongodump not found. Install MongoDB Database Tools.")
        return None
    except subprocess.TimeoutExpired:
        logger.error("mongodump timed out after 300s.")
        return None


def upload_to_s3(archive_path: Path) -> bool:
    """Upload backup archive to S3 if credentials are configured."""
    bucket = getattr(settings, "BACKUP_S3_BUCKET", None)
    if not bucket:
        logger.warning("BACKUP_S3_BUCKET not set, skipping S3 upload.")
        return False

    try:
        import boto3
    except ImportError:
        logger.error("boto3 not installed. Run: pip install boto3")
        return False

    try:
        s3 = boto3.client("s3")
        key = f"proli-backups/{archive_path.name}"
        s3.upload_file(str(archive_path), bucket, key)
        logger.info(f"Uploaded to s3://{bucket}/{key}")
        return True
    except Exception as e:
        logger.error(f"S3 upload failed: {e}")
        return False


def cleanup_old_backups():
    """Retain last N daily + M weekly backups, delete the rest."""
    if not BACKUP_DIR.exists():
        return

    all_backups = sorted(
        BACKUP_DIR.glob("proli_*.gz"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    if not all_backups:
        return

    now = datetime.now()
    cutoff_daily = now - timedelta(days=DAILY_RETENTION)
    cutoff_weekly = now - timedelta(weeks=WEEKLY_RETENTION)

    kept = []
    weekly_kept = set()

    for backup in all_backups:
        mtime = datetime.fromtimestamp(backup.stat().st_mtime)
        age_days = (now - mtime).days

        # Keep all within daily retention
        if age_days <= DAILY_RETENTION:
            kept.append(backup)
            continue

        # Keep one per week within weekly retention
        week_key = mtime.strftime("%Y-W%W")
        if mtime >= cutoff_weekly and week_key not in weekly_kept:
            weekly_kept.add(week_key)
            kept.append(backup)
            continue

        # Delete the rest
        backup.unlink()
        logger.info(f"Deleted old backup: {backup.name}")

    logger.info(f"Retention cleanup: kept {len(kept)}, total files checked: {len(all_backups)}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Proli Database Backup")
    parser.add_argument("--upload-s3", action="store_true", help="Upload to S3 after backup")
    parser.add_argument("--cleanup", action="store_true", help="Run retention cleanup only")
    args = parser.parse_args()

    if args.cleanup:
        cleanup_old_backups()
        return

    archive_path = run_mongodump()
    if not archive_path:
        sys.exit(1)

    if args.upload_s3:
        upload_to_s3(archive_path)

    cleanup_old_backups()
    logger.info("Backup process complete.")


if __name__ == "__main__":
    main()
