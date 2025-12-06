import streamlit as st
import pandas as pd
from bson.objectid import ObjectId
from datetime import datetime, timedelta, time
from utils import users_collection, leads_collection, messages_collection, slots_collection, create_initial_schedule, generate_system_prompt
from components import render_chat_bubble
import pytz

def view_dashboard(T):
    st.title(T["title_dashboard"])
    c1, c2, c3 = st.columns(3)
    c1.metric(T["metric_total"], leads_collection.count_documents({}))
    c2.metric(T["metric_new"], leads_collection.count_documents({"status": "new"}), delta_color="inverse")
    c3.metric(T["metric_pros"], users_collection.count_documents({"is_active": True}))
    st.markdown("---")
    
    st.subheader(T["table_title"])
    leads = list(leads_collection.find().sort("created_at", -1).limit(50))
    
    if leads:
        data = []
        for l in leads:
            pro = users_collection.find_one({"_id": l.get("pro_id")})
            pro_name = pro["business_name"] if pro else T["unknown_pro"]
            
            # Translate status for display
            status_key = l["status"]
            status_display = T.get(status_key, status_key)
            
            data.append({
                "id": str(l["_id"]), "chat_id": l["chat_id"],
                T["col_date"]: l["created_at"].strftime("%d/%m %H:%M"),
                T["col_client"]: l["chat_id"].replace("@c.us", ""),
                T["col_pro"]: pro_name, T["col_details"]: l["details"], 
                T["col_status"]: status_display, # Display translated status
                "raw_status": status_key # Keep raw status for logic
            })
        df = pd.DataFrame(data)
        
        st.dataframe(
            df[[T["col_date"], T["col_client"], T["col_pro"], T["col_details"], T["col_status"]]], 
            width="stretch",
            hide_index=True
        )
        
        st.markdown("---")
        c_left, c_right = st.columns([1, 2])
        with c_left:
            st.markdown(f"### {T['action_update']}")
            options = {d["id"]: f"{d[T['col_date']]} | {d[T['col_client']]} | {d[T['col_details']]}" for d in data}
            selected_id = st.selectbox(T["select_lead"], options.keys(), format_func=lambda x: options[x], label_visibility="collapsed")
            
            if selected_id:
                curr_lead = next(item for item in data if item["id"] == selected_id)
                
                # Use raw status for logic, translated for display
                status_opts = ["new", "contacted", "booked", "closed", "cancelled"]
                curr_st_raw = curr_lead["raw_status"]
                
                if curr_st_raw not in status_opts: status_opts.append(curr_st_raw)
                
                new_status = st.selectbox(
                    T["select_status"], 
                    status_opts, 
                    index=status_opts.index(curr_st_raw),
                    format_func=lambda x: T.get(x, x)
                )
                
                if st.button(T["btn_update"], type="primary"):
                    leads_collection.update_one({"_id": ObjectId(selected_id)}, {"$set": {"status": new_status}})
                    st.success(T["success_update"])
                    st.rerun()

        with c_right:
            if selected_id:
                st.markdown(f"### {T['chat_history']}")
                with st.container(height=500, border=True):
                    chat_id = curr_lead["chat_id"]
                    msgs = list(messages_collection.find({"chat_id": chat_id}).sort("timestamp", 1))
                    if msgs:
                        html = '<div class="chat-container">'
                        for m in msgs:
                            html += render_chat_bubble(m['text'], m['role'], m['timestamp'], T)
                        html += '</div>'
                        st.markdown(html, unsafe_allow_html=True)
                    else: st.info(T["no_chat"])
    else: st.info(T["no_leads_found"])

def view_schedule(T):
    st.title(T["title_schedule"])
    
    # 1. Select Pro
    pros = list(users_collection.find())
    pro_map = {p["business_name"]: p for p in pros}
    selected_pro_name = st.selectbox(T["sch_select_pro"], list(pro_map.keys()))
    
    if selected_pro_name:
        pro = pro_map[selected_pro_name]
        tz = pytz.timezone('Asia/Jerusalem')
        
        tab_daily, tab_bulk = st.tabs([T["tab_daily"], T["tab_bulk"]])
        
        # --- TAB 1: DAILY EDITOR ---
        with tab_daily:
            st.markdown("### " + T["tab_daily"])
            
            # Select Date
            selected_date = st.date_input(T["sch_date"], value=datetime.now().date())
            
            # Fetch existing slots for this day (in UTC)
            # We need to query a range covering the whole day in UTC
            # Local Start of Day -> UTC
            day_start_local = tz.localize(datetime.combine(selected_date, time.min))
            day_end_local = tz.localize(datetime.combine(selected_date, time.max))
            
            day_start_utc = day_start_local.astimezone(pytz.utc)
            day_end_utc = day_end_local.astimezone(pytz.utc)
            
            slots_cursor = slots_collection.find({
                "pro_id": pro["_id"],
                "start_time": {"$gte": day_start_utc, "$lte": day_end_utc}
            }).sort("start_time", 1)
            
            original_slots = list(slots_cursor)
            original_ids = {str(s["_id"]) for s in original_slots}
            
            # Prepare data for editor
            editor_data = []
            for s in original_slots:
                # Convert UTC to Local for display
                local_start = s["start_time"].replace(tzinfo=pytz.utc).astimezone(tz)
                local_end = s["end_time"].replace(tzinfo=pytz.utc).astimezone(tz)
                
                editor_data.append({
                    "_id": str(s["_id"]),
                    "start_time": local_start.time(),
                    "end_time": local_end.time(),
                    "is_taken": s["is_taken"]
                })
            
            # Create DataFrame
            df = pd.DataFrame(editor_data)
            if df.empty:
                # Initialize empty DF with correct columns if no slots
                df = pd.DataFrame(columns=["_id", "start_time", "end_time", "is_taken"])
            
            # Show Data Editor
            edited_df = st.data_editor(
                df,
                column_config={
                    "_id": None, # Hide ID
                    "start_time": st.column_config.TimeColumn(T["sch_start_time"], format="HH:mm", step=60),
                    "end_time": st.column_config.TimeColumn(T["sch_end_time"], format="HH:mm", step=60),
                    "is_taken": st.column_config.CheckboxColumn(T["sch_taken"], default=False)
                },
                num_rows="dynamic",
                use_container_width=True,
                key=f"editor_{pro['_id']}_{selected_date}" # Unique key per day
            )
            
            if st.button(T["sch_save"], type="primary"):
                # --- PROCESSING CHANGES ---
                
                # 1. Identify Deletions
                # IDs present in original but missing in edited_df (rows deleted by user)
                current_ids = set(edited_df["_id"].dropna().astype(str))
                ids_to_delete = original_ids - current_ids
                
                if ids_to_delete:
                    slots_collection.delete_many({"_id": {"$in": [ObjectId(oid) for oid in ids_to_delete]}})
                
                # 2. Identify Updates & Inserts
                new_slots = []
                updates_count = 0
                
                for index, row in edited_df.iterrows():
                    # Parse Times
                    s_time = row["start_time"]
                    e_time = row["end_time"]
                    
                    if not s_time or not e_time: continue # Skip invalid rows
                    
                    # Combine with Date -> Local -> UTC
                    try:
                        dt_start = tz.localize(datetime.combine(selected_date, s_time)).astimezone(pytz.utc)
                        dt_end = tz.localize(datetime.combine(selected_date, e_time)).astimezone(pytz.utc)
                    except Exception:
                        continue # Skip if time is invalid
                        
                    slot_data = {
                        "pro_id": pro["_id"],
                        "start_time": dt_start,
                        "end_time": dt_end,
                        "is_taken": row["is_taken"]
                    }
                    
                    row_id = row.get("_id")
                    
                    if row_id and isinstance(row_id, str) and row_id in original_ids:
                        # Update existing
                        slots_collection.update_one(
                            {"_id": ObjectId(row_id)},
                            {"$set": slot_data}
                        )
                        updates_count += 1
                    else:
                        # Insert new (ID is NaN or None or new)
                        new_slots.append(slot_data)
                
                if new_slots:
                    slots_collection.insert_many(new_slots)
                
                st.success(f"{T['sch_success']}")
                st.rerun()

        # --- TAB 2: BULK GENERATOR ---
        with tab_bulk:
            st.markdown("### " + T["sch_config_title"])
            with st.container(border=True):
                c1, c2 = st.columns(2)
                with c1:
                    # Default: Tomorrow
                    start_date = st.date_input(T["sch_start_date"], value=datetime.now().date() + timedelta(days=1))
                    start_hour = st.number_input(T["sch_start_hour"], 0, 23, 8)
                    duration = st.number_input(T["sch_duration"], 15, 120, 60, step=15)
                with c2:
                    # Default: Next week
                    end_date = st.date_input(T["sch_end_date"], value=datetime.now().date() + timedelta(days=7))
                    end_hour = st.number_input(T["sch_end_hour"], 0, 23, 18)
                
                weekdays_opts = [6, 0, 1, 2, 3, 4, 5] 
                selected_days = st.multiselect(
                    T["sch_workdays"], 
                    weekdays_opts, 
                    default=[6, 0, 1, 2, 3], 
                    format_func=lambda x: T[f"day_{(x + 1) % 7}"]
                )
                
                st.markdown("---")
                c_gen, c_clear = st.columns([1, 1])
                
                with c_gen:
                    if st.button(T["sch_gen_btn"], type="primary"):
                        new_slots = []
                        curr_d = start_date
                        
                        while curr_d <= end_date:
                            if curr_d.weekday() in selected_days:
                                try:
                                    day_start = tz.localize(datetime.combine(curr_d, time(start_hour, 0)))
                                    day_end = tz.localize(datetime.combine(curr_d, time(end_hour, 0)))
                                except:
                                    curr_d += timedelta(days=1)
                                    continue

                                curr_slot = day_start
                                while curr_slot + timedelta(minutes=duration) <= day_end:
                                    slot_end = curr_slot + timedelta(minutes=duration)
                                    new_slots.append({
                                        "pro_id": pro["_id"],
                                        "start_time": curr_slot.astimezone(pytz.utc),
                                        "end_time": slot_end.astimezone(pytz.utc),
                                        "is_taken": False
                                    })
                                    curr_slot = slot_end
                            
                            curr_d += timedelta(days=1)
                        
                        if new_slots:
                            slots_collection.insert_many(new_slots)
                            st.success(f"{T['sch_msg_generated']} ({len(new_slots)})")
                            st.rerun()
                        else:
                            st.warning("No slots generated.")

                with c_clear:
                    if st.button(T["sch_clear_btn"]):
                         slots_collection.delete_many({
                             "pro_id": pro["_id"], 
                             "start_time": {"$gt": datetime.utcnow()}
                         })
                         st.success(T["sch_msg_cleared"])
                         st.rerun()

def view_add_pro(T):
    st.title(T["add_pro_title"])
    with st.form("new_pro"):
        c1, c2 = st.columns(2)
        with c1:
            name = st.text_input(T["new_name"])
            phone = st.text_input(T["new_phone"])
            ptype = st.selectbox(T["new_type"], ["plumber", "electrician"], format_func=lambda x: T.get(f"type_{x}", x))
        with c2:
            areas = st.text_input(T["new_areas"])
            prices = st.text_area(T["new_prices"])
        
        if st.form_submit_button(T["btn_create"], type="primary"):
            if name and phone and areas:
                prompt, keywords = generate_system_prompt(name, ptype, areas, prices)
                new_pro = {
                    "business_name": name, "phone_number": phone, "is_active": True, "plan": "basic",
                    "created_at": datetime.utcnow(), "service_areas": [x.strip() for x in areas.split(",")],
                    "keywords": keywords, "system_prompt": prompt, "social_proof": {"rating": 5.0}, "is_verified": True
                }
                res = users_collection.insert_one(new_pro)
                create_initial_schedule(res.inserted_id)
                st.success(T["success_create"])
                st.balloons()

def view_settings(T):
    st.title(T["settings_title"])
    pros = list(users_collection.find())
    pro_map = {p["business_name"]: p for p in pros}
    selected = st.selectbox(T["select_pro"], list(pro_map.keys()))
    if selected:
        p = pro_map[selected]
        with st.form("settings"):
            st.subheader(f"{T['edit_title']}: {selected}")
            c1, c2 = st.columns(2)
            with c1:
                phone = st.text_input(T["phone"], p.get("phone_number"))
                areas = st.text_area(T["areas"], ", ".join(p.get("service_areas", [])))
            with c2:
                active = st.checkbox(T["active"], p.get("is_active", True))
                keywords = st.text_input(T["keywords"], ", ".join(p.get("keywords", [])))
            prompt = st.text_area(T["prompt_title"], p.get("system_prompt", ""), height=300)
            
            if st.form_submit_button(T["save_btn"]):
                users_collection.update_one({"_id": p["_id"]}, {"$set": {
                    "phone_number": phone, "is_active": active,
                    "service_areas": [x.strip() for x in areas.split(",")],
                    "keywords": [x.strip() for x in keywords.split(",")],
                    "system_prompt": prompt
                }})
                st.success(T["success_save"])

def view_pros(T):
    st.title(T["pros_title"])
    pros = list(users_collection.find())
    
    # מטריקה מהירה
    c1, c2 = st.columns(2)
    c1.metric(T["metric_pros"], len(pros))
    c2.metric(T["status_active"], len([p for p in pros if p.get("is_active", True)]))
    
    st.markdown("---")
    
    for p in pros:
        is_active = p.get('is_active', True)
        status_label = T["status_active"] if is_active else T["status_inactive"]
        status_color = "green" if is_active else "red"
        
        with st.expander(f" {p['business_name']}", expanded=True):
            c1, c2 = st.columns([3, 1])
            with c1:
                st.write(f"📍 **{T['areas']}:** {', '.join(p.get('service_areas', []))}")
                st.write(f"🏷️ **{T['keywords']}:** {', '.join(p.get('keywords', [])[:5])}...")
            with c2:
                st.write(f"📞 {p.get('phone_number')}")
                st.write(f"⭐ {p.get('social_proof', {}).get('rating', 'N/A')}")
                st.markdown(f":{status_color}[**{status_label}**]")