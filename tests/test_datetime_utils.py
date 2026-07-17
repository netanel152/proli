"""Unit tests for app.core.datetime_utils.parse_iso_to_utc (PRO-9).

Pure function — no DB/mocks required.
"""

import pytest
from datetime import datetime, timezone

from app.core.datetime_utils import parse_iso_to_utc


def test_parse_iso_to_utc_with_explicit_offset_converts_to_correct_utc_instant():
    result = parse_iso_to_utc("2026-07-18T10:00:00+03:00")

    assert result == datetime(2026, 7, 18, 7, 0, 0, tzinfo=timezone.utc)
    assert result.tzinfo == timezone.utc


def test_parse_iso_to_utc_naive_string_interpreted_as_israel_local_time():
    # Israel is UTC+3 in July (Daylight Saving Time / summer).
    result = parse_iso_to_utc("2026-07-18T10:00:00")

    assert result == datetime(2026, 7, 18, 7, 0, 0, tzinfo=timezone.utc)
    assert result.tzinfo == timezone.utc


def test_parse_iso_to_utc_z_suffix_is_honored_as_utc():
    result = parse_iso_to_utc("2026-07-18T10:00:00Z")

    assert result == datetime(2026, 7, 18, 10, 0, 0, tzinfo=timezone.utc)
    assert result.tzinfo == timezone.utc


@pytest.mark.parametrize(
    "value",
    [None, "", "   ", "בהקדם"],
    ids=["none", "empty_string", "whitespace_only", "hebrew_junk"],
)
def test_parse_iso_to_utc_returns_none_for_empty_or_unparseable_input(value):
    assert parse_iso_to_utc(value) is None
