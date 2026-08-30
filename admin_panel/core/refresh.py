"""Auto-refresh interval helpers for the admin panel (PRO-141).

Streamlit-free on purpose. ``admin_panel/main.py`` calls
``st.set_page_config`` at module scope and cannot be imported outside a
Streamlit runtime, so any decision worth testing has to live somewhere a
test can reach — the same seam as ``lead_queries`` (PRO-161),
``schedule_queries`` (PRO-158) and ``analytics_queries`` (PRO-140).

Auto-refresh used to be ``time.sleep(interval); st.rerun()`` at the bottom
of the script run, which blocked the script-run thread for up to 120
seconds: while it slept the page ignored every click, so turning "Live" on
made the operator's main tool feel broken. It is now a client-side timer
(``streamlit_autorefresh.st_autorefresh``), and these helpers own the
pieces of that with a wrong answer worth guarding against.
"""

from datetime import datetime

import pytz

_IL_TZ = pytz.timezone("Asia/Jerusalem")

# The intervals the sidebar slider offers, in seconds.
#
# 15s was offered before PRO-141 and is deliberately gone. The leads frame
# is served through ``@st.cache_data(ttl=30)`` (``views/home.py``), so the
# table cannot be fresher than 30 seconds whatever the slider says — while
# the metric tiles above it are uncached ``count_documents`` calls that do
# update on every tick. A 15s interval therefore bought no table freshness,
# doubled the reruns and the Mongo traffic, and put a counter that says
# "Needs Review: 4" above a table showing three, for up to 30 seconds. It
# only looked harmless while the blocking timer rarely completed a cycle.
# A stored 15 from an older session resolves to the default below.
REFRESH_INTERVAL_OPTIONS = (30, 60, 120)

DEFAULT_REFRESH_INTERVAL_SECONDS = 30

# `st.data_editor` widget state lives under the widget's key and holds
# `edited_rows` / `added_rows` / `deleted_rows`. The leads editor uses a
# fixed key; the schedule editor builds one per pro and date
# (`editor_{pro}_{date}_{n}`), hence the prefix as well as the literal.
_EDITOR_STATE_KEYS = ("leads_editor",)
_EDITOR_KEY_PREFIX = "editor_"
_PENDING_EDIT_FIELDS = ("edited_rows", "added_rows", "deleted_rows")

# Set by a view immediately before `st.rerun()` when a save has been
# committed, and popped by that view when it renders the confirmation.
# Because the sidebar runs *before* the view, a save flash is still present
# on the run where the editor's widget state is stale-but-not-yet-collected
# — see `has_unsaved_edits`.
_SAVE_FLASH_KEYS = ("leads_flash", "sch_flash")


def resolve_interval_seconds(value):
    """Coerce a stored refresh interval into one of the offered options.

    In the happy path ``value`` came from the slider and is already valid.
    It is validated anyway because both failure directions are ugly and
    neither is hypothetical:

    * ``st.select_slider(value=...)`` **raises** when the value is not in
      ``options``, so a stale or hand-set session value crashes the sidebar
      — taking the whole panel with it, not just the toggle.
    * A ``None`` or ``0`` reaching the component as ``interval=0`` is a
      rerun storm, and every rerun re-queries Mongo.

    Anything unrecognised falls back to
    ``DEFAULT_REFRESH_INTERVAL_SECONDS``; being wrong in that direction
    costs one refresh at 30s.
    """
    # bool is an int subclass — True would otherwise coerce to 1 and, being
    # absent from the options, land on the default anyway. Spelled out so
    # the behaviour is intentional rather than incidental.
    if isinstance(value, bool):
        return DEFAULT_REFRESH_INTERVAL_SECONDS

    try:
        seconds = int(value)
    except (TypeError, ValueError, OverflowError):
        # OverflowError is not decoration: int(float("inf")) raises it, and
        # it is an ArithmeticError — outside the other two — so it would
        # escape and crash the sidebar, the one thing this function exists
        # to prevent.
        return DEFAULT_REFRESH_INTERVAL_SECONDS

    if seconds not in REFRESH_INTERVAL_OPTIONS:
        return DEFAULT_REFRESH_INTERVAL_SECONDS

    return seconds


def refresh_interval_ms(value):
    """Milliseconds for ``st_autorefresh(interval=...)``.

    The component takes milliseconds; the slider and session state speak
    seconds. Converting in one place keeps a factor-of-1000 slip — a
    refresh every 30ms — out of the call site.
    """
    return resolve_interval_seconds(value) * 1000


def has_unsaved_edits(session_state):
    """True when a ``st.data_editor`` rendered last run holds uncommitted rows.

    The caller uses this to skip arming the timer, because a refresh that
    lands mid-edit *destroys* the edits: ``st.data_editor`` hashes the
    dataframe's bytes into its widget id, so one new inbound lead makes the
    next run a different widget with an empty ``edited_rows`` — the exact
    loss ``views/home.py`` warns about after the fact
    (``leads_msg_refresh_discarded``, PRO-161).

    ``debounce=True`` on the component is not enough on its own: it restarts
    the countdown on every *rerun*, so it covers a burst of typing but not
    the pause between the last cell edit and the Save click. And its
    protection runs backwards to the risk — a tick is only destructive when
    the frame actually changed, which is precisely when debounce cannot see
    it coming.

    The sidebar runs before the view dispatch, so this reads the previous
    run's editor state: "what was pending as of the last render", which is
    the right question — with one exception that has to be handled here.

    Streamlit drops stale widget state at the *end* of a completed run, so
    on the run immediately after a save the committed rows are still sitting
    in the editor's state. Left alone that produced the worst version of
    this feature: the operator saves successfully and the sidebar answers
    "refresh paused — unsaved table edits", about edits that were just
    written, and then does not arm the timer — and since nothing schedules
    another run, auto-refresh stays off until the next click. A save flash
    is set immediately before that rerun and popped by the view further down
    the same run, so its presence here means "those rows are saved, the
    state you can see is residue".

    Not covered, deliberately: a run that ends in an uncaught view exception
    skips Streamlit's widget GC entirely, so genuinely abandoned editor
    state can keep the pause on across repeated crash runs. The indicator
    says "paused" throughout, and any interaction that reaches a clean run
    clears it.
    """
    if any(session_state.get(key) for key in _SAVE_FLASH_KEYS):
        return False

    for key, value in session_state.items():
        key = str(key)
        if key not in _EDITOR_STATE_KEYS and not key.startswith(_EDITOR_KEY_PREFIX):
            continue
        if not isinstance(value, dict):
            continue
        if any(value.get(field) for field in _PENDING_EDIT_FIELDS):
            return True
    return False


def last_refresh_label(now=None):
    """Wall-clock ``HH:MM:SS`` in Israel time for the "updated at" caption.

    The pulsing "Live" dot is a fixed CSS animation with no connection to
    the timer, so it cannot report the three ways a client-side timer goes
    quiet without saying so: ``debounce`` restarting the countdown on every
    rerun (an operator clicking steadily can hold "Live" on and get no
    refresh at all), a browser throttling ``setInterval`` in a background
    tab to roughly once a minute, or the component's iframe never loading.
    A timestamp of the run that produced what is on screen stays true under
    all three — it asserts what happened rather than what is scheduled.

    ``strftime`` formats a datetime's *own* wall clock, so an injected
    ``now`` has to be converted rather than merely formatted: passing an
    aware UTC value would otherwise print UTC while this claims to return
    Israel time. A naive value is treated as already-Israel, matching
    ``app/core/datetime_utils``.
    """
    if now is None:
        return datetime.now(_IL_TZ).strftime("%H:%M:%S")
    if now.tzinfo is None:
        now = _IL_TZ.localize(now)
    return now.astimezone(_IL_TZ).strftime("%H:%M:%S")
