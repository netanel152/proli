"""
Tests for audit_service.py: admin action logging.
"""
import pytest
from app.services.audit_service import log_action, get_audit_log, get_audit_log_count


@pytest.mark.asyncio
async def test_log_action(mock_db):
    await log_action("admin@test.com", "approve_pro_test", {"pro_id": "123"})

    entry = await mock_db.audit_log.find_one({"action": "approve_pro_test"})
    assert entry is not None
    assert entry["admin_user"] == "admin@test.com"
    assert entry["details"]["pro_id"] == "123"
    assert "timestamp" in entry


@pytest.mark.asyncio
async def test_log_action_no_details(mock_db):
    await log_action("admin", "login_test")

    entry = await mock_db.audit_log.find_one({"action": "login_test"})
    assert entry is not None
    assert entry["details"] == {}


@pytest.mark.asyncio
async def test_get_audit_log(mock_db):
    count_before = await get_audit_log_count()
    for i in range(5):
        await log_action("admin", f"batch_action_{i}")

    entries = await get_audit_log(limit=3)
    assert len(entries) == 3


@pytest.mark.asyncio
async def test_get_audit_log_count(mock_db):
    count_before = await get_audit_log_count()
    await log_action("admin", "count_a")
    await log_action("admin", "count_b")

    count_after = await get_audit_log_count()
    assert count_after == count_before + 2
