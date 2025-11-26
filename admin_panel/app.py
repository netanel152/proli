import streamlit as st
import pandas as pd
from pymongo import MongoClient
import os
import sys
from datetime import datetime
from dotenv import load_dotenv
from bson.objectid import ObjectId

# --- הגדרת נתיבים ---
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)

load_dotenv(os.path.join(parent_dir, ".env"))

# --- הגדרות עמוד ---
st.set_page_config(
    page_title="Fixi Admin",
    page_icon="🛠️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- מילון תרגומים ---
TRANS = {
    "HE": {
        "dir": "rtl", "align": "right",
        "nav_title": "תפריט ראשי",
        "nav_dashboard": "דשבורד ולידים", "nav_settings": "הגדרות בוט", "nav_pros": "אנשי מקצוע",
        "title_dashboard": "📊 מרכז שליטה",
        "metric_total": "סה\"כ לידים", "metric_new": "לידים חדשים", "metric_pros": "אנשי מקצוע",
        "table_title": "📋 ניהול לידים",
        "col_date": "תאריך", "col_details": "פרטים", "col_status": "סטטוס", "col_client": "לקוח", "col_pro": "טופל ע\"י",
        "chat_history": "💬 היסטוריית שיחה",
        "no_chat": "אין היסטוריה זמינה.",
        "action_update": "ניהול ליד נבחר",
        "btn_update": "עדכן סטטוס",
        "success_update": "עודכן בהצלחה!",
        "settings_title": "🧠 הגדרות בוט",
        "select_pro": "בחר פרופיל לעריכה",
        "edit_title": "עריכת פרופיל",
        "save_btn": "שמור שינויים",
        "success_save": "השינויים נשמרו בהצלחה!",
        "pros_title": "👷 צוות העובדים",
        "phone": "טלפון", "active": "פעיל?", "areas": "אזורי שירות", "keywords": "מילות מפתח",
        "prompt_title": "הנחיות מערכת", "prompt_desc": "הגדרות התנהגות הבוט", "rating": "דירוג",
        "role_user": "לקוח", "role_bot": "בוט",
        "status_active": "פעיל", "status_inactive": "לא פעיל"
    },
    "EN": {
        "dir": "ltr", "align": "left",
        "nav_title": "Main Menu",
        "nav_dashboard": "Dashboard", "nav_settings": "Settings", "nav_pros": "Professionals",
        "title_dashboard": "📊 Control Center",
        "metric_total": "Total Leads", "metric_new": "New Leads", "metric_pros": "Active Pros",
        "table_title": "📋 Leads Management",
        "col_date": "Date", "col_details": "Details", "col_status": "Status", "col_client": "Client", "col_pro": "Assignee",
        "chat_history": "💬 Chat History",
        "no_chat": "No history available.",
        "action_update": "Manage Selected Lead",
        "btn_update": "Update Status",
        "success_update": "Updated successfully!",
        "settings_title": "🧠 Bot Settings",
        "select_pro": "Select Profile",
        "edit_title": "Edit Profile",
        "save_btn": "Save Changes",
        "success_save": "Changes saved successfully!",
        "pros_title": "👷 Team List",
        "phone": "Phone", "active": "Active?", "areas": "Service Areas", "keywords": "Keywords",
        "prompt_title": "System Prompt", "prompt_desc": "Behavior settings", "rating": "Rating",
        "role_user": "Client", "role_bot": "Bot",
        "status_active": "Active", "status_inactive": "Inactive"
    }
}

# --- CSS  ---
def load_css(lang_code):
    direction = "rtl" if lang_code == "HE" else "ltr"
    align = "right" if lang_code == "HE" else "left"
    
    st.markdown("""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Heebo:wght@300;400;500;700;900&display=swap');
        </style>
    """, unsafe_allow_html=True)
    
    st.markdown(f"""
    <style>
        /* הגדרת פונט - רק לאלמנטים טקסטואליים מובהקים כדי לא לשבור אייקונים */
        html, body, p, h1, h2, h3, h4, h5, h6, input, textarea, button, .stMarkdown {{
            font-family: 'Heebo', sans-serif !important;
            direction: {direction};
            text-align: {align};
        }}
        
        /* כותרות */
        h1 {{ font-size: 3rem !important; font-weight: 900 !important; opacity: 0.9; }}
        h2 {{ font-size: 2.2rem !important; font-weight: 700 !important; opacity: 0.8; }}
        h3 {{ font-size: 1.6rem !important; font-weight: 600 !important; }}
        
        /* יישור כללי לאפליקציה */
        .stApp {{
            direction: {direction};
        }}
        
        /* כרטיסי מטריקות */
        div[data-testid="stMetric"] {{
            background-color: var(--secondary-background-color);
            border: 1px solid rgba(128, 128, 128, 0.2);
            padding: 18px;
            border-radius: 16px;
            text-align: center !important;
        }}
        
        div[data-testid="stMetricLabel"] p {{
            font-size: 1.3rem !important;
            font-weight: 700 !important;
            color: var(--text-color) !important;
            opacity: 0.9;
        }}
        
        div[data-testid="stMetricValue"] {{
            font-size: 3.5rem !important;
            font-weight: 900 !important;
            color: #4F8BF9 !important;
        }}
        
        .chat-container {{
            display: flex;
            flex-direction: column;
            gap: 15px;
            padding: 20px;
            background-color: rgba(0,0,0,0.02);
            border-radius: 12px;
        }}
        
        .chat-bubble {{
            padding: 12px 18px;
            border-radius: 18px;
            max-width: 80%;
            font-size: 1.1rem;
            line-height: 1.5;
            box-shadow: 0 1px 2px rgba(0,0,0,0.1);
            position: relative;
            font-family: 'Heebo', sans-serif !important;
        }}
        
        .user-msg {{
            background-color: rgba(37, 99, 235, 0.15);
            color: var(--text-color);
            align-self: { 'flex-start' if direction == 'rtl' else 'flex-end' };
            margin-{ 'left' if direction == 'rtl' else 'right' }: auto;
            border-bottom-{ 'right' if direction == 'rtl' else 'left' }-radius: 4px;
            text-align: {align};
        }}
        
        .bot-msg {{
            background-color: var(--secondary-background-color);
            border: 1px solid rgba(128, 128, 128, 0.2);
            color: var(--text-color);
            align-self: { 'flex-end' if direction == 'rtl' else 'flex-start' };
            margin-{ 'right' if direction == 'rtl' else 'left' }: auto;
            border-bottom-{ 'left' if direction == 'rtl' else 'right' }-radius: 4px;
            text-align: {align};
        }}
        
        .chat-meta {{
            font-size: 0.85rem;
            font-weight: 700;
            margin-bottom: 6px;
            opacity: 0.6;
            display: block;
        }}
        
        .stButton button {{
            font-size: 1.2rem !important;
            font-weight: 700;
            border-radius: 10px;
        }}

        .streamlit-expanderHeader {{
            font-family: 'Heebo', sans-serif !important; /* פונט רק לטקסט */
            font-size: 1.2rem !important; 
            font-weight: 600 !important;
            background-color: transparent !important;
            color: var(--text-color) !important;
            direction: {direction} !important;
        }}
        
        /* וידוא שהאייקון לא נדרס - משתמש בפונט המקורי של האייקונים */
        .streamlit-expanderHeader svg, .streamlit-expanderHeader i, .material-icons {{
            font-family: 'Material Icons' !important; 
        }}
    </style>
    """, unsafe_allow_html=True)

# --- חיבור ל-DB ---
@st.cache_resource
def init_connection():
    return MongoClient(os.getenv("MONGO_URI"))

client = init_connection()
db = client.fixi_db
users_collection = db.users
leads_collection = db.leads
messages_collection = db.messages

# --- אפליקציה ---
with st.sidebar:
    st.title("Fixi Admin")
    lang = st.selectbox("Language / שפה", ["HE", "EN"])
    T = TRANS[lang]
    load_css(lang)
    st.divider()
    page = st.radio(T["nav_title"], [T["nav_dashboard"], T["nav_settings"], T["nav_pros"]])

# --- דשבורד ---
if page == T["nav_dashboard"]:
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
                "id": str(l["_id"]),
                "chat_id": l["chat_id"],
                T["col_date"]: l["created_at"].strftime("%d/%m %H:%M"),
                T["col_client"]: l["chat_id"].replace("@c.us", ""),
                T["col_pro"]: pro_name,
                T["col_details"]: l["details"],
                T["col_status"]: l["status"]
            })
        
        df = pd.DataFrame(data)
        st.dataframe(df[[T["col_date"], T["col_client"], T["col_pro"], T["col_details"], T["col_status"]]], use_container_width=True, hide_index=True)
        
        st.markdown("---")
        
        c_left, c_right = st.columns([1, 2])
        
        with c_left:
            st.markdown(f"### {T['action_update']}")
            options = {d["id"]: f"{d[T['col_date']]} | {d[T['col_client']]} | {d[T['col_details']]}" for d in data}
            selected_id = st.selectbox("Select Lead", options.keys(), format_func=lambda x: options[x], label_visibility="collapsed")
            
            if selected_id:
                curr_lead = next(item for item in data if item["id"] == selected_id)
                new_status = st.selectbox("Status", ["new", "contacted", "closed", "cancelled"], index=["new", "contacted", "closed", "cancelled"].index(curr_lead[T["col_status"]]))
                
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
                        # --- התיקון הקריטי: בניית HTML ללא הזחות ---
                        chat_html = '<div class="chat-container">'
                        for m in msgs:
                            is_user = m['role'] == 'user'
                            cls = "user-msg" if is_user else "bot-msg"
                            name = T["role_user"] if is_user else T["role_bot"]
                            icon = "👤" if is_user else "🤖"
                            time_str = m['timestamp'].strftime("%H:%M")
                            msg_text = m['text']
                            
                            # שים לב: ה-HTML כתוב בשורה אחת או ללא הזחה בתחילת השורה
                            chat_html += f"""<div class="chat-bubble {cls}"><span class="chat-meta">{icon} {name} • {time_str}</span>{msg_text}</div>"""
                        
                        chat_html += '</div>'
                        st.markdown(chat_html, unsafe_allow_html=True)
                    else:
                        st.info(T["no_chat"])
    else:
        st.info("No leads found.")

# --- הגדרות ---
elif page == T["nav_settings"]:
    st.title(T["settings_title"])
    pros = list(users_collection.find())
    pro_map = {p["business_name"]: p for p in pros}
    selected = st.selectbox(T["select_pro"], list(pro_map.keys()))
    
    if selected:
        p = pro_map[selected]
        with st.form("settings_form"):
            st.subheader(f"{T['edit_title']}: {selected}")
            c1, c2 = st.columns(2)
            with c1:
                phone = st.text_input(T["phone"], p.get("phone_number", ""))
                areas = st.text_area(T["areas"], ", ".join(p.get("service_areas", [])))
            
            with c2:
                active = st.checkbox(T["active"], p.get("is_active", True))
                keywords = st.text_input(T["keywords"], ", ".join(p.get("keywords", [])))

            st.markdown(f"### {T['prompt_title']}")
            st.caption(T['prompt_desc'])
            prompt = st.text_area("Prompt", p.get("system_prompt", ""), height=300, label_visibility="collapsed")
            
            if st.form_submit_button(T["save_btn"]):
                users_collection.update_one({"_id": p["_id"]}, {"$set": {
                    "phone_number": phone, 
                    "is_active": active,
                    "service_areas": [x.strip() for x in areas.split(",") if x.strip()],
                    "keywords": [x.strip() for x in keywords.split(",") if x.strip()],
                    "system_prompt": prompt
                }})
                st.success(T["success_save"])

# --- אנשי מקצוע ---
elif page == T["nav_pros"]:
    st.title(T["pros_title"])
    pros = list(users_collection.find())
    
    # מטריקה מהירה
    c1, c2 = st.columns(2)
    c1.metric(T["metric_pros"], len(pros))
    c2.metric("Active", len([p for p in pros if p.get("is_active", True)]))
    
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