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
    
    # 1. בחירת איש מקצוע
    pros = list(users_collection.find())
    pro_map = {p["business_name"]: p for p in pros}
    selected_pro_name = st.selectbox(T["sch_select_pro"], list(pro_map.keys()))
    
    if selected_pro_name:
        pro = pro_map[selected_pro_name]
        
        # --- GENERATOR SECTION ---
        with st.expander(T["sch_config_title"]):
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
            
            # Day selection (0=Mon, 6=Sun)
            # Map python weekday to translation key: 0->day_1 (Mon), 6->day_0 (Sun)
            weekdays_opts = [6, 0, 1, 2, 3, 4, 5] # Ordered Sun-Sat for display
            selected_days = st.multiselect(
                T["sch_workdays"], 
                weekdays_opts, 
                default=[6, 0, 1, 2, 3], # Sun-Thu default
                format_func=lambda x: T[f"day_{(x + 1) % 7}"]
            )
            
            c_gen, c_clear = st.columns([1, 1])
            with c_gen:
                if st.button(T["sch_gen_btn"], type="primary"):
                    new_slots = []
                    tz = pytz.timezone('Asia/Jerusalem')
                    curr_d = start_date
                    
                    while curr_d <= end_date:
                        if curr_d.weekday() in selected_days:
                            # Localize start/end times for the day
                            try:
                                day_start = tz.localize(datetime.combine(curr_d, time(start_hour, 0)))
                                day_end = tz.localize(datetime.combine(curr_d, time(end_hour, 0)))
                            except:
                                # Fallback if time conversion fails
                                curr_d += timedelta(days=1)
                                continue

                            curr_slot = day_start
                            while curr_slot + timedelta(minutes=duration) <= day_end:
                                slot_end = curr_slot + timedelta(minutes=duration)
                                # Convert to UTC for storage
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
                        st.warning("No slots generated (check dates/hours).")

            with c_clear:
                if st.button(T["sch_clear_btn"]):
                     # Delete future slots
                     slots_collection.delete_many({
                         "pro_id": pro["_id"], 
                         "start_time": {"$gt": datetime.utcnow()}
                     })
                     st.success(T["sch_msg_cleared"])
                     st.rerun()
        
        st.markdown("---")

        # 2. פילטר תאריך (אופציונלי)
        filter_date = st.date_input(T["sch_filter_date"], value=None)
        
        # 3. שליפת סלוטים
        query = {"pro_id": pro["_id"]}
        
        if filter_date:
            # סינון ליום ספציפי (UTC מול מקומי זה טריקי, נעשה סינון פשוט בזיכרון או טווח)
            # לצורך הפשטות: נשלוף הכל (לשבוע הקרוב זה מעט) ונסנן בפייתון
            pass
            
        # שליפת סלוטים עתידיים בלבד
        slots = list(slots_collection.find(
            {"pro_id": pro["_id"], "start_time": {"$gt": datetime.utcnow()}}
        ).sort("start_time", 1))
        
        if slots:
            # הכנת דאטה לטבלה עריכה
            data = []
            for s in slots:
                # המרה לשעון מקומי לתצוגה
                local_time = s['start_time'].replace(tzinfo=pytz.utc).astimezone(pytz.timezone('Asia/Jerusalem'))
                
                # סינון תאריך אם נבחר
                if filter_date and local_time.date() != filter_date:
                    continue
                    
                data.append({
                    "id": str(s["_id"]),
                    T["sch_date"]: local_time.strftime("%d/%m/%Y"),
                    T["sch_time"]: local_time.strftime("%H:%M"),
                    T["sch_taken"]: s["is_taken"]
                })
            
            if not data:
                st.info(T["no_slots"])
            else:
                df = pd.DataFrame(data)
                
                # --- טבלה עריכה (Data Editor) ---
                edited_df = st.data_editor(
                    df,
                    column_config={
                        "id": None, # מוסתר
                        T["sch_date"]: st.column_config.TextColumn(disabled=True),
                        T["sch_time"]: st.column_config.TextColumn(disabled=True),
                        T["sch_taken"]: st.column_config.CheckboxColumn(
                            label=T["sch_taken"],
                            help=T["slot_help"],
                            default=False,
                        )
                    },
                    hide_index=True,
                    use_container_width=True,
                    key="schedule_editor" # מפתח ייחודי
                )
                
                # כפתור שמירה
                if st.button(T["sch_save"], type="primary"):
                    # מעבר על השורות ומציאת שינויים
                    updates_count = 0
                    for index, row in edited_df.iterrows():
                        slot_id = row["id"]
                        new_status = row[T["sch_taken"]]
                        
                        # עדכון ב-DB
                        res = slots_collection.update_one(
                            {"_id": ObjectId(slot_id)},
                            {"$set": {"is_taken": new_status}}
                        )
                        if res.modified_count > 0:
                            updates_count += 1
                    
                    if updates_count > 0:
                        st.success(f"{T['sch_success']} ({updates_count} {T['msg_changes']})")
                        st.rerun()
                    else:
                        st.info(T["no_changes"])
        else:
            st.warning(T["schedule_empty"])

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