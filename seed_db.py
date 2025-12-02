from app.core.database import users_collection, slots_collection
from datetime import datetime, timedelta
import pytz

# הגדרת אזור זמן ישראל
IL_TZ = pytz.timezone('Asia/Jerusalem')

# --- תבנית פרומפט מעודכנת: כוללת בטיחות, רישיונות וניהול יומן ---
prompt_template = """
אתה 'פיקסי', העוזר האישי והחברמן של '{business_name}'.
המטרה: אבחון, הרגעה, בניית אמון וסגירת תור.

*** הנחיות אמון ובטיחות (Trust & Safety) ***
1. **סטטוס רישיון:** אם הלקוח שואל על מקצועיות או חושש מחאפרים, הדגש: "{license_info}".
2. **בטיחות בחירום:** אם הלקוח מדווח על הצפה/פיצוץ, תן מיד הנחיה: "{safety_advice}".

*** הנחיות לניהול יומן וסגירה ***
1. **זמינות:** אל תשאל "מתי נוח לך?". בדוק את היומן למטה והצע: "יש לי מקום ביום X בשעה Y".
2. **סגירה:** ברגע שהלקוח בוחר שעה, תוציא פקודה: [DEAL: <יום ושעה> | <עיר> | <תיאור>]
3. **מיקום:** וודא שהלקוח באזור השירות.

*** הנחיות לניתוח תמונה ***
1. זהה את הרכיב (ברז, סיפון, דוד).
2. אם אתה מזהה בעיה, ציין זאת כדי להראות מקצועיות.

מחירון: {prices}
אזורי שירות: {areas}
"""

# 1. יוסי (מרכז) - הגרסה המאומתת
yossi_profile = {
    "business_name": "יוסי אינסטלציה",
    "phone_number": "972524828796", # המספר שלך לבדיקות
    "is_active": True, "plan": "pro", "created_at": datetime.now(pytz.utc),
    
    # --- תוספת: אימות ורישיון ---
    "is_verified": True,
    "license_number": "2045593",
    
    "service_areas": ["בני ברק", "רמת גן", "גבעתיים", "תל אביב"],
    "keywords": ["מים", "נזילה", "סתימה", "דוד", "כיור", "אסלה", "הצפה"],
    "social_proof": {"rating": 9.9, "review_count": 420},
    
    "system_prompt": prompt_template.format(
        business_name="יוסי אינסטלציה",
        areas="בני ברק, רמת גן, גבעתיים, תל אביב",
        prices="ביקור: 250, סתימה: 350-450, דוד: 450",
        # --- המידע שיוזרק לבוט ---
        license_info="אני עוסק מורשה ואינסטלטור מוסמך (רישיון 2045593). המערכת אימתה את התעודות שלי.",
        safety_advice="גש מיד לשיבר הראשי (ליד שעון המים) וסגור אותו כדי לעצור את ההצפה!"
    )
}

# 2. דוד (שרון)
david_profile = {
    "business_name": "דוד המהיר",
    "phone_number": "972509999999",
    "is_active": True, "plan": "basic", "created_at": datetime.now(pytz.utc),
    
    "is_verified": False, # לא מאומת
    "license_number": None,
    
    "service_areas": ["נתניה", "חדרה", "קיסריה", "כפר יונה"],
    "keywords": ["מים", "נזילה", "סתימה", "דוד"],
    "social_proof": {"rating": 4.7},
    
    "system_prompt": prompt_template.format(
        business_name="דוד המהיר",
        areas="נתניה, חדרה, קיסריה",
        prices="ביקור: 200, סתימה: 300",
        license_info="יש לי ניסיון של 10 שנים בתחום.",
        safety_advice="סגור את ברז המים הראשי של הדירה!"
    )
}

def generate_slots(pro_id, days=7):
    slots = []
    # מתחילים מהיום
    now_il = datetime.now(IL_TZ)
    start_date = now_il.replace(hour=8, minute=0, second=0, microsecond=0)
    
    for i in range(days):
        current_day = start_date + timedelta(days=i)
        
        # ימי חול בלבד (0=שני ... 4=שישי, 5=שבת, 6=ראשון)
        # נניח עובדים ראשון-חמישי (ראשון=6, שני=0, שלישי=1, רביעי=2, חמישי=3)
        if current_day.weekday() in [4, 5]: continue 
            
        # סלוטים: 08:00 עד 18:00
        for hour in range(8, 18, 2):
            slot_il = current_day.replace(hour=hour)
            # המרה ל-UTC לשמירה ב-DB
            slot_utc = slot_il.astimezone(pytz.utc)
            
            slots.append({
                "pro_id": pro_id,
                "start_time": slot_utc, 
                "end_time": slot_utc + timedelta(hours=2),
                "is_taken": False,
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
        print(f"✅ Created {p['business_name']} (Verified: {p.get('is_verified', False)}) with {len(slots)} slots.")

if __name__ == "__main__":
    seed()