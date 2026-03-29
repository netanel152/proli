"""
Demo data seed script for testing the Admin Panel UI.

Creates:
  - 4 professionals (plumber, electrician, handyman, locksmith)
  - 1 pending-approval professional
  - 20+ leads across all statuses
  - Chat history for each lead
  - Calendar slots for next 7 days
  - Audit log entries
  - Admin user (admin / admin123)

Usage:
    python scripts/seed_demo.py
"""

import asyncio
import os
import sys
import random
from datetime import datetime, timedelta, timezone

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import (
    users_collection,
    leads_collection,
    slots_collection,
    messages_collection,
    reviews_collection,
    consent_collection,
    audit_log_collection,
    admins_collection,
)
from app.core.constants import LeadStatus

# --- Data ---

PROS = [
    {
        "business_name": "יוסי אינסטלציה",
        "phone_number": "972500000001",
        "type": "plumber",
        "service_areas": ["תל אביב", "רמת גן", "גבעתיים"],
        "is_active": True,
        "is_verified": True,
        "social_proof": {"rating": 4.9, "review_count": 23},
        "location": {"type": "Point", "coordinates": [34.7818, 32.0853]},
        "system_prompt": "אתה יוסי, אינסטלטור מנוסה עם 15 שנות ניסיון. אזורי שירות: תל אביב, רמת גן, גבעתיים",
        "prices_for_prompt": "ביקור: 250₪\nפתיחת סתימה: 400₪\nהחלפת ברז: 350₪",
        "keywords": ["אינסטלטור", "שרברב", "נזילה", "סתימה", "ברז", "צנרת"],
        "license_number": "PL-2019-4521",
        "profile_image_url": "",
    },
    {
        "business_name": "משה חשמל ומיזוג",
        "phone_number": "972500000002",
        "type": "electrician",
        "service_areas": ["חולון", "בת ים", "ראשון לציון"],
        "is_active": True,
        "is_verified": True,
        "social_proof": {"rating": 4.7, "review_count": 15},
        "location": {"type": "Point", "coordinates": [34.7742, 32.0158]},
        "system_prompt": "אתה משה, חשמלאי מוסמך. תן דגש על בטיחות. אזורי שירות: חולון, בת ים, ראשון לציון",
        "prices_for_prompt": "ביקור: 300₪\nהתקנת שקע: 150₪\nתיקון קצר: 250₪",
        "keywords": ["חשמלאי", "חשמל", "שקע", "תאורה", "קצר", "מפסק"],
        "license_number": "EL-2020-7832",
        "profile_image_url": "",
    },
    {
        "business_name": "דני הנדימן",
        "phone_number": "972500000003",
        "type": "handyman",
        "service_areas": ["תל אביב", "הרצליה", "רעננה"],
        "is_active": True,
        "is_verified": False,
        "social_proof": {"rating": 4.5, "review_count": 8},
        "location": {"type": "Point", "coordinates": [34.7913, 32.1663]},
        "system_prompt": "אתה דני, איש תחזוקה כללי. אזורי שירות: תל אביב, הרצליה, רעננה",
        "prices_for_prompt": "ביקור: 200₪\nהרכבת רהיט: 300₪\nתיקון כללי: 250₪",
        "keywords": ["הנדימן", "תיקון", "הרכבה", "מדף", "דלת", "תחזוקה"],
        "license_number": "",
        "profile_image_url": "",
    },
    {
        "business_name": "אבי המנעולן",
        "phone_number": "972500000004",
        "type": "locksmith",
        "service_areas": ["פתח תקוה", "רמת גן", "בני ברק"],
        "is_active": False,
        "is_verified": True,
        "social_proof": {"rating": 4.8, "review_count": 31},
        "location": {"type": "Point", "coordinates": [34.8873, 32.0841]},
        "system_prompt": "אתה אבי, מנעולן מוסמך. אזורי שירות: פתח תקוה, רמת גן, בני ברק",
        "prices_for_prompt": "פתיחת דלת: 200₪\nהחלפת צילינדר: 350₪\nהתקנת מנעול: 400₪",
        "keywords": ["מנעולן", "מפתח", "דלת", "נעילה", "צילינדר"],
        "license_number": "LK-2018-1199",
        "profile_image_url": "",
    },
]

PENDING_PRO = {
    "business_name": "רון צבעי מקצועי",
    "phone_number": "972500000005",
    "type": "painter",
    "service_areas": ["תל אביב", "רמת גן"],
    "is_active": False,
    "is_verified": False,
    "pending_approval": True,
    "social_proof": {"rating": 0, "review_count": 0},
    "system_prompt": "",
    "prices_for_prompt": "צביעת חדר: 800₪\nצביעת דירה: 3000₪",
    "keywords": [],
    "license_number": "",
    "profile_image_url": "",
}

ISSUES = [
    "נזילה מתחת לכיור במטבח",
    "סתימה באסלה",
    "ברז לא נסגר כמו שצריך",
    "קצר חשמלי בסלון",
    "אור לא עובד בחדר שינה",
    "דלת לא נסגרת",
    "מנעול תקוע",
    "דוד שמש לא מחמם",
    "נזילה מהתקרה",
    "שקע חשמל לא עובד",
    "הצפה בחדר אמבטיה",
    "התקנת מדף מרחף",
    "תיקון ארון מטבח",
    "החלפת גוף תאורה",
    "התקנת מזגן",
]

ADDRESSES = [
    "רחוב הרצל 15, תל אביב",
    "שדרות רוטשילד 42, תל אביב",
    "רחוב סוקולוב 8, חולון",
    "רחוב ויצמן 30, רמת גן",
    "רחוב ז'בוטינסקי 12, בני ברק",
    "רחוב אהרונוביץ 5, בת ים",
    "דרך בן גוריון 100, ראשון לציון",
    "רחוב אוסישקין 22, הרצליה",
    "רחוב ביאליק 18, רעננה",
    "רחוב העצמאות 7, פתח תקוה",
]

CHAT_FLOWS = [
    [
        ("user", "היי, צריך עזרה דחופה"),
        ("model", "היי! אני כאן בשבילך. מה קרה?"),
        ("user", "{issue}"),
        ("model", "הבנתי, {issue}. מיד אחפש לך איש מקצוע פנוי באזור שלך. באיזו עיר אתה?"),
        ("user", "{city}"),
        ("model", "מעולה! מצאתי בשבילך. הוא יכול להגיע {time}. מתאים לך?"),
    ],
    [
        ("user", "שלום, יש לי בעיה בבית"),
        ("model", "שלום! ספר לי מה קורה ואני אעזור לך"),
        ("user", "{issue}"),
        ("model", "אני מבין. בוא נמצא לך פתרון מהיר. איפה אתה גר?"),
        ("user", "{city}"),
        ("model", "אני בודק זמינות של אנשי מקצוע באזור {city}..."),
        ("model", "מצאתי! יש לנו מקצוען מעולה באזור. הוא פנוי {time}. לסגור תור?"),
        ("user", "כן בבקשה"),
        ("model", "מעולה! התור נקבע. ישלח לך הודעת אישור. שיהיה בהצלחה!"),
    ],
    [
        ("user", "אהלן, מחפש {pro_type}"),
        ("model", "אהלן! בטח, אני אעזור לך. מה הבעיה בדיוק?"),
        ("user", "{issue}"),
        ("model", "הבנתי. באיזו עיר?"),
        ("user", "{city}"),
    ],
]

STATUSES_DISTRIBUTION = [
    LeadStatus.NEW, LeadStatus.NEW, LeadStatus.NEW,
    LeadStatus.CONTACTED, LeadStatus.CONTACTED,
    LeadStatus.BOOKED, LeadStatus.BOOKED, LeadStatus.BOOKED,
    LeadStatus.COMPLETED, LeadStatus.COMPLETED, LeadStatus.COMPLETED, LeadStatus.COMPLETED,
    LeadStatus.REJECTED,
    LeadStatus.CLOSED,
    "cancelled",
]

PHONES = [f"97250{random.randint(1000000, 9999999)}" for _ in range(25)]


async def clear_db():
    print("Clearing database...")
    await users_collection.delete_many({})
    await leads_collection.delete_many({})
    await slots_collection.delete_many({})
    await messages_collection.delete_many({})
    await reviews_collection.delete_many({})
    await consent_collection.delete_many({})
    await audit_log_collection.delete_many({})
    await admins_collection.delete_many({})
    print("Done.")


async def create_pros():
    print("Creating professionals...")
    pro_ids = []
    for pro_data in PROS:
        doc = {
            **pro_data,
            "role": "professional",
            "plan": "basic",
            "created_at": datetime.now(timezone.utc) - timedelta(days=random.randint(14, 60)),
        }
        result = await users_collection.insert_one(doc)
        pro_ids.append(result.inserted_id)
        print(f"  + {pro_data['business_name']} ({pro_data['type']})")

    # Pending approval
    pending = {
        **PENDING_PRO,
        "role": "professional",
        "plan": "basic",
        "created_at": datetime.now(timezone.utc) - timedelta(hours=6),
    }
    await users_collection.insert_one(pending)
    print(f"  + {PENDING_PRO['business_name']} (pending approval)")

    return pro_ids


async def create_slots(pro_ids):
    print("Creating calendar slots (next 7 days)...")
    slots = []
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    for pro_id in pro_ids:
        for day_offset in range(7):
            current_day = today + timedelta(days=day_offset)
            # Skip Friday/Saturday
            if current_day.weekday() in [4, 5]:
                continue
            for hour in range(8, 18):
                start_time = current_day.replace(hour=hour)
                end_time = start_time + timedelta(hours=1)
                is_taken = random.choice([True, False, False, False])  # 25% taken
                slots.append({
                    "pro_id": pro_id,
                    "start_time": start_time,
                    "end_time": end_time,
                    "is_taken": is_taken
                })

    if slots:
        await slots_collection.insert_many(slots)
    print(f"  Created {len(slots)} slots.")


async def create_leads(pro_ids):
    print("Creating leads...")
    leads_to_create = []
    messages_to_create = []

    # Active pros only
    active_pro_ids = pro_ids[:3]  # First 3 are active

    for i in range(20):
        status = random.choice(STATUSES_DISTRIBUTION)
        # Ensure status is a string value
        if hasattr(status, 'value'):
            status_val = status.value if hasattr(status, 'value') else status
        else:
            status_val = status

        pro_id = random.choice(active_pro_ids) if status_val != "new" else (random.choice(active_pro_ids) if random.random() > 0.4 else None)
        created_offset = random.randint(0, 14)
        created_at = datetime.now(timezone.utc) - timedelta(days=created_offset, hours=random.randint(0, 12))

        issue = random.choice(ISSUES)
        address = random.choice(ADDRESSES)
        phone = PHONES[i % len(PHONES)]
        chat_id = f"{phone}@c.us"

        lead = {
            "chat_id": chat_id,
            "pro_id": pro_id,
            "status": status_val,
            "issue_type": issue,
            "full_address": address,
            "appointment_time": (created_at + timedelta(days=1)).strftime("%Y-%m-%d %H:00") if status_val in ["booked", "completed"] else "?",
            "created_at": created_at,
            "details": f"{issue} | {address}",
            "source": "whatsapp",
        }
        leads_to_create.append(lead)

        # Chat history
        flow = random.choice(CHAT_FLOWS)
        city = address.split(",")[-1].strip() if "," in address else "תל אביב"
        time_options = ["היום בשעה 14:00", "מחר בבוקר", "היום אחה\"צ", "מחר ב-10:00"]
        pro_types = ["אינסטלטור", "חשמלאי", "הנדימן", "מנעולן"]

        for j, (role, text) in enumerate(flow):
            msg_text = text.format(
                issue=issue,
                city=city,
                time=random.choice(time_options),
                pro_type=random.choice(pro_types),
            )
            messages_to_create.append({
                "chat_id": chat_id,
                "role": role,
                "text": msg_text,
                "timestamp": created_at + timedelta(seconds=j * 20),
            })

    # Insert
    for lead in leads_to_create:
        await leads_collection.insert_one(lead)

    if messages_to_create:
        await messages_collection.insert_many(messages_to_create)

    print(f"  Created {len(leads_to_create)} leads, {len(messages_to_create)} messages.")


async def create_reviews(pro_ids):
    print("Creating reviews...")
    reviews = []
    for pro_id in pro_ids[:3]:
        for _ in range(random.randint(2, 6)):
            reviews.append({
                "pro_id": pro_id,
                "rating": random.choice([4, 4, 5, 5, 5, 3]),
                "text": random.choice([
                    "שירות מעולה, הגיע בזמן",
                    "מקצועי ואמין",
                    "עבודה טובה, ממליץ",
                    "תיקן מהר ובמחיר הוגן",
                    "בסדר, אבל קצת יקר",
                    "מעולה! הכל עובד מושלם",
                ]),
                "created_at": datetime.now(timezone.utc) - timedelta(days=random.randint(1, 30)),
            })
    if reviews:
        await reviews_collection.insert_many(reviews)
    print(f"  Created {len(reviews)} reviews.")


async def create_audit_log():
    print("Creating audit log entries...")
    entries = []
    now = datetime.now(timezone.utc)

    actions = [
        ("admin", "login", {}),
        ("admin", "create_pro", {"name": "יוסי אינסטלציה"}),
        ("admin", "create_pro", {"name": "משה חשמל ומיזוג"}),
        ("admin", "edit_lead", {"lead_id": "demo1", "changes": {"status": "booked"}}),
        ("admin", "edit_lead", {"lead_id": "demo2", "changes": {"status": "completed"}}),
        ("admin", "trigger_scheduler", {}),
        ("admin", "edit_scheduler_config", {"is_active": True, "run_time": "08:00"}),
        ("admin", "approve_pro", {"name": "דני הנדימן"}),
        ("admin", "edit_safety_settings", {"stale_monitor": True, "sos_healer": True}),
        ("admin", "login", {}),
        ("admin", "delete_lead", {"lead_id": "demo3"}),
        ("admin", "send_completion_check", {"lead_id": "demo4"}),
    ]

    for i, (user, action, details) in enumerate(actions):
        entries.append({
            "admin_user": user,
            "action": action,
            "details": details,
            "timestamp": now - timedelta(hours=len(actions) - i, minutes=random.randint(0, 30)),
        })

    if entries:
        await audit_log_collection.insert_many(entries)
    print(f"  Created {len(entries)} audit log entries.")


async def create_admin_user():
    """Create a demo admin user: admin / ProliAdmin123456"""
    print("Creating admin user (admin / ProliAdmin123456)...")
    import bcrypt
    password_hash = bcrypt.hashpw("ProliAdmin123456".encode(), bcrypt.gensalt()).decode()
    await admins_collection.insert_one({
        "username": "admin",
        "password_hash": password_hash,
        "role": "owner",
        "created_at": datetime.now(timezone.utc),
    })
    print("  Admin user created. Login: admin / ProliAdmin123456")


async def create_settings():
    """Create scheduler config in settings."""
    from motor.motor_asyncio import AsyncIOMotorClient
    from app.core.config import settings as app_settings
    import certifi

    ca = certifi.where() if "+srv" in app_settings.MONGO_URI else None
    kwargs = {"tlsCAFile": ca} if ca else {}
    client = AsyncIOMotorClient(app_settings.MONGO_URI, **kwargs)
    db = client.proli_db
    settings_col = db.settings

    await settings_col.delete_many({"_id": "scheduler_config"})
    await settings_col.insert_one({
        "_id": "scheduler_config",
        "is_active": True,
        "run_time": "08:00",
        "trigger_now": False,
        "stale_monitor_active": True,
        "sos_healer_active": True,
        "sos_reporter_active": True,
        "last_run_date": datetime.now(timezone.utc) - timedelta(hours=3),
    })
    print("  Scheduler settings created.")


async def seed():
    print("\n=== Proli Demo Seed ===\n")

    await clear_db()
    pro_ids = await create_pros()
    await create_slots(pro_ids)
    await create_leads(pro_ids)
    await create_reviews(pro_ids)
    await create_audit_log()
    await create_admin_user()
    await create_settings()

    print("\n=== Seed Complete! ===")
    print("Start the admin panel with: streamlit run admin_panel/main.py")
    print("Login: admin / admin123\n")


if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(seed())
