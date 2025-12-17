import sys
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Add the project root directory to the Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import users_collection, slots_collection, leads_collection, messages_collection
from datetime import datetime, timedelta, timezone
import pytz
import random

# הגדרת אזור זמן ישראל
IL_TZ = pytz.timezone('Asia/Jerusalem')

# --- תבנית פרומפט מאוחדת וגנרית ---
prompt_template = """
אתה 'פיקסי', העוזר האישי של '{business_name}'.
המטרה: אבחון מהיר, הרגעה וסגירת תור.

*** הנחיות שפה וטון (Persona) ***
- תהיה קצר ותכליתי (עד 2 משפטים לתגובה).
- טון ישראלי, "דוגרי", יעיל ואדיב. 
- השתמש באימוג'י אחד בלבד להודעה. 🛠️

*** הגנה משפטית ותמחור ***
1. **מחיר:** ציין עלות ביקור והדגש שהיא הערכה בלבד שמתקזזת בתיקון. 
2. **אחריות:** הבהר שפיקסי היא פלטפורמת תיווך והאחריות המקצועית היא על איש המקצוע בלבד.
3. **אמון:** {license_info}

*** בטיחות בחירום ***
אם הלקוח מדווח על סכנה מיידית או תקלה קריטית: {safety_advice}

*** חילוץ פרטי עסקה [DEAL] ***
ברגע שהלקוח מאשר מועד, הוצא פקודה בפורמט: [DEAL: <יום ושעה> | <עיר/מיקום הלקוח> | <תיאור>]
- מיקום: חובה לציין את העיר/כתובת שהלקוח מסר.
- תיאור: כלול סיכום קצר של התקלה. אם נשלחה מדיה (תמונה/הקלטה), סכם אותה במילים (למשל: 'סיכום הקלטה: הלקוח אומר שיש ריח שרוף').

מחירון: {prices}
אזורי שירות: {areas}
"""

# 1. יוסי (אינסטלטור)
yossi_profile = {
    "business_name": "יוסי אינסטלציה",
    "phone_number": "972524828796", 
    "is_active": True, "plan": "pro", "created_at": datetime.now(timezone.utc),
    "is_verified": True,
    "license_number": "2045593",
    "service_areas": ["בני ברק", "רמת גן", "גבעתיים", "תל אביב"],
    "keywords": ["מים", "נזילה", "סתימה", "דוד", "אינסטלטור"],
    "social_proof": {"rating": 4.9, "review_count": 420},
    "system_prompt": prompt_template.format(
        business_name="יוסי אינסטלציה",
        areas="בני ברק, רמת גן, גבעתיים, תל אביב",
        prices="ביקור: 250, סתימה: 350-450, דוד: 450",
        license_info="אני אינסטלטור מוסמך (רישיון 2045593).",
        safety_advice="גש מיד לשיבר הראשי וסגור אותו!"
    )
}

# 2. משה (חשמלאי)
moshe_profile = {
    "business_name": "משה חשמל ובטיחות",
    "phone_number": "972541112222",
    "is_active": True, "plan": "pro", "created_at": datetime.now(timezone.utc),
    "is_verified": True,
    "license_number": "EL-998877",
    "service_areas": ["תל אביב", "חולון", "ראשון לציון"],
    "keywords": ["חשמל", "קצר", "פחת", "חשמלאי"],
    "social_proof": {"rating": 5.0, "review_count": 85},
    "system_prompt": prompt_template.format(
        business_name="משה חשמל ובטיחות",
        areas="תל אביב, חולון, ראשון לציון",
        prices="ביקור: 300, החלפת שקע: 150, לוח חשמל: 1200",
        license_info="אני חשמלאי מוסמך (רישיון EL-998877).",
        safety_advice="אל תיגע בכלום! הורד את המפסק הראשי בלוח החשמל מיד."
    )
}

# --- Functions (generate_slots, seed_leads, seed) נשארות כמעט ללא שינוי ---
# וודא רק שאתה משתמש ב-datetime.now(timezone.utc) עבור עקביות.

def generate_slots(pro_id, days=14):
    slots = []
    now_il = datetime.now(IL_TZ)
    start_date = now_il.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    
    for i in range(days):
        current_day = start_date + timedelta(days=i)
        if current_day.weekday() in [4, 5]: continue 
        for hour in range(8, 18, 1):
            slot_il = current_day.replace(hour=hour, minute=0)
            if slot_il < now_il: continue
            slot_utc = slot_il.astimezone(pytz.utc)
            slots.append({
                "pro_id": pro_id,
                "start_time": slot_utc, 
                "end_time": slot_utc + timedelta(hours=1),
                "is_taken": random.random() < 0.2,
            })
    return slots

def seed():
    print("🌱 Seeding DB with Market-Ready logic...")
    users_collection.delete_many({})
    slots_collection.delete_many({})
    leads_collection.delete_many({})
    messages_collection.delete_many({})
    
    for p in [yossi_profile, moshe_profile]: # הוסף כאן את דוד אם תרצה
        res = users_collection.insert_one(p)
        pro_id = res.inserted_id
        slots = generate_slots(pro_id)
        if slots: slots_collection.insert_many(slots)
        print(f"✅ Created {p['business_name']}")

if __name__ == "__main__":
    seed()