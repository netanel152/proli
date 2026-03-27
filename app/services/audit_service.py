"""
Audit Service - Logs admin panel actions for accountability.
"""

from datetime import datetime, timezone
from app.core.database import audit_log_collection
from app.core.logger import logger


async def log_action(admin_user: str, action: str, details: dict | None = None) -> None:
    """Record an admin action to the audit log."""
    entry = {
        "admin_user": admin_user,
        "action": action,
        "details": details or {},
        "timestamp": datetime.now(timezone.utc),
    }
    try:
        await audit_log_collection.insert_one(entry)
    except Exception as e:
        logger.error(f"Failed to write audit log: {e}")


async def get_audit_log(limit: int = 100, skip: int = 0) -> list:
    """Retrieve recent audit log entries."""
    cursor = audit_log_collection.find().sort("timestamp", -1).skip(skip).limit(limit)
    return await cursor.to_list(length=limit)


async def get_audit_log_count() -> int:
    """Get total count of audit log entries."""
    return await audit_log_collection.count_documents({})
