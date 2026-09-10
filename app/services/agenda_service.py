"""PRO-147 — pure formatting for the pro's job lists and the daily agenda.

Every pro-facing job list (``עבודות``, ``פרטים``, the ``סיימתי`` / ``ביטול``
selection prompts and the 08:00 agenda) renders through here so the four of
them cannot disagree about three things:

1. **Order** — by when the work happens (``appointment_datetime`` ascending),
   not by when the lead was created. Leads with no resolved datetime (an
   open-ended "בהקדם" request) come last, under their own day header, so an
   ASAP job never buries tomorrow morning's.
2. **Time rendering** — absolute Israel time ("היום 14:30", "מחר 09:00",
   "ראשון 3.9 11:00") from ``appointment_datetime``. The customer's free-text
   ``appointment_time`` ("מחר בבוקר") is shown only when no datetime was
   resolved: it reads the same three days later, which is how a job got lost.
3. **Pagination** — a list longer than ``PRO_LIST_PAGE_SIZE`` says how many
   rows it is showing out of how many and offers ``עוד`` for the rest. Rows
   are numbered across the whole list, so a reply of "12" is valid before the
   second page has even been shown.

No database access here — callers fetch, this module sorts and renders.
"""

from datetime import date, datetime, timezone
from typing import Callable, Iterable, List, Optional, Tuple

import pytz

from app.core.constants import WorkerConstants
from app.core.messages import Messages

IL_TZ = pytz.timezone("Asia/Jerusalem")

# Row renderer: (global 1-based row number, lead document, rendered time) → text.
RowRenderer = Callable[[int, dict, str], str]


def now_israel(now: Optional[datetime] = None) -> datetime:
    """The current wall-clock time in Israel. ``now`` is injectable for tests
    and for callers that already computed it once per request."""
    return (now or datetime.now(timezone.utc)).astimezone(IL_TZ)


def to_israel(value) -> Optional[datetime]:
    """``appointment_datetime`` as an aware Israel-local datetime, or ``None``
    when the lead carries no usable datetime.

    Mongo hands back naive datetimes (the driver is not ``tz_aware``) that were
    stored as UTC — the same assumption ``matching_service.book_slot_for_lead``
    and the scheduler already make. Anything that is not a ``datetime`` (a
    stray string, ``None``) is treated as "not resolved".
    """
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(IL_TZ)


def _day_label(day: date) -> Tuple[str, str]:
    """(weekday name, ``d.m``) for a date that is neither today nor tomorrow."""
    weekday = Messages.Pro.WEEKDAY_NAMES[day.weekday()]
    return weekday, f"{day.day}.{day.month}"


def format_appointment(lead: dict, now_il: datetime) -> str:
    """Absolute Israel-time string for a lead's appointment, e.g. ``היום 14:30``.

    Falls back to the raw ``appointment_time`` text (or ``Fallbacks.TIME_UNSET``)
    only when no ``appointment_datetime`` was resolved.
    """
    appt = to_israel(lead.get("appointment_datetime"))
    if appt is None:
        return lead.get("appointment_time") or Messages.Fallbacks.TIME_UNSET

    hhmm = appt.strftime("%H:%M")
    today = now_il.date()
    delta = (appt.date() - today).days
    if delta == 0:
        return Messages.Pro.TIME_TODAY.format(time=hhmm)
    if delta == 1:
        return Messages.Pro.TIME_TOMORROW.format(time=hhmm)
    weekday, day = _day_label(appt.date())
    return Messages.Pro.TIME_DATE.format(weekday=weekday, date=day, time=hhmm)


def day_header(lead: dict, now_il: datetime) -> str:
    """The bold day header a lead's row is grouped under."""
    appt = to_israel(lead.get("appointment_datetime"))
    if appt is None:
        return Messages.Pro.DAY_HEADER_UNSCHEDULED
    today = now_il.date()
    delta = (appt.date() - today).days
    if delta == 0:
        return Messages.Pro.DAY_HEADER_TODAY
    if delta == 1:
        return Messages.Pro.DAY_HEADER_TOMORROW
    weekday, day = _day_label(appt.date())
    return Messages.Pro.DAY_HEADER_DATE.format(weekday=weekday, date=day)


def sort_by_appointment(leads: Iterable[dict]) -> List[dict]:
    """Chronological by ``appointment_datetime``; leads without one last.

    Stable, so callers that pre-sort (e.g. newest-created first) keep that
    order inside the unscheduled group.
    """
    far_future = datetime.max.replace(tzinfo=timezone.utc)

    def key(lead: dict):
        appt = to_israel(lead.get("appointment_datetime"))
        return (appt is None, appt or far_future)

    return sorted(leads, key=key)


def render_page(
    leads: List[dict],
    row: RowRenderer,
    *,
    now_il: datetime,
    offset: int = 0,
    total: Optional[int] = None,
    page_size: int = WorkerConstants.PRO_LIST_PAGE_SIZE,
) -> Tuple[List[str], Optional[int]]:
    """Render one page of an already-sorted list.

    Returns ``(lines, next_offset)`` — ``next_offset`` is ``None`` on the last
    page. ``lines`` carries a day header whenever the day changes (always for
    the first row of the page, so a continuation page is self-describing),
    the numbered rows, and a "shown X–Y of Z" footer whenever the list does
    not fit in one page. ``total`` defaults to ``len(leads)``; pass the real
    collection count when ``leads`` was fetched with a cap.
    """
    total = len(leads) if total is None else max(total, len(leads))
    offset = max(0, min(offset, len(leads)))
    end_index = offset + page_size
    page = leads[offset:end_index]

    lines: List[str] = []
    current_header = None
    for i, lead in enumerate(page):
        header = day_header(lead, now_il)
        if header != current_header:
            lines.append(header)
            current_header = header
        lines.append(row(offset + i + 1, lead, format_appointment(lead, now_il)))

    end = offset + len(page)
    next_offset = end if end < total else None
    if next_offset is not None:
        lines.append(
            Messages.Pro.LIST_PAGE_MORE.format(first=offset + 1, last=end, total=total)
        )
    elif offset > 0:
        lines.append(
            Messages.Pro.LIST_PAGE_END.format(first=offset + 1, last=end, total=total)
        )
    return lines, next_offset
