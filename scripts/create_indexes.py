"""Create every MongoDB index — CLI wrapper.

The table and the logic live in ``app/db/indexes.py``, which the API also runs
on every boot. A manual run only primes a brand-new database before its first
API boot, or shows the list.

    python scripts/create_indexes.py
"""

import asyncio
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.indexes import create_all_indexes

if __name__ == "__main__":
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(create_all_indexes())
