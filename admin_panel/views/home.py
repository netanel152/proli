import streamlit as st
import pandas as pd
from bson.objectid import ObjectId
from datetime import datetime
from admin_panel.core.utils import users_collection, leads_collection, messages_collection, send_completion_check_sync
from admin_panel.ui.components import render_chat_bubble, render_kanban_column, render_status_pill, STATUS_COLORS
from admin_panel.core.auth import log_audit, get_current_role
from admin_panel.core.rbac import can_edit, has_permission
import pytz
import os
import sys
from app.core.logger import logger
from app.core.constants import AdminDefaults, Defaults

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

KANBAN_STATUSES = ["new", "contacted", "booked", "completed", "rejected", "closed", "cancelled"]
ALL_STATUSES = ["new", "contacted", "booked", "completed", "rejected", "closed", "cancelled"]


def view_leads_dashboard(T):
    st.title(T["title_dashboard"])
    st.caption(T.get("page_desc_dashboard", "View and manage incoming leads."))

    # Tabs: Kanban | Table | Create
    tab_kanban, tab_table, tab_create = st.tabs([
        T.get("tab_kanban", "Board"),
        T.get("tab_dashboard", "Table"),
        T.get("tab_create_lead", "Create"),
    ])

    # --- Shared Data ---
    all_pros = list(users_collection.find())
    pro_map_id_to_name = {p["_id"]: p.get("business_name", AdminDefaults.UNKNOWN_PRO) for p in all_pros}
    pro_map_name_to_id = {p.get("business_name", AdminDefaults.UNKNOWN_PRO): p["_id"] for p in all_pros}
    pro_names = [p.get("business_name", AdminDefaults.UNKNOWN_PRO) for p in all_pros]
    pro_names.insert(0, T["unknown_pro"])

    @st.cache_data(ttl=30)
    def get_leads_data():
        leads = list(leads_collection.find().sort("created_at", -1).limit(100))
        if not leads:
            return pd.DataFrame()

        data = []
        for l in leads:
            issue_type = l.get("issue_type", l.get("issue", l.get("details", "")))
            appointment_time = l.get("appointment_time", l.get("time_preference", "?"))
            full_address = l.get("full_address", l.get("address", "?"))

            if not l.get("issue_type") and not l.get("issue") and "[DEAL:" in str(l.get("details", "")):
                try:
                    parts = l["details"].split("[DEAL:")[1].split("]")[0].split("|")
                    if len(parts) >= 3:
                        appointment_time = parts[0].strip()
                        full_address = parts[1].strip()
                        issue_type = parts[2].strip()
                except:
                    pass

            display_details = l.get("details", "")
            if not display_details and issue_type:
                display_details = f"{issue_type} | {appointment_time} | {full_address}"
            if issue_type != l.get("details", ""):
                display_details = f"{issue_type} | {appointment_time} | {full_address}"

            pro_id = l.get("pro_id")
            pro_name = pro_map_id_to_name.get(pro_id, T["unknown_pro"])

            data.append({
                "id": str(l["_id"]),
                "date": l["created_at"].astimezone(pytz.timezone('Asia/Jerusalem')),
                "client": l["chat_id"].replace("@c.us", ""),
                "professional": pro_name,
                "details_summary": display_details,
                "status": l.get("status", "N/A"),
                "_chat_id": l["chat_id"]
            })
        return pd.DataFrame(data)

    leads_df = get_leads_data()

    # --- Metrics Row (shared across tabs) ---
    total_count = leads_collection.count_documents({})
    new_count = leads_collection.count_documents({"status": "new"})
    booked_count = leads_collection.count_documents({"status": "booked"})
    active_pros = users_collection.count_documents({"is_active": True})

    # ==========================================
    # TAB 1: KANBAN BOARD
    # ==========================================
    with tab_kanban:
        # Metrics
        c1, c2, c3, c4 = st.columns(4)
        c1.metric(T["metric_total"], total_count)
        c2.metric(T["metric_new"], new_count)
        c3.metric(T.get("metric_booked", "Booked"), booked_count)
        c4.metric(T["metric_pros"], active_pros)

        st.markdown("")

        if leads_df.empty:
            st.info(T["no_leads_found"])
        else:
            # Group leads by status
            grouped = {}
            for status in KANBAN_STATUSES:
                mask = leads_df["status"] == status
                grouped[status] = leads_df[mask].to_dict('records')

            # Render Kanban columns
            cols = st.columns(len(KANBAN_STATUSES))
            for i, status in enumerate(KANBAN_STATUSES):
                with cols[i]:
                    column_html = render_kanban_column(status, grouped.get(status, []), T)
                    st.markdown(column_html, unsafe_allow_html=True)

        st.markdown("")

        # Quick actions on selected lead (below Kanban)
        if not leads_df.empty:
            _render_lead_detail_section(leads_df, T, all_pros, pro_map_name_to_id)

    # ==========================================
    # TAB 2: TABLE VIEW
    # ==========================================
    with tab_table:
        # Metrics
        c1, c2, c3, c4 = st.columns(4)
        c1.metric(T["metric_total"], total_count)
        c2.metric(T["metric_new"], new_count)
        c3.metric(T.get("metric_booked", "Booked"), booked_count)
        c4.metric(T["metric_pros"], active_pros)

        st.markdown("")

        if not leads_df.empty:
            # Export CSV
            csv = leads_df.to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                label=T.get("export_csv", "Export CSV"),
                data=csv,
                file_name=f'proli_leads_{datetime.now().strftime("%Y-%m-%d")}.csv',
                mime='text/csv',
                key="export_leads_csv"
            )

        if leads_df.empty:
            st.info(T["no_leads_found"])
        else:
            st.subheader(T["table_title"])

            status_options = ALL_STATUSES

            if 'original_leads_df' not in st.session_state:
                st.session_state.original_leads_df = leads_df.copy()

            edited_df = st.data_editor(
                leads_df,
                key="leads_editor",
                column_config={
                    "id": None,
                    "_chat_id": None,
                    "date": st.column_config.DatetimeColumn(
                        T["col_date"],
                        format="D MMM YYYY, h:mm a",
                        width="medium",
                        disabled=True
                    ),
                    "client": st.column_config.TextColumn(T["col_client"], width="small", disabled=True),
                    "professional": st.column_config.SelectboxColumn(
                        T["col_pro"],
                        width="medium",
                        options=pro_names,
                        required=False
                    ),
                    "details_summary": st.column_config.TextColumn(T["col_details"], width="large"),
                    "status": st.column_config.SelectboxColumn(
                        T["col_status"],
                        options=status_options,
                        width="small",
                        required=True
                    )
                },
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic"
            )

            # Save changes button
            if can_edit(get_current_role()):
                if st.button(T.get("save_btn", "Save Changes"), type="primary", key="save_dashboard_changes"):
                    changes = st.session_state.leads_editor.get("edited_rows", {})
                    if not changes:
                        st.toast(T.get("no_changes", "No changes."))
                    else:
                        updated_count = 0
                        for row_idx, changed_data in changes.items():
                            lead_id = st.session_state.original_leads_df.iloc[row_idx]["id"]
                            update_payload = {}

                            if "status" in changed_data:
                                update_payload["status"] = changed_data["status"]
                            if "details_summary" in changed_data:
                                update_payload["details"] = changed_data["details_summary"]
                                update_payload["issue_type"] = changed_data["details_summary"]
                            if "professional" in changed_data:
                                new_pro_name = changed_data["professional"]
                                if new_pro_name == T["unknown_pro"]:
                                    update_payload["pro_id"] = None
                                else:
                                    update_payload["pro_id"] = pro_map_name_to_id.get(new_pro_name)

                            if update_payload:
                                leads_collection.update_one({"_id": ObjectId(lead_id)}, {"$set": update_payload})
                                log_audit("edit_lead", {"lead_id": lead_id, "changes": update_payload})
                                updated_count += 1

                        st.success(f"{updated_count} {T.get('msg_changes', 'changes saved')}!")
                        st.cache_data.clear()
                        st.rerun()

            st.markdown("")

            # Row selection actions
            selected_rows = st.session_state.leads_editor.get("selection", {}).get("rows", [])
            if selected_rows:
                selected_row_index = selected_rows[0]
                selected_lead = edited_df.iloc[selected_row_index]
                _render_selected_lead_actions(selected_lead, T)
            else:
                st.info(T.get("select_row_prompt", "Select a row to see actions."))

    # ==========================================
    # TAB 3: CREATE LEAD
    # ==========================================
    with tab_create:
        if not can_edit(get_current_role()):
            st.warning(T.get("no_permission_create", "You don't have permission to create leads."))
            return

        st.header(T.get("create_lead_title", "Create a New Lead"))

        with st.form("create_lead_form"):
            c1, c2 = st.columns(2)
            with c1:
                new_phone = st.text_input(T.get("input_client_phone", "Phone (WhatsApp)"), placeholder="972501234567")
                new_status = st.selectbox(
                    T.get("input_status", "Initial Status"),
                    options=["new", "contacted", "booked", "closed"],
                    index=0
                )
            with c2:
                pro_names_create = [p.get("business_name", AdminDefaults.UNKNOWN_PRO) for p in all_pros]
                pro_names_create.insert(0, T["unknown_pro"])
                selected_pro_name = st.selectbox(T.get("input_pro", "Assign Professional"), options=pro_names_create)

            new_details = st.text_area(T.get("input_issue", "Issue / Details"), placeholder="e.g., Leaking faucet in the kitchen...")

            submitted = st.form_submit_button(T.get("submit_create_lead", "Create Lead"), type="primary")

            if submitted:
                if not new_phone:
                    st.error(T.get("error_phone_required", "Phone number is required."))
                else:
                    try:
                        clean_phone = ''.join(filter(str.isdigit, new_phone))
                        chat_id = f"{clean_phone}@c.us"

                        assigned_pro_id = None
                        if selected_pro_name != T["unknown_pro"]:
                            assigned_pro_id = pro_map_name_to_id.get(selected_pro_name)

                        new_lead_doc = {
                            "chat_id": chat_id,
                            "details": new_details,
                            "issue_type": new_details,
                            "status": new_status,
                            "pro_id": assigned_pro_id,
                            "created_at": datetime.now(pytz.utc),
                            "full_address": AdminDefaults.MANUAL_LABEL,
                            "appointment_time": AdminDefaults.MANUAL_LABEL,
                            "source": AdminDefaults.MANUAL_SOURCE
                        }

                        leads_collection.insert_one(new_lead_doc)
                        log_audit("create_lead", {"chat_id": chat_id, "status": new_status})
                        logger.info(f"Admin manually created lead for {chat_id}")
                        st.success(T.get("create_lead_success", "Lead created successfully!"))
                        st.cache_data.clear()
                    except Exception as e:
                        st.error(f"Error creating lead: {e}")


def _render_lead_detail_section(leads_df, T, all_pros, pro_map_name_to_id):
    """Render the lead detail / quick-action section below the Kanban board."""
    st.markdown("---")
    st.subheader(T.get("lead_quick_actions", "Lead Details"))

    lead_options = [f"{row['client']} — {row['status']} — {str(row.get('details_summary',''))[:40]}" for _, row in leads_df.iterrows()]
    selected_idx = st.selectbox(
        T.get("select_lead", "Select Lead"),
        range(len(lead_options)),
        format_func=lambda i: lead_options[i],
        key="kanban_lead_select"
    )

    if selected_idx is not None:
        selected_lead = leads_df.iloc[selected_idx]
        _render_selected_lead_actions(selected_lead, T)


def _render_selected_lead_actions(selected_lead, T):
    """Render actions for a selected lead (shared between Kanban and Table views)."""
    c1, c2 = st.columns([1, 3])

    with c1:
        # Status pill
        status = selected_lead.get("status", "new")
        pill_html = render_status_pill(status, T)
        st.markdown(pill_html, unsafe_allow_html=True)
        st.markdown("")

        # Delete action
        if has_permission(get_current_role(), "delete_leads"):
            if st.button(T.get('delete_btn', 'Delete Lead'), key=f"delete_{selected_lead['id']}", type="secondary"):
                st.session_state[f"confirm_delete_{selected_lead['id']}"] = True

        # Manual customer check
        if can_edit(get_current_role()):
            if st.button(T.get("check_customer_btn", "Customer Check"), key=f"check_{selected_lead['id']}"):
                try:
                    send_completion_check_sync(selected_lead['id'])
                    log_audit("send_completion_check", {"lead_id": selected_lead['id']})
                    st.success(T.get("check_sent", "Check sent!"))
                except Exception as e:
                    st.error(f"Failed: {e}")

        # Delete confirmation
        if st.session_state.get(f"confirm_delete_{selected_lead['id']}"):
            st.warning(T.get("confirm_delete", "Are you sure?"))
            cy, cn = st.columns(2)
            if cy.button(T["confirm_yes"], key=f"yes_del_{selected_lead['id']}"):
                leads_collection.delete_one({"_id": ObjectId(selected_lead['id'])})
                log_audit("delete_lead", {"lead_id": selected_lead['id']})
                logger.info(f"Admin deleted lead {selected_lead['id']}")
                st.success(T["success_delete"])
                del st.session_state[f"confirm_delete_{selected_lead['id']}"]
                st.cache_data.clear()
                st.rerun()
            if cn.button(T["confirm_no"], key=f"no_del_{selected_lead['id']}"):
                del st.session_state[f"confirm_delete_{selected_lead['id']}"]
                st.rerun()

    with c2:
        # Chat History
        chat_id = selected_lead["_chat_id"]
        msgs = list(messages_collection.find({"chat_id": chat_id}).sort("timestamp", 1))

        with st.expander(f"{T['chat_history']} ({len(msgs)})", expanded=True):
            if msgs:
                with st.container(height=350, border=False):
                    html_chat = "".join(render_chat_bubble(m['text'], m['role'], m.get('timestamp'), T) for m in msgs)
                    st.markdown(f'<div class="chat-container">{html_chat}</div>', unsafe_allow_html=True)
            else:
                st.info(T["no_chat"])
