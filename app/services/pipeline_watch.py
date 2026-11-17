"""PRO-17 — the lead-pipeline watch, run by the worker every 10 minutes.

The 2026-04-18 post-mortem asked for something to watch the two counters behind
``/health/leads``. A GitHub Actions schedule did that, but GitHub's scheduler
skips and delays runs under load, so the watch now runs in the worker itself:

* It reads the two counts straight from Mongo, the same queries the endpoint
  makes (``lead_pipeline_counts``), so no token is needed and none is handed to
  anything outside the process.
* It applies the same rules as ``scripts/check_health_leads.py``: page when
  ``pending_review_count`` stays above 5 for more than 30 minutes (re-paged
  hourly), and when ``stuck_contacted_count`` rises and stays up for more than
  30 minutes (once per climb). State lives in Redis (``pipeline_watch:state``).
* Each run is wrapped in a Sentry Crons check-in. A missed check-in is what
  catches the case nothing in the worker can report: the worker, or its
  scheduler, being dead.
"""

import json
import time
from datetime import datetime, timedelta, timezone

from app.core.constants import LeadStatus, WorkerConstants
from app.core.database import leads_collection
from app.core.logger import logger
from app.core.redis_client import get_redis_client
from app.core.sentry import cron_checkin
from app.services.notification_service import page_operator

PENDING_THRESHOLD = 5
SUSTAINED = timedelta(minutes=30)
REPAGE_EVERY = timedelta(minutes=60)
STATE_KEY = "pipeline_watch:state"
STATE_TTL_SECONDS = 7 * 24 * 3600


async def lead_pipeline_counts(now: datetime | None = None) -> tuple[int, int]:
    """(pending_review_count, stuck_contacted_count) — what /health/leads reports."""
    now = now or datetime.now(timezone.utc)
    stuck_threshold = now - timedelta(
        hours=WorkerConstants.UNASSIGNED_LEAD_TIMEOUT_HOURS
    )
    pending = await leads_collection.count_documents(
        {"status": LeadStatus.PENDING_ADMIN_REVIEW}
    )
    stuck = await leads_collection.count_documents(
        {"status": LeadStatus.CONTACTED, "created_at": {"$lt": stuck_threshold}}
    )
    return pending, stuck


def _since(state: dict, key: str, now: datetime) -> timedelta:
    return now - datetime.fromisoformat(state.setdefault(key, now.isoformat()))


def evaluate(
    state: dict, counts: tuple[int, int], now: datetime
) -> tuple[dict, list[str]]:
    """Return (new state, page messages to send now). Pure, so it is tested alone."""
    state = dict(state)
    pages = []
    pending, stuck = counts

    if pending > PENDING_THRESHOLD:
        if _since(state, "pending_since", now) > SUSTAINED:
            last = state.get("pending_paged_at")
            if last is None or now - datetime.fromisoformat(last) >= REPAGE_EVERY:
                pages.append(
                    f"pending_review_count={pending} has been above "
                    f"{PENDING_THRESHOLD} for more than 30 min: the admin queue is "
                    "not being worked, or the Healer is looping."
                )
                state["pending_paged_at"] = now.isoformat()
    else:
        state.pop("pending_since", None)
        state.pop("pending_paged_at", None)

    baseline = state.get("stuck_baseline")
    if baseline is None or stuck <= baseline:
        state["stuck_baseline"] = stuck
        state.pop("stuck_rise_since", None)
    elif _since(state, "stuck_rise_since", now) > SUSTAINED:
        pages.append(
            f"stuck_contacted_count rose from {baseline} to {stuck} and has not "
            "come back for more than 30 min: the Healer is off or silently failing."
        )
        # One page per climb: only a further rise pages again.
        state["stuck_baseline"] = stuck
        state.pop("stuck_rise_since", None)
    return state, pages


async def _load_state(redis) -> dict:
    raw = await redis.get(STATE_KEY)
    return json.loads(raw) if raw else {}


async def run_pipeline_watch() -> None:
    """One watch tick, wrapped in a Sentry Crons check-in.

    ``ok`` when the counts were read and judged (a page is the watch working,
    not failing); ``error`` when they could not be read, which Sentry turns into
    an issue on its own.
    """
    started = time.monotonic()
    check_in_id = cron_checkin("in_progress")
    status = "error"
    try:
        now = datetime.now(timezone.utc)
        counts = await lead_pipeline_counts(now)
        redis = await get_redis_client()
        state, pages = evaluate(await _load_state(redis), counts, now)
        await redis.set(STATE_KEY, json.dumps(state), ex=STATE_TTL_SECONDS)
        for message in pages:
            page_operator(f"Lead pipeline: {message}")
        logger.info(
            f"[Pipeline watch] pending_review={counts[0]} stuck_contacted={counts[1]} "
            f"pages={len(pages)}"
        )
        status = "ok"
    except Exception as e:
        logger.error(f"[Pipeline watch] could not read the lead pipeline: {e}")
        raise
    finally:
        cron_checkin(status, check_in_id, duration=time.monotonic() - started)
