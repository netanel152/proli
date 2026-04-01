"""
Tests for data_management_service.py: consent tracking, data export, data deletion.
GDPR/Israeli Privacy Law compliance functions.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from bson import ObjectId
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
    await mock_db.consent.insert_one({
        "chat_id": "972501111111@c.us",
        "accepted": True,
        "timestamp": datetime.now(timezone.utc),
    })

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

    await mock_db.users.insert_one({
        "phone_number": "972501111111", "role": "customer",
    })
    await mock_db.leads.insert_one({"chat_id": chat_id, "status": "new"})
    await mock_db.messages.insert_one({"chat_id": chat_id, "text": "hello", "role": "user"})
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
    monkeypatch.setattr(app.services.data_management_service, "StateManager", mock_state)
    monkeypatch.setattr(app.services.data_management_service, "ContextManager", mock_ctx)

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
