import sys
import os
import streamlit as st
from streamlit_autorefresh import st_autorefresh
from datetime import datetime, timedelta
import time

# Add project root to path
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

from admin_panel.core.config import TRANS
from admin_panel.core.refresh import (
    DEFAULT_REFRESH_INTERVAL_SECONDS,
    REFRESH_INTERVAL_OPTIONS,
    has_unsaved_edits,
    last_refresh_label,
    refresh_interval_ms,
    resolve_interval_seconds,
)
from admin_panel.ui.components import load_css
from admin_panel.core.auth import check_password, logout, get_manager
from app.core.sentry import init_sentry, sentry_active, should_send

# Idempotent (module state survives Streamlit's per-interaction reruns), so
# this runs the real init exactly once per server process. No integrations
# are enabled for the admin service — Streamlit swallows exceptions into its
# own error UI, so the view dispatch below captures explicitly instead.
init_sentry("proli-admin")

from admin_panel.views.home import view_leads_dashboard
from admin_panel.views.professionals import view_professionals
from admin_panel.views.schedule import view_schedule_editor
from admin_panel.views.settings import view_system_settings
from admin_panel.views.analytics import view_analytics

# --- Page Config ---
st.set_page_config(
    page_title="Proli Admin",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

cookie_manager = get_manager()
cookies = cookie_manager.get_all()

# --- Authentication ---
if not check_password(cookies):
    st.stop()

# --- Language Logic ---
if "lang_code" not in st.session_state:
    st.session_state.lang_code = cookies.get("proli_lang", "EN")

T = TRANS[st.session_state.lang_code]
load_css(st.session_state.lang_code, T)

# --- Sidebar ---
with st.sidebar:
    # Top row: Branding + Logout
    c_brand, c_logout = st.columns([3, 1])
    with c_brand:
        st.title("⚡ Proli")
    with c_logout:
        st.markdown("")
        logout(cookie_manager, T)

    # User info
    username = st.session_state.get("admin_username", "")
    role = st.session_state.get("admin_role", "")
    if username:
        st.caption(f"{username} · {role}")

    st.markdown("")

    # Language Selector (compact)
    lang_options = ["HE", "EN"]
    try:
        default_index = lang_options.index(st.session_state.lang_code)
    except ValueError:
        default_index = 0

    selected_lang = st.selectbox(
        T.get("lang_label", "Language / שפה"),
        lang_options,
        index=default_index,
        key="lang_select",
    )

    if selected_lang != st.session_state.lang_code:
        st.session_state.lang_code = selected_lang
        expires = datetime.now() + timedelta(days=365)
        cookie_manager.set("proli_lang", selected_lang, expires_at=expires)
        time.sleep(0.05)
        st.rerun()

    st.divider()

    # --- Navigation ---
    page_options = [
        T["nav_dashboard"],
        T["nav_professionals"],
        T["nav_schedule"],
        T.get("nav_analytics", "Analytics"),
        T["nav_settings"],
    ]

    if "current_page" not in st.session_state:
        st.session_state.current_page = page_options[0]

    if st.session_state.current_page not in page_options:
        st.session_state.current_page = page_options[0]

    def on_nav_change():
        if "nav_radio" in st.session_state:
            st.session_state.current_page = st.session_state.nav_radio

    page = st.radio(
        T["nav_title"],
        page_options,
        index=page_options.index(st.session_state.current_page),
        key="nav_radio",
        on_change=on_nav_change,
        label_visibility="collapsed",
    )

    st.divider()

    # --- Auto-Refresh Toggle ---
    auto_refresh = st.toggle(
        T.get("auto_refresh", "Auto-refresh"),
        # No `value=` lookup: the widget's own `auto_refresh_toggle` key is
        # what carries the setting across reruns. The old default read a
        # session key named "auto_refresh" that nothing ever wrote (it only
        # exists as a translation key), so it was always False and read as
        # if state were being restored from somewhere.
        key="auto_refresh_toggle",
    )

    if auto_refresh:
        refresh_interval = st.select_slider(
            T.get("refresh_interval", "Interval"),
            options=list(REFRESH_INTERVAL_OPTIONS),
            # Validated, not passed through: select_slider raises when the
            # value is absent from `options`, and that exception would take
            # down the whole sidebar rather than just this control.
            value=resolve_interval_seconds(
                st.session_state.get(
                    "refresh_interval_val", DEFAULT_REFRESH_INTERVAL_SECONDS
                )
            ),
            format_func=lambda x: f"{x}s",
            key="refresh_interval_slider",
        )
        st.session_state["refresh_interval_val"] = refresh_interval

        # PRO-141 — the refresh is a client-side timer, not a sleep in the
        # script-run thread. This renders a zero-height component that calls
        # back after `interval` ms; between ticks the thread is free, so the
        # page stays responsive to clicks (the old `time.sleep(interval)`
        # blocked every interaction for up to 120s).
        #
        # It sits here, above the view dispatch, on purpose: the old block
        # ran *after* the try/except below, so any view exception silently
        # stopped auto-refresh until somebody noticed the page had gone
        # stale.
        #
        # The timer is not armed at all while a data_editor holds
        # uncommitted rows. A tick that lands mid-edit destroys them — the
        # editor's widget id hashes the dataframe's bytes, so one new
        # inbound lead makes the next run a different widget with empty
        # `edited_rows` (PRO-161's `leads_msg_refresh_discarded` reports
        # that loss after the fact). `debounce` alone does not cover it: it
        # restarts the countdown on every rerun, which protects a burst of
        # typing but not the pause between the last edit and the Save
        # click.
        #
        # `has_unsaved_edits` treats a pending save flash as "no unsaved
        # edits" precisely because this block runs before the view: without
        # that, a successful save would be answered with "paused — unsaved
        # edits" about rows that were just written, and the timer would stay
        # disarmed until the operator clicked something.
        if has_unsaved_edits(st.session_state):
            st.markdown(
                f"""
                <div class="refresh-indicator" dir="{T.get('dir', 'ltr')}">
                    <div class="refresh-dot refresh-dot--paused"></div>
                    <span>{T.get("refresh_paused_edits",
                                 "Refresh paused — unsaved table edits")}</span>
                </div>
            """,
                unsafe_allow_html=True,
            )
        else:
            st_autorefresh(
                interval=refresh_interval_ms(refresh_interval),
                debounce=True,
                key="auto_refresh_tick",
            )

            # The pulsing dot is a fixed CSS animation with no connection to
            # the timer, so on its own it claims "live" through every way a
            # client-side timer goes quiet: debounce restarting the
            # countdown on every rerun, a browser throttling setInterval in
            # a background tab to ~1/min, or the iframe never loading. The
            # timestamp is of the run that produced what is on screen, so it
            # stays true in all three cases.
            st.markdown(
                f"""
                <div class="refresh-indicator" dir="{T.get('dir', 'ltr')}">
                    <div class="refresh-dot"></div>
                    <span>{T.get("refresh_live", "Live")} ·
                          {T.get("refresh_last", "Updated")}
                          {last_refresh_label()}</span>
                </div>
            """,
                unsafe_allow_html=True,
            )


# --- Page Rendering ---
current_selection = st.session_state.get("current_page", page)

try:
    if current_selection == T["nav_dashboard"]:
        view_leads_dashboard(T)
    elif current_selection == T["nav_professionals"]:
        view_professionals(T)
    elif current_selection == T["nav_schedule"]:
        view_schedule_editor(T)
    elif current_selection == T.get("nav_analytics", "Analytics"):
        view_analytics(T)
    elif current_selection == T["nav_settings"]:
        view_system_settings(T)
except Exception as _view_exc:
    # Streamlit renders its own red error box but swallows the exception
    # from every outer hook (no excepthook fires) — without this, an admin
    # panel crash is invisible outside the operator's browser tab.
    #
    # Throttled per (view, exception type) since PRO-141: auto-refresh is
    # armed in the sidebar *above* this block, so the timer now survives a
    # failing view instead of dying with it. A crashing view under a 15s
    # timer would otherwise re-capture and `flush(timeout=2)` four times a
    # minute per open tab, for as long as the tab is open. Same helper and
    # same reasoning as the APScheduler job-error listener.
    if sentry_active() and should_send(
        f"admin_view:{current_selection}:{type(_view_exc).__name__}"
    ):
        import sentry_sdk

        sentry_sdk.set_tag("view", str(current_selection))
        sentry_sdk.capture_exception(_view_exc)
        sentry_sdk.flush(timeout=2)
    raise  # let Streamlit still render its error UI

# Auto-refresh is armed in the sidebar above (PRO-141). Nothing belongs here:
# a sleep-then-rerun at the end of the script run is what made the panel
# unresponsive for up to 120s at a time.
