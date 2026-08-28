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
    """Per-professional lead counts, completion/rejection rates, avg rating.

    Names come from a batched ``$lookup`` and ratings from ONE reviews
    aggregation — deliberately no per-pro queries (the deleted async copy had
    an N+1 here; this shape is the keeper).

    **Rejections are counted from ``rejected_by``, never from resting status
    (PRO-157).** Since PRO-117 made ``REJECTED`` a way-station — a rejected
    lead is immediately re-matched to the next pro or escalated — no lead
    rests in it, so the old status-based count read a permanent 0 and the
    rejecting pro vanished from the table the moment the lead was
    re-attributed. ``rejected_by`` is the durable record: multikey, so one
    lead counts against every pro who explicitly rejected it, and a pro whose
    every lead was rejected away still gets a (rejection-only) row.

    **The denominator decision:** ``total_leads`` keeps meaning "leads
    currently attributed to this pro" — folding rejected-and-gone leads back
    in would drag ``completion_rate`` down for a reason unrelated to
    completion. The declined-share lives in its own metric instead:
    ``rejection_rate = rejected / (total_leads + rejected)``.

    **Why ``orphaned_rejections`` is subtracted.** Only the *successful*
    rematch rewrites ``pro_id`` (``monitor_service.reassign_lead``); every
    escalation branch — no replacement found, ``MAX_REASSIGNMENTS``
    exhausted, no usable location, ``pro_offer_send_failed``, and
    ``pro_flow._escalate_rejected_lead`` — moves the lead to
    PENDING_ADMIN_REVIEW and leaves ``pro_id`` pointing at the pro who
    rejected it. Counting those raw would put one lead in *both* pipelines:
    the pro who declined their only lead would render 50% declined and 0%
    completed instead of 100% and "no data". So a lead parked in
    PENDING_ADMIN_REVIEW / REJECTED whose ``pro_id`` appears in its own
    ``rejected_by`` is not attributed to that pro.

    **Two different windows, deliberately.** Attribution is windowed on
    ``created_at`` — which ``reassign_lead`` *rewrites* on every successful
    hop, so for a rematched lead it means "last assigned at", not "created
    at". Rejections are windowed on ``last_rejected_at`` instead: it is the
    rejection's own timestamp, it is index-backed (the compound
    ``(rejected_by, last_rejected_at)`` index), and it keeps the escalation
    path visible — an escalated lead keeps its original ``created_at``, so a
    ``created_at`` window would hide exactly the failures this tab exists to
    surface. Its known limit: ``last_rejected_at`` holds only the *latest*
    rejection, so for a lead declined by several pros in turn every hop is
    dated to the last one — bounded by ``MAX_REASSIGNMENTS``.

    Remaining limit, accepted: a lead reassigned away for SLA *silence* (no
    explicit דחה) is not attributable to the losing pro at all
    (``reassigned_from`` holds only the last hop), so ``rejection_rate``
    measures explicit declines, not every lost offer.
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
                "booked": {
                    "$sum": {"$cond": [{"$eq": ["$status", LeadStatus.BOOKED]}, 1, 0]}
                },
                # Leads still carrying this pro's id only because an
                # escalation branch never cleared it (see the docstring).
                # $expr is NOT usable here — mongomock silently returns an
                # empty result set for it, so the test would pass for the
                # wrong reason; the $cond form behaves identically on both.
                "orphaned_rejections": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$pro_id",
                                            {"$ifNull": ["$rejected_by", []]},
                                        ]
                                    },
                                    {
                                        "$in": [
                                            "$status",
                                            [
                                                LeadStatus.PENDING_ADMIN_REVIEW,
                                                LeadStatus.REJECTED,
                                            ],
                                        ]
                                    },
                                ]
                            },
                            1,
                            0,
                        ]
                    }
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

    # Rejections, from the durable PRO-117 record, windowed on the rejection's
    # own timestamp (see the docstring for why not created_at). $setUnion
    # de-duplicates the array before counting: nothing stops a lead being
    # re-assigned by an admin to a pro who already declined it and declined
    # again, and rejected_by is appended to unconditionally.
    rejections: dict = {}
    for r in db.leads.aggregate(
        [
            {
                "$match": {
                    "last_rejected_at": {"$gte": cutoff},
                    "rejected_by": {"$exists": True, "$ne": []},
                }
            },
            {
                "$project": {
                    "rejected_by": {
                        "$setUnion": [{"$ifNull": ["$rejected_by", []]}, []]
                    }
                }
            },
            {"$unwind": "$rejected_by"},
            {"$group": {"_id": "$rejected_by", "count": {"$sum": 1}}},
        ]
    ):
        rejections[r["_id"]] = r["count"]

    # Pros who appear only through rejections (everything they held was
    # re-attributed) still deserve a row — batch their names in one query,
    # keeping the function N+1-free.
    seen_ids = {doc["_id"] for doc in raw_results}
    rejection_only_ids = [pid for pid in rejections if pid not in seen_ids]
    rejection_only_names: dict = {}
    if rejection_only_ids:
        for user in db.users.find(
            {"_id": {"$in": rejection_only_ids}}, {"business_name": 1}
        ):
            rejection_only_names[user["_id"]] = user.get("business_name", "Unknown")

    # Batch fetch all ratings in one aggregation instead of N per-pro queries
    pro_ids = [doc["_id"] for doc in raw_results] + rejection_only_ids
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
        total = doc["total_leads"] - doc.get("orphaned_rejections", 0)
        completed = doc["completed"]
        rejected = rejections.get(doc["_id"], 0)
        offers = total + rejected

        results.append(
            {
                "name": pro_name,
                "total_leads": total,
                "completed": completed,
                "rejected": rejected,
                "booked": doc["booked"],
                # None, not 0 — a pro holding nothing in this window has no
                # completion rate to show, and a 0% progress bar reads as
                # "completes nothing". Streamlit renders a null as empty.
                "completion_rate": (
                    round((completed / total * 100), 1) if total > 0 else None
                ),
                "rejection_rate": (
                    round((rejected / offers * 100), 1) if offers > 0 else None
                ),
                "avg_rating": ratings.get(doc["_id"]) or "-",
            }
        )

    # The $sort ran on the raw count, before orphaned leads were subtracted —
    # re-sort on the number the operator actually reads.
    results.sort(key=lambda r: -r["total_leads"])

    # Rejection-only rows, after the attributed rows, highest decline count
    # first. rejection_rate is 100% *within this window* — the pro may still
    # hold leads assigned before the cutoff.
    for pid in sorted(rejection_only_ids, key=lambda p: -rejections[p]):
        results.append(
            {
                "name": rejection_only_names.get(pid, "Unknown"),
                "total_leads": 0,
                "completed": 0,
                "rejected": rejections[pid],
                "booked": 0,
                "completion_rate": None,
                "rejection_rate": 100.0,
                "avg_rating": ratings.get(pid) or "-",
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
