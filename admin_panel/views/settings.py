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

# Sync access to audit_log collection
from pymongo import MongoClient
from app.core.config import settings as app_settings
import certifi

_ca = certifi.where() if "+srv" in app_settings.MONGO_URI else None
_kwargs = {"tlsCAFile": _ca} if _ca else {}
_sync_client = MongoClient(app_settings.MONGO_URI, **_kwargs)
_audit_col = _sync_client.proli_db.audit_log


def view_system_settings(T):
    st.title(T["settings_title"])
    st.caption(T.get("page_desc_settings", "Configure system-wide settings, such as the auto-scheduler."))

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
        st.header(T.get("scheduler_title", "⏰ Auto-Scheduler"))

        config = settings_collection.find_one({"_id": "scheduler_config"})
        if not config:
            config = {"_id": "scheduler_config", "run_time": "08:00", "is_active": True, "trigger_now": False}
            settings_collection.insert_one(config)

        with st.container(border=True):
            c1, c2, c3 = st.columns(3)

            is_active = c1.checkbox(T.get("sch_active", "Active"), value=config.get("is_active", True))

            last_run_val = config.get("last_run_date")
            last_run_str = last_run_val.strftime("%Y-%m-%d %H:%M") if isinstance(last_run_val, datetime) else T.get("never", "Never")
            c1.caption(f"{T.get('last_run', 'Last Run')}: {last_run_str}")

            try:
                t_obj = datetime.strptime(config.get("run_time", "08:00"), "%H:%M").time()
            except (ValueError, TypeError):
                t_obj = time(8, 0)

            new_time = c2.time_input(T.get("sch_run_time", "Run Time (UTC)"), value=t_obj)

            if can_edit_settings(role):
                if c3.button(T.get("sch_run_now", "Run Now")):
                    settings_collection.update_one(
                        {"_id": "scheduler_config"},
                        {"$set": {"trigger_now": True}}
                    )
                    log_audit("trigger_scheduler")
                    st.toast(T.get("sch_triggered", "Triggered! The scheduler will run on the next cycle."), icon="✅")

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
        st.header(T.get("safety_title", "🛡️ Safety & Monitoring"))
        st.caption(T.get("safety_desc", "Control the automated recovery and monitoring agents."))

        config = settings_collection.find_one({"_id": "scheduler_config"}) or {}

        with st.container(border=True):
            st.markdown("##### " + T.get("sos_controls", "SOS Controls"))

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
            st.header("Admin Users")

            admins = list_admins()

            if admins:
                st.markdown("#### Current Admins")
                for admin in admins:
                    with st.container(border=True):
                        c1, c2, c3 = st.columns([2, 2, 1])
                        c1.markdown(f"**{admin['username']}**")
                        c2.markdown(f"Role: `{admin.get('role', 'unknown')}`")

                        if admin["username"] != get_current_username():
                            new_role = c1.selectbox(
                                "Change role",
                                options=[r.value for r in AdminRole],
                                index=[r.value for r in AdminRole].index(admin.get("role", "viewer")),
                                key=f"role_{admin['username']}",
                            )
                            if c2.button("Update Role", key=f"update_{admin['username']}"):
                                update_admin_role(admin["username"], new_role)
                                log_audit("update_admin_role", {"target": admin["username"], "new_role": new_role})
                                st.rerun()

                            if c3.button("Delete", key=f"deladmin_{admin['username']}", type="secondary"):
                                delete_admin(admin["username"])
                                log_audit("delete_admin", {"target": admin["username"]})
                                st.rerun()
                        else:
                            c3.caption("(you)")
            else:
                st.info("No database admins yet. Using env var authentication.")

            st.markdown("---")
            st.markdown("#### Add New Admin")
            with st.form("add_admin_form"):
                new_username = st.text_input("Username")
                new_password = st.text_input("Password", type="password")
                new_role = st.selectbox("Role", options=[r.value for r in AdminRole], index=1)
                if st.form_submit_button("Create Admin", type="primary"):
                    if not new_username or not new_password:
                        st.error("Username and password are required.")
                    elif len(new_password) < 6:
                        st.error("Password must be at least 6 characters.")
                    else:
                        if create_admin(new_username, new_password, new_role):
                            log_audit("create_admin", {"target": new_username, "role": new_role})
                            st.success(f"Admin '{new_username}' created!")
                            st.rerun()
                        else:
                            st.error(f"Username '{new_username}' already exists.")

    # --- TAB: Audit Log ---
    if can_view_audit_log(role):
        with tab_objects[tab_idx]:
            tab_idx += 1
            st.header("Audit Log")

            logs = list(_audit_col.find().sort("timestamp", -1).limit(200))

            if not logs:
                st.info("No audit log entries yet.")
            else:
                for entry in logs:
                    ts = entry.get("timestamp", "")
                    if isinstance(ts, datetime):
                        ts = ts.strftime("%Y-%m-%d %H:%M:%S")
                    user = entry.get("admin_user", "?")
                    action = entry.get("action", "?")
                    details = entry.get("details", {})
                    detail_str = ", ".join(f"{k}={v}" for k, v in details.items()) if details else ""

                    st.markdown(
                        f"`{ts}` **{user}** — {action}" + (f" ({detail_str})" if detail_str else "")
                    )
