import streamlit as st
import pandas as pd
from datetime import datetime, time
from admin_panel.core.utils import settings_collection
from admin_panel.core.audit_queries import (
    DEFAULT_PAGE_SIZE,
    PAGE_SIZE_OPTIONS,
    build_audit_filter,
    clamp_page,
    count_audit_entries,
    fetch_audit_page,
    format_audit_rows,
    page_count,
)
from admin_panel.core.auth import (
    log_audit,
    get_current_role,
    get_current_username,
    create_admin,
    delete_admin,
    list_admins,
    update_admin_role,
)
from admin_panel.core.rbac import (
    can_edit_settings,
    can_manage_admins,
    can_view_audit_log,
    AdminRole,
)

from admin_panel.core.auth import _audit_col
from app.core.config import settings as app_settings
import pytz

IL_TZ = pytz.timezone("Asia/Jerusalem")


def _step_audit_page(delta):
    """Move the audit viewer's page. Runs as a button `on_click`, i.e.
    before the script body, so the clamp below sees the new value."""
    st.session_state["audit_page"] = st.session_state.get("audit_page", 1) + delta


def _reset_audit_page_on_filter_change(signature):
    """Return to page 1 whenever the result set itself changes.

    `clamp_page` only rescues *overflow*. Without this, narrowing a filter
    while on page 5 lands the operator on page 5 of the new results — rows
    201-250 of what they just searched for — and the thing they were
    looking for is simply not on screen, which reads as "it was never
    logged".

    Deliberately a signature comparison in the script body rather than
    `on_change` hooks on the six widgets. Streamlit runs widget callbacks
    in frontend widget-id order, which this code does not control, so
    editing a filter and clicking Next in one interaction could run the
    reset *before* the step and leave the operator on page 2 of a search
    they just typed. Comparing values instead is order-independent: a
    changed filter always wins over a same-run page step.
    """
    if st.session_state.get("audit_filter_sig") != signature:
        st.session_state["audit_filter_sig"] = signature
        st.session_state["audit_page"] = 1


def view_system_settings(T):
    st.title(T["settings_title"])
    st.caption(T.get("page_desc_settings", "Configure system-wide settings."))

    role = get_current_role()

    # --- Tabs ---
    tabs = [
        T.get("tab_scheduler", "Scheduler"),
        T.get("tab_safety", "Safety"),
    ]
    if can_manage_admins(role):
        tabs.append(T.get("tab_admins", "Admin Users"))
    if can_view_audit_log(role):
        tabs.append(T.get("tab_audit", "Audit Log"))

    tab_objects = st.tabs(tabs)
    tab_idx = 0

    # --- TAB: Scheduler Settings ---
    with tab_objects[tab_idx]:
        tab_idx += 1
        st.subheader(T.get("scheduler_title", "Auto-Scheduler"))

        config = settings_collection.find_one({"_id": "scheduler_config"})
        if not config:
            config = {
                "_id": "scheduler_config",
                "run_time": "08:00",
                "is_active": True,
                "trigger_now": False,
            }
            settings_collection.insert_one(config)

        with st.container(border=True):
            c1, c2, c3 = st.columns(3)

            is_active = c1.checkbox(
                T.get("sch_active", "Active"), value=config.get("is_active", True)
            )

            last_run_val = config.get("last_run_date")
            if isinstance(last_run_val, datetime):
                if last_run_val.tzinfo is None:
                    last_run_val = pytz.utc.localize(last_run_val)
                last_run_str = last_run_val.astimezone(IL_TZ).strftime("%Y-%m-%d %H:%M")
            else:
                last_run_str = T.get("never", "Never")
            c1.caption(f"{T.get('last_run', 'Last Run')}: {last_run_str}")

            try:
                t_obj = datetime.strptime(
                    config.get("run_time", "08:00"), "%H:%M"
                ).time()
            except (ValueError, TypeError):
                t_obj = time(8, 0)

            new_time = c2.time_input(
                T.get("sch_run_time", "Run Time (UTC)"), value=t_obj
            )

            if can_edit_settings(role):
                if c3.button(T.get("sch_run_now", "Run Now"), use_container_width=True):
                    settings_collection.update_one(
                        {"_id": "scheduler_config"}, {"$set": {"trigger_now": True}}
                    )
                    log_audit("trigger_scheduler")
                    st.toast(T.get("sch_triggered", "Triggered!"))

                st.markdown("")
                if st.button(T.get("sch_save_config", "Save Config"), type="primary"):
                    settings_collection.update_one(
                        {"_id": "scheduler_config"},
                        {
                            "$set": {
                                "is_active": is_active,
                                "run_time": new_time.strftime("%H:%M"),
                            }
                        },
                        upsert=True,
                    )
                    log_audit(
                        "edit_scheduler_config",
                        {
                            "is_active": is_active,
                            "run_time": new_time.strftime("%H:%M"),
                        },
                    )
                    st.success(T["success_save"])
                    st.rerun()

    # --- TAB: Safety & Monitoring ---
    with tab_objects[tab_idx]:
        tab_idx += 1
        st.subheader(T.get("safety_title", "Safety & Monitoring"))
        st.caption(
            T.get(
                "safety_desc", "Control the automated recovery and monitoring agents."
            )
        )

        config = settings_collection.find_one({"_id": "scheduler_config"}) or {}

        with st.container(border=True):
            col_sos1, col_sos2, col_sos3 = st.columns(3)

            mon_active = col_sos1.checkbox(
                T.get("stale_mon_active", "Stale Job Monitor"),
                value=config.get("stale_monitor_active", True),
                help="Checks for booked jobs that haven't been completed.",
            )

            healer_active = col_sos2.checkbox(
                T.get("sos_healer_active", "SOS Auto-Healer"),
                value=config.get("sos_healer_active", True),
                help="Automatically reassigns leads that Pros ignored.",
            )

            reporter_active = col_sos3.checkbox(
                T.get("sos_reporter_active", "SOS Admin Reporter"),
                value=config.get("sos_reporter_active", True),
                help="Sends batched reports of stuck leads to Admin WhatsApp.",
            )

            if can_edit_settings(role):
                st.markdown("")
                if st.button(
                    T.get("save_safety", "Save Safety Settings"), type="primary"
                ):
                    settings_collection.update_one(
                        {"_id": "scheduler_config"},
                        {
                            "$set": {
                                "stale_monitor_active": mon_active,
                                "sos_healer_active": healer_active,
                                "sos_reporter_active": reporter_active,
                            }
                        },
                        upsert=True,
                    )
                    log_audit(
                        "edit_safety_settings",
                        {
                            "stale_monitor": mon_active,
                            "sos_healer": healer_active,
                            "sos_reporter": reporter_active,
                        },
                    )
                    st.success(T["success_save"])
                    st.rerun()

    # --- TAB: Admin Users ---
    if can_manage_admins(role):
        with tab_objects[tab_idx]:
            tab_idx += 1
            st.subheader(T.get("admin_users_title", "Admin Users"))

            admins = list_admins()

            if admins:
                for admin in admins:
                    with st.container(border=True):
                        c1, c2, c3 = st.columns([2, 2, 1])
                        c1.markdown(f"**{admin['username']}**")
                        c2.markdown(f"`{admin.get('role', 'unknown')}`")

                        if admin["username"] != get_current_username():
                            new_role = c1.selectbox(
                                T.get("admin_role", "Role"),
                                options=[r.value for r in AdminRole],
                                index=[r.value for r in AdminRole].index(
                                    admin.get("role", "viewer")
                                ),
                                key=f"role_{admin['username']}",
                            )
                            if c2.button(
                                T.get("admin_update_role", "Update"),
                                key=f"update_{admin['username']}",
                                use_container_width=True,
                            ):
                                update_admin_role(admin["username"], new_role)
                                log_audit(
                                    "update_admin_role",
                                    {"target": admin["username"], "new_role": new_role},
                                )
                                st.rerun()

                            if c3.button(
                                T.get("admin_delete", "Delete"),
                                key=f"deladmin_{admin['username']}",
                                type="secondary",
                                use_container_width=True,
                            ):
                                delete_admin(admin["username"])
                                log_audit("delete_admin", {"target": admin["username"]})
                                st.rerun()
                        else:
                            c3.caption(T.get("admin_you", "(you)"))
            else:
                st.info(T.get("no_db_admins", "No database admins yet."))

            st.markdown("")
            st.markdown(f"##### {T.get('add_new_admin', 'Add New Admin')}")
            with st.form("add_admin_form"):
                c1, c2 = st.columns(2)
                with c1:
                    new_username = st.text_input(T.get("admin_username", "Username"))
                    new_password = st.text_input(
                        T.get("admin_password", "Password"), type="password"
                    )
                with c2:
                    new_role = st.selectbox(
                        T.get("admin_role", "Role"),
                        options=[r.value for r in AdminRole],
                        index=1,
                    )

                if st.form_submit_button(
                    T.get("admin_create_btn", "Create Admin"), type="primary"
                ):
                    if not new_username or not new_password:
                        st.error(
                            T.get(
                                "admin_required_fields",
                                "Username and password are required.",
                            )
                        )
                    elif len(new_password) < 6:
                        st.error(
                            T.get(
                                "admin_password_short",
                                "Password must be at least 6 characters.",
                            )
                        )
                    else:
                        if create_admin(new_username, new_password, new_role):
                            log_audit(
                                "create_admin",
                                {"target": new_username, "role": new_role},
                            )
                            st.success(
                                T.get(
                                    "admin_created", "Admin '{name}' created!"
                                ).replace("{name}", new_username)
                            )
                            st.rerun()
                        else:
                            st.error(
                                T.get(
                                    "admin_exists", "Username '{name}' already exists."
                                ).replace("{name}", new_username)
                            )

    # --- TAB: Audit Log ---
    if can_view_audit_log(role):
        with tab_objects[tab_idx]:
            tab_idx += 1
            st.subheader(T.get("audit_log_title", "Audit Log"))

            # PRO-142: this tab used to be a hardcoded `.limit(200)`, so on a
            # busy panel the accountability trail simply stopped existing past
            # the most recent 200 actions — with nothing on screen saying so.
            #
            # The tab's first sibling shows a "Run Time (UTC)" field, so the
            # clock these dates and timestamps mean has to be stated rather
            # than assumed. It is Israel time on both sides: the table
            # renders in it and the date filters cut their days on it.
            st.caption(
                T.get("audit_tz_note", "All times are Israel time (Asia/Jerusalem).")
            )

            f_subject, f_user, f_action = st.columns([2, 1, 1])
            with f_subject:
                flt_subject = st.text_input(
                    T.get("audit_filter_subject", "ID or name"),
                    key="audit_flt_subject",
                    placeholder=T.get(
                        "audit_filter_subject_ph", "lead / pro id, username"
                    ),
                )
            with f_user:
                flt_user = st.text_input(
                    T.get("audit_filter_user", "User"),
                    key="audit_flt_user",
                    placeholder=T.get("audit_filter_user_ph", "admin username"),
                )
            with f_action:
                flt_action = st.text_input(
                    T.get("audit_filter_action", "Action"),
                    key="audit_flt_action",
                    placeholder=T.get("audit_filter_action_ph", "e.g. delete_lead"),
                )

            f_since, f_until, c_size, _f_pad = st.columns([1, 1, 1, 1])
            with f_since:
                flt_since = st.date_input(
                    T.get("audit_filter_since", "From"),
                    value=None,
                    key="audit_flt_since",
                )
            with f_until:
                flt_until = st.date_input(
                    T.get("audit_filter_until", "To"),
                    value=None,
                    key="audit_flt_until",
                )
            with c_size:
                page_size = st.selectbox(
                    T.get("audit_page_size", "Rows per page"),
                    PAGE_SIZE_OPTIONS,
                    index=PAGE_SIZE_OPTIONS.index(DEFAULT_PAGE_SIZE),
                    key="audit_page_size",
                )

            _reset_audit_page_on_filter_change(
                (flt_subject, flt_user, flt_action, flt_since, flt_until, page_size)
            )

            if flt_since and flt_until and flt_since > flt_until:
                # Otherwise this renders as "no entries match", which is
                # indistinguishable from the action genuinely not being
                # logged — the one wrong conclusion this tab must not invite.
                st.warning(
                    T.get(
                        "audit_dates_inverted",
                        "The From date is after the To date, so nothing can match.",
                    )
                )

            query = build_audit_filter(
                user=flt_user,
                action=flt_action,
                since=flt_since,
                until=flt_until,
                subject=flt_subject,
            )
            total = count_audit_entries(_audit_col, query)

            # The page number is kept in session state rather than in a
            # number_input with a dynamic max_value: filtering down to three
            # results while sitting on page seven is an ordinary sequence of
            # clicks, and a widget whose stored value exceeds its own
            # max_value raises instead of correcting itself. Clamping every
            # run keeps that impossible.
            st.session_state.setdefault("audit_page", 1)
            pages = page_count(total, page_size)

            # Clamp *before* the buttons render, and step the page from an
            # `on_click` callback rather than from the button's return value.
            # Callbacks run before the script body, so the page number, the
            # buttons' disabled state and the rows below all agree within one
            # run. Reading the button's return value instead evaluates
            # `disabled` against the pre-click value: after stepping off page
            # 1, Prev would render greyed out for a full run and the operator
            # could not go back without first clicking Next again.
            page = clamp_page(st.session_state["audit_page"], total, page_size)
            st.session_state["audit_page"] = page

            c_prev, c_next, _c_pad = st.columns([1, 1, 4])
            with c_prev:
                # Plain words, no chevrons. `‹`/`›` are mirrored by the bidi
                # algorithm so they happen to point correctly in Hebrew, but
                # only while the button inherits RTL — nothing pins the
                # direction of `.stButton` — and they are quotation marks, so
                # a screen reader announces them as such before the label.
                # The buttons' order already carries the direction.
                st.button(
                    T.get("audit_prev_page", "Previous"),
                    disabled=page <= 1,
                    on_click=_step_audit_page,
                    args=(-1,),
                    use_container_width=True,
                    key="audit_prev_btn",
                )
            with c_next:
                st.button(
                    T.get("audit_next_page", "Next"),
                    disabled=page >= pages,
                    on_click=_step_audit_page,
                    args=(1,),
                    use_container_width=True,
                    key="audit_next_btn",
                )

            skip = (page - 1) * page_size

            entries = fetch_audit_page(
                _audit_col, limit=page_size, skip=skip, query=query
            )

            # The count gets its own line rather than the last column of a
            # control row: the whole premise of PRO-142 is that the old
            # truncation was invisible, so how much history exists is the one
            # thing this tab must not whisper. Guarded on `entries` too — a
            # count and a fetch are separate queries, and entries deleted
            # between them would otherwise render a backwards range.
            if total and entries:
                showing = (
                    T.get("audit_showing", "Showing {a}-{b} of {n}")
                    .replace("{a}", str(skip + 1))
                    .replace("{b}", str(skip + len(entries)))
                    .replace("{n}", str(total))
                )
                if pages > 1:
                    showing += " · " + T.get("audit_page_of", "page {p}/{q}").replace(
                        "{p}", str(page)
                    ).replace("{q}", str(pages))
                st.caption(showing)

            if not entries:
                # A filtered-to-nothing result and a genuinely empty log are
                # different facts, and an operator checking whether an action
                # was recorded must not read the first as the second.
                if query:
                    st.info(
                        T.get(
                            "audit_no_matches",
                            "No entries match these filters.",
                        )
                    )
                else:
                    st.info(T.get("no_audit_entries", "No audit log entries yet."))
            else:
                df = pd.DataFrame(format_audit_rows(entries, IL_TZ))
                st.dataframe(
                    df,
                    column_config={
                        "time": st.column_config.TextColumn(
                            T.get("audit_col_time", "Time"), width="medium"
                        ),
                        "user": st.column_config.TextColumn(
                            T.get("audit_col_user", "User"), width="small"
                        ),
                        "action": st.column_config.TextColumn(
                            T.get("audit_col_action", "Action"), width="small"
                        ),
                        "details": st.column_config.TextColumn(
                            T.get("audit_col_details", "Details"), width="large"
                        ),
                    },
                    hide_index=True,
                    use_container_width=True,
                )
