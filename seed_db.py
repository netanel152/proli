from app.core.database import users_collection, slots_collection
from datetime import datetime, timedelta
import pytz

# הגדרת אזור זמן ישראל
IL_TZ = pytz.timezone('Asia/Jerusalem')

prompt_template = """
אתה 'פיקסי', העוזר האישי והחברמן של '{business_name}'.
המטרה שלך: להרגיע, לאבחן, לתת מחיר, ולסגור תור.

*** הנחיות לניתוח תמונה ***
1. זהה את הרכיב (ברז, סיפון, דוד).
2. שאל שאלות מנחות אם צריך (יש נזילה?).

*** הנחיות לסגירת עסקה וניהול יומן ***
1. **זמינות:** בדוק את היומן. הצע רק שעות פנויות מהרשימה.
2. **סגירה:** ברגע שנסגרה שעה -> [DEAL: <יום ושעה> | <עיר> | <תיאור>]

מחירון: {prices}
אזורי שירות: {areas}
"""

# 1. יוסי (מרכז)
yossi_profile = {
    "business_name": "יוסי אינסטלציה",
    "phone_number": "524828796", # המספר שלך
    "is_active": True, "plan": "pro", "created_at": datetime.now(pytz.utc),
    "service_areas": ["בני ברק", "רמת גן", "גבעתיים", "תל אביב"],
    "keywords": ["מים", "נזילה", "סתימה", "דוד", "כיור", "אסלה", "הצפה"],
    "social_proof": {"rating": 9.9},
    "system_prompt": prompt_template.format(
        business_name="יוסי אינסטלציה",
        areas="בני ברק, רמת גן, גבעתיים, תל אביב",
        prices="ביקור: 250, סתימה: 350-450"
    )
}

# 2. דוד (שרון)
david_profile = {
    "business_name": "דוד המהיר",
    "phone_number": "524828796",
    "is_active": True, "plan": "basic", "created_at": datetime.now(pytz.utc),
    "service_areas": ["נתניה", "חדרה", "קיסריה", "כפר יונה"],
    "keywords": ["מים", "נזילה", "סתימה", "דוד"],
    "social_proof": {"rating": 4.7},
    "system_prompt": prompt_template.format(
        business_name="דוד המהיר",
        areas="נתניה, חדרה, קיסריה",
        prices="ביקור: 200, סתימה: 300"
    )
}

def generate_slots(pro_id, days=7):
    slots = []
    # מתחילים מהיום
    now_il = datetime.now(IL_TZ)
    start_date = now_il.replace(hour=8, minute=0, second=0, microsecond=0)
    
    for i in range(days):
        current_day = start_date + timedelta(days=i)
        
        # מדלגים על ימים שכבר עברו (אם מריצים בערב)
        # (אופציונלי - כרגע ניצור גם להיום)

        # ימי חול בלבד (ראשון עד חמישי) - 6=Sunday in Python's default? No, 6=Sunday in some, 4=Friday 5=Saturday.
        # Python: Mon=0, Sun=6. בישראל עובדים ראשון(6)-חמישי(3). שישי(4)-שבת(5) חופש.
        if current_day.weekday() in [4, 5]: continue 
            
        # סלוטים: 08:00 עד 18:00
        for hour in range(8, 18, 2):
            # יצירת זמן מקומי (ישראל)
            slot_il = current_day.replace(hour=hour)
            
            # המרה ל-UTC לשמירה ב-DB (קריטי!!!)
            slot_utc = slot_il.astimezone(pytz.utc)
            
            slots.append({
                "pro_id": pro_id,
                "start_time": slot_utc, # נשמר כ-UTC
                "end_time": slot_utc + timedelta(hours=2),
                "is_taken": False,
                # שדה עזר לתצוגה
                "display_time": slot_il.strftime("%d/%m %H:%M") 
            })
    return slots

def seed():
    users_collection.delete_many({})
    slots_collection.delete_many({})
    
    for p in [yossi_profile, david_profile]:
        res = users_collection.insert_one(p)
        slots = generate_slots(res.inserted_id)
        slots_collection.insert_many(slots)
        print(f"✅ Created {p['business_name']} with {len(slots)} slots (UTC Synced).")

if __name__ == "__main__":
    seed()