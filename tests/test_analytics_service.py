"""
Tests for analytics_service.py: business metrics and aggregation pipelines.
Note: mongomock has limited aggregation support. Tests focus on basic scenarios.
"""
import pytest
import pytest_asyncio
from bson import ObjectId
from datetime import datetime, timedelta, timezone
from app.core.constants import LeadStatus
from app.services.analytics_service import (
    get_overview_metrics,
)


@pytest_asyncio.fixture
async def analytics_db(mock_db):
    """Seed DB with analytics test data."""
    now = datetime.now(timezone.utc)

    pro_id = ObjectId()
    await mock_db.users.insert_one({
        "_id": pro_id, "business_name": "Test Pro",
        "role": "professional", "is_active": True,
    })

    await mock_db.leads.insert_one({
        "status": LeadStatus.NEW, "created_at": now, "pro_id": pro_id,
        "chat_id": "analytics_user1@c.us",
    })
    await mock_db.leads.insert_one({
        "status": LeadStatus.COMPLETED, "created_at": now, "pro_id": pro_id,
        "chat_id": "analytics_user2@c.us",
    })
    await mock_db.leads.insert_one({
        "status": LeadStatus.COMPLETED, "created_at": now, "pro_id": pro_id,
        "chat_id": "analytics_user3@c.us",
    })
    await mock_db.leads.insert_one({
        "status": LeadStatus.REJECTED, "created_at": now, "pro_id": pro_id,
        "chat_id": "analytics_user4@c.us",
    })

    await mock_db.reviews.insert_one({"pro_id": pro_id, "rating": 5})

    return mock_db, pro_id


# --- get_overview_metrics ---

@pytest.mark.asyncio
async def test_overview_metrics(analytics_db):
    db, pro_id = analytics_db

    result = await get_overview_metrics()

    assert result["total_leads"] >= 4
    assert result["completed_leads"] >= 2
    assert result["active_pros"] >= 1
    assert result["total_reviews"] >= 1
    assert result["conversion_rate"] > 0


@pytest.mark.asyncio
async def test_overview_metrics_structure(mock_db):
    """Verify overview returns correct keys even with no data."""
    result = await get_overview_metrics()

    assert "total_leads" in result
    assert "completed_leads" in result
    assert "active_pros" in result
    assert "conversion_rate" in result
    assert "leads_today" in result
    assert "leads_this_week" in result
