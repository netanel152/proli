"""Tests for admin_panel/core/refresh.py — PRO-141.

Auto-refresh used to end the Streamlit script run with
``time.sleep(interval); st.rerun()`` (interval up to 120s), blocking the
script-run thread so the admin panel ignored every click while it slept.
It is now a client-side timer (``streamlit_autorefresh.st_autorefresh``),
driven by the interval math in ``admin_panel/core/refresh.py``, paused
while a ``st.data_editor`` holds unsaved rows (``has_unsaved_edits``), and
labelled with the Israel-local time of the run that produced what's on
screen (``last_refresh_label``).

``admin_panel/main.py`` calls ``st.set_page_config`` at module scope and
cannot be imported outside a Streamlit runtime, so the regression guard at
the bottom of this file reads the source text instead (precedent:
``tests/test_agent_pack_drift.py``, ``tests/test_claude_config.py``).
"""

import io
import re
import tokenize
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytz

from admin_panel.core.config import TRANS
from admin_panel.core.refresh import (
    DEFAULT_REFRESH_INTERVAL_SECONDS,
    REFRESH_INTERVAL_OPTIONS,
    has_unsaved_edits,
    last_refresh_label,
    refresh_interval_ms,
    resolve_interval_seconds,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MAIN_PY = _REPO_ROOT / "admin_panel" / "main.py"
_REQUIREMENTS_TXT = _REPO_ROOT / "requirements.txt"
_IL_TZ = pytz.timezone("Asia/Jerusalem")


def _code_without_comments(source: str) -> str:
    """Strip comments so the regression guard scans code, not prose.

    ``main.py`` explains the PRO-141 fix in a comment that itself quotes the
    old bug (the old ``time.sleep(interval)``), so a plain substring/regex
    scan of the raw source would trip on the explanation rather than on a
    reintroduced bug.
    """
    return "".join(
        "" if tok.type == tokenize.COMMENT else tok.string
        for tok in tokenize.generate_tokens(io.StringIO(source).readline)
    )


@pytest.mark.parametrize("seconds", REFRESH_INTERVAL_OPTIONS)
def test_resolve_interval_seconds_returns_valid_options_unchanged(seconds):
    assert resolve_interval_seconds(seconds) == seconds


@pytest.mark.parametrize(
    "value",
    [None, 0, -5, 7, 15, "abc", [], True, False],
    ids=[
        "none",
        "zero",
        "negative",
        "out_of_range",
        # 15 is not junk input: it's the interval a session could still
        # hold from before 15s was dropped from REFRESH_INTERVAL_OPTIONS
        # (a 15s tick could never outrun the leads frame's 30s cache TTL).
        # A stale 15 must migrate to the default, not crash
        # st.select_slider(value=...), which raises when value is absent
        # from options.
        "stale_pre_pro141_15s_option",
        "non_numeric",
        "list",
        "true",
        "false",
    ],
)
def test_resolve_interval_seconds_falls_back_to_default_on_invalid_input(value):
    assert resolve_interval_seconds(value) == DEFAULT_REFRESH_INTERVAL_SECONDS


def test_resolve_interval_seconds_falls_back_to_default_on_overflow():
    # int(float("inf")) raises OverflowError, not TypeError/ValueError — a
    # distinct exception class the function has to catch separately.
    assert resolve_interval_seconds(float("inf")) == DEFAULT_REFRESH_INTERVAL_SECONDS


@pytest.mark.parametrize(
    ("value", "expected"),
    [("30", 30), (30.9, 30)],
    ids=["numeric_string", "float_truncation"],
)
def test_resolve_interval_seconds_coerces_string_and_float(value, expected):
    assert resolve_interval_seconds(value) == expected


def test_refresh_interval_ms_is_seconds_times_1000():
    # Hard literals first: a shared bug in resolve_interval_seconds (e.g. if
    # it started returning milliseconds) would still pass a check computed
    # by calling resolve_interval_seconds again, so pin two known values —
    # both still-valid options now that 15 has been dropped.
    assert refresh_interval_ms(30) == 30000
    assert refresh_interval_ms(120) == 120000

    assert refresh_interval_ms(REFRESH_INTERVAL_OPTIONS[0]) == (
        resolve_interval_seconds(REFRESH_INTERVAL_OPTIONS[0]) * 1000
    )
    assert (
        refresh_interval_ms("not a number") == DEFAULT_REFRESH_INTERVAL_SECONDS * 1000
    )


# --- has_unsaved_edits: pause-while-dirty guard ---


@pytest.mark.parametrize(
    ("session_state", "expected"),
    [
        (
            {
                "leads_editor": {
                    "edited_rows": {},
                    "added_rows": [],
                    "deleted_rows": [],
                }
            },
            False,
        ),
        ({"leads_editor": {"edited_rows": {0: {"status": "new"}}}}, True),
        ({"editor_abc_2026-09-01_3": {"added_rows": [{"x": 1}]}}, True),
        ({"leads_editor": {"deleted_rows": [2]}}, True),
        ({"nav_radio": {"edited_rows": {0: 1}}}, False),
        ({"leads_editor": "nope"}, False),
        ({}, False),
        (
            {
                "leads_editor": {"edited_rows": {0: {"s": 1}}},
                "leads_flash": {"updated": 1},
            },
            False,
        ),
        (
            {
                "editor_a_b_1": {"added_rows": [{"x": 1}]},
                "sch_flash": {"inserted": 1},
            },
            False,
        ),
        (
            {
                "leads_editor": {"edited_rows": {0: {"s": 1}}},
                "leads_flash": None,
            },
            True,
        ),
    ],
    ids=[
        "leads_editor_all_empty",
        "leads_editor_edited_rows",
        "schedule_editor_prefix_added_rows",
        "leads_editor_deleted_rows",
        # Proves the scan keys off the editor key, not just the presence of
        # an edited_rows field on any dict in session_state.
        "non_editor_key_with_edited_rows_field_ignored",
        "non_dict_editor_value_does_not_crash",
        "empty_session_state",
        "leads_flash_suppresses_post_save_residue",
        "sch_flash_suppresses_post_save_residue",
        # The interesting one: a falsy flash value must not suppress a real
        # pause, which is exactly what a sloppy `key in session_state`
        # rewrite of the flash check would get wrong.
        "falsy_flash_value_does_not_suppress_real_pause",
    ],
)
def test_has_unsaved_edits(session_state, expected):
    assert has_unsaved_edits(session_state) is expected


# --- last_refresh_label: Israel-local "updated at" caption ---


def test_last_refresh_label_formats_israel_localized_datetime():
    il_dt = _IL_TZ.localize(datetime(2026, 8, 30, 14, 5, 7))
    assert last_refresh_label(il_dt) == "14:05:07"


def test_last_refresh_label_no_argument_matches_hh_mm_ss():
    assert re.fullmatch(r"\d{2}:\d{2}:\d{2}", last_refresh_label())


def test_last_refresh_label_converts_utc_to_israel_time():
    # Asia/Jerusalem is UTC+3 (IDT, summer time) on 2026-08-30 — verified
    # directly against the fixed implementation, not assumed.
    utc_dt = datetime(2026, 8, 30, 9, 5, 7, tzinfo=timezone.utc)
    assert last_refresh_label(utc_dt) == "12:05:07"


def test_last_refresh_label_treats_naive_datetime_as_already_israel():
    # The branch the astimezone fix introduced: a naive `now` has no tzinfo
    # to convert from, so it is localized to Israel rather than assumed UTC
    # — matching app/core/datetime_utils's convention. A future refactor
    # that instead treats naive input as UTC would silently shift this by
    # the UTC/IDT offset, so pin it directly.
    naive_dt = datetime(2026, 8, 30, 14, 5, 7)
    assert last_refresh_label(naive_dt) == "14:05:07"


@pytest.mark.parametrize(
    "key", ["refresh_live", "refresh_last", "refresh_paused_edits"]
)
def test_refresh_i18n_keys_present_and_non_empty_in_both_languages(key):
    for lang, lang_dict in TRANS.items():
        assert key in lang_dict, f"{key} missing from TRANS[{lang}]"
        assert lang_dict[key].strip(), f"{key} is blank in TRANS[{lang}]"


def test_main_py_uses_client_side_autorefresh_timer():
    source = _MAIN_PY.read_text(encoding="utf-8")

    assert "from streamlit_autorefresh import st_autorefresh" in source
    assert "st_autorefresh(" in source

    # No blocking sleep on the refresh interval. A short literal sleep (e.g.
    # the ~line-95 cookie-propagation wait before st.rerun()) is legitimate
    # and must stay allowed; only a sleep whose argument is not a small
    # literal (i.e. a variable/session-state-derived `interval`) is the
    # regression this guards against. Comments are stripped first because
    # main.py's own PRO-141 explanation quotes `time.sleep(interval)` while
    # describing the bug this guard exists to catch.
    #
    # Deliberate limits, not oversights: this only inspects main.py (a
    # blocking sleep reintroduced inside a view module would slip through),
    # and only the `time.sleep(` spelling (`from time import sleep; sleep(x)`
    # would slip through too). Both are acceptable for a targeted PRO-141
    # guard — do not over-trust this as a general "no blocking sleep" check.
    code = _code_without_comments(source)
    for match in re.finditer(r"time\.sleep\(\s*([^)]*)\)", code):
        arg = match.group(1).strip()
        try:
            arg_value = float(arg)
        except ValueError:
            pytest.fail(
                f"time.sleep() called with a non-literal argument {arg!r} — "
                "looks like the blocking auto-refresh sleep is back"
            )
        assert arg_value < 1.0, (
            f"time.sleep({arg}) is not a short literal wait — "
            "looks like the blocking auto-refresh sleep is back"
        )

    # The pause-while-dirty guard: a refresh landing mid-edit silently
    # destroys unsaved st.data_editor rows (PRO-161's
    # leads_msg_refresh_discarded), so the timer must only be armed after
    # checking has_unsaved_edits. This only proves the call exists and
    # precedes st_autorefresh(, not that the branching around it is
    # correct — a substring/order check is deliberately the cheap half of
    # the guard.
    assert "has_unsaved_edits(" in code
    assert code.index("has_unsaved_edits(") < code.index("st_autorefresh(")


def test_requirements_pin_streamlit_autorefresh():
    requirements = _REQUIREMENTS_TXT.read_text(encoding="utf-8")
    assert re.search(r"^streamlit-autorefresh==", requirements, re.MULTILINE), (
        "streamlit-autorefresh must be pinned in requirements.txt — "
        "admin_panel/main.py imports it at module scope"
    )
