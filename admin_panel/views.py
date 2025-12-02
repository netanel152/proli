import streamlit as st
import pandas as pd
from bson.objectid import ObjectId
from datetime import datetime
# ייבוא ישיר (ללא נקודות)
from utils import users_collection, leads_collection, messages_collection, create_initial_schedule, generate_system_prompt
from components import render_chat_bubble

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
            pro_name = pro["business_name"] if pro else "Unknown"
            data.append({
                "id": str(l["_id"]), "chat_id": l["chat_id"],
                T["col_date"]: l["created_at"].strftime("%d/%m %H:%M"),
                T["col_client"]: l["chat_id"].replace("@c.us", ""),
                T["col_pro"]: pro_name, T["col_details"]: l["details"], T["col_status"]: l["status"]
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
            selected_id = st.selectbox("Select Lead", options.keys(), format_func=lambda x: options[x], label_visibility="collapsed")
            
            if selected_id:
                curr_lead = next(item for item in data if item["id"] == selected_id)
                status_opts = ["new", "contacted", "booked", "closed", "cancelled"]
                curr_st = curr_lead[T["col_status"]]
                if curr_st not in status_opts: status_opts.append(curr_st)
                new_status = st.selectbox("Status", status_opts, index=status_opts.index(curr_st))
                
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

def view_add_pro(T):
    st.title(T["add_pro_title"])
    with st.form("new_pro"):
        c1, c2 = st.columns(2)
        with c1:
            name = st.text_input(T["new_name"])
            phone = st.text_input(T["new_phone"])
            ptype = st.selectbox(T["new_type"], ["plumber", "electrician"])
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