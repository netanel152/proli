"""Audit-log reads and writes for the admin panel — streamlit-free (PRO-142).

There used to be two audit implementations. The live one was
``admin_panel/core/auth.py:_log_audit_sync``, a pymongo insert with no
tests, driving 15 call sites plus login/logout. The tested one was
``app/services/audit_service.py``, a complete async implementation that
**production never called** — its only references were its own test file
and one ``conftest`` patch block. So the code with tests was dead and the
code carrying the accountability trail was unverified.

This module is the survivor, and it is the *sync* one on purpose.
Streamlit runs synchronously: driving the async service from the panel
would mean ``asyncio.run(...)`` at every call site, and because motor binds
a client to the event loop that created it, a fresh loop per call is the
classic "attached to a different loop" failure — an audit write that works
for one admin and dies for the second. Extracting sync logic behind an
injected collection is also what PRO-140 (``analytics_queries``), PRO-158
(``schedule_queries``) and PRO-161 (``lead_queries``) each settled on.

The collection is a parameter so tests can pass mongomock.
"""

import re
from datetime import datetime, timedelta, timezone

import pytz

from app.core.logger import logger

_IL_TZ = pytz.timezone("Asia/Jerusalem")

# Page sizes offered by the viewer. The old viewer had no pagination at all
# — a hardcoded `.limit(200)` — so on a busy panel the audit trail simply
# stopped existing beyond the most recent 200 actions, silently.
PAGE_SIZE_OPTIONS = (25, 50, 100, 200)
DEFAULT_PAGE_SIZE = 50

# `details` keys the write sites use to name the thing an action was about.
# Kept as data so the subject filter and the call sites can be checked
# against each other instead of drifting silently.
SUBJECT_DETAIL_KEYS = ("lead_id", "pro_id", "chat_id", "target", "name")


def write_audit_entry(audit_col, username, action, details=None):
    """Insert one audit entry. Returns True when the write landed.

    **Fail-open, and that is the whole contract.** This is called from
    inside admin actions that have already happened — a lead was deleted, a
    pro approved, a role changed. Raising here would turn "we failed to
    record what you did" into "your action appears to have failed", which
    is strictly worse: the operator would retry a destructive action that
    already succeeded. The failure goes to the log (and Sentry, via the
    ERROR bridge) instead.

    Timestamps are timezone-aware UTC. The old sync writer used
    ``datetime.utcnow()`` — naive, and deprecated in 3.12 — while the async
    implementation it duplicated wrote aware UTC; consolidating to one
    implementation means consolidating to one convention.

    Note this changes what is *written*, not what is read: BSON stores an
    instant with no zone, and pymongo hands it back **naive** unless the
    client sets ``tz_aware=True`` (ours does not). See
    ``format_audit_rows``.
    """
    entry = {
        "admin_user": username,
        "action": action,
        "details": details or {},
        "timestamp": datetime.now(timezone.utc),
    }
    try:
        audit_col.insert_one(entry)
        return True
    except Exception as e:
        # The entry is lost, so this line is the only remaining trace —
        # carry the actor and the exception *type* (a DuplicateKeyError and
        # an AutoReconnect are different operational stories). `details` is
        # deliberately not interpolated: it carries lead and pro ids.
        logger.error(
            f"Audit log write failed for {username!r} action {action!r}: {e!r}"
        )
        return False


def build_audit_filter(user=None, action=None, since=None, until=None, subject=None):
    """Build the Mongo query for the viewer's filters.

    Pure, so the query shape is testable without a database. ``user`` and
    ``action`` are case-insensitive substring matches — an operator looking
    for "delete" should not have to know whether the action is
    ``delete_lead`` or ``delete_admin``. Blank strings are treated as
    absent, so an empty filter box does not become a filter matching
    nothing.

    ``since``/``until`` are dates; ``until`` is made inclusive by advancing
    to the end of that day, because a filter that silently excludes today's
    entries is the kind of thing an operator discovers only after
    concluding an action was never recorded.
    """
    query = {}

    user = (user or "").strip()
    if user:
        query["admin_user"] = {"$regex": _escape_regex(user), "$options": "i"}

    action = (action or "").strip()
    if action:
        query["action"] = {"$regex": _escape_regex(action), "$options": "i"}

    subject = (subject or "").strip()
    if subject:
        # The thing an audit question is usually *about* — "who deleted this
        # lead" — is the id in `details`, not the user or the action. Every
        # call site stamps it under one of these keys as a string, so one
        # regex across them answers the question the other filters cannot.
        rx = {"$regex": _escape_regex(subject), "$options": "i"}
        query["$or"] = [{f"details.{key}": rx} for key in SUBJECT_DETAIL_KEYS]

    window = {}
    if since is not None:
        window["$gte"] = _day_start_utc(since)
    if until is not None:
        window["$lt"] = _day_end_utc(until)
    if window:
        query["timestamp"] = window

    return query


def count_audit_entries(audit_col, query=None):
    """Total entries matching ``query`` — the denominator for pagination."""
    return audit_col.count_documents(query or {})


def fetch_audit_page(audit_col, limit=DEFAULT_PAGE_SIZE, skip=0, query=None):
    """One page of entries, newest first.

    A non-positive ``limit`` falls back to the default rather than being
    passed through: pymongo reads ``.limit(0)`` as *no limit*, so the one
    value a caller might reasonably use to mean "nothing" would instead
    load the entire collection into a DataFrame.
    """
    if limit <= 0:
        limit = DEFAULT_PAGE_SIZE
    cursor = (
        audit_col.find(query or {})
        # `_id` breaks ties, and the tie-break is not decoration: a single
        # operator action often writes several entries in the same
        # millisecond, and logins cluster. Sorting on `timestamp` alone
        # gives Mongo no defined order within a tie, and `skip`/`limit`
        # runs as a *separate query per page* — so an entry could appear on
        # both page 2 and page 3, or on neither. "Did my change get
        # recorded?" would then be answerable wrongly, which is the one
        # thing an audit log may not do.
        .sort([("timestamp", -1), ("_id", -1)])
        .skip(max(0, skip))
        .limit(limit)
    )
    return list(cursor)


def page_count(total, page_size):
    """How many pages ``total`` entries fill. Always at least 1.

    An empty result still has one (empty) page — returning 0 would make the
    page selector's range empty and crash the widget that renders it.
    """
    if page_size <= 0:
        page_size = DEFAULT_PAGE_SIZE
    return max(1, -(-total // page_size))  # ceil division


def clamp_page(page, total, page_size):
    """Keep the requested page inside the range the data actually has.

    The filter widgets and the page selector are independent, so filtering
    down to 3 results while sitting on page 7 is a normal sequence of
    clicks, not misuse — and an unclamped skip would answer it with an
    empty table that looks like "nothing was ever logged".
    """
    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 1
    return max(1, min(page, page_count(total, page_size)))


def format_audit_rows(entries, tz=None):
    """Render entries into the viewer's display rows.

    Lifted out of the view so the timestamp normalisation is testable.

    **Do not "simplify" the naive branch away.** It is not a legacy path
    for pre-PRO-142 rows: BSON stores no timezone and pymongo returns naive
    datetimes unless the client is built with ``tz_aware=True``, which this
    panel's client is not — so *every* row read from Mongo arrives naive,
    however it was written. Dropping that branch would make
    ``astimezone`` treat the value as the server's local time and shift the
    whole audit trail by the UTC offset, which reads as plausible times
    rather than as an error. The aware branch covers callers that hand in
    aware values directly (tests, or a future ``tz_aware`` client).
    """
    tz = tz or pytz.timezone("Asia/Jerusalem")
    rows = []
    for entry in entries:
        ts = entry.get("timestamp", "")
        if isinstance(ts, datetime):
            if ts.tzinfo is None:
                ts = pytz.utc.localize(ts)
            ts = ts.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")
        details = entry.get("details") or {}
        rows.append(
            {
                "time": ts,
                "user": entry.get("admin_user", "?"),
                "action": entry.get("action", "?"),
                "details": ", ".join(
                    f"{k}={_format_detail_value(v)}" for k, v in details.items()
                ),
            }
        )
    return rows


def _format_detail_value(value):
    """Render one detail value for the operator-facing column.

    Lists are joined rather than shown as a Python repr — ``edit_lead``
    stamps ``fields=['status', 'notes']``, and quotes and brackets in a
    table cell read as data corruption rather than as a list.
    """
    if isinstance(value, (list, tuple)):
        return "/".join(str(v) for v in value)
    if isinstance(value, dict):
        # `edit_lead` stamps the whole change payload as a dict
        # (`lead_queries`), which as a repr fills the cell with braces and
        # quotes and buries the one thing being read: what changed.
        return " ".join(f"{k}={_format_detail_value(v)}" for k, v in value.items())
    return str(value)


def _escape_regex(value):
    """Escape a user-typed filter so it matches literally.

    Without this an operator typing ``(`` gets a PyMongo regex error
    instead of no results, and ``.*`` would silently match everything.
    """
    return re.escape(value)


def _day_start_utc(day, tz=None):
    """Start of the operator's *local* day, expressed as UTC.

    Deliberately not UTC midnight. The table renders every timestamp in
    Israel time, so the operator picks dates against an Israel clock; a
    UTC cut would answer "From the 30th to the 30th" with Israel
    03:00→03:00, hiding anything logged in the first three hours of that
    day and quietly including three hours of the next. An empty result
    from that reads as "the action was never recorded", which is the one
    conclusion an audit log must never invite by accident.

    ``tz`` exists for tests. Israel transitions at 02:00, so local midnight
    is never ambiguous or non-existent and pytz's default ``is_dst=False``
    is safe here; a zone that transitions *at* midnight would need that
    spelled out.
    """
    tz = tz or _IL_TZ
    return tz.localize(datetime(day.year, day.month, day.day)).astimezone(timezone.utc)


def _day_end_utc(day, tz=None):
    """Exclusive upper bound: the start of the operator's next local day.

    Computed by advancing the *local* day and converting, not by adding 24
    hours, so a DST transition yields a correct 23- or 25-hour window.
    """
    return _day_start_utc(day + timedelta(days=1), tz)
