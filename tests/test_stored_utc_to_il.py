"""Naive datetimes read back from Mongo are UTC, whatever the process's TZ.

The Mongo client is not ``tz_aware``, so a slot's ``start_time`` comes back
naive. Every Railway service runs with ``TZ`` set to Israel time (its log
timestamps carry ``+02:00``), and a bare ``.astimezone(IL_TZ)`` on a naive
value reads it as *local* time — so the reschedule list and its confirmation
showed every slot two or three hours early in staging and production, while
the suite, running in UTC, saw the right hour. These tests run under an
Israel ``TZ`` to pin that.
"""

import os
import time
from datetime import datetime, timezone

import pytest

from app.core.datetime_utils import IL_TZ, stored_utc_to_il


@pytest.fixture
def israel_process_tz():
    saved = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Jerusalem"
    time.tzset()
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = saved
        time.tzset()


def test_a_naive_value_is_read_as_utc_under_an_israel_process_tz(israel_process_tz):
    stored = datetime(2026, 11, 22, 8, 0)  # 08:00 UTC, as Mongo returns it
    # The trap this helper exists for: the bare call gets it wrong here.
    assert stored.astimezone(IL_TZ).strftime("%H:%M") == "08:00"
    assert stored_utc_to_il(stored).strftime("%H:%M") == "10:00"


def test_summer_time_is_three_hours_ahead(israel_process_tz):
    assert stored_utc_to_il(datetime(2026, 7, 1, 8, 0)).strftime("%H:%M") == "11:00"


def test_an_aware_value_is_converted_not_relabelled(israel_process_tz):
    aware = datetime(2026, 11, 22, 8, 0, tzinfo=timezone.utc)
    assert stored_utc_to_il(aware).strftime("%H:%M") == "10:00"
    assert stored_utc_to_il(aware.astimezone(IL_TZ)).strftime("%H:%M") == "10:00"


def test_the_reschedule_call_sites_use_it():
    """Both reschedule renderings and the panel's lead date go through the helper."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for rel, needle in (
        (
            "app/services/customer_flow.py",
            'stored_utc_to_il(chosen_slot["start_time"])',
        ),
        ("app/services/dispatch_guards.py", 'stored_utc_to_il(slot["start_time"])'),
        ("admin_panel/core/lead_queries.py", 'stored_utc_to_il(lead["created_at"])'),
    ):
        with open(os.path.join(root, rel), encoding="utf8") as fh:
            assert needle in fh.read(), rel
