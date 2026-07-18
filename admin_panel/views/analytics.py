"""
Analytics Dashboard - Business metrics and reporting.
"""

import statistics
import streamlit as st
import pandas as pd
from datetime import datetime, timedelta, timezone
from pymongo import MongoClient
from app.core.config import settings
from app.core.constants import LeadStatus, WorkerConstants
from app.core.database import DB_NAME
import certifi

_ca = certifi.where() if "+srv" in settings.MONGO_URI else None
_kwargs = {"tlsCAFile": _ca} if _ca else {}
_sync_client = MongoClient(settings.MONGO_URI, **_kwargs)
_db = _sync_client[DB_NAME]


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


def _get_status_history_metrics(days: int = 30) -> dict:
    """Funnel timing derived from each lead's ``status_history`` (PRO-57).

    Returns median NEW->BOOKED time (hours) and the contacted->booked
    conversion rate. Leads created before this feature carry no
    ``status_history`` and are simply skipped — fully backward compatible.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cursor = _db.leads.find(
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


def _get_daily_volume(days: int = 30) -> list[dict]:
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
        for doc in _db.leads.aggregate(pipeline)
    ]


def _get_pro_performance(days: int = 30) -> list[dict]:
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

    raw_results = list(_db.leads.aggregate(pipeline))

    # Batch fetch all ratings in one aggregation instead of N per-pro queries
    pro_ids = [doc["_id"] for doc in raw_results]
    ratings: dict = {}
    for r in _db.reviews.aggregate(
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


def _get_leads_by_type(days: int = 30) -> list[dict]:
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
        for doc in _db.leads.aggregate(pipeline)
    ]


def _get_revenue_stats(days: int = 30) -> dict:
    """GMV + platform commission over COMPLETED leads with a recorded final_price
    (PRO-33). Mirrors analytics_service.get_revenue_stats for the sync admin panel."""
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
    for doc in _db.leads.aggregate(pipeline):
        gmv = doc.get("gmv") or 0
        priced = doc.get("priced_jobs") or 0
        result = {
            "gmv": round(gmv, 2),
            "commission": round(doc.get("commission") or 0, 2),
            "priced_jobs": priced,
            "avg_ticket": round(gmv / priced, 2) if priced else None,
        }
    return result


def _get_finops_stats() -> list[dict]:
    """Fetch AI token usage per professional."""
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
    return list(_db.users.aggregate(pipeline))


def view_analytics(T):
    st.title(T.get("analytics_title", "Analytics & Reporting"))
    st.caption(
        T.get(
            "analytics_desc",
            "Business metrics, lead funnels, and professional performance.",
        )
    )

    # Time range selector
    days = st.selectbox(
        T.get("analytics_range", "Time range"),
        options=[7, 14, 30, 60, 90],
        index=2,
        format_func=lambda d: f"{d} {T.get('days', 'days')}",
    )

    # --- Overview Metrics ---
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Leads in the selected period
    period_leads = _db.leads.count_documents({"created_at": {"$gte": cutoff}})
    period_completed = _db.leads.count_documents(
        {"created_at": {"$gte": cutoff}, "status": LeadStatus.COMPLETED}
    )
    leads_today = _db.leads.count_documents({"created_at": {"$gte": today_start}})
    active_pros = _db.users.count_documents({"is_active": True, "role": "professional"})

    # Calculate conversion for the period
    conv_rate = (
        round((period_completed / period_leads * 100), 1) if period_leads > 0 else 0
    )

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric(f"{T.get('metric_total', 'Leads')} ({days}d)", period_leads)
    c2.metric(T.get("analytics_today", "Today"), leads_today)
    c3.metric(
        f"{T.get('analytics_completed', 'Completed')} ({days}d)", period_completed
    )
    c4.metric(T.get("conversion_rate", "Conv. Rate"), f"{conv_rate}%")
    c5.metric(T.get("metric_pros", "Active Pros"), active_pros)

    st.markdown("")

    # --- Tabs ---
    tab_funnel, tab_volume, tab_pros, tab_types, tab_revenue, tab_finops = st.tabs(
        [
            T.get("tab_funnel", "Lead Funnel"),
            T.get("tab_volume", "Daily Volume"),
            T.get("tab_pro_perf", "Pro Performance"),
            T.get("tab_by_type", "By Service Type"),
            T.get("tab_revenue", "Revenue (GMV)"),
            "FinOps (AI Costs)",
        ]
    )

    with tab_funnel:
        st.subheader(T.get("funnel_title", "Lead Conversion Funnel"))

        funnel = _get_lead_funnel(days)
        if any(v > 0 for v in funnel.values()):
            funnel_order = [
                "new",
                "contacted",
                "booked",
                "completed",
                "rejected",
                "closed",
                "cancelled",
            ]
            funnel_data = pd.DataFrame(
                [
                    {"Status": s.capitalize(), "Count": funnel.get(s, 0)}
                    for s in funnel_order
                ]
            )
            st.bar_chart(funnel_data, x="Status", y="Count", color="#2563EB")

            # Conversion metrics
            total = sum(funnel.values())
            if total > 0:
                c1, c2, c3 = st.columns(3)
                contacted_rate = round(
                    (
                        funnel.get("contacted", 0)
                        + funnel.get("booked", 0)
                        + funnel.get("completed", 0)
                    )
                    / total
                    * 100,
                    1,
                )
                booked_rate = round(
                    (funnel.get("booked", 0) + funnel.get("completed", 0))
                    / total
                    * 100,
                    1,
                )
                completed_rate = round(funnel.get("completed", 0) / total * 100, 1)
                c1.metric(
                    T.get("contacted_rate", "Contacted Rate"), f"{contacted_rate}%"
                )
                c2.metric(T.get("booked_rate", "Booked Rate"), f"{booked_rate}%")
                c3.metric(
                    T.get("completed_rate", "Completed Rate"), f"{completed_rate}%"
                )

                # Time-in-stage funnel metrics from status_history (PRO-57).
                st.markdown("")
                st.caption(
                    T.get(
                        "time_in_stage_caption",
                        "Time-in-stage & conversion (from status history)",
                    )
                )
                sh = _get_status_history_metrics(days)
                m1, m2 = st.columns(2)
                median_h = sh["median_new_to_booked_hours"]
                m1.metric(
                    T.get("median_new_booked", "Median time to book (NEW→BOOKED)"),
                    f"{median_h}h" if median_h is not None else "—",
                    help=f"n={sh['sample_new_to_booked']} "
                    + T.get("leads_with_history", "leads with history"),
                )
                conv = sh["contacted_to_booked_pct"]
                m2.metric(
                    T.get("contacted_booked_conv", "Booked (of contacted)"),
                    f"{conv}%" if conv is not None else "—",
                    help=f"n={sh['sample_contacted']} "
                    + T.get("contacted_leads", "contacted leads"),
                )
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
                    "name": st.column_config.TextColumn(
                        T.get("col_pro_name", "Professional")
                    ),
                    "total_leads": st.column_config.NumberColumn(
                        T.get("col_total", "Total")
                    ),
                    "completed": st.column_config.NumberColumn(
                        T.get("col_completed", "Completed")
                    ),
                    "rejected": st.column_config.NumberColumn(
                        T.get("col_rejected", "Rejected")
                    ),
                    "booked": st.column_config.NumberColumn(
                        T.get("col_booked", "Booked")
                    ),
                    "completion_rate": st.column_config.ProgressColumn(
                        T.get("col_rate", "Completion %"),
                        min_value=0,
                        max_value=100,
                        format="%.1f%%",
                    ),
                    "avg_rating": st.column_config.TextColumn(
                        T.get("col_rating", "Rating")
                    ),
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

    with tab_revenue:
        st.subheader(T.get("revenue_title", "Revenue & Commission (GMV)"))
        st.caption(
            T.get(
                "revenue_desc",
                "Captured deal value on completed jobs. GMV = sum of final prices; "
                "commission = platform take-rate. Jobs with no recorded price are excluded.",
            )
        )

        rev = _get_revenue_stats(days)
        if rev["priced_jobs"] > 0:
            r1, r2, r3, r4 = st.columns(4)
            r1.metric(f"{T.get('revenue_gmv', 'GMV')} ({days}d)", f"₪{rev['gmv']:,.0f}")
            r2.metric(
                f"{T.get('revenue_commission', 'Commission')} ({days}d)",
                f"₪{rev['commission']:,.2f}",
            )
            r3.metric(T.get("revenue_priced_jobs", "Priced jobs"), rev["priced_jobs"])
            avg_ticket = rev["avg_ticket"]
            r4.metric(
                T.get("revenue_avg_ticket", "Avg ticket"),
                f"₪{avg_ticket:,.0f}" if avg_ticket is not None else "—",
            )
            st.caption(
                T.get("revenue_takerate_note", "Take-rate")
                + f": {WorkerConstants.COMMISSION_RATE:.0%}"
            )
        else:
            st.info(
                T.get(
                    "revenue_no_data",
                    "No priced jobs yet — pros record the charged amount after completing a job.",
                )
            )

    with tab_finops:
        st.subheader("FinOps: Lifetime AI Token Usage")
        st.caption(
            "Monitoring cumulative Google Gemini token consumption per professional to track overall API costs."
        )

        tokens_data = _get_finops_stats()
        if tokens_data:
            df_tokens = pd.DataFrame(tokens_data)

            col_a, col_b = st.columns([2, 1])
            with col_a:
                st.dataframe(
                    df_tokens,
                    column_config={
                        "name": "Professional",
                        "phone": "Phone",
                        "tokens": st.column_config.NumberColumn(
                            "Tokens Used", format="%d"
                        ),
                    },
                    hide_index=True,
                    use_container_width=True,
                )
            with col_b:
                total_tokens = df_tokens["tokens"].sum()
                st.metric("Total System Tokens", f"{total_tokens:,}")
                st.info(
                    f"Estimated Cost: ${round(total_tokens / 1_000_000 * 0.15, 4)}"
                )  # Rough Flash Lite 2.5 estimate

            st.markdown("### Token Distribution")
            st.bar_chart(df_tokens, x="name", y="tokens", color="#F59E0B")
        else:
            st.info("No token usage data available.")
