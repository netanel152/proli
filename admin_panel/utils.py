import streamlit as st
from pymongo import MongoClient
import os
import sys
from datetime import datetime, timedelta
from dotenv import load_dotenv
import pytz

# טעינת סביבה
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)
load_dotenv(os.path.join(parent_dir, ".env"))

# חיבור ל-DB
@st.cache_resource
def init_connection():
    return MongoClient(os.getenv("MONGO_URI"))

client = init_connection()
db = client.fixi_db
users_collection = db.users
leads_collection = db.leads
messages_collection = db.messages
slots_collection = db.slots

# עזרי לוגיקה
def generate_system_prompt(name, profession, areas, prices):
    """מייצר פרומפט ומילות מפתח לפי המקצוע"""
    if profession == "plumber":
        role = "אינסטלטור מומחה"
        safety = "סגור את השיבר הראשי מיד!"
        keywords = ["מים", "נזילה", "סתימה", "דוד", "כיור", "אסלה", "הצפה", "רטיבות", "ברז"]
    else:
        role = "חשמלאי מוסמך"
        safety = "הורד את המפסק הראשי ואל תיגע בחוטים!"
        keywords = ["חשמל", "קצר", "אור", "שקע", "פחת", "נשרף", "חוטים"]

    prompt = f"""
אתה 'פיקסי', העוזר האישי של '{name}'.
תפקיד: {role}.
המטרה: אבחון, הרגעה וסגירת תור.

*** הנחיות בטיחות (חובה) ***
במקרה חירום (הצפה/עשן/סכנה):
1. תגית: [URGENT]
2. הנחיה: "{safety}"

*** ניהול יומן וסגירה ***
1. בדוק זמינות ביומן למטה והצע רק שעות פנויות.
2. בסגירה: [DEAL: <יום ושעה> | <עיר> | <תיאור>]

מחירון: {prices}
אזורי שירות: {areas}
"""
    return prompt, keywords

def create_initial_schedule(pro_id):
    """יוצר יומן לשבוע הקרוב (ימי חול בלבד, 08:00-18:00)"""
    slots = []
    now_utc = datetime.now(pytz.utc)
    # מתחילים ממחר בבוקר
    start_date = now_utc.replace(hour=6, minute=0, second=0, microsecond=0) + timedelta(days=1)
    
    for i in range(7): 
        current_day = start_date + timedelta(days=i)
        # דילוג על שישי-שבת (4=Fri, 5=Sat)
        if current_day.weekday() in [4, 5]: continue
        
        # סלוטים של שעתיים: 08:00-18:00 (שעון ישראל = UTC+2/3)
        # נניח 06:00 UTC = 08:00/09:00 IL
        for hour in range(6, 16, 2): 
            s_time = current_day.replace(hour=hour)
            slots.append({
                "pro_id": pro_id,
                "start_time": s_time,
                "end_time": s_time + timedelta(hours=2),
                "is_taken": False
            })
    if slots:
        slots_collection.insert_many(slots)