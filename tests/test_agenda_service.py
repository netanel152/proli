"""PRO-147 — pure formatting/sorting/pagination in app/services/agenda_service.py.

No DB, no Redis: every function here is pure, so these tests only feed in
plain dicts and datetimes and check the returned strings/order.
"""

from datetime import datetime, timezone

import pytest

from app.core.constants import WorkerConstants
from app.core.messages import Messages
from app.services import agenda_service
from tests.copy_util import static_prefix

_PAGE_SIZE = WorkerConstants.PRO_LIST_PAGE_SIZE

# A fixed "now" so day-bucket math (today/tomorrow/other) is deterministic.
# Anchored well away from midnight so Israel's UTC+2/+3 offset can never
# push a same-UTC-day appointment across the Israel calendar-day boundary.
_ANCHOR_UTC = datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc)
_NOW_IL = agenda_service.now_israel(_ANCHOR_UTC)


def test_sort_by_appointment_orders_dated_ascending_and_nulls_last_stable():
    lead_a = {"id": "a", "appointment_datetime": datetime(2026, 3, 12, 10, 0)}
    lead_b = {"id": "b", "appointment_datetime": datetime(2026, 3, 11, 9, 0)}
    lead_c = {"id": "c"}  # no key at all
    lead_d = {"id": "d", "appointment_datetime": None}  # key present, None

    result = agenda_service.sort_by_appointment([lead_a, lead_b, lead_c, lead_d])

    # b (11th) before a (12th); the two null leads keep their original
    # relative order (c before d) since the sort is stable.
    assert [lead["id"] for lead in result] == ["b", "a", "c", "d"]


# --- format_appointment ----------------------------------------------------

_TODAY_NAIVE = datetime(2026, 3, 10, 12, 30)  # naive, no tzinfo — pymongo shape
_TOMORROW_NAIVE = datetime(2026, 3, 11, 7, 0)
_OTHER_DAY_NAIVE = datetime(2026, 3, 15, 9, 0)

# Expected hh:mm derived from the same UTC->Israel conversion under test, so
# the parametrize table doesn't have to hardcode a DST offset by hand.
_TODAY_LOCAL = agenda_service.to_israel(_TODAY_NAIVE)
_TOMORROW_LOCAL = agenda_service.to_israel(_TOMORROW_NAIVE)
_OTHER_LOCAL = agenda_service.to_israel(_OTHER_DAY_NAIVE)

assert (_TODAY_LOCAL.date() - _NOW_IL.date()).days == 0
assert (_TOMORROW_LOCAL.date() - _NOW_IL.date()).days == 1
assert (_OTHER_LOCAL.date() - _NOW_IL.date()).days not in (0, 1)


@pytest.mark.parametrize(
    "lead, expected",
    [
        pytest.param(
            {"appointment_datetime": _TODAY_NAIVE},
            Messages.Pro.TIME_TODAY.format(time=_TODAY_LOCAL.strftime("%H:%M")),
            id="today-naive-utc",
        ),
        pytest.param(
            {"appointment_datetime": _TOMORROW_NAIVE},
            Messages.Pro.TIME_TOMORROW.format(time=_TOMORROW_LOCAL.strftime("%H:%M")),
            id="tomorrow",
        ),
        pytest.param(
            {"appointment_datetime": _OTHER_DAY_NAIVE},
            Messages.Pro.TIME_DATE.format(
                weekday=Messages.Pro.WEEKDAY_NAMES[_OTHER_LOCAL.date().weekday()],
                date=f"{_OTHER_LOCAL.date().day}.{_OTHER_LOCAL.date().month}",
                time=_OTHER_LOCAL.strftime("%H:%M"),
            ),
            id="other-day",
        ),
        pytest.param(
            {"appointment_datetime": None, "appointment_time": "בשעות הערב"},
            "בשעות הערב",
            id="null-falls-back-to-raw-appointment-time",
        ),
        pytest.param(
            {},
            Messages.Fallbacks.TIME_UNSET,
            id="null-and-no-raw-time-falls-back-to-fallback-constant",
        ),
    ],
)
def test_format_appointment(lead, expected):
    assert agenda_service.format_appointment(lead, _NOW_IL) == expected


def test_render_page_groups_rows_under_one_header_per_day_change():
    leads = [
        {"id": 1, "appointment_datetime": _TODAY_NAIVE},
        {"id": 2, "appointment_datetime": _TODAY_NAIVE},
        {"id": 3, "appointment_datetime": _TOMORROW_NAIVE},
        {"id": 4, "appointment_datetime": _TOMORROW_NAIVE},
    ]

    def row(num, lead, time_str):
        return f"L{lead['id']}"

    lines, next_offset = agenda_service.render_page(
        leads, row, now_il=_NOW_IL, offset=0, total=4
    )

    assert lines == [
        Messages.Pro.DAY_HEADER_TODAY,
        "L1",
        "L2",
        Messages.Pro.DAY_HEADER_TOMORROW,
        "L3",
        "L4",
    ]
    assert next_offset is None


@pytest.mark.parametrize(
    "n, offset, expected_next_offset, expected_footer",
    [
        pytest.param(_PAGE_SIZE - 5, 0, None, None, id="fits-in-one-page-no-footer"),
        pytest.param(_PAGE_SIZE, 0, None, None, id="exactly-page-size-no-footer"),
        pytest.param(
            _PAGE_SIZE + 1, 0, _PAGE_SIZE, "more", id="page-size-plus-one-continues"
        ),
        pytest.param(
            _PAGE_SIZE + 5,
            _PAGE_SIZE,
            None,
            "end",
            id="later-page-is-the-last-page",
        ),
    ],
)
def test_render_page_pagination_boundary_and_footer(
    n, offset, expected_next_offset, expected_footer
):
    # All-unscheduled leads: single header group, isolates the footer/offset
    # logic from the day-grouping behaviour covered above.
    leads = [{"id": i} for i in range(n)]

    def row(num, lead, time_str):
        return f"row-{num}"

    lines, next_offset = agenda_service.render_page(
        leads, row, now_il=_NOW_IL, offset=offset, total=n
    )

    assert next_offset == expected_next_offset

    more_prefix = static_prefix(Messages.Pro.LIST_PAGE_MORE)
    end_prefix = static_prefix(Messages.Pro.LIST_PAGE_END)
    joined = "\n".join(lines)

    if expected_footer is None:
        assert more_prefix not in joined
        assert end_prefix not in joined
    elif expected_footer == "more":
        expected_line = Messages.Pro.LIST_PAGE_MORE.format(
            first=offset + 1, last=offset + _PAGE_SIZE, total=n
        )
        assert expected_line in lines
    else:  # "end"
        expected_line = Messages.Pro.LIST_PAGE_END.format(
            first=offset + 1, last=n, total=n
        )
        assert expected_line in lines
