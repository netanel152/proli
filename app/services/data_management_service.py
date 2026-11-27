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
from app.core.database import (  # noqa: F401 — slots_collection is a monkeypatch seam
    consent_collection,
    users_collection,
    leads_collection,
    slots_collection,  # unused here; tests/conftest.py rebinds it, so it must exist
    messages_collection,
    reviews_collection,
    wa_delivery_collection,
)
from app.core.redis_client import get_redis_client
from app.services.state_manager_service import StateManager
from app.services.context_manager_service import ContextManager
from app.core.phone import mask_chat_id, strip_suffix
from app.core.logger import logger

# Every Redis key that carries a chat_id, beyond what StateManager.clear_state
# (state:, state_meta:, consent_hold:) and ContextManager.clear_context
# (context:) already remove. tests/test_data_management.py scans app/ for
# chat-keyed f-strings and fails when a family is missing from this list.
#
# Not listed: ``lock:chat:{chat_id}``. It is the per-chat mutual-exclusion lock
# (a random token, no personal data, TTL in seconds), and deleting it while a
# turn holds it would let a second turn run concurrently.
_PER_CHAT_KEY_TEMPLATES = (
    "rate_limit:{chat_id}",
    "rate_limit:pro_search:{chat_id}",
    "rl:inbound:{chat_id}",
    "rl:trips:{chat_id}",
    "retry_notice:{chat_id}",
    "chat_order:{chat_id}",
    "life_threat_paged:{chat_id}",
    "wa:window:{chat_id}",
    "wa:window:page:{chat_id}",
)
# Families with a suffix after the chat_id (one key per day).
_PER_CHAT_KEY_PATTERNS = ("ai:daily:{chat_id}:*",)
# Sorted sets that hold the chat_id as a member, not in the key name.
_PER_CHAT_ZSET_MEMBERS = ("wa:quality:business_initiated",)


def _reviews_filter(chat_id: str) -> dict:
    """Reviews are written under ``customer_chat_id`` (customer_flow); the
    ``chat_id`` branch keeps any older document written under that name."""
    return {"$or": [{"customer_chat_id": chat_id}, {"chat_id": chat_id}]}


async def _delete_per_chat_redis_keys(chat_id: str) -> int:
    """Remove every chat-keyed Redis entry not owned by StateManager/ContextManager.

    Returns how many keys or set members were removed.
    """
    redis = await get_redis_client()
    keys = [t.format(chat_id=chat_id) for t in _PER_CHAT_KEY_TEMPLATES]
    for pattern in _PER_CHAT_KEY_PATTERNS:
        keys.extend(
            [k async for k in redis.scan_iter(match=pattern.format(chat_id=chat_id))]
        )
    removed = await redis.delete(*keys) if keys else 0
    for zset in _PER_CHAT_ZSET_MEMBERS:
        removed += await redis.zrem(zset, chat_id)
    return removed


async def record_consent(chat_id: str, accepted: bool) -> None:
    """Record user's consent decision."""
    await consent_collection.update_one(
        {"chat_id": chat_id},
        {
            "$set": {
                "chat_id": chat_id,
                "accepted": accepted,
                "timestamp": datetime.now(timezone.utc),
            }
        },
        upsert=True,
    )
    logger.info(
        f"Consent {'accepted' if accepted else 'declined'} for {mask_chat_id(chat_id)}"
    )


async def has_consent(chat_id: str) -> bool | None:
    """Check if user has given consent. Returns None if no record exists."""
    record = await consent_collection.find_one({"chat_id": chat_id})
    if record is None:
        return None
    return record.get("accepted", False)


async def consent_accepted_within(chat_id: str, seconds: int) -> bool:
    """Did this chat accept consent in the last `seconds`? (PRO-292)

    The consent request opens with "שלום וברוכים הבאים לפרולי", and it is never
    logged to `messages`, so the model cannot see it. The pipeline asks this to
    tell the dispatcher the customer has already been welcomed.
    """
    record = await consent_collection.find_one({"chat_id": chat_id})
    if not record or not record.get("accepted"):
        return False
    stamp = record.get("timestamp")
    if not isinstance(stamp, datetime):
        return False
    if stamp.tzinfo is None:  # Mongo returns naive UTC
        stamp = stamp.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - stamp).total_seconds() <= seconds


async def export_user_data(chat_id: str) -> dict:
    """Export all data associated with a chat_id (right-to-access)."""
    phone = strip_suffix(chat_id)

    user = await users_collection.find_one({"phone_number": {"$in": [phone, chat_id]}})
    leads = await leads_collection.find({"chat_id": chat_id}).to_list(length=500)
    messages = await messages_collection.find({"chat_id": chat_id}).to_list(length=5000)
    reviews = await reviews_collection.find(_reviews_filter(chat_id)).to_list(
        length=100
    )
    deliveries = await wa_delivery_collection.find({"chat_id": chat_id}).to_list(
        length=5000
    )
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
        "leads": [serialize(lead) for lead in leads],
        "messages": [serialize(m) for m in messages],
        "reviews": [serialize(r) for r in reviews],
        "message_deliveries": [serialize(d) for d in deliveries],
        "consent": serialize(consent),
    }


async def delete_user_data(chat_id: str) -> dict:
    """Delete all data associated with a chat_id (right-to-delete)."""
    results = {}
    results["messages"] = (
        await messages_collection.delete_many({"chat_id": chat_id})
    ).deleted_count
    results["leads"] = (
        await leads_collection.delete_many({"chat_id": chat_id})
    ).deleted_count
    results["reviews"] = (
        await reviews_collection.delete_many(_reviews_filter(chat_id))
    ).deleted_count
    results["message_deliveries"] = (
        await wa_delivery_collection.delete_many({"chat_id": chat_id})
    ).deleted_count
    results["consent"] = (
        await consent_collection.delete_many({"chat_id": chat_id})
    ).deleted_count

    # Clear Redis: state + state_meta + consent_hold, context, then the rest.
    await StateManager.clear_state(chat_id)
    await ContextManager.clear_context(chat_id)
    results["redis_keys"] = await _delete_per_chat_redis_keys(chat_id)

    # Mark consent as deleted (not remove user profile if they're a pro)
    logger.info(f"Deleted user data for {mask_chat_id(chat_id)}: {results}")
    return results
