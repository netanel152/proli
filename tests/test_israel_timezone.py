"""Israel time is defined once: ``app.core.datetime_utils.IL_TZ``.

Thirteen files used to build their own ``pytz.timezone("Asia/Jerusalem")``.
They agreed, but nothing made them, and a second definition is how a module
ends up on a different zone (or a different library) from the rest.
"""

from pathlib import Path

import pytz

from app.core.datetime_utils import IL_TZ

ROOT = Path(__file__).resolve().parent.parent
ZONE = "Asia/Jerusalem"

# Files allowed to spell the zone name, and why. Neither builds a timezone.
EXCEPTED = {
    # The TIMEZONE setting's default: configuration, not a definition.
    "app/core/config.py",
    # Operator-facing copy (TRANS "audit_tz_note") that names the zone.
    "admin_panel/core/config.py",
}


def test_the_constant_is_israel_time():
    assert IL_TZ is pytz.timezone(ZONE)


def test_the_zone_is_defined_in_exactly_one_source_file():
    found = sorted(
        str(path.relative_to(ROOT))
        for folder in ("app", "admin_panel")
        for path in (ROOT / folder).rglob("*.py")
        if ZONE in path.read_text(encoding="utf-8")
    )
    assert [f for f in found if f not in EXCEPTED] == ["app/core/datetime_utils.py"]
    # An exception that no longer names the zone should leave the list.
    assert EXCEPTED <= set(found)
