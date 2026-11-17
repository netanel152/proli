"""No naive ``datetime.now()`` under app/ or admin_panel/.

A bare ``datetime.now()`` returns the server's local wall clock with no zone.
The servers run on UTC, so an operator-facing "today" read that way is the
wrong day for the two or three hours after midnight in Israel, and a stored
timestamp read that way cannot be told apart from a UTC one. Pass a zone:
``timezone.utc`` for instants, ``IL_TZ`` for what an operator reads as a date.
"""

import ast
from datetime import date, datetime, timezone
from pathlib import Path

from admin_panel.core.schedule_queries import bulk_generator_default_dates

ROOT = Path(__file__).resolve().parent.parent


def _is_naive_now(node: ast.AST) -> bool:
    if not (isinstance(node, ast.Call) and not node.args and not node.keywords):
        return False
    func = node.func
    if not (isinstance(func, ast.Attribute) and func.attr == "now"):
        return False
    owner = func.value
    # datetime.now() and datetime.datetime.now()
    return (isinstance(owner, ast.Name) and owner.id == "datetime") or (
        isinstance(owner, ast.Attribute) and owner.attr == "datetime"
    )


def test_no_bare_datetime_now_remains():
    found = []
    for folder in ("app", "admin_panel"):
        for path in (ROOT / folder).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            found += [
                f"{path.relative_to(ROOT)}:{node.lineno}"
                for node in ast.walk(tree)
                if _is_naive_now(node)
            ]
    assert found == []


def test_the_check_catches_both_spellings():
    for source in ("datetime.now()", "datetime.datetime.now()"):
        assert _is_naive_now(ast.parse(source).body[0].value)
    for source in ("datetime.now(timezone.utc)", "datetime.now(tz=IL_TZ)"):
        assert not _is_naive_now(ast.parse(source).body[0].value)


def test_at_2330_utc_the_schedule_default_tomorrow_is_the_israel_date():
    # 23:30 UTC on 17 Nov is 01:30 on 18 Nov in Israel: "tomorrow" is the 19th,
    # where a server-local (UTC) clock would have said the 18th.
    start, end = bulk_generator_default_dates(
        datetime(2026, 11, 17, 23, 30, tzinfo=timezone.utc)
    )
    assert start == date(2026, 11, 19)
    assert end == date(2026, 11, 25)


def test_at_noon_utc_israel_and_utc_agree():
    start, end = bulk_generator_default_dates(
        datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
    )
    assert (start, end) == (date(2026, 11, 18), date(2026, 11, 24))
