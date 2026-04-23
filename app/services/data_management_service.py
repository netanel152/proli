"""
Data Management Service - GDPR/Israeli Privacy Law compliance.

Handles consent tracking, user data export, and right-to-delete.

NOTE: `export_user_data` and `delete_user_data` are implemented but not yet
wired to any endpoint or admin action. They are kept here so the compliance
code is ready the moment we need to expose a DSAR (Data Subject Access
Request) flow. When adding the integration, surface them via either an
admin-only route or a dedicated CLI script; do not call them from
user-facing message handlers without auth.
"""

from datetime import datetime, timezone
from app.core.database import (
    consent_collection,
    users_collection,
    leads_collection,
    messages_collection,
    reviews_collection,
    slots_collection,
)
from app.services.state_manager_service import StateManager
from app.services.context_manager_service import ContextManager
from app.core.logger import logger


async def record_consent(chat_id: str, accepted: bool) -> None:
    """Record user's consent decision."""
    await consent_collection.update_one(
        {"chat_id": chat_id},
        {"$set": {
            "chat_id": chat_id,
            "accepted": accepted,
            "timestamp": datetime.now(timezone.utc),
        }},
        upsert=True,
    )
    logger.info(f"Consent {'accepted' if accepted else 'declined'} for {chat_id}")


async def has_consent(chat_id: str) -> bool | None:
    """Check if user has given consent. Returns None if no record exists."""
    record = await consent_collection.find_one({"chat_id": chat_id})
    if record is None:
        return None
    return record.get("accepted", False)


async def export_user_data(chat_id: str) -> dict:
    """Export all data associated with a chat_id (right-to-access)."""
    phone = chat_id.replace("@c.us", "")

    user = await users_collection.find_one(
        {"phone_number": {"$in": [phone, chat_id]}}
    )
    leads = await leads_collection.find({"chat_id": chat_id}).to_list(length=500)
    messages = await messages_collection.find({"chat_id": chat_id}).to_list(length=5000)
    reviews = await reviews_collection.find({"chat_id": chat_id}).to_list(length=100)
    consent = await consent_collection.find_one({"chat_id": chat_id})

    # Convert ObjectIds to strings for JSON serialization
    def serialize(doc):
        if doc is None:
            return None
        doc = dict(doc)
        for key, val in doc.items():
            if hasattr(val, "__str__") and type(val).__name__ == "ObjectId":
                doc[key] = str(val)
            elif isinstance(val, datetime):
                doc[key] = val.isoformat()
        return doc

    return {
        "chat_id": chat_id,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "user_profile": serialize(user),
        "leads": [serialize(l) for l in leads],
        "messages": [serialize(m) for m in messages],
        "reviews": [serialize(r) for r in reviews],
        "consent": serialize(consent),
    }


async def delete_user_data(chat_id: str) -> dict:
    """Delete all data associated with a chat_id (right-to-delete)."""
    phone = chat_id.replace("@c.us", "")

    results = {}
    results["messages"] = (await messages_collection.delete_many({"chat_id": chat_id})).deleted_count
    results["leads"] = (await leads_collection.delete_many({"chat_id": chat_id})).deleted_count
    results["reviews"] = (await reviews_collection.delete_many({"chat_id": chat_id})).deleted_count
    results["consent"] = (await consent_collection.delete_many({"chat_id": chat_id})).deleted_count

    # Clear Redis state and context
    await StateManager.clear_state(chat_id)
    await ContextManager.clear_context(chat_id)

    # Mark consent as deleted (not remove user profile if they're a pro)
    logger.info(f"Deleted user data for {chat_id}: {results}")
    return results
