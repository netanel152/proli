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
    """PRO-157: previously this fixture parked a lead in REJECTED status and
    asserted ``rejected`` off of it directly. Since PRO-117, REJECTED is a
    way-station — a rejected lead is immediately re-matched to the next pro
    or escalated, so nothing ever rests there and that column always read 0
    for a pro whose lead moved on. The durable record is ``rejected_by``, so
    the "rejected" lead below is modeled the way the system actually
    produces one: escalated to PENDING_ADMIN_REVIEW with no current pro_id,
    carrying ``rejected_by: [pro_id]``. It therefore does NOT count toward
    this pro's ``total_leads``/``completion_rate`` (those still mean "leads
    currently attributed to this pro") but does count toward ``rejected``
    and the new ``rejection_rate``.
    """
    now = _now()
    pro_id = ObjectId()
    db.users.insert_one(
        {"_id": pro_id, "business_name": "Test Pro", "role": "professional"}
    )
    db.leads.insert_many(
        [
            {"status": LeadStatus.COMPLETED.value, "created_at": now, "pro_id": pro_id},
            {"status": LeadStatus.COMPLETED.value, "created_at": now, "pro_id": pro_id},
            {"status": LeadStatus.BOOKED.value, "created_at": now, "pro_id": pro_id},
            # rejected by this pro, then escalated — no longer attributed to
            # them, but still counted via the durable rejected_by array
            {
                "status": LeadStatus.PENDING_ADMIN_REVIEW.value,
                "created_at": now,
                "pro_id": None,
                "rejected_by": [pro_id],
                "last_rejected_at": now,
            },
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
    assert row["total_leads"] == 3
    assert row["completed"] == 2
    assert row["booked"] == 1
    assert row["completion_rate"] == 66.7
    assert row["rejected"] == 1
    # rejection_rate = rejected / (total_leads + rejected) * 100 = 1 / 4 * 100
    assert row["rejection_rate"] == 25.0
    assert row["avg_rating"] == 4.5


def test_pro_performance_unknown_pro_and_no_reviews(db):
    """A pro_id with no users doc renders as Unknown; no reviews -> a dash."""
    db.leads.insert_one(
        {"status": LeadStatus.BOOKED.value, "created_at": _now(), "pro_id": ObjectId()}
    )
    perf = aq.get_pro_performance(db, days=30)
    assert perf[0]["name"] == "Unknown"
    assert perf[0]["avg_rating"] == "-"


def test_pro_performance_rejected_by_is_multikey_counts_both_pros(db):
    """PRO-157: one lead's ``rejected_by`` array can name several pros (each
    of them declined it before it was re-matched or escalated) — $unwind
    must count the lead once against EACH pro, not just the first.
    """
    now = _now()
    pro_a = ObjectId()
    pro_b = ObjectId()
    db.users.insert_many(
        [
            {"_id": pro_a, "business_name": "Pro A", "role": "professional"},
            {"_id": pro_b, "business_name": "Pro B", "role": "professional"},
        ]
    )
    db.leads.insert_one(
        {
            "status": LeadStatus.PENDING_ADMIN_REVIEW.value,
            "created_at": now,
            "pro_id": None,
            "rejected_by": [pro_a, pro_b],
            "last_rejected_at": now,
        }
    )
    perf = aq.get_pro_performance(db, days=30)
    by_id = {r["name"]: r for r in perf}
    assert by_id["Pro A"]["rejected"] == 1
    assert by_id["Pro B"]["rejected"] == 1


def test_pro_performance_rejection_window_excludes_old_rejections(db):
    """PRO-157: a rejection is windowed on ``last_rejected_at``, not
    ``created_at`` (``reassign_lead`` rewrites ``created_at`` on every
    successful rematch, so it can't be trusted as "when was this rejected").
    A rejection whose own ``last_rejected_at`` falls outside the ``days``
    window must not count, even though the lead's ``created_at`` is inside
    it. Combined with an in-window rejection for the same pro to prove only
    the in-window one is counted.
    """
    now = _now()
    pro_id = ObjectId()
    db.users.insert_one(
        {"_id": pro_id, "business_name": "Windowed Pro", "role": "professional"}
    )
    db.leads.insert_many(
        [
            # last_rejected_at outside the 30-day window -> must not count,
            # even though created_at is inside it
            {
                "status": LeadStatus.PENDING_ADMIN_REVIEW.value,
                "created_at": now,
                "pro_id": None,
                "rejected_by": [pro_id],
                "last_rejected_at": now - timedelta(days=45),
            },
            # last_rejected_at inside the window -> counts
            {
                "status": LeadStatus.PENDING_ADMIN_REVIEW.value,
                "created_at": now,
                "pro_id": None,
                "rejected_by": [pro_id],
                "last_rejected_at": now,
            },
        ]
    )
    perf = aq.get_pro_performance(db, days=30)
    assert len(perf) == 1
    assert perf[0]["rejected"] == 1


def test_pro_performance_rejection_only_row_after_attributed_rows(db):
    """A pro with zero currently-attributed leads but present in
    ``rejected_by`` still gets a row — name batched from ``db.users``,
    zeroed totals, 100% rejection rate — appended AFTER the attributed
    pro's row.
    """
    now = _now()
    attributed_pro = ObjectId()
    rejection_only_pro = ObjectId()
    db.users.insert_many(
        [
            {
                "_id": attributed_pro,
                "business_name": "Attributed Pro",
                "role": "professional",
            },
            {
                "_id": rejection_only_pro,
                "business_name": "Rejection Only Pro",
                "role": "professional",
            },
        ]
    )
    db.leads.insert_many(
        [
            {
                "status": LeadStatus.BOOKED.value,
                "created_at": now,
                "pro_id": attributed_pro,
            },
            {
                "status": LeadStatus.PENDING_ADMIN_REVIEW.value,
                "created_at": now,
                "pro_id": None,
                "rejected_by": [rejection_only_pro],
                "last_rejected_at": now,
            },
        ]
    )
    perf = aq.get_pro_performance(db, days=30)
    assert [r["name"] for r in perf] == ["Attributed Pro", "Rejection Only Pro"]
    rejection_row = perf[1]
    assert rejection_row["total_leads"] == 0
    assert rejection_row["completed"] == 0
    assert rejection_row["booked"] == 0
    # None, not 0 — a rejection-only row has no completion data at all.
    assert rejection_row["completion_rate"] is None
    assert rejection_row["rejected"] == 1
    assert rejection_row["rejection_rate"] == 100.0


def test_pro_performance_rejection_only_rows_ordered_by_rejection_count(db):
    """Among rejection-only rows (no attributed leads at all), the pro with
    more rejections is listed first.
    """
    now = _now()
    pro_few = ObjectId()
    pro_many = ObjectId()
    db.users.insert_many(
        [
            {"_id": pro_few, "business_name": "Few Rejections", "role": "professional"},
            {
                "_id": pro_many,
                "business_name": "Many Rejections",
                "role": "professional",
            },
        ]
    )
    db.leads.insert_many(
        [
            {
                "status": LeadStatus.PENDING_ADMIN_REVIEW.value,
                "created_at": now,
                "pro_id": None,
                "rejected_by": [pro_few],
                "last_rejected_at": now,
            },
            {
                "status": LeadStatus.PENDING_ADMIN_REVIEW.value,
                "created_at": now,
                "pro_id": None,
                "rejected_by": [pro_many],
                "last_rejected_at": now,
            },
            {
                "status": LeadStatus.PENDING_ADMIN_REVIEW.value,
                "created_at": now,
                "pro_id": None,
                "rejected_by": [pro_many],
                "last_rejected_at": now,
            },
        ]
    )
    perf = aq.get_pro_performance(db, days=30)
    assert [r["name"] for r in perf] == ["Many Rejections", "Few Rejections"]
    assert perf[0]["rejected"] == 2
    assert perf[1]["rejected"] == 1


def test_pro_performance_rejection_only_row_rating_still_batched(db):
    """A rejection-only pro's ``avg_rating`` must still be populated from the
    ratings batch — a regression here would silently show "-" for a pro who
    otherwise has real reviews.
    """
    now = _now()
    rejection_only_pro = ObjectId()
    db.users.insert_one(
        {
            "_id": rejection_only_pro,
            "business_name": "Rated Rejection Only Pro",
            "role": "professional",
        }
    )
    db.leads.insert_one(
        {
            "status": LeadStatus.PENDING_ADMIN_REVIEW.value,
            "created_at": now,
            "pro_id": None,
            "rejected_by": [rejection_only_pro],
            "last_rejected_at": now,
        }
    )
    db.reviews.insert_many(
        [
            {"pro_id": rejection_only_pro, "rating": 5},
            {"pro_id": rejection_only_pro, "rating": 3},
        ]
    )
    perf = aq.get_pro_performance(db, days=30)
    assert len(perf) == 1
    assert perf[0]["avg_rating"] == 4.0


def test_pro_performance_empty_rejected_by_creates_no_phantom_row(db):
    """A lead with ``rejected_by: []`` (explicitly empty) or the field
    entirely absent must not count toward anyone, and must not create a
    rejection-only row for anybody.
    """
    now = _now()
    pro_id = ObjectId()
    db.users.insert_one(
        {"_id": pro_id, "business_name": "Untouched Pro", "role": "professional"}
    )
    db.leads.insert_many(
        [
            {
                "status": LeadStatus.PENDING_ADMIN_REVIEW.value,
                "created_at": now,
                "pro_id": None,
                "rejected_by": [],
            },
            {
                "status": LeadStatus.PENDING_ADMIN_REVIEW.value,
                "created_at": now,
                "pro_id": None,
                # rejected_by absent entirely
            },
            {
                "status": LeadStatus.BOOKED.value,
                "created_at": now,
                "pro_id": pro_id,
            },
        ]
    )
    perf = aq.get_pro_performance(db, days=30)
    assert len(perf) == 1
    assert perf[0]["name"] == "Untouched Pro"
    assert perf[0]["rejected"] == 0
    assert perf[0]["rejection_rate"] == 0


def test_pro_performance_rejected_and_escalated_lead_not_double_counted(db):
    """PRO-157 code-review blocker: a lead the pro rejected and that then
    escalated must not land in BOTH pipelines. Only a *successful* rematch
    (``monitor_service.reassign_lead``) rewrites ``pro_id``; every escalation
    branch — ``pro_flow._escalate_rejected_lead`` and monitor_service's
    no-replacement / MAX_REASSIGNMENTS-exhausted / no-usable-location /
    pro_offer_send_failed branches — leaves ``pro_id`` pointing at the pro
    who rejected it. Before the ``orphaned_rejections`` subtraction this read
    as total_leads=1 (still attributed via pro_id) AND rejected=1 (via
    rejected_by): 50% rejection_rate, 0% completion_rate. The correct read is
    100% rejection_rate and no completion data at all.
    """
    now = _now()
    pro_id = ObjectId()
    db.users.insert_one(
        {"_id": pro_id, "business_name": "Escalated Pro", "role": "professional"}
    )
    db.leads.insert_one(
        {
            "status": LeadStatus.PENDING_ADMIN_REVIEW.value,
            "created_at": now,
            "pro_id": pro_id,
            "rejected_by": [pro_id],
            "last_rejected_at": now,
        }
    )
    perf = aq.get_pro_performance(db, days=30)
    assert len(perf) == 1
    row = perf[0]
    assert row["total_leads"] == 0
    assert row["rejected"] == 1
    assert row["rejection_rate"] == 100.0
    assert row["completion_rate"] is None


def test_pro_performance_duplicate_rejected_by_entries_count_once(db):
    """``rejected_by: [pro_a, pro_a]`` is reachable in production: admin_flow
    does not exclude ``rejected_by`` when re-assigning, and pro_flow appends
    to it unconditionally, so the same pro can appear twice in one lead's
    array. ``$setUnion`` must de-duplicate before counting.
    """
    now = _now()
    pro_a = ObjectId()
    db.users.insert_one(
        {"_id": pro_a, "business_name": "Pro A", "role": "professional"}
    )
    db.leads.insert_one(
        {
            "status": LeadStatus.PENDING_ADMIN_REVIEW.value,
            "created_at": now,
            "pro_id": None,
            "rejected_by": [pro_a, pro_a],
            "last_rejected_at": now,
        }
    )
    perf = aq.get_pro_performance(db, days=30)
    assert len(perf) == 1
    assert perf[0]["rejected"] == 1


def test_pro_performance_rejection_counted_by_last_rejected_at_not_created_at(db):
    """The escalation case a ``created_at`` window used to hide: a lead
    created 60 days ago (outside a 30-day view) but rejected — and thus
    escalated — yesterday IS counted, because ``reassign_lead`` only rewrites
    ``created_at`` on a *successful* rematch; an escalated lead keeps its
    original ``created_at``. Paired with
    ``test_pro_performance_rejection_window_excludes_old_rejections`` above,
    which proves the converse (recent ``created_at``, stale
    ``last_rejected_at`` -> not counted).
    """
    now = _now()
    pro_id = ObjectId()
    db.users.insert_one(
        {"_id": pro_id, "business_name": "Late Rejector", "role": "professional"}
    )
    db.leads.insert_one(
        {
            "status": LeadStatus.PENDING_ADMIN_REVIEW.value,
            "created_at": now - timedelta(days=60),
            "pro_id": None,
            "rejected_by": [pro_id],
            "last_rejected_at": now - timedelta(days=1),
        }
    )
    perf = aq.get_pro_performance(db, days=30)
    assert len(perf) == 1
    assert perf[0]["rejected"] == 1


def test_pro_performance_rejection_only_unknown_pro_falls_back_to_unknown_name(db):
    """A ``rejected_by`` id with no matching ``users`` document falls back to
    the name "Unknown" instead of raising or silently dropping the row.
    """
    now = _now()
    unknown_pro = ObjectId()
    db.leads.insert_one(
        {
            "status": LeadStatus.PENDING_ADMIN_REVIEW.value,
            "created_at": now,
            "pro_id": None,
            "rejected_by": [unknown_pro],
            "last_rejected_at": now,
        }
    )
    perf = aq.get_pro_performance(db, days=30)
    assert len(perf) == 1
    assert perf[0]["name"] == "Unknown"
    assert perf[0]["rejected"] == 1


def test_pro_performance_resorts_by_adjusted_total_not_raw_count(db):
    """The initial ``$sort`` runs on the raw per-pro lead count, before
    ``orphaned_rejections`` is subtracted. The final ordering must reflect
    the adjusted ``total_leads`` the operator actually reads, not the raw
    one, or a pro who mostly rejects-and-escalates would rank above a pro
    who genuinely holds more leads.
    """
    now = _now()
    pro_x = ObjectId()  # raw total_leads=3, 2 orphaned -> adjusted=1
    pro_y = ObjectId()  # raw total_leads=2, 0 orphaned -> adjusted=2
    db.users.insert_many(
        [
            {"_id": pro_x, "business_name": "Pro X", "role": "professional"},
            {"_id": pro_y, "business_name": "Pro Y", "role": "professional"},
        ]
    )
    db.leads.insert_many(
        [
            {"status": LeadStatus.BOOKED.value, "created_at": now, "pro_id": pro_x},
            {
                "status": LeadStatus.PENDING_ADMIN_REVIEW.value,
                "created_at": now,
                "pro_id": pro_x,
                "rejected_by": [pro_x],
                "last_rejected_at": now,
            },
            {
                "status": LeadStatus.REJECTED.value,
                "created_at": now,
                "pro_id": pro_x,
                "rejected_by": [pro_x],
                "last_rejected_at": now,
            },
            {"status": LeadStatus.BOOKED.value, "created_at": now, "pro_id": pro_y},
            {"status": LeadStatus.COMPLETED.value, "created_at": now, "pro_id": pro_y},
        ]
    )
    perf = aq.get_pro_performance(db, days=30)
    names = [r["name"] for r in perf]
    # raw counts would sort Pro X (3) before Pro Y (2); adjusted counts must
    # sort Pro Y (2) before Pro X (1).
    assert names.index("Pro Y") < names.index("Pro X")
    by_name = {r["name"]: r for r in perf}
    assert by_name["Pro X"]["total_leads"] == 1
    assert by_name["Pro Y"]["total_leads"] == 2


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
