"""MongoDB backup — CLI wrapper.

The backup itself lives in ``app/db/backup.py``, which the worker's nightly
job runs as ``python -m app.db.backup --upload-s3``. Same flags, same exit
codes:

    python scripts/backup.py                  # Local backup only
    python scripts/backup.py --upload-s3      # Local + S3 upload (strict: exit 1 if it doesn't land)
    python scripts/backup.py --cleanup        # Run retention cleanup
"""

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.backup import main

if __name__ == "__main__":
    main()
