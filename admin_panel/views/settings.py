import streamlit as st
from datetime import datetime, time
from admin_panel.core.utils import settings_collection

def view_system_settings(T):
    st.title(T["settings_title"])
    st.caption(T.get("page_desc_settings", "Configure system-wide settings, such as the auto-scheduler."))

    # --- Scheduler Settings ---
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
        
        if c3.button(T.get("sch_run_now", "Run Now")):
            settings_collection.update_one(
                {"_id": "scheduler_config"},
                {"$set": {"trigger_now": True}}
            )
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
            st.success(T["success_save"])
            st.rerun()

    # --- Safety & Monitoring Settings ---
    st.header(T.get("safety_title", "🛡️ Safety & Monitoring"))
    st.caption(T.get("safety_desc", "Control the automated recovery and monitoring agents."))
    
    with st.container(border=True):
        st.markdown("##### " + T.get("sos_controls", "SOS Controls"))
        
        col_sos1, col_sos2, col_sos3 = st.columns(3)
        
        # 1. Stale Monitor
        mon_active = col_sos1.checkbox(
            T.get("stale_mon_active", "Stale Job Monitor"), 
            value=config.get("stale_monitor_active", True),
            help="Checks for booked jobs that haven't been completed."
        )
        
        # 2. SOS Healer
        healer_active = col_sos2.checkbox(
            T.get("sos_healer_active", "SOS Auto-Healer"), 
            value=config.get("sos_healer_active", True),
            help="Automatically reassigns leads that Pros ignored."
        )
        
        # 3. SOS Reporter
        reporter_active = col_sos3.checkbox(
            T.get("sos_reporter_active", "SOS Admin Reporter"), 
            value=config.get("sos_reporter_active", True),
            help="Sends batched reports of stuck leads to Admin WhatsApp."
        )
        
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
            st.success(T["success_save"])
            st.rerun()
