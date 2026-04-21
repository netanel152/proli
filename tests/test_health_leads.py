"""
Tests for GET /health/leads — the business-level lead pipeline signal.

Why this exists: the 2026-04-18 post-mortem exposed two failure modes the
operational healthcheck was blind to — a growing PENDING_ADMIN_REVIEW
backlog (Healer looping) and stale CONTACTED leads (Healer silently
failing). The endpoint surfaces both counts so an external monitor
(Better Uptime / Cronitor / Sentry Crons) can page on threshold breach.

Tests here cover:
  1. Empty DB → both counters zero.
  2. Mixed leads → counters reflect only the matching statuses.
  3. Stuck-threshold boundary: a CONTACTED lead just under 24h does NOT
     count as stuck; a CONTACTED lead older than 24h does.
"""
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from bson import ObjectId
from fastapi.testclient import TestClient

from app.core.constants import LeadStatus, WorkerConstants
from app.main import app


@pytest_asyncio.fixture
async def client(mock_db, monkeypatch):
    """
    Wire the mongomock leads collection into the health route and ensure
    the collection starts empty for each test.

    The autouse conftest patches `app.core.database.leads_collection`, but
    the health route does `from ... import leads_collection` so it holds a
    reference to the pre-patch object. Re-patch here so the endpoint hits
    the mock DB. `mock_db` is module-scoped, so we must explicitly clear
    between tests — otherwise fixtures from earlier tests leak in.
    """
    import app.api.routes.health as health_route
    monkeypatch.setattr(health_route, "leads_collection", mock_db.leads)
    await mock_db.leads.delete_many({})
    yield TestClient(app)
    await mock_db.leads.delete_many({})


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_health_leads_empty_db(client):
    """No leads at all → both counters are zero, status ok."""
    resp = client.get("/health/leads")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["pending_review_count"] == 0
    assert body["stuck_contacted_count"] == 0
    assert body["stuck_threshold_hours"] == WorkerConstants.UNASSIGNED_LEAD_TIMEOUT_HOURS


@pytest.mark.asyncio
async def test_health_leads_counts_pending_and_stuck(client, mock_db):
    """
    Mixed fixture: 2 pending_admin_review, 1 fresh contacted, 1 stale
    contacted, 1 completed. Expect pending=2, stuck=1.
    """
    now = _now()
    stale_cutoff = now - timedelta(hours=WorkerConstants.UNASSIGNED_LEAD_TIMEOUT_HOURS + 1)
    fresh = now - timedelta(minutes=5)

    await mock_db.leads.insert_many([
        {"_id": ObjectId(), "status": LeadStatus.PENDING_ADMIN_REVIEW, "created_at": now},
        {"_id": ObjectId(), "status": LeadStatus.PENDING_ADMIN_REVIEW, "created_at": now},
        {"_id": ObjectId(), "status": LeadStatus.CONTACTED, "created_at": fresh},
        {"_id": ObjectId(), "status": LeadStatus.CONTACTED, "created_at": stale_cutoff},
        {"_id": ObjectId(), "status": LeadStatus.COMPLETED, "created_at": stale_cutoff},
    ])

    resp = client.get("/health/leads")
    assert resp.status_code == 200
    body = resp.json()
    assert body["pending_review_count"] == 2
    assert body["stuck_contacted_count"] == 1


@pytest.mark.asyncio
async def test_health_leads_threshold_boundary(client, mock_db):
    """
    Boundary check: a CONTACTED lead just *under* the 24h threshold is NOT
    stuck; one just over it is. Guards against a `<=` vs `<` regression in
    the Mongo query.
    """
    now = _now()
    hours = WorkerConstants.UNASSIGNED_LEAD_TIMEOUT_HOURS
    just_under = now - timedelta(hours=hours, minutes=-5)  # 5 min newer than cutoff
    just_over = now - timedelta(hours=hours, minutes=5)    # 5 min older than cutoff

    await mock_db.leads.insert_many([
        {"_id": ObjectId(), "status": LeadStatus.CONTACTED, "created_at": just_under},
        {"_id": ObjectId(), "status": LeadStatus.CONTACTED, "created_at": just_over},
    ])

    resp = client.get("/health/leads")
    assert resp.status_code == 200
    body = resp.json()
    assert body["stuck_contacted_count"] == 1
