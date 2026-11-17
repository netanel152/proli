"""
Tests for data_management_service.py: consent tracking, data export, data deletion.
GDPR/Israeli Privacy Law compliance functions.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone
from app.services.data_management_service import (
    record_consent,
    has_consent,
    export_user_data,
    delete_user_data,
)
import app.services.data_management_service


# --- record_consent ---


@pytest.mark.asyncio
async def test_record_consent_accept(mock_db):
    await record_consent("972501111111@c.us", accepted=True)

    doc = await mock_db.consent.find_one({"chat_id": "972501111111@c.us"})
    assert doc is not None
    assert doc["accepted"] is True


@pytest.mark.asyncio
async def test_record_consent_decline(mock_db):
    await record_consent("972501111111@c.us", accepted=False)

    doc = await mock_db.consent.find_one({"chat_id": "972501111111@c.us"})
    assert doc is not None
    assert doc["accepted"] is False


@pytest.mark.asyncio
async def test_record_consent_upsert(mock_db):
    """Second call updates, not duplicates."""
    await record_consent("972501111111@c.us", accepted=False)
    await record_consent("972501111111@c.us", accepted=True)

    count = await mock_db.consent.count_documents({"chat_id": "972501111111@c.us"})
    assert count == 1

    doc = await mock_db.consent.find_one({"chat_id": "972501111111@c.us"})
    assert doc["accepted"] is True


# --- has_consent ---


@pytest.mark.asyncio
async def test_has_consent_none(mock_db):
    result = await has_consent("nonexistent@c.us")
    assert result is None


@pytest.mark.asyncio
async def test_has_consent_true(mock_db):
    await mock_db.consent.insert_one(
        {
            "chat_id": "972501111111@c.us",
            "accepted": True,
            "timestamp": datetime.now(timezone.utc),
        }
    )

    result = await has_consent("972501111111@c.us")
    assert result is True


@pytest.mark.asyncio
async def test_has_consent_false(mock_db):
    # Use record_consent to ensure correct document shape
    await record_consent("972502222222@c.us", accepted=False)

    result = await has_consent("972502222222@c.us")
    assert result is False


# --- export_user_data ---


@pytest.mark.asyncio
async def test_export_user_data(mock_db):
    chat_id = "972501111111@c.us"

    await mock_db.users.insert_one(
        {
            "phone_number": "972501111111",
            "role": "customer",
        }
    )
    await mock_db.leads.insert_one({"chat_id": chat_id, "status": "new"})
    await mock_db.messages.insert_one(
        {"chat_id": chat_id, "text": "hello", "role": "user"}
    )
    await mock_db.consent.insert_one({"chat_id": chat_id, "accepted": True})

    result = await export_user_data(chat_id)

    assert result["chat_id"] == chat_id
    assert result["user_profile"] is not None
    assert len(result["leads"]) == 1
    assert len(result["messages"]) == 1
    assert result["consent"]["accepted"] is True


@pytest.mark.asyncio
async def test_export_empty_user(mock_db):
    result = await export_user_data("nonexistent@c.us")

    assert result["user_profile"] is None
    assert len(result["leads"]) == 0
    assert len(result["messages"]) == 0


# --- delete_user_data ---


@pytest.mark.asyncio
async def test_delete_user_data(mock_db, monkeypatch):
    chat_id = "972509999999@c.us"  # Unique to avoid collision

    await mock_db.messages.insert_one({"chat_id": chat_id, "text": "hi"})
    await mock_db.leads.insert_one({"chat_id": chat_id, "status": "new"})
    await mock_db.reviews.insert_one({"chat_id": chat_id, "rating": 5})
    await record_consent(chat_id, accepted=True)

    # Mock Redis-based services
    mock_state = MagicMock()
    mock_state.clear_state = AsyncMock()
    mock_ctx = MagicMock()
    mock_ctx.clear_context = AsyncMock()
    monkeypatch.setattr(
        app.services.data_management_service, "StateManager", mock_state
    )
    monkeypatch.setattr(
        app.services.data_management_service, "ContextManager", mock_ctx
    )

    result = await delete_user_data(chat_id)

    assert result["messages"] >= 1
    assert result["leads"] >= 1
    assert result["reviews"] >= 1
    assert result["consent"] >= 1

    # Verify collections are empty for this chat_id
    assert await mock_db.messages.count_documents({"chat_id": chat_id}) == 0
    assert await mock_db.leads.count_documents({"chat_id": chat_id}) == 0

    mock_state.clear_state.assert_called_once_with(chat_id)
    mock_ctx.clear_context.assert_called_once_with(chat_id)


# --- PRO-247: export/delete cover every per-chat kind ---

# mock_db is module-scoped, so each test below uses its own chat ids.
_DSAR_CHAT = "972507777777@c.us"
_DELETED_CHAT = "972507777778@c.us"
_OTHER_CHAT = "972506666666@c.us"


async def _seed_every_per_chat_kind(mock_db, redis, chat_id):
    """One document / key of every kind that names ``chat_id``, written the way
    production writes it (reviews under ``customer_chat_id``, PRO-247)."""
    await mock_db.messages.insert_one({"chat_id": chat_id, "text": "hi"})
    await mock_db.leads.insert_one({"chat_id": chat_id, "status": "new"})
    await mock_db.reviews.insert_one({"customer_chat_id": chat_id, "rating": 5})
    await mock_db.wa_delivery.insert_one(
        {"_id": f"wamid.{chat_id}", "chat_id": chat_id, "kind": "text"}
    )
    await mock_db.consent.insert_one({"chat_id": chat_id, "accepted": True})
    for key in (
        f"state:{chat_id}",
        f"state_meta:{chat_id}",
        f"consent_hold:{chat_id}",
        f"rate_limit:{chat_id}",
        f"rate_limit:pro_search:{chat_id}",
        f"rl:inbound:{chat_id}",
        f"rl:trips:{chat_id}",
        f"retry_notice:{chat_id}",
        f"wa:window:{chat_id}",
        f"wa:window:page:{chat_id}",
        f"ai:daily:{chat_id}:2026-11-16",
        f"ai:daily:{chat_id}:2026-11-17",
    ):
        await redis.set(key, "1")
    await redis.rpush(f"context:{chat_id}", '{"role": "user", "text": "hi"}')
    await redis.zadd("wa:quality:business_initiated", {chat_id: 1.0})


@pytest.mark.asyncio
async def test_export_finds_reviews_written_under_customer_chat_id(mock_db):
    await mock_db.reviews.insert_one({"customer_chat_id": _DSAR_CHAT, "rating": 4})
    await mock_db.wa_delivery.insert_one(
        {"_id": "wamid.1", "chat_id": _DSAR_CHAT, "kind": "template"}
    )

    result = await export_user_data(_DSAR_CHAT)

    assert [r["rating"] for r in result["reviews"]] == [4]
    assert [d["kind"] for d in result["message_deliveries"]] == ["template"]


@pytest.mark.asyncio
async def test_delete_leaves_nothing_for_the_chat_and_spares_other_chats(
    mock_db, fake_redis
):
    await _seed_every_per_chat_kind(mock_db, fake_redis, _DELETED_CHAT)
    await _seed_every_per_chat_kind(mock_db, fake_redis, _OTHER_CHAT)

    result = await delete_user_data(_DELETED_CHAT)

    assert result["reviews"] == 1
    assert result["message_deliveries"] == 1
    for name in ("messages", "leads", "wa_delivery", "consent"):
        assert (
            await mock_db[name].count_documents({"chat_id": _DELETED_CHAT}) == 0
        ), name
    assert (
        await mock_db.reviews.count_documents({"customer_chat_id": _DELETED_CHAT}) == 0
    )

    leftover = [k async for k in fake_redis.scan_iter(match=f"*{_DELETED_CHAT}*")]
    assert leftover == []
    assert (
        await fake_redis.zscore("wa:quality:business_initiated", _DELETED_CHAT) is None
    )

    # The other chat is untouched.
    assert await mock_db.reviews.count_documents({"customer_chat_id": _OTHER_CHAT}) == 1
    assert await mock_db.wa_delivery.count_documents({"chat_id": _OTHER_CHAT}) == 1
    assert len([k async for k in fake_redis.scan_iter(match=f"*{_OTHER_CHAT}*")]) == 13
    assert await fake_redis.zscore("wa:quality:business_initiated", _OTHER_CHAT) == 1.0


def test_every_chat_keyed_redis_family_is_covered_by_delete():
    """Drift guard: a new ``f"<family>:{chat_id}"`` key anywhere in app/ must be
    added to the delete path (or to the deliberate exclusions here)."""
    import re
    from pathlib import Path

    from app.services import data_management_service as dms

    covered = {
        # StateManager.clear_state
        "state:",
        "state_meta:",
        "consent_hold:",
        # ContextManager.clear_context
        "context:",
        # Deliberately kept: the per-chat mutual-exclusion lock (see module).
        "lock:chat:",
    }
    covered |= {t.split("{chat_id}")[0] for t in dms._PER_CHAT_KEY_TEMPLATES}
    covered |= {t.split("{chat_id}")[0] for t in dms._PER_CHAT_KEY_PATTERNS}

    root = Path(__file__).resolve().parents[1] / "app"
    literal = re.compile(r'f"([a-z_:]+:)\{(?:ctx\.)?chat_id\}')
    via_const = re.compile(r'f"\{(_[A-Z_]+)\}\{chat_id\}"')
    found = {}
    for path in root.rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        for prefix in literal.findall(src):
            found.setdefault(prefix, path.name)
        for const in via_const.findall(src):
            m = re.search(rf'^{const}\s*=\s*"([^"]+)"', src, re.M)
            assert m, f"{path.name}: cannot resolve {const}"
            found.setdefault(m.group(1), path.name)

    # The scan must actually see the families it is guarding.
    assert {"state:", "context:", "rl:inbound:", "wa:window:"} <= set(found)
    missing = {p: f for p, f in found.items() if p not in covered}
    assert not missing, f"chat-keyed Redis families not deleted by DSAR: {missing}"
