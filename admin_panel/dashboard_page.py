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

# Allow imports from the root directory to access the 'app' module
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app.services.workflow import send_customer_completion_check

def view_leads_dashboard(T):
    st.title(T["title_dashboard"])
    st.caption(T.get("page_desc_dashboard", "View and manage incoming leads, update their status, and review conversation history."))
    
    # --- Metrics ---
    c1, c2, c3, c_ref = st.columns([2, 2, 2, 1])
    c1.metric(T["metric_total"], leads_collection.count_documents({}))
    c2.metric(T["metric_new"], leads_collection.count_documents({"status": "new"}))
    c3.metric(T["metric_pros"], users_collection.count_documents({"is_active": True}))
    if c_ref.button(T.get("refresh_btn", "Refresh"), width='stretch'):
        st.rerun()

    st.markdown("---")
    
    # --- Data Loading ---
    @st.cache_data(ttl=30)
    def get_leads_data():
        leads = list(leads_collection.find().sort("created_at", -1).limit(100))
        if not leads:
            return pd.DataFrame()
        
        pro_cache = {p["_id"]: p["business_name"] for p in users_collection.find()}

        data = []
        for l in leads:
            # Format Details Summary
            issue = l.get("issue_type", l.get("details", ""))
            time = l.get("appointment_time", "?")
            addr = l.get("full_address", "?")
            
            # Smart parsing fallback if enriched fields are missing but [DEAL] tag exists in details
            if not l.get("issue_type") and "[DEAL:" in str(l.get("details", "")):
                 try:
                     parts = l["details"].split("[DEAL:")[1].split("]")[0].split("|")
                     if len(parts) >= 3:
                         time = parts[0].strip()
                         addr = parts[1].strip()
                         issue = parts[2].strip()
                 except:
                     pass
            
            # Fallback if enrichment failed
            if not l.get("issue_type") and not issue:
                display_details = l["details"]
            else:
                display_details = f"🔧 {issue} | 🕒 {time} | 📍 {addr}"

            data.append({
                "id": str(l["_id"]),
                "date": l["created_at"].astimezone(pytz.timezone('Asia/Jerusalem')),
                "client": l["chat_id"].replace("@c.us", ""),
                "professional": pro_cache.get(l.get("pro_id"), T["unknown_pro"]),
                "details_summary": display_details,
                "status": l.get("status", "N/A"),
                "_chat_id": l["chat_id"] 
            })
        return pd.DataFrame(data)

    leads_df = get_leads_data()

    if not leads_df.empty:
        csv = leads_df.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            label="📥 Export Leads to CSV",
            data=csv,
            file_name=f'fixi_leads_{datetime.now().strftime("%Y-%m-%d")}.csv',
            mime='text/csv',
            key="export_leads_csv"
        )
    
    if leads_df.empty:
        st.info(T["no_leads_found"])
        st.stop()

    # --- Smart Table (Data Editor) ---
    st.subheader(T["table_title"])
    
    status_options = ["new", "contacted", "booked", "closed", "completed", "cancelled"]
    
    # Store original df in session state to compare against edits
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
            ),
            "client": st.column_config.TextColumn(T["col_client"], width="small"),
            "professional": st.column_config.TextColumn(T["col_pro"], width="small"),
            "details_summary": st.column_config.TextColumn(T["col_details"], width="large", help="Summary of the request"),
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

    # --- Actions for Edited and Selected Data ---
    
    # 1. Save Changes
    if st.button(T.get("save_btn", "Save Changes"), type="primary"):
        changes = st.session_state.leads_editor.get("edited_rows", {})
        if not changes:
            st.toast("No changes to save.")
        else:
            updated_count = 0
            for row_idx, changed_data in changes.items():
                lead_id = st.session_state.original_leads_df.iloc[row_idx]["id"]
                
                # Sanitize changed data
                update_payload = {}
                if "status" in changed_data:
                    update_payload["status"] = changed_data["status"]
                
                if update_payload:
                    leads_collection.update_one({"_id": ObjectId(lead_id)}, {"$set": update_payload})
                    updated_count += 1
            
            st.success(f"{updated_count} leads updated successfully!")
            st.cache_data.clear() 
            st.rerun()

    st.markdown("---")

    # 2. Actions on Selected Row
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
            if st.button("📱 בדיקה מול לקוח", key=f"check_{selected_lead['id']}", help="Send a WhatsApp message to the customer to verify job completion.", width='stretch'):
                try:
                    asyncio.run(send_customer_completion_check(selected_lead['id'], triggered_by="admin"))
                    st.success("✅ Sent completion check to customer!")
                except Exception as e:
                    st.error(f"Failed to send check: {e}")

            if st.session_state.get(f"confirm_delete_{selected_lead['id']}"):
                st.warning(T.get("confirm_delete", "Are you sure?"))
                cy, cn = st.columns(2)
                if cy.button(T["confirm_yes"], key=f"yes_del_{selected_lead['id']}"):
                    leads_collection.delete_one({"_id": ObjectId(selected_lead['id'])})
                    st.success(T["success_delete"])
                    del st.session_state[f"confirm_delete_{selected_lead['id']}"]
                    st.cache_data.clear()
                    st.rerun()
                if cn.button(T["confirm_no"], key=f"no_del_{selected_lead['id']}"):
                    del st.session_state[f"confirm_delete_{selected_lead['id']}"]
                    st.rerun()
        
        with c2:
            # Chat History Action
            chat_id = selected_lead["_chat_id"]
            # Use messages_collection to fetch history
            msgs = list(messages_collection.find({"chat_id": chat_id}).sort("timestamp", 1))
            
            with st.expander(f"💬 {T['chat_history']} ({len(msgs)})", expanded=True):
                if msgs:
                    # Scrollable container for chat bubbles
                    with st.container(height=400, border=False):
                        html_chat = "".join(render_chat_bubble(m['text'], m['role'], m.get('timestamp'), T) for m in msgs)
                        st.markdown(f'<div class="chat-container">{html_chat}</div>', unsafe_allow_html=True)
                else:
                    st.info(T["no_chat"])
    else:
        st.info(T.get("select_row_prompt", "Select a row in the table to see actions like chat history or delete."))