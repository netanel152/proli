"""Helpers for turning model-produced date/time strings into storable datetimes.

The AI extracts a free-text ``appointment_time`` (what the customer literally
said) plus a resolved ``appointment_datetime`` in ISO 8601. This module converts
that ISO string into a timezone-aware UTC ``datetime`` for storage, failing soft
(returning ``None``) on anything it can't parse so a bad model output never
breaks lead creation.
"""

from datetime import datetime, timezone
from typing import Optional

import pytz

from app.core.logger import logger

# A naive datetime from the model is interpreted as Israel local time, because
# the prompt gives the model the current time in Israel as its anchor.
_IL_TZ = pytz.timezone("Asia/Jerusalem")


# PRO-73: customer-facing "cold" re-engagement jobs (SOS healer, lead janitor,
# SLA deflection, the PRO-56 reassignment offer) must never message a customer
# outside daytime hours — a 3am ping is both a WhatsApp spam signal and bad UX.
BUSINESS_HOURS_START = 8
BUSINESS_HOURS_END = 21  # exclusive (last contact hour is 20:xx)


def within_business_hours(now: Optional[datetime] = None) -> bool:
    """True if the current Israel-local time is within customer-contact hours
    (08:00–21:00). Gates cold customer-facing scheduler jobs (PRO-73)."""
    now_il = (now or datetime.now(timezone.utc)).astimezone(_IL_TZ)
    return BUSINESS_HOURS_START <= now_il.hour < BUSINESS_HOURS_END


def parse_iso_to_utc(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO 8601 string into a timezone-aware UTC datetime.

    Returns ``None`` for empty, non-string, or unparseable input so callers can
    persist the result unconditionally (a missing appointment_datetime is a
    valid state, not an error).
    """
    if not value or not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    # datetime.fromisoformat accepts a trailing 'Z' only on 3.11+; normalise it
    # so behaviour is identical across interpreters.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        logger.debug(f"parse_iso_to_utc: could not parse {value!r}")
        return None
    if dt.tzinfo is None:
        # is_dst=False (pytz default): a wall-clock time inside the spring-forward
        # gap resolves an hour off rather than raising. Once-a-year, one-hour edge
        # for a scheduling hint — acceptable, and never a crash.
        dt = _IL_TZ.localize(dt)
    return dt.astimezone(timezone.utc)
