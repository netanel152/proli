import streamlit as st
from datetime import datetime, time
from admin_panel.core.utils import settings_collection
from admin_panel.core.auth import (
    log_audit, get_current_role, get_current_username,
    create_admin, delete_admin, list_admins, update_admin_role,
)
from admin_panel.core.rbac import (
    can_edit_settings, can_manage_admins, can_view_audit_log, AdminRole,
)

from admin_panel.core.auth import _audit_col
from app.core.config import settings as app_settings
import pytz

IL_TZ = pytz.timezone("Asia/Jerusalem")


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
            config = {"_id": "scheduler_config", "run_time": "08:00", "is_active": True, "trigger_now": False}
            settings_collection.insert_one(config)

        with st.container(border=True):
            c1, c2, c3 = st.columns(3)

            is_active = c1.checkbox(T.get("sch_active", "Active"), value=config.get("is_active", True))

            last_run_val = config.get("last_run_date")
            if isinstance(last_run_val, datetime):
                if last_run_val.tzinfo is None:
                    last_run_val = pytz.utc.localize(last_run_val)
                last_run_str = last_run_val.astimezone(IL_TZ).strftime("%Y-%m-%d %H:%M")
            else:
                last_run_str = T.get("never", "Never")
            c1.caption(f"{T.get('last_run', 'Last Run')}: {last_run_str}")

            try:
                t_obj = datetime.strptime(config.get("run_time", "08:00"), "%H:%M").time()
            except (ValueError, TypeError):
                t_obj = time(8, 0)

            new_time = c2.time_input(T.get("sch_run_time", "Run Time (UTC)"), value=t_obj)

            if can_edit_settings(role):
                if c3.button(T.get("sch_run_now", "Run Now"), use_container_width=True):
                    settings_collection.update_one(
                        {"_id": "scheduler_config"},
                        {"$set": {"trigger_now": True}}
                    )
                    log_audit("trigger_scheduler")
                    st.toast(T.get("sch_triggered", "Triggered!"))

                st.markdown("")
                if st.button(T.get("sch_save_config", "Save Config"), type="primary"):
                    settings_collection.update_one(
                        {"_id": "scheduler_config"},
                        {"$set": {
                            "is_active": is_active,
                            "run_time": new_time.strftime("%H:%M")
                        }},
                        upsert=True
                    )
                    log_audit("edit_scheduler_config", {"is_active": is_active, "run_time": new_time.strftime("%H:%M")})
                    st.success(T["success_save"])
                    st.rerun()

    # --- TAB: Safety & Monitoring ---
    with tab_objects[tab_idx]:
        tab_idx += 1
        st.subheader(T.get("safety_title", "Safety & Monitoring"))
        st.caption(T.get("safety_desc", "Control the automated recovery and monitoring agents."))

        config = settings_collection.find_one({"_id": "scheduler_config"}) or {}

        with st.container(border=True):
            col_sos1, col_sos2, col_sos3 = st.columns(3)

            mon_active = col_sos1.checkbox(
                T.get("stale_mon_active", "Stale Job Monitor"),
                value=config.get("stale_monitor_active", True),
                help="Checks for booked jobs that haven't been completed."
            )

            healer_active = col_sos2.checkbox(
                T.get("sos_healer_active", "SOS Auto-Healer"),
                value=config.get("sos_healer_active", True),
                help="Automatically reassigns leads that Pros ignored."
            )

            reporter_active = col_sos3.checkbox(
                T.get("sos_reporter_active", "SOS Admin Reporter"),
                value=config.get("sos_reporter_active", True),
                help="Sends batched reports of stuck leads to Admin WhatsApp."
            )

            if can_edit_settings(role):
                st.markdown("")
                if st.button(T.get("save_safety", "Save Safety Settings"), type="primary"):
                    settings_collection.update_one(
                        {"_id": "scheduler_config"},
                        {"$set": {
                            "stale_monitor_active": mon_active,
                            "sos_healer_active": healer_active,
                            "sos_reporter_active": reporter_active
                        }},
                        upsert=True
                    )
                    log_audit("edit_safety_settings", {
                        "stale_monitor": mon_active,
                        "sos_healer": healer_active,
                        "sos_reporter": reporter_active,
                    })
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
                                index=[r.value for r in AdminRole].index(admin.get("role", "viewer")),
                                key=f"role_{admin['username']}",
                            )
                            if c2.button(T.get("admin_update_role", "Update"), key=f"update_{admin['username']}", use_container_width=True):
                                update_admin_role(admin["username"], new_role)
                                log_audit("update_admin_role", {"target": admin["username"], "new_role": new_role})
                                st.rerun()

                            if c3.button(T.get("admin_delete", "Delete"), key=f"deladmin_{admin['username']}", type="secondary", use_container_width=True):
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
                    new_password = st.text_input(T.get("admin_password", "Password"), type="password")
                with c2:
                    new_role = st.selectbox(T.get("admin_role", "Role"), options=[r.value for r in AdminRole], index=1)

                if st.form_submit_button(T.get("admin_create_btn", "Create Admin"), type="primary"):
                    if not new_username or not new_password:
                        st.error(T.get("admin_required_fields", "Username and password are required."))
                    elif len(new_password) < 6:
                        st.error(T.get("admin_password_short", "Password must be at least 6 characters."))
                    else:
                        if create_admin(new_username, new_password, new_role):
                            log_audit("create_admin", {"target": new_username, "role": new_role})
                            st.success(T.get("admin_created", "Admin '{name}' created!").replace("{name}", new_username))
                            st.rerun()
                        else:
                            st.error(T.get("admin_exists", "Username '{name}' already exists.").replace("{name}", new_username))

    # --- TAB: Audit Log ---
    if can_view_audit_log(role):
        with tab_objects[tab_idx]:
            tab_idx += 1
            st.subheader(T.get("audit_log_title", "Audit Log"))

            logs = list(_audit_col.find().sort("timestamp", -1).limit(200))

            if not logs:
                st.info(T.get("no_audit_entries", "No audit log entries yet."))
            else:
                # Render as a clean table
                log_data = []
                for entry in logs:
                    ts = entry.get("timestamp", "")
                    if isinstance(ts, datetime):
                        if ts.tzinfo is None:
                            ts = pytz.utc.localize(ts)
                        ts = ts.astimezone(IL_TZ).strftime("%Y-%m-%d %H:%M:%S")
                    details = entry.get("details", {})
                    detail_str = ", ".join(f"{k}={v}" for k, v in details.items()) if details else ""

                    log_data.append({
                        "time": ts,
                        "user": entry.get("admin_user", "?"),
                        "action": entry.get("action", "?"),
                        "details": detail_str,
                    })

                import pandas as pd
                df = pd.DataFrame(log_data)
                st.dataframe(
                    df,
                    column_config={
                        "time": st.column_config.TextColumn(T.get("audit_col_time", "Time"), width="medium"),
                        "user": st.column_config.TextColumn(T.get("audit_col_user", "User"), width="small"),
                        "action": st.column_config.TextColumn(T.get("audit_col_action", "Action"), width="small"),
                        "details": st.column_config.TextColumn(T.get("audit_col_details", "Details"), width="large"),
                    },
                    hide_index=True,
                    use_container_width=True,
                )
