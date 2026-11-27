from pymongo import MongoClient, uri_parser
import os
import certifi
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from bson import ObjectId
import pytz

from app.core.datetime_utils import IL_TZ

from app.core.constants import is_prod_like_env
from app.core.pro_trades import trades_line
from app.core.slot_writes import insert_slots_sync

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
        "role": "אינסטלטור",
        "safety": "לסגור את השיבר הראשי מיד.",
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
        "role": "חשמלאי",
        "safety": "להוריד את המפסק הראשי ולא לגעת בחוטים.",
        "keywords": ["חשמל", "קצר", "אור", "שקע", "פחת", "נשרף", "חוטים"],
    },
    "handyman": {
        "role": "הנדימן",
        "safety": "להתרחק מהאזור עד שאיש המקצוע יגיע.",
        "keywords": ["תיקון", "הרכבה", "מדף", "דלת", "צירים", "תחזוקה", "ריהוט"],
    },
    "locksmith": {
        "role": "מנעולן",
        "safety": "לא לנסות לפרוץ לבד, זה עלול לגרום נזק.",
        "keywords": ["מנעול", "מפתח", "דלת", "נעילה", "כספת", "פריצה", "צילינדר"],
    },
    "painter": {
        "role": "צבעי",
        "safety": "לאוורר את החדר ולצאת ממנו עד שאיש המקצוע יגיע.",
        "keywords": ["צבע", "קיר", "שיפוץ", "טפט", "סדקים", "לכה", "גבס"],
    },
    "cleaner": {
        "role": "ניקיון",
        "safety": "לא לערבב חומרי ניקוי שונים, ולאוורר את החדר.",
        "keywords": ["ניקיון", "עובש", "אבנית", "חיטוי", "שטיח", "ספה", "חלונות"],
    },
    "general": {
        "role": "איש מקצוע כללי",
        "safety": "להתרחק מהאזור עד שאיש המקצוע יגיע.",
        "keywords": ["שירות", "תיקון", "בעיה", "עזרה", "ביקור"],
    },
}


def generate_system_prompt(name, profession, areas, prices, trades=None):
    """מייצר פרומפט ומילות מפתח לפי המקצוע

    `trades` (PRO-234) adds the `תחומי עבודה:` line, so the persona names the
    work this pro takes rather than implying a handyman takes anything.
    """
    config = PROFESSION_CONFIG.get(profession, PROFESSION_CONFIG["general"])
    role = config["role"]
    safety = config["safety"]
    keywords = config["keywords"]

    # PRO-169: the persona is product copy — it sets the voice the model imitates
    # for every customer of this pro, so it follows docs/COPY_STYLE_GUIDE.md.
    # The former "[URGENT]" tag instruction was dropped: nothing in app/ parses
    # it, so the model was being asked to emit a token the customer would have
    # seen verbatim (§7). The "[DEAL: ...]" closing tag went the same way
    # (PRO-272): only the JSON is_deal that PRO_BASE_SYSTEM asks for closes a
    # deal, and a marker in the text is stripped and decides nothing.
    # Emergency handling is keyword-based in workflow_service (PRO-121).
    # PRO-245: the 2026-11-17 voice. פרולי speaks as "אנחנו" and coordinates
    # the job for the pro; it never speaks as the pro, who is the one who
    # arrives. No em dash, emoji, bold or slashed gender form in the persona
    # either: the model copies the register it is shown.
    prompt = f"""
פרולי, השירות שמתאם עבודות עבור '{name}'.
המקצוע של '{name}': {role}. לא לתאר את איש המקצוע כמוסמך או כמומחה: את זה איננו בודקים.
מטרה: להבין את הבעיה, להרגיע, ולאסוף את הפרטים לתיאום.

*** בטיחות (חובה) ***
במקרה חירום (הצפה, עשן, סכנה) ההודעה נפתחת בהנחיה: "{safety}"

*** סגנון (docs/COPY_STYLE_GUIDE.md) ***
- כל הודעה היא בועה קצרה בוואטסאפ: משפט אחד עד שלושה משפטים קצרים. רעיון אחד בשורה, שאלה אחת בכל הודעה.
- מדברים בשם פרולי, בלשון "אנחנו" ("הבנו", "רשמנו", "נעדכן"). לא בגוף ראשון יחיד, ולא בשם '{name}': מי שמגיע לעבודה הוא איש המקצוע, לא אנחנו ("מתי נוח שאיש המקצוע יגיע?").
- לא לחזור על מה שהלקוח כתב. את השם של הלקוח כותבים פעם אחת לכל היותר.
- עברית ניטרלית: לנסח בלי פועל ממוגדר ("אפשר לשלוח תמונה?"). בלי צורות עם לוכסן, ולעולם לא בלשון זכר בלבד.
- בלי אימוג'י, בלי קו מפריד ארוך, בלי הדגשות, בלי כותרות ובלי רשימות.
- בלי אנגלית בתוך עברית. כשמזכירים את המערכת, "פרולי", לא Proli.
- מחירים, שעות וכתובות בסוף שורה או בשורה נפרדת.
- לא להבטיח מה שהמערכת לא עושה: בלי הנחות, בלי חזרה טלפונית, בלי אישור סופי. האישור מגיע מהמערכת אחרי שאיש המקצוע מאשר.

*** ניהול יומן וסגירה ***
1. להציע רק שעות פנויות מתוך היומן שלמטה.
2. בסגירה: לסכם את הפרטים לאישור, בלי תגיות או סימונים בתוך ההודעה.

מחירון: {prices}
אזורי שירות: {areas}
"""
    line = trades_line(trades or [])
    if line:
        prompt += line + "\n"
    return prompt, keywords


def create_initial_schedule(pro_id):
    """יוצר יומן לשבוע הקרוב (ימי חול בלבד, 08:00-18:00)"""
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
    # PRO-277: approve runs this too, over days the pro may already have.
    insert_slots_sync(slots_collection, slots)


def send_completion_check_sync(lead_id: str):
    """
    Sync version of send_customer_completion_check for use in Streamlit.
    Uses sync PyMongo; the send goes through the outbound facade (PRO-86), which
    previously was a raw httpx.post straight at the legacy vendor and therefore skipped
    the circuit breaker and the kill switch.

    Returns True when the facade accepted the message, and False when it did not:
    breaker engaged, closed window, or a provider that cannot transmit. True is
    still not delivery confirmation.
    """
    from app.core.constants import Defaults
    from app.core.messages import Messages
    from app.core.phone import to_chat_id
    from app.providers.whatsapp import template_registry
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
    sent = send_text_sync(
        to_chat_id(customer_chat_id),
        message_text,
        template_key="completion_check",
        params={"pro_name": pro_name},
        buttons=template_registry.buttons_for("completion_check"),
    )

    # Stamp the lead exactly like the async path does. An operator send bypasses
    # the cap (it is a deliberate human action) but must still restart the
    # cooldown, or the 30-min stale-job monitor would nudge again minutes later.
    # Only a send that went: nothing reached the customer otherwise, and the
    # monitor's own check should still be able to.
    if sent:
        leads_collection.update_one(
            {"_id": ObjectId(lead_id)},
            {
                "$inc": {"completion_check_sent_count": 1},
                "$set": {"completion_check_sent_at": datetime.now(timezone.utc)},
            },
        )
    return sent
