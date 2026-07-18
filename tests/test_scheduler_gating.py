"""PRO-73 — business-hours + toggle gating for cold customer-facing jobs.

Covers the two pure gating primitives directly (no scheduler-lock / Redis
plumbing): the Israel-time business-hours window and the per-job allow gate,
which together stop the SOS healer / lead janitor / SLA deflection from
messaging a customer at 3am or before an operator warms up the number.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytz

import app.scheduler as sched
from app.core.datetime_utils import within_business_hours

_IL = pytz.timezone("Asia/Jerusalem")


def _il_utc(hour, minute=0):
    """A UTC-aware datetime for the given Israel local wall-clock time."""
    return _IL.localize(datetime(2026, 7, 20, hour, minute)).astimezone(timezone.utc)


def test_within_business_hours_daytime_true():
    assert within_business_hours(_il_utc(10, 0)) is True


def test_within_business_hours_night_false():
    assert within_business_hours(_il_utc(3, 0)) is False


def test_within_business_hours_boundaries():
    assert within_business_hours(_il_utc(8, 0)) is True  # 08:00 — in
    assert within_business_hours(_il_utc(20, 59)) is True  # last contact minute
    assert within_business_hours(_il_utc(21, 0)) is False  # 21:00 — out


def _mock_settings(config):
    m = MagicMock()
    m.find_one = AsyncMock(return_value=config)
    return m


@pytest.mark.asyncio
async def test_cold_job_allowed_only_in_hours_and_toggled_on(monkeypatch):
    monkeypatch.setattr(sched, "within_business_hours", lambda *a, **k: True)

    # in hours + toggle explicitly on → allowed
    monkeypatch.setattr(
        sched, "settings_collection", _mock_settings({"sos_healer_active": True})
    )
    assert await sched._customer_cold_job_allowed("sos_healer_active") is True

    # toggle explicitly off → blocked
    monkeypatch.setattr(
        sched, "settings_collection", _mock_settings({"sos_healer_active": False})
    )
    assert await sched._customer_cold_job_allowed("sos_healer_active") is False

    # toggle absent → default OFF (pilot safety)
    monkeypatch.setattr(sched, "settings_collection", _mock_settings({}))
    assert await sched._customer_cold_job_allowed("lead_janitor_active") is False

    # no config doc at all → blocked
    monkeypatch.setattr(sched, "settings_collection", _mock_settings(None))
    assert await sched._customer_cold_job_allowed("sla_monitor_active") is False


@pytest.mark.asyncio
async def test_cold_job_blocked_outside_hours_even_when_toggled_on(monkeypatch):
    monkeypatch.setattr(sched, "within_business_hours", lambda *a, **k: False)
    monkeypatch.setattr(
        sched, "settings_collection", _mock_settings({"sos_healer_active": True})
    )
    assert await sched._customer_cold_job_allowed("sos_healer_active") is False
