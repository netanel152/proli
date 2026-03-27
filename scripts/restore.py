"""
Proli Database Restore Script

Restores MongoDB from a gzip backup archive created by backup.py.

Usage:
    python scripts/restore.py backups/proli_20260326_020000_thursday.gz
    python scripts/restore.py --latest              # Restore most recent backup
    python scripts/restore.py --from-s3 <key>       # Download from S3 then restore
"""

import os
import sys
import subprocess
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings
from app.core.logger import logger

BACKUP_DIR = Path(__file__).parent.parent / "backups"


def find_latest_backup() -> Path | None:
    """Find the most recent backup file."""
    if not BACKUP_DIR.exists():
        return None
    backups = sorted(
        BACKUP_DIR.glob("proli_*.gz"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return backups[0] if backups else None


def download_from_s3(s3_key: str) -> Path | None:
    """Download a backup from S3."""
    bucket = getattr(settings, "BACKUP_S3_BUCKET", None)
    if not bucket:
        logger.error("BACKUP_S3_BUCKET not set.")
        return None

    try:
        import boto3
    except ImportError:
        logger.error("boto3 not installed. Run: pip install boto3")
        return None

    BACKUP_DIR.mkdir(exist_ok=True)
    local_path = BACKUP_DIR / Path(s3_key).name

    try:
        s3 = boto3.client("s3")
        s3.download_file(bucket, s3_key, str(local_path))
        logger.info(f"Downloaded s3://{bucket}/{s3_key} -> {local_path}")
        return local_path
    except Exception as e:
        logger.error(f"S3 download failed: {e}")
        return None


def run_mongorestore(archive_path: Path, drop: bool = True) -> bool:
    """Run mongorestore from a gzip archive."""
    if not archive_path.exists():
        logger.error(f"Archive not found: {archive_path}")
        return False

    mongo_uri = settings.MONGO_URI
    cmd = [
        "mongorestore",
        f"--uri={mongo_uri}",
        f"--archive={archive_path}",
        "--gzip",
    ]
    if drop:
        cmd.append("--drop")

    logger.info(f"Restoring from {archive_path} (drop={'yes' if drop else 'no'})...")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            logger.error(f"mongorestore failed: {result.stderr}")
            return False

        logger.info("Restore complete.")
        return True

    except FileNotFoundError:
        logger.error("mongorestore not found. Install MongoDB Database Tools.")
        return False
    except subprocess.TimeoutExpired:
        logger.error("mongorestore timed out after 600s.")
        return False


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Proli Database Restore")
    parser.add_argument("archive", nargs="?", help="Path to backup archive")
    parser.add_argument("--latest", action="store_true", help="Restore most recent local backup")
    parser.add_argument("--from-s3", metavar="KEY", help="S3 key to download and restore")
    parser.add_argument("--no-drop", action="store_true", help="Don't drop existing collections before restore")
    args = parser.parse_args()

    archive_path = None

    if args.from_s3:
        archive_path = download_from_s3(args.from_s3)
    elif args.latest:
        archive_path = find_latest_backup()
        if not archive_path:
            logger.error("No local backups found.")
            sys.exit(1)
        logger.info(f"Latest backup: {archive_path.name}")
    elif args.archive:
        archive_path = Path(args.archive)
    else:
        parser.print_help()
        sys.exit(1)

    if not archive_path:
        sys.exit(1)

    # Safety confirmation
    print(f"\nAbout to restore from: {archive_path.name}")
    print(f"Target: {settings.MONGO_URI}")
    if not args.no_drop:
        print("WARNING: This will DROP all existing collections first.")
    response = input("\nProceed? (yes/no): ").strip().lower()
    if response != "yes":
        print("Aborted.")
        sys.exit(0)

    success = run_mongorestore(archive_path, drop=not args.no_drop)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
