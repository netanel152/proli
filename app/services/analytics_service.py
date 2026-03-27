"""
Analytics Service - MongoDB aggregation pipelines for business metrics.

Provides lead funnel, pro performance, response times, and volume stats.
All functions are async (Motor) for use by both API and admin panel.
"""

from datetime import datetime, timedelta, timezone
from bson.objectid import ObjectId
from app.core.database import leads_collection, messages_collection, users_collection, reviews_collection
from app.core.constants import LeadStatus
from app.core.logger import logger


async def get_lead_funnel(days: int = 30) -> dict:
    """
    Lead conversion funnel: count leads by status within a date range.
    Returns: {"new": N, "contacted": N, "booked": N, "completed": N, "rejected": N, ...}
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    pipeline = [
        {"$match": {"created_at": {"$gte": cutoff}}},
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ]

    results = {}
    async for doc in leads_collection.aggregate(pipeline):
        results[doc["_id"]] = doc["count"]

    # Ensure all statuses are present
    for status in LeadStatus:
        results.setdefault(status.value, 0)

    return results


async def get_daily_volume(days: int = 30) -> list[dict]:
    """
    Daily lead creation volume.
    Returns: [{"date": "2026-03-25", "count": 5}, ...]
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    pipeline = [
        {"$match": {"created_at": {"$gte": cutoff}}},
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}},
            "count": {"$sum": 1},
        }},
        {"$sort": {"_id": 1}},
    ]

    results = []
    async for doc in leads_collection.aggregate(pipeline):
        results.append({"date": doc["_id"], "count": doc["count"]})

    return results


async def get_pro_performance(pro_id: str | None = None, days: int = 30) -> list[dict]:
    """
    Performance metrics per professional.
    Returns list of: {pro_id, name, total_leads, completed, rejected, completion_rate, avg_rating}
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    match_stage = {"created_at": {"$gte": cutoff}, "pro_id": {"$exists": True, "$ne": None}}
    if pro_id:
        match_stage["pro_id"] = ObjectId(pro_id)

    pipeline = [
        {"$match": match_stage},
        {"$group": {
            "_id": "$pro_id",
            "total_leads": {"$sum": 1},
            "completed": {"$sum": {"$cond": [{"$eq": ["$status", LeadStatus.COMPLETED]}, 1, 0]}},
            "rejected": {"$sum": {"$cond": [{"$eq": ["$status", LeadStatus.REJECTED]}, 1, 0]}},
            "booked": {"$sum": {"$cond": [{"$eq": ["$status", LeadStatus.BOOKED]}, 1, 0]}},
        }},
        {"$sort": {"total_leads": -1}},
    ]

    results = []
    async for doc in leads_collection.aggregate(pipeline):
        pid = doc["_id"]
        pro = await users_collection.find_one({"_id": pid})
        pro_name = pro.get("business_name", "Unknown") if pro else "Unknown"

        total = doc["total_leads"]
        completed = doc["completed"]
        completion_rate = round((completed / total * 100), 1) if total > 0 else 0

        # Get average rating from reviews
        avg_rating = None
        rating_pipeline = [
            {"$match": {"pro_id": pid}},
            {"$group": {"_id": None, "avg": {"$avg": "$rating"}, "count": {"$sum": 1}}},
        ]
        async for r in reviews_collection.aggregate(rating_pipeline):
            avg_rating = round(r["avg"], 1) if r["avg"] else None

        results.append({
            "pro_id": str(pid),
            "name": pro_name,
            "total_leads": total,
            "completed": completed,
            "rejected": doc["rejected"],
            "booked": doc["booked"],
            "completion_rate": completion_rate,
            "avg_rating": avg_rating,
        })

    return results


async def get_response_time_stats(days: int = 30) -> dict:
    """
    Average time from lead creation to first pro message (in minutes).
    Returns: {"avg_minutes": N, "median_minutes": N, "sample_size": N}
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Get leads that have pro_id assigned
    leads_cursor = leads_collection.find({
        "created_at": {"$gte": cutoff},
        "pro_id": {"$exists": True, "$ne": None},
    }).limit(500)

    response_times = []
    async for lead in leads_cursor:
        lead_created = lead.get("created_at")
        if not lead_created:
            continue

        # Find first model message after lead creation
        first_response = await messages_collection.find_one(
            {
                "chat_id": lead["chat_id"],
                "role": "model",
                "timestamp": {"$gte": lead_created},
            },
            sort=[("timestamp", 1)],
        )

        if first_response and first_response.get("timestamp"):
            delta = (first_response["timestamp"] - lead_created).total_seconds() / 60
            if 0 < delta < 1440:  # Sanity: under 24 hours
                response_times.append(delta)

    if not response_times:
        return {"avg_minutes": None, "median_minutes": None, "sample_size": 0}

    response_times.sort()
    avg = round(sum(response_times) / len(response_times), 1)
    median = round(response_times[len(response_times) // 2], 1)

    return {
        "avg_minutes": avg,
        "median_minutes": median,
        "sample_size": len(response_times),
    }


async def get_overview_metrics() -> dict:
    """
    High-level dashboard metrics.
    """
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=7)

    total_leads = await leads_collection.count_documents({})
    leads_today = await leads_collection.count_documents({"created_at": {"$gte": today_start}})
    leads_this_week = await leads_collection.count_documents({"created_at": {"$gte": week_start}})
    active_pros = await users_collection.count_documents({"is_active": True, "role": "professional"})
    completed_leads = await leads_collection.count_documents({"status": LeadStatus.COMPLETED})
    total_reviews = await reviews_collection.count_documents({})

    conversion_rate = round((completed_leads / total_leads * 100), 1) if total_leads > 0 else 0

    return {
        "total_leads": total_leads,
        "leads_today": leads_today,
        "leads_this_week": leads_this_week,
        "active_pros": active_pros,
        "completed_leads": completed_leads,
        "total_reviews": total_reviews,
        "conversion_rate": conversion_rate,
    }


async def get_leads_by_pro_type(days: int = 30) -> list[dict]:
    """
    Lead distribution by professional type.
    Returns: [{"type": "plumber", "count": N}, ...]
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    pipeline = [
        {"$match": {"created_at": {"$gte": cutoff}, "pro_id": {"$exists": True, "$ne": None}}},
        {"$lookup": {
            "from": "users",
            "localField": "pro_id",
            "foreignField": "_id",
            "as": "pro",
        }},
        {"$unwind": {"path": "$pro", "preserveNullAndEmptyArrays": True}},
        {"$group": {
            "_id": "$pro.type",
            "count": {"$sum": 1},
        }},
        {"$sort": {"count": -1}},
    ]

    results = []
    async for doc in leads_collection.aggregate(pipeline):
        results.append({
            "type": doc["_id"] or "unassigned",
            "count": doc["count"],
        })

    return results
