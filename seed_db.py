from app.core.database import users_collection
from datetime import datetime

# תבנית פרומפט משופרת - ראייה חדה ולידים עשירים
prompt_template = """
אתה 'פיקסי', העוזר האישי והחברמן של '{business_name}'.
המטרה שלך: להרגיע, לאבחן במדויק, ולסגור תור.

*** הנחיות לניתוח תמונה (Image Analysis) ***
כשהלקוח שולח תמונה:
1. תאר לעצמך מה אתה רואה (ברז? סיפון? רטיבות בקיר? מים עומדים?).
2. **אל תקפוץ למסקנות!** אם אתה רואה ברז, תשאל: "אני רואה את הברז, יש ממנו נזילה?". אם אתה רואה כיור מלא מים, תשאל: "נראה שיש סתימה, נכון?"
3. תהיה מקצועי. אל תמציא תקלות שלא רואים.

*** הנחיות לסגירת עסקה (The Money Time) ***
ברגע שנסגרה שעה, תוציא את הפקודה הבאה עם **כל הפרטים**:
Format: [DEAL: <Day & Time> | <City & Address> | <Full Problem Description>]

Example:
User: "Come tomorrow at 10 to Netanya, leaky tap."
You: "[DEAL: מחר 10:00 | נתניה | נזילה בברז מטבח] מעולה! רשמתי את זה."

*** שפה והתנהגות ***
- דבר עברית קצרה וטבעית.
- בדוק היסטוריה לפני שאלות (אל תשאל שוב עיר אם כבר אמרו לך).

מחירון: {prices}
אזורי שירות: {areas}
"""

# 1. יוסי (מרכז)
yossi_profile = {
    "business_name": "יוסי אינסטלציה",
    "phone_number": "972524828796", # שים את המספר שלך
    "is_active": True, "plan": "pro", "created_at": datetime.now(),
    "service_areas": ["בני ברק", "רמת גן", "גבעתיים", "תל אביב"],
    "social_proof": {"rating": 9.9},
    "system_prompt": prompt_template.format(
        business_name="יוסי אינסטלציה",
        areas="בני ברק, רמת גן, גבעתיים, תל אביב",
        prices="ביקור: 250, סתימה: 350-450, החלפת ברז: 300"
    )
}

# 2. דוד (שרון)
david_profile = {
    "business_name": "דוד המהיר",
    "phone_number": "972509999999",
    "is_active": True, "plan": "basic", "created_at": datetime.now(),
    "service_areas": ["נתניה", "חדרה", "קיסריה", "כפר יונה"],
    "social_proof": {"rating": 4.7},
    "system_prompt": prompt_template.format(
        business_name="דוד המהיר",
        areas="נתניה, חדרה, קיסריה",
        prices="ביקור: 200, סתימה: 300"
    )
}

def seed():
    users_collection.delete_many({})
    users_collection.insert_many([yossi_profile, david_profile])
    print("✅ DB Updated: Better Vision & Richer Leads!")

if __name__ == "__main__":
    seed()