import streamlit as st
from pymongo import MongoClient
import os
import certifi
import httpx
from datetime import datetime, timedelta
from dotenv import load_dotenv
from bson import ObjectId
import pytz

# Load environment variables
load_dotenv()

# Standalone MongoDB Connection for Admin Panel
mongo_uri = os.getenv("MONGO_URI") or os.getenv("MONGODB_URI") or os.getenv("MONGO_URL")
if not mongo_uri:
    # Fallback to local if env var is missing (safety)
    mongo_uri = "mongodb://localhost:27017/proli_db"

ca_file = certifi.where() if "+srv" in mongo_uri else None
kwargs = {"tlsCAFile": ca_file} if ca_file else {}

client = MongoClient(mongo_uri, **kwargs)
db = client.proli_db # Assuming 'proli_db' is your database name

users_collection = db.users
leads_collection = db.leads
messages_collection = db.messages
slots_collection = db.slots
settings_collection = db.settings

# עזרי לוגיקה
PROFESSION_CONFIG = {
    "plumber": {
        "role": "אינסטלטור מומחה",
        "safety": "סגור את השיבר הראשי מיד!",
        "keywords": ["מים", "נזילה", "סתימה", "דוד", "כיור", "אסלה", "הצפה", "רטיבות", "ברז"],
    },
    "electrician": {
        "role": "חשמלאי מוסמך",
        "safety": "הורד את המפסק הראשי ואל תיגע בחוטים!",
        "keywords": ["חשמל", "קצר", "אור", "שקע", "פחת", "נשרף", "חוטים"],
    },
    "handyman": {
        "role": "איש תחזוקה כללי",
        "safety": "ודא שהאזור בטוח לעבודה.",
        "keywords": ["תיקון", "הרכבה", "מדף", "דלת", "צירים", "תחזוקה", "ריהוט"],
    },
    "locksmith": {
        "role": "מנעולן מוסמך",
        "safety": "אל תנסה לפרוץ בעצמך, זה עלול לגרום נזק.",
        "keywords": ["מנעול", "מפתח", "דלת", "נעילה", "כספת", "פריצה", "צילינדר"],
    },
    "painter": {
        "role": "צבעי מקצועי",
        "safety": "אוורר את החדר היטב בזמן העבודה.",
        "keywords": ["צבע", "קיר", "שיפוץ", "טפט", "סדקים", "לכה", "גבס"],
    },
    "cleaner": {
        "role": "מומחה ניקיון",
        "safety": "אל תערבב חומרי ניקוי שונים.",
        "keywords": ["ניקיון", "עובש", "אבנית", "חיטוי", "שטיח", "ספה", "חלונות"],
    },
    "general": {
        "role": "איש מקצוע כללי",
        "safety": "ודא שהאזור בטוח לפני תחילת העבודה.",
        "keywords": ["שירות", "תיקון", "בעיה", "עזרה", "ביקור"],
    },
}

def generate_system_prompt(name, profession, areas, prices):
    """מייצר פרומפט ומילות מפתח לפי המקצוע"""
    config = PROFESSION_CONFIG.get(profession, PROFESSION_CONFIG["general"])
    role = config["role"]
    safety = config["safety"]
    keywords = config["keywords"]

    prompt = f"""
אתה 'פרולי', העוזר האישי של '{name}'.
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
    IL_TZ = pytz.timezone('Asia/Jerusalem')
    slots = []
    # Start from tomorrow morning in Israel time, then convert to UTC
    now_il = datetime.now(IL_TZ)
    # Strip tzinfo to get a naive date, then re-localize per slot
    start_date_naive = (now_il + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)

    for i in range(7):
        current_day = start_date_naive + timedelta(days=i)
        # דילוג על שישי-שבת (4=Fri, 5=Sat)
        if current_day.weekday() in [4, 5]:
            continue

        # סלוטים של שעתיים: 08:00-18:00 Israel time
        for hour in range(8, 18, 2):
            s_time_il = IL_TZ.localize(current_day.replace(hour=hour))
            s_time_utc = s_time_il.astimezone(pytz.utc)
            slots.append({
                "pro_id": pro_id,
                "start_time": s_time_utc,
                "end_time": s_time_utc + timedelta(hours=2),
                "is_taken": False
            })
    if slots:
        slots_collection.insert_many(slots)


def send_completion_check_sync(lead_id: str):
    """
    Sync version of send_customer_completion_check for use in Streamlit.
    Uses sync PyMongo + sync httpx instead of async Motor/httpx.
    """
    from app.core.constants import LeadStatus, Defaults
    from app.core.messages import Messages

    lead = leads_collection.find_one({"_id": ObjectId(lead_id)})
    if not lead or lead.get("status") != LeadStatus.BOOKED:
        raise ValueError(f"Lead {lead_id} not found or not in BOOKED status")

    customer_chat_id = lead["chat_id"]
    pro = users_collection.find_one({"_id": lead["pro_id"]})
    pro_name = pro.get("business_name", Defaults.GENERIC_PRO_NAME) if pro else Defaults.GENERIC_PRO_NAME

    chat_id = f"{customer_chat_id}" if customer_chat_id.endswith("@c.us") else f"{customer_chat_id}@c.us"

    instance_id = os.getenv("GREEN_API_INSTANCE_ID")
    api_token = os.getenv("GREEN_API_TOKEN")

    payload = {
        "chatId": chat_id,
        "message": Messages.Customer.COMPLETION_CHECK.format(pro_name=pro_name),
        "buttons": [
            {"buttonId": Messages.Keywords.BUTTON_CONFIRM_FINISH, "buttonText": {"displayText": Messages.Keywords.BUTTON_TITLE_YES_FINISHED}},
            {"buttonId": Messages.Keywords.BUTTON_NOT_FINISHED, "buttonText": {"displayText": Messages.Keywords.BUTTON_TITLE_NO_NOT_YET}}
        ]
    }

    url = f"https://api.green-api.com/waInstance{instance_id}/sendInteractiveMessage/{api_token}"
    resp = httpx.post(url, json=payload, timeout=30.0)
    resp.raise_for_status()
    return resp.json()