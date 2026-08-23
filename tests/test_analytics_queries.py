"""Tests for admin_panel/core/analytics_queries.py — the ONE analytics implementation.

PRO-140: these pipelines existed twice (a tested-but-dead async service and the
live-but-untested sync panel copy, GMV/commission math included). The async
copy is gone; the sync copy moved to a streamlit-free module with ``db``
injected — so the functions the operator's Revenue tab renders are exactly the
ones under test here. Uses a plain synchronous ``mongomock`` database: the
panel is sync pymongo end-to-end, unlike the motor-based app services.
"""

from datetime import datetime, timedelta, timezone

import mongomock
import pytest
from bson import ObjectId

from admin_panel.core import analytics_queries as aq
from app.core.constants import LeadStatus


@pytest.fixture
def db():
    return mongomock.MongoClient()["proli_test"]


def _now():
    return datetime.now(timezone.utc)


# --- get_lead_funnel ---


def test_lead_funnel_counts_and_zero_fills(db):
    now = _now()
    db.leads.insert_many(
        [
            {"status": LeadStatus.NEW.value, "created_at": now},
            {"status": LeadStatus.NEW.value, "created_at": now},
            {"status": LeadStatus.COMPLETED.value, "created_at": now},
            # outside the window — must not count
            {"status": LeadStatus.NEW.value, "created_at": now - timedelta(days=40)},
        ]
    )
    funnel = aq.get_lead_funnel(db, days=30)
    assert funnel[LeadStatus.NEW.value] == 2
    assert funnel[LeadStatus.COMPLETED.value] == 1
    # every status present even with zero leads — charts rely on the shape
    for status in LeadStatus:
        assert status.value in funnel
    assert funnel[LeadStatus.CANCELLED.value] == 0


# --- get_revenue_stats (PRO-33 money math) ---


def test_revenue_stats_sums_gmv_and_commission(db):
    now = _now()
    pro_id = ObjectId()
    db.leads.insert_many(
        [
            {
                "status": LeadStatus.COMPLETED.value,
                "created_at": now,
                "pro_id": pro_id,
                "final_price": 400,
                "commission_amount": 40.0,
            },
            {
                "status": LeadStatus.COMPLETED.value,
                "created_at": now,
                "pro_id": pro_id,
                "final_price": 600,
                "commission_amount": 60.0,
            },
            # completed but unpriced -> excluded
            {"status": LeadStatus.COMPLETED.value, "created_at": now, "pro_id": pro_id},
            # priced but not completed -> excluded
            {
                "status": LeadStatus.BOOKED.value,
                "created_at": now,
                "pro_id": pro_id,
                "final_price": 999,
                "commission_amount": 99.9,
            },
        ]
    )
    result = aq.get_revenue_stats(db, days=30)
    assert result["gmv"] == 1000
    assert result["commission"] == 100.0
    assert result["priced_jobs"] == 2
    assert result["avg_ticket"] == 500


def test_revenue_stats_empty_returns_zeros(db):
    result = aq.get_revenue_stats(db, days=30)
    assert result == {"gmv": 0, "commission": 0, "priced_jobs": 0, "avg_ticket": None}


def test_revenue_stats_window_excludes_old_leads(db):
    db.leads.insert_one(
        {
            "status": LeadStatus.COMPLETED.value,
            "created_at": _now() - timedelta(days=45),
            "final_price": 500,
            "commission_amount": 50.0,
        }
    )
    assert aq.get_revenue_stats(db, days=30)["priced_jobs"] == 0
    assert aq.get_revenue_stats(db, days=60)["priced_jobs"] == 1


# --- get_pro_performance ---


def test_pro_performance_rates_names_and_batched_ratings(db):
    now = _now()
    pro_id = ObjectId()
    db.users.insert_one(
        {"_id": pro_id, "business_name": "Test Pro", "role": "professional"}
    )
    db.leads.insert_many(
        [
            {"status": LeadStatus.COMPLETED.value, "created_at": now, "pro_id": pro_id},
            {"status": LeadStatus.COMPLETED.value, "created_at": now, "pro_id": pro_id},
            {"status": LeadStatus.REJECTED.value, "created_at": now, "pro_id": pro_id},
            {"status": LeadStatus.BOOKED.value, "created_at": now, "pro_id": pro_id},
            # unassigned lead -> not attributed to anyone
            {"status": LeadStatus.NEW.value, "created_at": now, "pro_id": None},
        ]
    )
    db.reviews.insert_many(
        [{"pro_id": pro_id, "rating": 5}, {"pro_id": pro_id, "rating": 4}]
    )

    perf = aq.get_pro_performance(db, days=30)
    assert len(perf) == 1
    row = perf[0]
    assert row["name"] == "Test Pro"
    assert row["total_leads"] == 4
    assert row["completed"] == 2
    assert row["rejected"] == 1
    assert row["booked"] == 1
    assert row["completion_rate"] == 50.0
    assert row["avg_rating"] == 4.5


def test_pro_performance_unknown_pro_and_no_reviews(db):
    """A pro_id with no users doc renders as Unknown; no reviews -> a dash."""
    db.leads.insert_one(
        {"status": LeadStatus.BOOKED.value, "created_at": _now(), "pro_id": ObjectId()}
    )
    perf = aq.get_pro_performance(db, days=30)
    assert perf[0]["name"] == "Unknown"
    assert perf[0]["avg_rating"] == "-"


# --- get_leads_by_type ---


def test_leads_by_type_groups_and_defaults_unassigned(db):
    now = _now()
    plumber = ObjectId()
    db.users.insert_one({"_id": plumber, "type": "plumber"})
    orphan = ObjectId()  # no users doc -> type is None -> "unassigned"
    db.leads.insert_many(
        [
            {"created_at": now, "pro_id": plumber},
            {"created_at": now, "pro_id": plumber},
            {"created_at": now, "pro_id": orphan},
        ]
    )
    result = {r["type"]: r["count"] for r in aq.get_leads_by_type(db, days=30)}
    assert result == {"plumber": 2, "unassigned": 1}


# --- get_status_history_metrics (PRO-57) ---


def test_status_history_metrics_median_and_conversion(db):
    now = _now()
    db.leads.insert_many(
        [
            {
                "created_at": now,
                "status_history": [
                    {"status": LeadStatus.NEW.value, "at": now},
                    {"status": LeadStatus.CONTACTED.value, "at": now},
                    {"status": LeadStatus.BOOKED.value, "at": now + timedelta(hours=2)},
                ],
            },
            {
                "created_at": now,
                "status_history": [
                    {"status": LeadStatus.NEW.value, "at": now},
                    {"status": LeadStatus.CONTACTED.value, "at": now},
                    # contacted but never booked
                ],
            },
            # pre-feature lead: no history -> skipped, not crashed on
            {"created_at": now},
        ]
    )
    m = aq.get_status_history_metrics(db, days=30)
    assert m["median_new_to_booked_hours"] == 2.0
    assert m["contacted_to_booked_pct"] == 50.0
    assert m["sample_new_to_booked"] == 1
    assert m["sample_contacted"] == 2


def test_status_history_metrics_empty(db):
    m = aq.get_status_history_metrics(db, days=30)
    assert m["median_new_to_booked_hours"] is None
    assert m["contacted_to_booked_pct"] is None


# --- get_finops_stats ---


def test_finops_stats_orders_by_tokens_and_falls_back_to_name(db):
    db.users.insert_many(
        [
            {
                "role": "professional",
                "business_name": "Big Spender",
                "total_tokens_used": 9000,
                "phone_number": "972501111111",
            },
            {
                "role": "professional",
                "name": "No Business Name",
                "total_tokens_used": 100,
                "phone_number": "972502222222",
            },
            # zero usage -> excluded
            {"role": "professional", "business_name": "Idle", "total_tokens_used": 0},
            # customer -> excluded
            {"role": "customer", "total_tokens_used": 5000},
        ]
    )
    rows = aq.get_finops_stats(db)
    assert [r["name"] for r in rows] == ["Big Spender", "No Business Name"]
    assert rows[0]["tokens"] == 9000
