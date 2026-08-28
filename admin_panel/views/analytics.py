"""
Analytics Dashboard - Business metrics and reporting.
"""

import streamlit as st
import pandas as pd
from datetime import datetime, timedelta, timezone
from pymongo import MongoClient
from app.core.config import settings
from app.core.constants import LeadStatus, WorkerConstants
from app.core.database import DB_NAME
from app.core.logger import logger
from admin_panel.core import analytics_queries as aq
import certifi

_mongo_uri = settings.MONGO_URI.get_secret_value()  # PRO-94: SecretStr
_ca = certifi.where() if "+srv" in _mongo_uri else None
_kwargs = {"tlsCAFile": _ca} if _ca else {}
_sync_client = MongoClient(_mongo_uri, **_kwargs)
_db = _sync_client[DB_NAME]


def _fetch(T, fn, *args, **kwargs):
    """Run one analytics query against the panel's DB, guarded (PRO-140).

    A failed aggregation used to surface as a raw Streamlit traceback; now the
    operator gets a spinner while it runs and an st.error if it fails, and the
    section renders its empty-state instead of crashing the page.
    """
    try:
        with st.spinner(T.get("analytics_loading", "Loading…")):
            return fn(_db, *args, **kwargs)
    except Exception as e:
        logger.error(f"Analytics query {getattr(fn, '__name__', fn)} failed: {e}")
        st.error(
            T.get(
                "analytics_query_error",
                "Failed to load this section — check the database connection and refresh.",
            )
        )
        return None


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

    # Leads in the selected period — guarded: if Mongo is unreachable, show one
    # error and stop instead of a raw traceback (PRO-140).
    def _overview_counts(db):
        return (
            db.leads.count_documents({"created_at": {"$gte": cutoff}}),
            db.leads.count_documents(
                {"created_at": {"$gte": cutoff}, "status": LeadStatus.COMPLETED}
            ),
            db.leads.count_documents({"created_at": {"$gte": today_start}}),
            db.users.count_documents({"is_active": True, "role": "professional"}),
        )

    counts = _fetch(T, _overview_counts)
    if counts is None:
        st.stop()
    period_leads, period_completed, leads_today, active_pros = counts

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

        funnel = _fetch(T, aq.get_lead_funnel, days)
        if funnel and any(v > 0 for v in funnel.values()):
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
                sh = _fetch(T, aq.get_status_history_metrics, days) or {
                    "median_new_to_booked_hours": None,
                    "contacted_to_booked_pct": None,
                    "sample_new_to_booked": 0,
                    "sample_contacted": 0,
                }
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

        volume = _fetch(T, aq.get_daily_volume, days)
        if volume:
            df = pd.DataFrame(volume)
            st.line_chart(df, x="date", y="count", color="#2563EB")
        else:
            st.info(T.get("no_data", "No data available for this period."))

    with tab_pros:
        st.subheader(T.get("pro_perf_title", "Professional Performance"))
        # PRO-157: the table now carries two different windows and a row type
        # that looks like a data bug without this sentence. Global RTL CSS
        # already covers st.caption, so no dir= wrapper is needed.
        st.caption(
            T.get(
                "pro_perf_caption",
                "Rows with no leads are pros who only declined offers in this "
                "window — their leads were reassigned. Declined % is out of "
                "known offers (leads held plus declines). An empty cell means "
                "no data for the window, not zero.",
            )
        )

        perf = _fetch(T, aq.get_pro_performance, days)
        if perf:
            df = pd.DataFrame(perf)
            st.dataframe(
                df,
                column_config={
                    "name": st.column_config.TextColumn(
                        T.get("col_pro_name", "Professional")
                    ),
                    "total_leads": st.column_config.NumberColumn(
                        T.get("col_total", "Total"),
                        help=T.get(
                            "help_col_total",
                            "Leads currently attributed to this pro in the "
                            "window. Declines are NOT part of this number.",
                        ),
                    ),
                    "completed": st.column_config.NumberColumn(
                        T.get("col_completed", "Completed")
                    ),
                    "rejected": st.column_config.NumberColumn(
                        T.get("col_rejected", "Rejected"),
                        help=T.get(
                            "help_col_rejected",
                            "Offers this pro explicitly declined — counted on "
                            "top of Total, not out of it, and dated by the "
                            "decline itself rather than the lead's creation.",
                        ),
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
                    "rejection_rate": st.column_config.NumberColumn(
                        T.get("col_rejection_rate", "Declined %"),
                        format="%.1f%%",
                    ),
                    "avg_rating": st.column_config.TextColumn(
                        T.get("col_rating", "Rating")
                    ),
                },
                column_order=(
                    "name",
                    "total_leads",
                    "completion_rate",
                    "rejection_rate",
                    "completed",
                    "booked",
                    "rejected",
                    "avg_rating",
                ),
                hide_index=True,
                use_container_width=True,
            )
        else:
            st.info(T.get("no_data", "No data available for this period."))

    with tab_types:
        st.subheader(T.get("by_type_title", "Leads by Service Type"))

        types = _fetch(T, aq.get_leads_by_type, days)
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

        rev = _fetch(T, aq.get_revenue_stats, days)
        if rev and rev["priced_jobs"] > 0:
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

        tokens_data = _fetch(T, aq.get_finops_stats)
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
