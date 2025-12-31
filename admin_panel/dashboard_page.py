import streamlit as st
import pandas as pd
from bson.objectid import ObjectId
from datetime import datetime
from admin_panel.utils import users_collection, leads_collection, messages_collection
from admin_panel.components import render_chat_bubble
import pytz
import asyncio
import os
import sys
from app.core.logger import logger

# Allow imports from the root directory to access the 'app' module
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app.services.workflow import send_customer_completion_check

def view_leads_dashboard(T):
    st.title(T["title_dashboard"])
    
    # Tabs for better organization
    tab_dash, tab_create = st.tabs([
        T.get("tab_dashboard", "📋 Dashboard"),
        T.get("tab_create_lead", "➕ Create New Lead")
    ])

    # --- TAB 1: DASHBOARD ---
    with tab_dash:
        st.caption(T.get("page_desc_dashboard", "View and manage incoming leads."))

        # Metrics
        c1, c2, c3, c_ref = st.columns([2, 2, 2, 1])
        c1.metric(T["metric_total"], leads_collection.count_documents({}))
        c2.metric(T["metric_new"], leads_collection.count_documents({"status": "new"}))
        c3.metric(T["metric_pros"], users_collection.count_documents({"is_active": True}))
        if c_ref.button(T.get("refresh_btn", "Refresh"), width='stretch', key="refresh_dashboard"):
            st.rerun()

        st.markdown("---")

        # Helpers for Professionals
        all_pros = list(users_collection.find())
        pro_map_id_to_name = {p["_id"]: p.get("business_name", "Unknown") for p in all_pros}
        pro_map_name_to_id = {p.get("business_name", "Unknown"): p["_id"] for p in all_pros}
        pro_names = [p.get("business_name", "Unknown") for p in all_pros]
        pro_names.insert(0, T["unknown_pro"]) # Add 'Unassigned' option

        # Data Loading
        @st.cache_data(ttl=30)
        def get_leads_data():
            leads = list(leads_collection.find().sort("created_at", -1).limit(100))
            if not leads:
                return pd.DataFrame()
            
            data = []
            for l in leads:
                # Format Details Summary
                # Prioritize new keys: issue_type, appointment_time, full_address
                # Fallback to old keys: issue, time_preference, address
                
                issue_type = l.get("issue_type", l.get("issue", l.get("details", "")))
                appointment_time = l.get("appointment_time", l.get("time_preference", "?"))
                full_address = l.get("full_address", l.get("address", "?"))
                
                # Smart parsing fallback for legacy data if still needed
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
                
                # If we have clean fields, prefer constructing a clean summary
                if issue_type != l.get("details", ""):
                     display_details = f"🔧 {issue_type} | 🕒 {appointment_time} | 📍 {full_address}"

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

        if not leads_df.empty:
            csv = leads_df.to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                label="📥 Export CSV",
                data=csv,
                file_name=f'fixi_leads_{datetime.now().strftime("%Y-%m-%d")}.csv',
                mime='text/csv',
                key="export_leads_csv"
            )
        
        if leads_df.empty:
            st.info(T["no_leads_found"])
        else:
            # --- Smart Table (Data Editor) ---
            st.subheader(T["table_title"])
            
            status_options = ["new", "contacted", "booked", "closed", "completed", "cancelled"]
            
            # Store original df to compare
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
                width='stretch',
                hide_index=True,
                num_rows="dynamic"
            )

            # --- Actions for Edited Data ---
            if st.button(T.get("save_btn", "Save Changes"), type="primary", key="save_dashboard_changes"):
                changes = st.session_state.leads_editor.get("edited_rows", {})
                if not changes:
                    st.toast(T.get("no_changes", "No changes."))
                else:
                    updated_count = 0
                    for row_idx, changed_data in changes.items():
                        lead_id = st.session_state.original_leads_df.iloc[row_idx]["id"]
                        
                        update_payload = {}
                        
                        # Handle Status Change
                        if "status" in changed_data:
                            update_payload["status"] = changed_data["status"]
                        
                        # Handle Details Change
                        if "details_summary" in changed_data:
                            update_payload["details"] = changed_data["details_summary"]
                            # Also update issue_type field if possible
                            update_payload["issue_type"] = changed_data["details_summary"]

                        # Handle Professional Change
                        if "professional" in changed_data:
                            new_pro_name = changed_data["professional"]
                            if new_pro_name == T["unknown_pro"]:
                                update_payload["pro_id"] = None
                            else:
                                update_payload["pro_id"] = pro_map_name_to_id.get(new_pro_name)

                        if update_payload:
                            leads_collection.update_one({"_id": ObjectId(lead_id)}, {"$set": update_payload})
                            updated_count += 1
                    
                    st.success(f"{updated_count} {T.get('msg_changes', 'changes saved')}!")
                    st.cache_data.clear() 
                    st.rerun()

            st.markdown("---")

            # --- Row Actions (Delete / Chat) ---
            selected_rows = st.session_state.leads_editor.get("selection", {}).get("rows", [])
            if selected_rows:
                selected_row_index = selected_rows[0]
                selected_lead = edited_df.iloc[selected_row_index]
                
                st.subheader(f"{T['action_update']}: {selected_lead['client']}")
                
                c1, c2 = st.columns([1,3])
                
                with c1:
                    # Delete Action
                    if st.button(f"🗑️ {T.get('delete_btn', 'Delete Lead')}", key=f"delete_{selected_lead['id']}", width='stretch'):
                         st.session_state[f"confirm_delete_{selected_lead['id']}"] = True

                    # Manual Customer Check Action
                    if st.button("📱 בדיקה מול לקוח", key=f"check_{selected_lead['id']}", help="Send WhatsApp verification", width='stretch'):
                        try:
                            asyncio.run(send_customer_completion_check(selected_lead['id'], triggered_by="admin"))
                            st.success("✅ Check sent!")
                        except Exception as e:
                            st.error(f"Failed: {e}")

                    if st.session_state.get(f"confirm_delete_{selected_lead['id']}"):
                        st.warning(T.get("confirm_delete", "Are you sure?"))
                        cy, cn = st.columns(2)
                        if cy.button(T["confirm_yes"], key=f"yes_del_{selected_lead['id']}"):
                            leads_collection.delete_one({"_id": ObjectId(selected_lead['id'])})
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
                    
                    with st.expander(f"💬 {T['chat_history']} ({len(msgs)})", expanded=True):
                        if msgs:
                            with st.container(height=400, border=False):
                                html_chat = "".join(render_chat_bubble(m['text'], m['role'], m.get('timestamp'), T) for m in msgs)
                                st.markdown(f'<div class="chat-container">{html_chat}</div>', unsafe_allow_html=True)
                        else:
                            st.info(T["no_chat"])
            else:
                st.info(T.get("select_row_prompt", "Select a row to see actions."))

    # --- TAB 2: CREATE LEAD ---
    with tab_create:
        st.header(T.get("create_lead_title", "Create a New Lead"))
        
        with st.form("create_lead_form"):
            c1, c2 = st.columns(2)
            with c1:
                # Phone is critical for chat_id
                new_phone = st.text_input(T.get("input_client_phone", "Phone (WhatsApp)"), placeholder="972501234567")
                new_status = st.selectbox(
                    T.get("input_status", "Initial Status"), 
                    options=["new", "contacted", "booked", "closed"],
                    index=0
                )
            with c2:
                # Pro assignment
                # Use the same pro list from dashboard
                pro_names_create = [p.get("business_name", "Unknown") for p in all_pros]
                pro_names_create.insert(0, T["unknown_pro"])
                
                selected_pro_name = st.selectbox(T.get("input_pro", "Assign Professional"), options=pro_names_create)

            new_details = st.text_area(T.get("input_issue", "Issue / Details"), placeholder="e.g., Leaking faucet in the kitchen...")
            
            submitted = st.form_submit_button(T.get("submit_create_lead", "Create Lead"), type="primary")
            
            if submitted:
                if not new_phone:
                    st.error(T.get("error_phone_required", "Phone number is required."))
                else:
                    # Logic to create lead
                    try:
                        # Normalize phone to chat_id
                        clean_phone = ''.join(filter(str.isdigit, new_phone))
                        chat_id = f"{clean_phone}@c.us"
                        
                        # Resolve Pro ID
                        assigned_pro_id = None
                        if selected_pro_name != T["unknown_pro"]:
                             assigned_pro_id = pro_map_name_to_id.get(selected_pro_name)

                        new_lead_doc = {
                            "chat_id": chat_id,
                            "details": new_details,
                            "issue_type": new_details, # Standardized
                            "status": new_status,
                            "pro_id": assigned_pro_id,
                            "created_at": datetime.now(pytz.utc),
                            "full_address": "Manual", # Standardized
                            "appointment_time": "Manual", # Standardized
                            "source": "manual_admin"
                        }
                        
                        leads_collection.insert_one(new_lead_doc)
                        logger.info(f"Admin manually created lead for {chat_id}")
                        st.success(T.get("create_lead_success", "Lead created successfully!"))
                        st.cache_data.clear() # Clear cache so dashboard updates
                        # Optional: Rerun to show empty form or switch tabs (switching tabs programmatically is hard in Streamlit, so just rerun)
                    except Exception as e:
                        st.error(f"Error creating lead: {e}")
