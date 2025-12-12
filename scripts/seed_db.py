import sys
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Add the project root directory to the Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import users_collection, slots_collection, leads_collection, messages_collection
from datetime import datetime, timedelta
import pytz
import random

# הגדרת אזור זמן ישראל
IL_TZ = pytz.timezone('Asia/Jerusalem')

# --- תבנית פרומפט מעודכנת ---
prompt_template = """
אתה 'פיקסי', העוזר האישי והחברמן של '{business_name}'.
המטרה: אבחון, הרגעה, בניית אמון וסגירת תור.

*** הנחיות אמון ובטיחות (Trust & Safety) ***
1. **סטטוס רישיון:** אם הלקוח שואל על מקצועיות או חושש מחאפרים, הדגש: "{license_info}".
2. **בטיחות בחירום:** אם הלקוח מדווח על הצפה/פיצוץ/סכנה, תן מיד הנחיה: "{safety_advice}".

*** הנחיות לניהול יומן וסגירה ***
1. **זמינות:** אל תשאל "מתי נוח לך?". בדוק את היומן למטה והצע: "יש לי מקום ביום X בשעה Y".
2. **סגירה:** ברגע שהלקוח בוחר שעה, תוציא פקודה: [DEAL: <יום ושעה> | <עיר> | <תיאור>]
3. **מיקום:** וודא שהלקוח באזור השירות ({areas}). אם לא - תנצל בנימוס.

*** הנחיות לניתוח תמונה ***
1. זהה את הרכיב בתמונה.
2. אם אתה מזהה תקלה, ציין זאת כדי להראות מקצועיות.

מחירון: {prices}
אזורי שירות: {areas}
"""

# 1. יוסי (מרכז) - הגרסה המאומתת
yossi_profile = {
    "business_name": "יוסי אינסטלציה",
    "phone_number": "972524828796", 
    "is_active": True, "plan": "pro", "created_at": datetime.now(pytz.utc),
    "is_verified": True,
    "license_number": "2045593",
    "service_areas": ["בני ברק", "רמת גן", "גבעתיים", "תל אביב"],
    "keywords": ["מים", "נזילה", "סתימה", "דוד", "כיור", "אסלה", "הצפה", "אינסטלטור"],
    "social_proof": {"rating": 4.9, "review_count": 420},
    "system_prompt": prompt_template.format(
        business_name="יוסי אינסטלציה",
        areas="בני ברק, רמת גן, גבעתיים, תל אביב",
        prices="ביקור: 250, סתימה: 350-450, דוד: 450",
        license_info="אני עוסק מורשה ואינסטלטור מוסמך (רישיון 2045593). המערכת אימתה את התעודות שלי.",
        safety_advice="גש מיד לשיבר הראשי (ליד שעון המים) וסגור אותו כדי לעצור את ההצפה!"
    )
}

# 2. דוד (שרון)
david_profile = {
    "business_name": "דוד המהיר",
    "phone_number": "972509999999",
    "is_active": True, "plan": "basic", "created_at": datetime.now(pytz.utc),
    "is_verified": False, 
    "license_number": None,
    "service_areas": ["נתניה", "חדרה", "קיסריה", "כפר יונה"],
    "keywords": ["מים", "נזילה", "סתימה", "דוד", "אינסטלטור"],
    "social_proof": {"rating": 4.5, "review_count": 12},
    "system_prompt": prompt_template.format(
        business_name="דוד המהיר",
        areas="נתניה, חדרה, קיסריה",
        prices="ביקור: 200, סתימה: 300",
        license_info="יש לי ניסיון של 10 שנים בתחום.",
        safety_advice="סגור את ברז המים הראשי של הדירה!"
    )
}

# 3. משה חשמל (חשמלאי)
moshe_profile = {
    "business_name": "משה חשמל ובטיחות",
    "phone_number": "972541112222",
    "is_active": True, "plan": "pro", "created_at": datetime.now(pytz.utc),
    "is_verified": True,
    "license_number": "EL-998877",
    "service_areas": ["תל אביב", "חולון", "בת ים", "ראשון לציון"],
    "keywords": ["חשמל", "קצר", "פחת", "שקע", "מנורה", "דוד שמש", "חשמלאי"],
    "social_proof": {"rating": 5.0, "review_count": 85},
    "system_prompt": prompt_template.format(
        business_name="משה חשמל ובטיחות",
        areas="תל אביב, חולון, בת ים, ראשון לציון",
        prices="ביקור: 300, החלפת שקע: 150, לוח חשמל: 1200",
        license_info="אני חשמלאי מוסמך (רישיון EL-998877). עבודה לפי התקן בלבד.",
        safety_advice="אל תיגע בכלום! גש ללוח החשמל והורד את המפסק הראשי מיד."
    ).replace("רכיב (ברז, סיפון, דוד)", "רכיב (לוח, שקע, מפסק)") # התאמה לחשמל
}

def generate_slots(pro_id, days=14):
    slots = []
    now_il = datetime.now(IL_TZ)
    # Start from next round hour
    start_date = now_il.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    
    for i in range(days):
        current_day = start_date + timedelta(days=i)
        
        # Skip Friday/Saturday (usually)
        if current_day.weekday() in [4, 5]: continue 
            
        # 08:00 - 18:00
        for hour in range(8, 18, 1): # 1 hour slots
            slot_il = current_day.replace(hour=hour, minute=0)
            
            # Don't create slots in the past
            if slot_il < now_il: continue

            slot_utc = slot_il.astimezone(pytz.utc)
            
            # Randomly mark some as taken to simulate a real calendar
            is_taken = random.random() < 0.2
            
            slots.append({
                "pro_id": pro_id,
                "start_time": slot_utc, 
                "end_time": slot_utc + timedelta(hours=1),
                "is_taken": is_taken,
            })
    return slots

def seed_leads(pro_id, pro_name):
    """Seed some fake leads for the dashboard"""
    statuses = ["New", "contacted", "booked", "completed", "cancelled"]
    fake_leads = []
    
    for _ in range(5):
        status = random.choice(statuses)
        created_il = datetime.now(IL_TZ) - timedelta(days=random.randint(0, 5))
        created_utc = created_il.astimezone(pytz.utc)
        
        fake_leads.append({
            "chat_id": f"97250{random.randint(1000000, 9999999)}@c.us",
            "pro_id": pro_id,
            "details": f"תקלה ב{random.choice(['מטבח', 'אמבטיה', 'חצר'])} - {pro_name}",
            "status": status,
            "created_at": created_utc,
            "waiting_for_rating": (status == "completed"),
            "media_url": None
        })
    
    if fake_leads:
        leads_collection.insert_many(fake_leads)

def seed():
    print("🌱 Seeding DB...")
    
    # Clear all collections
    users_collection.delete_many({})
    slots_collection.delete_many({})
    leads_collection.delete_many({})
    messages_collection.delete_many({})
    
    for p in [yossi_profile, david_profile, moshe_profile]:
        res = users_collection.insert_one(p)
        pro_id = res.inserted_id
        
        # Slots
        slots = generate_slots(pro_id)
        if slots:
            slots_collection.insert_many(slots)
            
        # Leads
        seed_leads(pro_id, p['business_name'])
        
        print(f"✅ Created {p['business_name']} with {len(slots)} slots and sample leads.")

    print("🚀 Database Seeded Successfully!")

if __name__ == "__main__":
    seed()
