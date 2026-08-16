import streamlit as st
from pymongo import MongoClient, uri_parser
import os
import certifi
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from bson import ObjectId
import pytz

from app.core.constants import is_prod_like_env

# Load environment variables
load_dotenv()

# Standalone MongoDB Connection for Admin Panel
mongo_uri = os.getenv("MONGO_URI") or os.getenv("MONGODB_URI") or os.getenv("MONGO_URL")
if not mongo_uri:
    # PRO-34: the guard covers staging as well as production — a staging admin
    # panel silently falling back to localhost would show an empty (or wrong)
    # database while looking healthy. is_prod_like_env is used rather than
    # settings.is_prod_like to keep this module import-order independent: it
    # reads os.environ directly right after its own load_dotenv(), with no
    # dependency on when Settings() is first constructed. (admin_panel/core/
    # auth.py does import settings, so the process still needs the full
    # required-var set — this is not an attempt to avoid that.)
    if is_prod_like_env(os.getenv("ENVIRONMENT")):
        raise ValueError(
            "MONGO_URI (or MONGODB_URI / MONGO_URL) is not set, but "
            f"ENVIRONMENT={os.getenv('ENVIRONMENT')!r} is treated as "
            "staging/production, where falling back to localhost would point "
            "the admin panel at an empty database. Set MONGO_URI in the admin "
            "panel's environment before starting Streamlit."
        )
    mongo_uri = "mongodb://localhost:27017/proli_db"

ca_file = certifi.where() if "+srv" in mongo_uri else None
kwargs = {"tlsCAFile": ca_file} if ca_file else {}

_parsed = uri_parser.parse_uri(mongo_uri)
_db_name = _parsed.get("database") or "proli_db"

client = MongoClient(mongo_uri, **kwargs)
db = client[_db_name]

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
        "keywords": [
            "מים",
            "נזילה",
            "סתימה",
            "דוד",
            "כיור",
            "אסלה",
            "הצפה",
            "רטיבות",
            "ברז",
        ],
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
    IL_TZ = pytz.timezone("Asia/Jerusalem")
    slots = []
    # Start from tomorrow morning in Israel time, then convert to UTC
    now_il = datetime.now(IL_TZ)
    # Strip tzinfo to get a naive date, then re-localize per slot
    start_date_naive = (now_il + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=None
    )

    for i in range(7):
        current_day = start_date_naive + timedelta(days=i)
        # דילוג על שישי-שבת (4=Fri, 5=Sat)
        if current_day.weekday() in [4, 5]:
            continue

        # סלוטים של שעתיים: 08:00-18:00 Israel time
        for hour in range(8, 18, 2):
            s_time_il = IL_TZ.localize(current_day.replace(hour=hour))
            s_time_utc = s_time_il.astimezone(pytz.utc)
            slots.append(
                {
                    "pro_id": pro_id,
                    "start_time": s_time_utc,
                    "end_time": s_time_utc + timedelta(hours=2),
                    "is_taken": False,
                }
            )
    if slots:
        slots_collection.insert_many(slots)


def send_completion_check_sync(lead_id: str):
    """
    Sync version of send_customer_completion_check for use in Streamlit.
    Uses sync PyMongo; the send goes through the outbound facade (PRO-86), which
    previously was a raw httpx.post straight at Green API and therefore skipped
    the circuit breaker and the kill switch.

    Returns True when the facade accepted the message. A suppressed send (breaker
    engaged) also returns True-ish semantics from the caller's point of view — it
    was handled, not lost — so callers must not treat the result as delivery
    confirmation.
    """
    from app.core.constants import Defaults
    from app.core.messages import Messages
    from app.core.phone import to_chat_id
    from app.providers.whatsapp.sync import send_text_sync

    lead = leads_collection.find_one({"_id": ObjectId(lead_id)})
    if not lead:
        raise ValueError(f"Lead {lead_id} not found")

    customer_chat_id = lead["chat_id"]
    pro = users_collection.find_one({"_id": lead["pro_id"]})
    pro_name = (
        pro.get("business_name", Defaults.GENERIC_PRO_NAME)
        if pro
        else Defaults.GENERIC_PRO_NAME
    )

    message_text = Messages.Customer.COMPLETION_CHECK.format(pro_name=pro_name)
    sent = send_text_sync(to_chat_id(customer_chat_id), message_text)

    # Stamp the lead exactly like the async path does. An operator send bypasses
    # the cap (it is a deliberate human action) but must still restart the
    # cooldown, or the 30-min stale-job monitor would nudge again minutes later.
    leads_collection.update_one(
        {"_id": ObjectId(lead_id)},
        {
            "$inc": {"completion_check_sent_count": 1},
            "$set": {"completion_check_sent_at": datetime.now(timezone.utc)},
        },
    )
    return sent
