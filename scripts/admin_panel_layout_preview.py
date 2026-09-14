"""A no-database Streamlit app that renders the admin panel's layout.

Why this exists
---------------
The responsive work (`docs/ADMIN_PANEL_RESPONSIVE_PLAN.md`) can be unit-tested
for *structure* — `tests/test_admin_responsive.py` does that — but "does it
actually look right at 375px in Hebrew" is a question only a browser answers.
The real panel cannot answer it cheaply: it needs a live Mongo and Redis, an
admin password and a session cookie, so in practice nobody renders it and the
layout goes unreviewed.

This harness renders the same chrome through **real Streamlit** with fake data:
the real `load_css` sheet, the real `render_metric_grid` / `render_kanban_column`
markup, and real Streamlit widgets for the parts the breakpoints target
(sidebar, nav radio, tabs, buttons, selectbox, data editor). So Streamlit's own
column stacking, its sidebar overlay and its tab strip are all genuine, not a
hand-rolled imitation.

What it does *not* prove: that the live panel's data-dependent branches render
the same. It is a layout preview, not an end-to-end test — the views' own logic
is covered by the unit suite. Screenshot it with
`scripts/admin_panel_screenshots.py`.

    streamlit run scripts/admin_panel_layout_preview.py -- --lang HE
    streamlit run scripts/admin_panel_layout_preview.py -- --lang HE --section widgets

`--lang` (HE default, EN the other) and `--section` (`dashboard` default,
`widgets` the other) are read from `sys.argv`, since Streamlit forwards
everything after `--` to the script.

The **widgets** section is the kitchen sink: every control class the panel
actually uses, one of each, on one page. It exists because the RTL defects
that survived the first pass were all in chrome the Dashboard happens not to
render — alerts, expanders, forms, date and number inputs, sliders, HTML
tables, chat bubbles. A defect you cannot render is a defect you cannot see.
"""

import os
import sys
from datetime import datetime

import streamlit as st

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from admin_panel.core.config import TRANS  # noqa: E402
from admin_panel.ui.components import (  # noqa: E402
    load_css,
    render_chat_bubble,
    render_kanban_column,
    render_metric_grid,
    render_status_pill,
)

NOW = datetime(2026, 9, 14, 14, 30)

KANBAN_STATUSES = [
    "pending_admin_review",
    "new",
    "contacted",
    "booked",
    "completed",
    "rejected",
    "closed",
    "cancelled",
]

# Two statuses hold cards and six are empty — the shape that exposes the
# tablet-band bug the UX review found, where empties claimed half a row each.
FAKE_LEADS = {
    "pending_admin_review": [
        {
            "client": "דנה כהן",
            "details_summary": "נזילה מתחת לכיור במטבח",
            "city": "תל אביב",
            "professional": "—",
        },
        {
            "client": "Yossi Levi",
            "details_summary": "Boiler not heating since Monday",
            "city": "חיפה",
            "professional": "—",
        },
    ],
    "booked": [
        {
            "client": "משה אברהם",
            "details_summary": "התקנת מזגן בסלון",
            "city": "ירושלים",
            "professional": "אבי שרברב",
        },
    ],
}


def _flag(name, allowed, default):
    """Read `--name value` out of `sys.argv`, falling back to `default`."""
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv) and sys.argv[i + 1] in allowed:
            return sys.argv[i + 1]
    return default


SECTIONS = ("dashboard", "widgets")

LANG = _flag("--lang", TRANS, "HE")
SECTION = _flag("--section", SECTIONS, "dashboard")
T = TRANS[LANG]

st.set_page_config(
    page_title="Proli Admin — layout preview",
    page_icon="⚡",
    layout="wide",
    # Matches the real panel, and is itself one of the things being previewed.
    initial_sidebar_state="auto",
)

load_css(LANG, T)

# --- Sidebar: the real widgets the phone breakpoint targets ----------------
with st.sidebar:
    c_brand, c_logout = st.columns([3, 1])
    with c_brand:
        st.title("⚡ Proli")
    with c_logout:
        st.markdown("")
        st.button("↩", key="preview_logout")

    st.caption("preview · layout only")
    st.markdown("")
    st.selectbox(T.get("lang_label", "Language / שפה"), ["HE", "EN"], key="lang_sel")
    st.divider()
    st.radio(
        T["nav_title"],
        [
            T["nav_dashboard"],
            T["nav_professionals"],
            T["nav_schedule"],
            T.get("nav_analytics", "Analytics"),
            T["nav_settings"],
        ],
        key="nav",
        label_visibility="collapsed",
    )
    st.divider()
    st.toggle(T.get("auto_refresh", "Auto-refresh"), key="auto")


def render_dashboard():
    # --- The Dashboard's shape -----------------------------------------
    st.title(T["nav_dashboard"])

    tab_kanban, tab_table, tab_create = st.tabs(
        [
            T.get("tab_kanban", "Board"),
            T.get("tab_table", "Table"),
            T.get("tab_create", "New"),
        ]
    )

    with tab_kanban:
        st.markdown(
            render_metric_grid(
                [
                    (T.get("metric_total", "Total"), 128),
                    (T.get("metric_pending_review", "Needs Review"), 2),
                    (T.get("metric_new", "New"), 9),
                    (T.get("metric_booked", "Booked"), 4),
                    (T.get("metric_pros", "Staff"), 6),
                ],
                T,
                weights=[1, 1.4, 1, 1, 1],
            ),
            unsafe_allow_html=True,
        )
        st.markdown("")

        # The pending-review strip: the queue the operator opens the panel for.
        st.markdown(f"#### {T.get('pending_admin_review', 'Needs review')} (2)")
        st.caption(T.get("assign_strip_hint", "Quick assignment to active pros."))
        for lead in FAKE_LEADS["pending_admin_review"]:
            st.markdown(f"**🚨 {lead['client']} · {lead['city']}**")
            col_pick, col_go = st.columns([3, 1])
            col_pick.selectbox(
                T.get("assign_pick_pro", "Pick a professional"),
                ["אבי שרברב", "מוסא חשמלאי"],
                index=None,
                placeholder=T.get("assign_pick_pro", "Pick a professional"),
                key=f"pick_{lead['client']}",
                label_visibility="collapsed",
            )
            col_go.button(
                T.get("assign_btn", "Assign"),
                key=f"go_{lead['client']}",
                type="primary",
                use_container_width=True,
            )

        st.markdown("")
        board = "".join(
            render_kanban_column(status, FAKE_LEADS.get(status, []), T)
            for status in KANBAN_STATUSES
        )
        st.markdown(
            f'<div class="kanban-board" dir="{T["dir"]}">{board}</div>',
            unsafe_allow_html=True,
        )

        st.markdown("")
        c1, c2 = st.columns([1, 3])
        with c1:
            st.markdown(render_status_pill("booked", T), unsafe_allow_html=True)
            st.markdown("")
            st.button(T.get("delete_btn", "Delete Lead"), key="del", type="secondary")
        with c2:
            st.text_input(T.get("input_details", "Details"), key="details")
            cy, cn = st.columns(2)
            cy.button(T.get("confirm_yes", "Yes"), key="yes", type="primary")
            cn.button(T.get("confirm_no", "No"), key="no")

    with tab_table:
        st.markdown(
            render_metric_grid(
                [
                    (T.get("metric_total", "Total"), 128),
                    (T.get("metric_pending_review", "Needs Review"), 2),
                    (T.get("metric_new", "New"), 9),
                    (T.get("metric_booked", "Booked"), 4),
                    (T.get("metric_pros", "Staff"), 6),
                ],
                T,
                weights=[1, 1.4, 1, 1, 1],
            ),
            unsafe_allow_html=True,
        )
        st.markdown("")
        st.dataframe(
            [
                {"id": "a1b2", "client": r["client"], "city": r["city"]}
                for rows in FAKE_LEADS.values()
                for r in rows
            ],
            use_container_width=True,
        )

    with tab_create:
        st.text_input(T.get("input_client", "Client"), key="new_client")
        st.text_input(T.get("input_phone", "Phone"), key="new_phone")
        st.button(T.get("action_save", "Save"), key="save", type="primary")


def render_widgets():
    """One of every control class the panel uses, on one page.

    Ordered by where the RTL defects actually were, worst first: alerts carry
    a hard accent border, tables and the data editor carry text alignment,
    the rest carry a chevron, a stepper or a handle that has a side.
    """
    st.title(T.get("nav_settings", "Widgets"))
    st.caption("preview · every control class, one of each")

    # --- Alerts: ~25 call sites across the four views ----------------------
    st.subheader("Alerts")
    st.success(T.get("template_saved", "Saved."))
    st.info(T.get("no_leads_found", "No leads found."))
    st.warning(T.get("geo_no_areas", "No service areas."))
    st.error(T.get("error_approve_pro", "Something went wrong."))

    # --- Expander ----------------------------------------------------------
    st.subheader("Expander")
    with st.expander(T.get("nav_professionals", "Details"), expanded=True):
        st.write(T.get("assign_strip_hint", "Quick assignment to active pros."))

    # --- Form + inputs -----------------------------------------------------
    st.subheader("Form")
    with st.form("preview_form"):
        st.text_input(T.get("input_client", "Client"), key="w_client")
        c1, c2 = st.columns(2)
        c1.date_input(T.get("sch_date", "Date"), key="w_date")
        c2.time_input(T.get("sch_start_hour", "Start"), key="w_time")
        st.number_input(T.get("sch_duration", "Duration"), 15, 120, 60, step=15)
        st.multiselect(
            T.get("nav_professionals", "Professionals"),
            ["אבי שרברב", "מוסא חשמלאי", "Dana Cohen"],
            default=["אבי שרברב"],
            key="w_multi",
        )
        st.slider(T.get("metric_total", "Total"), 0, 100, 40, key="w_slider")
        st.checkbox(T.get("auto_refresh", "Auto-refresh"), key="w_check")
        st.form_submit_button(T.get("action_save", "Save"), type="primary")

    # --- Status pills and chat --------------------------------------------
    st.subheader("Status pills")
    st.markdown(
        " ".join(render_status_pill(s, T) for s in KANBAN_STATUSES),
        unsafe_allow_html=True,
    )

    st.subheader("Chat")
    bubbles = "".join(
        [
            render_chat_bubble("שלום, יש לי נזילה במטבח", "user", NOW, T),
            render_chat_bubble("היי! אשמח לעזור. מה הכתובת?", "bot", NOW, T),
            render_chat_bubble("רחוב הרצל 12, תל אביב", "user", NOW, T),
        ]
    )
    st.markdown(f'<div class="chat-container">{bubbles}</div>', unsafe_allow_html=True)

    # --- Tables: the HTML one and the Glide one ----------------------------
    st.subheader("HTML table")
    st.markdown(
        """
| לקוח | עיר | סטטוס |
|---|---|---|
| דנה כהן | תל אביב | חדש |
| Yossi Levi | חיפה | הוזמן |
""",
        unsafe_allow_html=True,
    )

    st.subheader("Data editor (deliberate LTR island)")
    st.data_editor(
        [
            {"id": "a1b2", "client": "דנה כהן", "city": "תל אביב"},
            {"id": "c3d4", "client": "Yossi Levi", "city": "חיפה"},
        ],
        use_container_width=True,
        key="w_editor",
    )

    st.subheader("Download")
    st.download_button(
        T.get("action_export", "Export CSV"), "a,b\n1,2", "preview.csv", key="w_dl"
    )


if SECTION == "widgets":
    render_widgets()
else:
    render_dashboard()
