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

    # PRO-94: unwrapped at the point of use and handed straight to the
    # subprocess. Note it still lands in the child's argv, which is readable by
    # other processes on the same host — acceptable on a single-tenant Railway
    # container, tracked as a follow-up on PRO-94, and the reason this value is
    # never *also* put in a log line.
    mongo_uri = settings.MONGO_URI.get_secret_value()
    cmd = [
        "mongodump",
        f"--uri={mongo_uri}",
        f"--archive={archive_path}",
        "--gzip",
    ]

    logger.info(f"Starting MongoDB backup -> {archive_path}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            logger.error(f"mongodump failed: {result.stderr}")
            archive_path.unlink(missing_ok=True)
            return None

        # PRO-111: a 0-byte/truncated archive uploaded to S3 is the same false
        # confidence this issue is about, one layer down — treat it as failure.
        size = archive_path.stat().st_size if archive_path.exists() else 0
        if size < 1024:
            logger.error(f"mongodump produced a {size}B archive — treating as failure.")
            archive_path.unlink(missing_ok=True)
            return None

        logger.info(f"Backup complete: {archive_name} ({size / (1024 * 1024):.1f} MB)")
        return archive_path

    except FileNotFoundError:
        logger.error("mongodump not found. Install MongoDB Database Tools.")
        return None
    except subprocess.TimeoutExpired:
        logger.error("mongodump timed out after 300s.")
        # Don't leave a truncated .gz behind — retention would count it as a
        # valid daily and it could end up in someone's restore drill.
        archive_path.unlink(missing_ok=True)
        return None


def upload_to_s3(archive_path: Path) -> bool:
    """Upload backup archive to S3 if credentials are configured."""
    bucket = getattr(settings, "BACKUP_S3_BUCKET", None)
    if not bucket:
        # PRO-111: under --upload-s3 this is a hard failure, not a skip — an
        # archive left on Railway's ephemeral disk is wiped on redeploy.
        logger.error("BACKUP_S3_BUCKET not set — no durable target for backup.")
        return False

    try:
        import boto3
    except ImportError:
        logger.error("boto3 not installed. Run: pip install boto3")
        return False

    try:
        # PRO-111: endpoint_url routes to S3-compatible storage (Cloudflare R2)
        # when BACKUP_S3_ENDPOINT is set; unset falls through to regular AWS S3.
        s3 = boto3.client("s3", endpoint_url=settings.BACKUP_S3_ENDPOINT or None)
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

    logger.info(
        f"Retention cleanup: kept {len(kept)}, total files checked: {len(all_backups)}"
    )


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Proli Database Backup")
    parser.add_argument(
        "--upload-s3", action="store_true", help="Upload to S3 after backup"
    )
    parser.add_argument(
        "--cleanup", action="store_true", help="Run retention cleanup only"
    )
    args = parser.parse_args()

    if args.cleanup:
        cleanup_old_backups()
        return

    archive_path = run_mongodump()
    if not archive_path:
        sys.exit(1)

    if args.upload_s3 and not upload_to_s3(archive_path):
        # PRO-111: --upload-s3 is strict. Exiting 0 here made the scheduler
        # log "completed successfully" while nothing durable existed — the
        # exact false confidence this issue is about.
        cleanup_old_backups()
        sys.exit(1)

    cleanup_old_backups()
    logger.info("Backup process complete.")


if __name__ == "__main__":
    main()
