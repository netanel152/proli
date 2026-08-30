"""Tests for admin_panel/core/audit_queries.py — PRO-142.

There used to be two audit implementations: the live one
(``admin_panel/core/auth.py:_log_audit_sync``, pymongo, 15 call sites, no
tests) and a dead async one (``app/services/audit_service.py``, motor, with
tests, called by nothing). The dead one and its test file have been deleted;
``audit_queries.py`` is the survivor — the live path, now extracted behind
an injected collection (same seam as ``lead_queries``/``schedule_queries``/
``analytics_queries``) so it can finally be tested directly.

Deliberately does not import ``admin_panel.core.auth``: that module builds a
real ``MongoClient`` and imports streamlit at module scope. Testing
``audit_queries`` directly is the entire point of the extraction.
"""

from datetime import date, datetime, timedelta, timezone

import mongomock
import pytest
import pytz

from admin_panel.core.audit_queries import (
    DEFAULT_PAGE_SIZE,
    SUBJECT_DETAIL_KEYS,
    build_audit_filter,
    clamp_page,
    count_audit_entries,
    fetch_audit_page,
    format_audit_rows,
    page_count,
    write_audit_entry,
)


@pytest.fixture
def db():
    return mongomock.MongoClient()["proli_test"]


# --- write_audit_entry ---


class _SpyCollection:
    """Wraps a real (mongomock) collection to capture the exact dict handed
    to insert_one before it goes through the driver — mongomock strips
    tzinfo off datetimes on round-trip (matching real pymongo's default
    tz-naive read behavior), so the only way to check the *produced* value
    is aware UTC is to intercept it before storage, not after."""

    def __init__(self, real_col):
        self._real = real_col
        self.last_insert = None

    def insert_one(self, doc):
        self.last_insert = dict(doc)
        return self._real.insert_one(doc)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_write_audit_entry_stores_all_fields_with_aware_utc_timestamp(db):
    spy = _SpyCollection(db.audit_log)

    ok = write_audit_entry(spy, "admin1", "delete_lead", {"lead_id": "abc"})

    assert ok is True
    assert spy.last_insert["admin_user"] == "admin1"
    assert spy.last_insert["action"] == "delete_lead"
    assert spy.last_insert["details"] == {"lead_id": "abc"}
    ts = spy.last_insert["timestamp"]
    assert ts.tzinfo is not None
    assert ts.utcoffset() == timedelta(0)

    # Round-trip: the same four keys are what actually landed in the
    # collection (tzinfo itself is not asserted here — mongomock, like
    # pymongo by default, returns naive datetimes on read).
    doc = db.audit_log.find_one({})
    assert set(doc.keys()) >= {"admin_user", "action", "details", "timestamp"}


def test_write_audit_entry_no_details_stores_empty_dict(db):
    write_audit_entry(db.audit_log, "admin1", "login")

    doc = db.audit_log.find_one({})
    assert doc["details"] == {}


class _RaisingCollection:
    def insert_one(self, doc):
        raise RuntimeError("connection reset")


def test_write_audit_entry_fail_open_returns_false_without_raising():
    result = write_audit_entry(_RaisingCollection(), "admin1", "delete_lead")

    assert result is False


# --- build_audit_filter ---


def _entry(user, action, ts):
    return {"admin_user": user, "action": action, "details": {}, "timestamp": ts}


@pytest.mark.parametrize(
    "user, action, expected",
    [
        (None, None, {}),
        ("   ", "", {}),
        ("dana", None, {"admin_user": {"$regex": "dana", "$options": "i"}}),
        (None, "delete_lead", {"action": {"$regex": "delete_lead", "$options": "i"}}),
        (
            "dana",
            "delete_lead",
            {
                "admin_user": {"$regex": "dana", "$options": "i"},
                "action": {"$regex": "delete_lead", "$options": "i"},
            },
        ),
    ],
    ids=["no_args", "blank_strings", "user_only", "action_only", "both"],
)
def test_build_audit_filter_user_and_action(user, action, expected):
    assert build_audit_filter(user=user, action=action) == expected


def test_build_audit_filter_escapes_regex_metacharacters():
    query = build_audit_filter(user="a(b.*c")

    # The raw metacharacters must not reach the $regex value unescaped —
    # otherwise pymongo raises on '(' and '.*' would match everything.
    assert query["admin_user"]["$regex"] == r"a\(b\.\*c"


@pytest.mark.parametrize(
    "day, expected_gte, expected_lt",
    [
        (
            date(2026, 8, 30),  # IDT, UTC+3
            datetime(2026, 8, 29, 21, 0, tzinfo=timezone.utc),
            datetime(2026, 8, 30, 21, 0, tzinfo=timezone.utc),
        ),
        (
            date(2026, 1, 15),  # IST, UTC+2 -- pins the offset isn't hardcoded to +3
            datetime(2026, 1, 14, 22, 0, tzinfo=timezone.utc),
            datetime(2026, 1, 15, 22, 0, tzinfo=timezone.utc),
        ),
    ],
    ids=["summer_idt", "winter_ist"],
)
def test_build_audit_filter_date_window_uses_israel_local_day_boundaries(
    day, expected_gte, expected_lt
):
    # The viewer renders every timestamp in Israel time, so a filter built
    # from UTC midnight would answer "the 30th" with Israel 03:00->03:00 --
    # hiding the first three hours of the day and leaking three hours of
    # the next. Boundaries must be the *local* day, converted to UTC.
    query = build_audit_filter(since=day, until=day)

    window = query["timestamp"]
    assert window["$gte"] == expected_gte
    assert window["$lt"] == expected_lt
    assert window["$gte"].tzinfo is not None
    assert window["$lt"].tzinfo is not None


def test_build_audit_filter_date_window_includes_early_morning_israel_time_entry():
    # The regression itself: an entry at 01:00 Israel time on the filtered
    # day used to fall *before* the UTC-midnight $gte and was silently
    # excluded. Build the instant independently of the code under test.
    day = date(2026, 8, 30)
    instant = (
        pytz.timezone("Asia/Jerusalem")
        .localize(datetime(2026, 8, 30, 1, 0))
        .astimezone(timezone.utc)
    )

    query = build_audit_filter(since=day, until=day)

    window = query["timestamp"]
    assert window["$gte"] <= instant < window["$lt"]


@pytest.mark.parametrize(
    "day, expected_hours",
    [
        (date(2026, 10, 25), 25),  # fall-back: local day gains an hour
        (date(2026, 3, 27), 23),  # spring-forward: local day loses an hour
    ],
    ids=["fall_back_25h", "spring_forward_23h"],
)
def test_build_audit_filter_date_window_dst_transition_day_has_correct_length(
    day, expected_hours
):
    # Advancing the *local* day (not adding a flat 24h to the UTC value)
    # must produce the real 23- or 25-hour local day -- this is what would
    # break if someone "simplified" _day_end_utc back to
    # `_day_start_utc(day) + timedelta(days=1)`. Spring-forward is the
    # easier direction to get wrong, so both are pinned.
    query = build_audit_filter(since=day, until=day)

    window = query["timestamp"]
    assert window["$lt"] - window["$gte"] == timedelta(hours=expected_hours)


def test_build_audit_filter_subject_produces_or_clause_per_subject_detail_key():
    # Built from the exported constant, not a retyped list -- so a future
    # key added to SUBJECT_DETAIL_KEYS is automatically covered here rather
    # than silently going untested. "leadabc" has no regex metacharacters,
    # so this pins the $or *shape* only -- escaping is covered separately
    # by test_build_audit_filter_subject_escapes_regex_metacharacters.
    query = build_audit_filter(subject="leadabc")

    expected = [
        {f"details.{key}": {"$regex": "leadabc", "$options": "i"}}
        for key in SUBJECT_DETAIL_KEYS
    ]
    assert query["$or"] == expected


def test_build_audit_filter_blank_subject_adds_no_or_clause():
    assert build_audit_filter(subject="   ") == {}


def test_build_audit_filter_subject_escapes_regex_metacharacters():
    query = build_audit_filter(subject="a(b")

    for clause in query["$or"]:
        (rx,) = clause.values()
        assert rx["$regex"] == r"a\(b"


def test_build_audit_filter_subject_and_action_narrow_together_not_widen(db):
    # Key-presence alone cannot tell "AND" from "OR that happens to also
    # have an action key" -- both would satisfy `"action" in query and
    # "$or" in query`. Prove it end-to-end: two entries share the same
    # subject but only one has the filtered action: filtering by both must
    # return exactly that one, not both.
    ts = datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc)
    db.audit_log.insert_one(
        _entry("admin1", "delete_lead", ts) | {"details": {"lead_id": "leadabc"}}
    )
    db.audit_log.insert_one(
        _entry("admin1", "edit_lead", ts) | {"details": {"lead_id": "leadabc"}}
    )

    query = build_audit_filter(subject="leadabc", action="delete_lead")
    page = fetch_audit_page(db.audit_log, query=query)

    assert [e["action"] for e in page] == ["delete_lead"]


def test_build_audit_filter_subject_matches_details_dotted_path_end_to_end(db):
    # The one check that proves `details.<key>` is the right dotted path --
    # a typo there would leave every assertion above green while the filter
    # silently matched nothing against a real collection.
    ts = datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc)
    db.audit_log.insert_one(
        _entry("admin1", "delete_lead", ts) | {"details": {"lead_id": "lead-abc"}}
    )
    db.audit_log.insert_one(
        _entry("admin1", "approve_pro", ts) | {"details": {"pro_id": "pro-xyz"}}
    )
    db.audit_log.insert_one(_entry("admin1", "login", ts))

    query = build_audit_filter(subject="lead-abc")
    page = fetch_audit_page(db.audit_log, query=query)

    assert [e["action"] for e in page] == ["delete_lead"]


# --- fetch_audit_page / count_audit_entries ---


def test_fetch_audit_page_newest_first_respects_limit_and_skip(db):
    base = datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc)
    for i in range(5):
        db.audit_log.insert_one(
            _entry("admin1", f"action_{i}", base + timedelta(minutes=i))
        )

    page1 = fetch_audit_page(db.audit_log, limit=2, skip=0)
    page2 = fetch_audit_page(db.audit_log, limit=2, skip=2)

    assert [e["action"] for e in page1] == ["action_4", "action_3"]
    assert [e["action"] for e in page2] == ["action_2", "action_1"]


def test_fetch_audit_page_filters_by_query(db):
    base = datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc)
    db.audit_log.insert_one(_entry("admin1", "delete_lead", base))
    db.audit_log.insert_one(_entry("admin2", "login", base + timedelta(minutes=1)))

    query = {"admin_user": "admin2"}

    assert count_audit_entries(db.audit_log, query) == 1
    page = fetch_audit_page(db.audit_log, query=query)
    assert [e["action"] for e in page] == ["login"]


def test_fetch_audit_page_non_positive_limit_falls_back_to_default_not_unbounded(db):
    # pymongo reads .limit(0) as *no limit*, so a caller passing limit=0 to
    # mean "nothing" would otherwise load the whole collection into the
    # DataFrame. More than DEFAULT_PAGE_SIZE rows is required here -- with
    # fewer, "capped at 50" and "no limit at all" are indistinguishable.
    base = datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc)
    for i in range(DEFAULT_PAGE_SIZE + 10):
        db.audit_log.insert_one(
            _entry("admin1", f"action_{i}", base + timedelta(minutes=i))
        )

    page = fetch_audit_page(db.audit_log, limit=0)

    assert len(page) == DEFAULT_PAGE_SIZE


def test_fetch_audit_page_ties_split_across_pages_without_duplicates_or_drops(db):
    # A single operator action can write several entries with the exact same
    # timestamp (logins cluster too), and skip/limit runs as a *separate*
    # query per page. Sorting on timestamp alone leaves ties in undefined
    # order, so the same entry could land on two pages or on none -- the one
    # failure mode an audit log cannot have. `_id` breaks the tie. The exact
    # order within the tie is not the contract; disjoint-and-complete is.
    tied_ts = datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc)
    for i in range(6):
        db.audit_log.insert_one(_entry("admin1", f"a{i}", tied_ts))

    page1 = {e["action"] for e in fetch_audit_page(db.audit_log, limit=3, skip=0)}
    page2 = {e["action"] for e in fetch_audit_page(db.audit_log, limit=3, skip=3)}

    assert page1 & page2 == set()
    assert page1 | page2 == {f"a{i}" for i in range(6)}


# --- page_count ---


@pytest.mark.parametrize(
    "total, page_size, expected",
    [(0, 50, 1), (50, 50, 1), (51, 50, 2), (100, 50, 2)],
)
def test_page_count(total, page_size, expected):
    assert page_count(total, page_size) == expected


def test_page_count_non_positive_page_size_falls_back_to_default():
    assert page_count(10, 0) == page_count(10, DEFAULT_PAGE_SIZE)


# --- clamp_page ---


@pytest.mark.parametrize(
    "page, total, page_size, expected",
    [
        (7, 3, 50, 1),  # past the end -> last valid page (1, since page_count is 1)
        (0, 3, 50, 1),  # below range -> floor
        ("x", 3, 50, 1),  # non-int -> falls back to 1
        (2, 200, 50, 2),  # valid mid-range page passes through unchanged
    ],
)
def test_clamp_page(page, total, page_size, expected):
    assert clamp_page(page, total, page_size) == expected


# --- format_audit_rows ---


@pytest.mark.parametrize(
    "ts",
    [
        datetime(2026, 8, 30, 9, 5, 7),  # naive -> treated as UTC
        pytz.utc.localize(datetime(2026, 8, 30, 9, 5, 7)),  # aware UTC
    ],
    ids=["naive_treated_as_utc", "aware_utc"],
)
def test_format_audit_rows_naive_and_aware_render_same_israel_time(ts):
    # Israel is UTC+3 in August (DST) -> 09:05:07 UTC becomes 12:05:07 local.
    rows = format_audit_rows(
        [{"admin_user": "admin1", "action": "login", "details": {}, "timestamp": ts}]
    )

    assert rows[0]["time"] == "2026-08-30 12:05:07"


def test_format_audit_rows_details_none_renders_empty_string():
    rows = format_audit_rows(
        [
            {
                "admin_user": "admin1",
                "action": "login",
                "details": None,
                "timestamp": datetime(2026, 8, 30, tzinfo=timezone.utc),
            }
        ]
    )

    assert rows[0]["details"] == ""


def test_format_audit_rows_details_dict_renders_key_value_pairs():
    rows = format_audit_rows(
        [
            {
                "admin_user": "admin1",
                "action": "edit_lead",
                "details": {"lead_id": "abc", "status": "booked"},
                "timestamp": datetime(2026, 8, 30, tzinfo=timezone.utc),
            }
        ]
    )

    assert rows[0]["details"] == "lead_id=abc, status=booked"


def test_format_audit_rows_nested_dict_detail_value_flattens_not_repr():
    # edit_lead (lead_queries) stamps the whole change payload as a nested
    # dict -- {"changes": {...}} -- which as a bare repr would fill the
    # cell with Python's braces/quotes and bury what actually changed.
    rows = format_audit_rows(
        [
            {
                "admin_user": "admin1",
                "action": "edit_lead",
                "details": {"changes": {"status": "booked", "notes": "x"}},
                "timestamp": datetime(2026, 8, 30, tzinfo=timezone.utc),
            }
        ]
    )

    assert rows[0]["details"] == "changes=status=booked notes=x"


def test_format_audit_rows_non_datetime_timestamp_passes_through():
    rows = format_audit_rows(
        [{"admin_user": "admin1", "action": "login", "details": {}, "timestamp": "n/a"}]
    )

    assert rows[0]["time"] == "n/a"
