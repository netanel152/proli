"""
Production initialization script.
Run ONCE after deploying to production Railway.

- Creates the admin account in MongoDB (if it doesn't exist)
- Does NOT wipe any data
- Does NOT create fake pros or leads

Usage:
    python scripts/init_production.py --username admin --password <strong-password>
"""
import asyncio
import sys
import os
import argparse
import bcrypt
from datetime import datetime, timezone

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import admins_collection


async def init_production(username: str, password: str, role: str = "owner"):
    existing = await admins_collection.find_one({"username": username})
    if existing:
        print(f"Admin '{username}' already exists. Nothing to do.")
        return

    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    await admins_collection.insert_one({
        "username": username,
        "password_hash": password_hash,
        "role": role,
        "created_at": datetime.now(timezone.utc),
    })
    print(f"Created admin account: username='{username}' role='{role}'")
    print("Production init complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Initialize production admin account")
    parser.add_argument("--username", required=True, help="Admin username")
    parser.add_argument("--password", required=True, help="Admin password (min 8 chars)")
    parser.add_argument("--role", default="owner", choices=["owner", "manager", "viewer"])
    args = parser.parse_args()

    if len(args.password) < 8:
        print("Error: password must be at least 8 characters.")
        sys.exit(1)

    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(init_production(args.username, args.password, args.role))
