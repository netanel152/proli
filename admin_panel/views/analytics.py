"""
Analytics Dashboard - Business metrics and reporting.
"""

import streamlit as st
import pandas as pd
from datetime import datetime, timedelta, timezone
from pymongo import MongoClient
from app.core.config import settings
from app.core.constants import LeadStatus
import certifi

_ca = certifi.where() if "+srv" in settings.MONGO_URI else None
_kwargs = {"tlsCAFile": _ca} if _ca else {}
_sync_client = MongoClient(settings.MONGO_URI, **_kwargs)
_db = _sync_client.proli_db


def _get_lead_funnel(days: int = 30) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    pipeline = [
        {"$match": {"created_at": {"$gte": cutoff}}},
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ]
    results = {}
    for doc in _db.leads.aggregate(pipeline):
        results[doc["_id"]] = doc["count"]
    for status in LeadStatus:
        results.setdefault(status.value, 0)
    return results


def _get_daily_volume(days: int = 30) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    pipeline = [
        {"$match": {"created_at": {"$gte": cutoff}}},
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}},
            "count": {"$sum": 1},
        }},
        {"$sort": {"_id": 1}},
    ]
    return [{"date": doc["_id"], "count": doc["count"]} for doc in _db.leads.aggregate(pipeline)]


def _get_pro_performance(days: int = 30) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    pipeline = [
        {"$match": {"created_at": {"$gte": cutoff}, "pro_id": {"$exists": True, "$ne": None}}},
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
    for doc in _db.leads.aggregate(pipeline):
        pid = doc["_id"]
        pro = _db.users.find_one({"_id": pid})
        pro_name = pro.get("business_name", "Unknown") if pro else "Unknown"

        total = doc["total_leads"]
        completed = doc["completed"]
        rate = round((completed / total * 100), 1) if total > 0 else 0

        avg_rating = None
        for r in _db.reviews.aggregate([
            {"$match": {"pro_id": pid}},
            {"$group": {"_id": None, "avg": {"$avg": "$rating"}, "count": {"$sum": 1}}},
        ]):
            avg_rating = round(r["avg"], 1) if r["avg"] else None

        results.append({
            "name": pro_name,
            "total_leads": total,
            "completed": completed,
            "rejected": doc["rejected"],
            "booked": doc["booked"],
            "completion_rate": rate,
            "avg_rating": avg_rating or "-",
        })

    return results


def _get_leads_by_type(days: int = 30) -> list[dict]:
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
        {"$group": {"_id": "$pro.type", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    return [{"type": doc["_id"] or "unassigned", "count": doc["count"]} for doc in _db.leads.aggregate(pipeline)]


def view_analytics(T):
    st.title(T.get("analytics_title", "Analytics & Reporting"))
    st.caption(T.get("analytics_desc", "Business metrics, lead funnels, and professional performance."))

    # Time range selector
    days = st.selectbox(
        T.get("analytics_range", "Time range"),
        options=[7, 14, 30, 60, 90],
        index=2,
        format_func=lambda d: f"{d} {T.get('days', 'days')}",
    )

    # --- Overview Metrics ---
    total_leads = _db.leads.count_documents({})
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=7)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric(T.get("metric_total", "Total Leads"), total_leads)
    c2.metric(T.get("analytics_today", "Today"), _db.leads.count_documents({"created_at": {"$gte": today_start}}))
    c3.metric(T.get("analytics_week", "This Week"), _db.leads.count_documents({"created_at": {"$gte": week_start}}))
    c4.metric(T.get("analytics_completed", "Completed"), _db.leads.count_documents({"status": LeadStatus.COMPLETED}))

    active_pros = _db.users.count_documents({"is_active": True, "role": "professional"})
    c5.metric(T.get("metric_pros", "Active Pros"), active_pros)

    st.markdown("")

    # --- Tabs ---
    tab_funnel, tab_volume, tab_pros, tab_types = st.tabs([
        T.get("tab_funnel", "Lead Funnel"),
        T.get("tab_volume", "Daily Volume"),
        T.get("tab_pro_perf", "Pro Performance"),
        T.get("tab_by_type", "By Service Type"),
    ])

    with tab_funnel:
        st.subheader(T.get("funnel_title", "Lead Conversion Funnel"))

        funnel = _get_lead_funnel(days)
        if any(v > 0 for v in funnel.values()):
            funnel_order = ["new", "contacted", "booked", "completed", "rejected", "closed", "cancelled"]
            funnel_data = pd.DataFrame([
                {"Status": s.capitalize(), "Count": funnel.get(s, 0)}
                for s in funnel_order
            ])
            st.bar_chart(funnel_data, x="Status", y="Count", color="#2563EB")

            # Conversion metrics
            total = sum(funnel.values())
            if total > 0:
                c1, c2, c3 = st.columns(3)
                contacted_rate = round((funnel.get("contacted", 0) + funnel.get("booked", 0) + funnel.get("completed", 0)) / total * 100, 1)
                booked_rate = round((funnel.get("booked", 0) + funnel.get("completed", 0)) / total * 100, 1)
                completed_rate = round(funnel.get("completed", 0) / total * 100, 1)
                c1.metric(T.get("contacted_rate", "Contacted Rate"), f"{contacted_rate}%")
                c2.metric(T.get("booked_rate", "Booked Rate"), f"{booked_rate}%")
                c3.metric(T.get("completed_rate", "Completed Rate"), f"{completed_rate}%")
        else:
            st.info(T.get("no_data", "No data available for this period."))

    with tab_volume:
        st.subheader(T.get("volume_title", "Daily Lead Volume"))

        volume = _get_daily_volume(days)
        if volume:
            df = pd.DataFrame(volume)
            st.line_chart(df, x="date", y="count", color="#2563EB")
        else:
            st.info(T.get("no_data", "No data available for this period."))

    with tab_pros:
        st.subheader(T.get("pro_perf_title", "Professional Performance"))

        perf = _get_pro_performance(days)
        if perf:
            df = pd.DataFrame(perf)
            st.dataframe(
                df,
                column_config={
                    "name": st.column_config.TextColumn(T.get("col_pro_name", "Professional")),
                    "total_leads": st.column_config.NumberColumn(T.get("col_total", "Total")),
                    "completed": st.column_config.NumberColumn(T.get("col_completed", "Completed")),
                    "rejected": st.column_config.NumberColumn(T.get("col_rejected", "Rejected")),
                    "booked": st.column_config.NumberColumn(T.get("col_booked", "Booked")),
                    "completion_rate": st.column_config.ProgressColumn(T.get("col_rate", "Completion %"), min_value=0, max_value=100, format="%.1f%%"),
                    "avg_rating": st.column_config.TextColumn(T.get("col_rating", "Rating")),
                },
                hide_index=True,
                use_container_width=True,
            )
        else:
            st.info(T.get("no_data", "No data available for this period."))

    with tab_types:
        st.subheader(T.get("by_type_title", "Leads by Service Type"))

        types = _get_leads_by_type(days)
        if types:
            df = pd.DataFrame(types)
            st.bar_chart(df, x="type", y="count", color="#2563EB")
        else:
            st.info(T.get("no_data", "No data available for this period."))
