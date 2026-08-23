"""Analytics aggregations for the admin panel — the ONE implementation (PRO-140).

History: these pipelines used to exist twice — an async copy in
``app/services/analytics_service.py`` (tested, but dead: nothing in ``app/``
called it) and a sync copy inline in ``admin_panel/views/analytics.py``
(live, but untested — including the PRO-33 GMV/commission math). Two copies of
money math is a drift bug waiting to happen, so the async service was deleted
and the sync copy moved here, streamlit-free and with ``db`` injected, so the
same functions the operator's Revenue tab renders are the ones the tests run.

Every function takes a synchronous PyMongo ``Database`` as its first argument
(the panel is sync end-to-end; only WhatsApp sends cross into async land, via
``app.providers.whatsapp.sync``). Tests pass a ``mongomock`` database.
"""

import statistics
from datetime import datetime, timedelta, timezone

from app.core.constants import LeadStatus


def get_lead_funnel(db, days: int = 30) -> dict:
    """Lead conversion funnel: count of leads per status within the window.

    Returns a dict with EVERY LeadStatus present (zero-filled), so charts keep
    a stable shape when a status has no leads yet.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    pipeline = [
        {"$match": {"created_at": {"$gte": cutoff}}},
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ]
    results = {}
    for doc in db.leads.aggregate(pipeline):
        results[doc["_id"]] = doc["count"]
    for status in LeadStatus:
        results.setdefault(status.value, 0)
    return results


def get_status_history_metrics(db, days: int = 30) -> dict:
    """Funnel timing derived from each lead's ``status_history`` (PRO-57).

    Returns median NEW->BOOKED time (hours) and the contacted->booked
    conversion rate. Leads created before this feature carry no
    ``status_history`` and are simply skipped — fully backward compatible.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cursor = db.leads.find(
        {
            "created_at": {"$gte": cutoff},
            "status_history": {"$exists": True, "$ne": []},
        },
        {"status_history": 1},
    )

    durations_hours = []
    contacted = 0
    contacted_and_booked = 0

    for lead in cursor:
        # First timestamp we saw each status at (transitions are appended in order).
        first_at: dict = {}
        for entry in lead.get("status_history") or []:
            status = entry.get("status")
            status = getattr(status, "value", status)  # normalize Enum -> str
            at = entry.get("at")
            if status and at and status not in first_at:
                first_at[status] = at

        new_at = first_at.get(LeadStatus.NEW.value)
        booked_at = first_at.get(LeadStatus.BOOKED.value)
        if new_at and booked_at and booked_at >= new_at:
            durations_hours.append((booked_at - new_at).total_seconds() / 3600.0)

        if LeadStatus.CONTACTED.value in first_at:
            contacted += 1
            if LeadStatus.BOOKED.value in first_at:
                contacted_and_booked += 1

    return {
        "median_new_to_booked_hours": (
            round(statistics.median(durations_hours), 1) if durations_hours else None
        ),
        "contacted_to_booked_pct": (
            round(contacted_and_booked / contacted * 100, 1) if contacted else None
        ),
        "sample_new_to_booked": len(durations_hours),
        "sample_contacted": contacted,
    }


def get_daily_volume(db, days: int = 30) -> list[dict]:
    """Daily lead creation volume: [{"date": "YYYY-MM-DD", "count": N}, ...]."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    pipeline = [
        {"$match": {"created_at": {"$gte": cutoff}}},
        {
            "$group": {
                "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}},
                "count": {"$sum": 1},
            }
        },
        {"$sort": {"_id": 1}},
    ]
    return [
        {"date": doc["_id"], "count": doc["count"]}
        for doc in db.leads.aggregate(pipeline)
    ]


def get_pro_performance(db, days: int = 30) -> list[dict]:
    """Per-professional lead counts, completion rate and average rating.

    Names come from a batched ``$lookup`` and ratings from ONE reviews
    aggregation — deliberately no per-pro queries (the deleted async copy had
    an N+1 here; this shape is the keeper).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    pipeline = [
        {
            "$match": {
                "created_at": {"$gte": cutoff},
                "pro_id": {"$exists": True, "$ne": None},
            }
        },
        {
            "$group": {
                "_id": "$pro_id",
                "total_leads": {"$sum": 1},
                "completed": {
                    "$sum": {
                        "$cond": [{"$eq": ["$status", LeadStatus.COMPLETED]}, 1, 0]
                    }
                },
                "rejected": {
                    "$sum": {"$cond": [{"$eq": ["$status", LeadStatus.REJECTED]}, 1, 0]}
                },
                "booked": {
                    "$sum": {"$cond": [{"$eq": ["$status", LeadStatus.BOOKED]}, 1, 0]}
                },
            }
        },
        {"$sort": {"total_leads": -1}},
        # Batch join pro names — replaces per-pro find_one (N+1 eliminated)
        {
            "$lookup": {
                "from": "users",
                "localField": "_id",
                "foreignField": "_id",
                "as": "pro",
            }
        },
        {"$unwind": {"path": "$pro", "preserveNullAndEmptyArrays": True}},
    ]

    raw_results = list(db.leads.aggregate(pipeline))

    # Batch fetch all ratings in one aggregation instead of N per-pro queries
    pro_ids = [doc["_id"] for doc in raw_results]
    ratings: dict = {}
    for r in db.reviews.aggregate(
        [
            {"$match": {"pro_id": {"$in": pro_ids}}},
            {"$group": {"_id": "$pro_id", "avg": {"$avg": "$rating"}}},
        ]
    ):
        ratings[r["_id"]] = round(r["avg"], 1) if r["avg"] else None

    results = []
    for doc in raw_results:
        pro_name = (doc.get("pro") or {}).get("business_name", "Unknown")
        total = doc["total_leads"]
        completed = doc["completed"]
        rate = round((completed / total * 100), 1) if total > 0 else 0

        results.append(
            {
                "name": pro_name,
                "total_leads": total,
                "completed": completed,
                "rejected": doc["rejected"],
                "booked": doc["booked"],
                "completion_rate": rate,
                "avg_rating": ratings.get(doc["_id"]) or "-",
            }
        )

    return results


def get_leads_by_type(db, days: int = 30) -> list[dict]:
    """Lead distribution by professional type: [{"type": ..., "count": N}, ...]."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    pipeline = [
        {
            "$match": {
                "created_at": {"$gte": cutoff},
                "pro_id": {"$exists": True, "$ne": None},
            }
        },
        {
            "$lookup": {
                "from": "users",
                "localField": "pro_id",
                "foreignField": "_id",
                "as": "pro",
            }
        },
        {"$unwind": {"path": "$pro", "preserveNullAndEmptyArrays": True}},
        {"$group": {"_id": "$pro.type", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    return [
        {"type": doc["_id"] or "unassigned", "count": doc["count"]}
        for doc in db.leads.aggregate(pipeline)
    ]


def get_revenue_stats(db, days: int = 30) -> dict:
    """GMV + platform commission over COMPLETED leads with a recorded
    ``final_price`` (PRO-33). Leads without a price are excluded (nullable
    field — fully backward compatible). Returns zeros when nothing is priced.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    pipeline = [
        {
            "$match": {
                "created_at": {"$gte": cutoff},
                "status": LeadStatus.COMPLETED,
                "final_price": {"$exists": True, "$ne": None},
            }
        },
        {
            "$group": {
                "_id": None,
                "gmv": {"$sum": "$final_price"},
                "commission": {"$sum": "$commission_amount"},
                "priced_jobs": {"$sum": 1},
            }
        },
    ]
    result = {"gmv": 0, "commission": 0, "priced_jobs": 0, "avg_ticket": None}
    for doc in db.leads.aggregate(pipeline):
        gmv = doc.get("gmv") or 0
        priced = doc.get("priced_jobs") or 0
        result = {
            "gmv": round(gmv, 2),
            "commission": round(doc.get("commission") or 0, 2),
            "priced_jobs": priced,
            "avg_ticket": round(gmv / priced, 2) if priced else None,
        }
    return result


def get_finops_stats(db) -> list[dict]:
    """AI token usage per professional, highest first."""
    pipeline = [
        {
            "$match": {
                "role": "professional",
                "total_tokens_used": {"$exists": True, "$gt": 0},
            }
        },
        {
            "$project": {
                "name": {"$ifNull": ["$business_name", "$name"]},
                "tokens": "$total_tokens_used",
                "phone": "$phone_number",
            }
        },
        {"$sort": {"tokens": -1}},
    ]
    return list(db.users.aggregate(pipeline))
