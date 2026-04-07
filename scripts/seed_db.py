import asyncio
import os
import sys
import random
import bcrypt
from datetime import datetime, timedelta, timezone
from faker import Faker

# Add project root to sys.path
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

fake = Faker('he_IL')

# --- Constants ---
AREAS_CENTER = ["Tel Aviv", "Ramat Gan", "Givatayim"]
AREAS_SOUTH = ["Holon", "Bat Yam", "Rishon LeTsiyon"]

async def clear_db():
    print("🗑️  Clearing Database...")
    await users_collection.delete_many({})
    await leads_collection.delete_many({})
    await slots_collection.delete_many({})
    await messages_collection.delete_many({})
    await reviews_collection.delete_many({})
    await consent_collection.delete_many({})
    await audit_log_collection.delete_many({})
    await admins_collection.delete_many({})
    print("✅ Database Cleared.")

async def create_pros():
    print("👷 Creating Professionals...")
    
    pros = [
        {
            "business_name": 'נתנאל אינסטלציה',
            "phone_number": '972524828796',
            "role": "professional",
            "type": "plumber",
            "service_areas": AREAS_CENTER,
            "categories": ["plumber"],
            "is_active": True,
            "is_verified": True,
            "social_proof": {"rating": 4.9, "review_count": 12},
            "location": {"type": "Point", "coordinates": [34.7818, 32.0853]},
            "system_prompt": "אתה נתנאל, אינסטלטור מנוסה עם 15 שנות ניסיון. היה אדיב ומקצועי. אזורי שירות: Tel Aviv, Ramat Gan, Givatayim",
            "price_list": {"ביקור": 250, "פתיחת סתימה": 400},
            "prices_for_prompt": "ביקור: 250₪\nפתיחת סתימה: 400₪",
            "keywords": ["אינסטלטור", "שרברב", "נזילה", "סתימה", "ברז"],
            "plan": "basic",
            "created_at": datetime.now(timezone.utc) - timedelta(days=30),
        },
        {
            "business_name": "משה חשמל ומיזוג",
            "phone_number": "972500000002",
            "role": "professional",
            "type": "electrician",
            "service_areas": AREAS_SOUTH,
            "categories": ["electrician"],
            "is_active": True,
            "is_verified": True,
            "social_proof": {"rating": 4.7, "review_count": 8},
            "location": {"type": "Point", "coordinates": [34.7742, 32.0158]},
            "system_prompt": "אתה משה, חשמלאי מוסמך. תן דגש על בטיחות. אזורי שירות: Holon, Bat Yam, Rishon LeTsiyon",
            "price_list": {"ביקור": 300, "התקנת שקע": 150},
            "prices_for_prompt": "ביקור: 300₪\nהתקנת שקע: 150₪",
            "keywords": ["חשמלאי", "חשמל", "שקע", "תאורה", "קצר"],
            "plan": "basic",
            "created_at": datetime.now(timezone.utc) - timedelta(days=30),
        },
    ]
    
    pro_ids = []
    for pro_data in pros:
        result = await users_collection.insert_one(pro_data)
        pro_ids.append(result.inserted_id)
        print(f"   Created Pro: {pro_data['business_name']} ({result.inserted_id})")
        
    return pro_ids

async def create_slots(pro_ids):
    print("📅 Creating Calendar Slots (Next 7 days)...")
    slots = []
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    
    for pro_id in pro_ids:
        for day_offset in range(7):
            current_day = today + timedelta(days=day_offset)
            # Create slots from 08:00 to 18:00
            for hour in range(8, 18):
                start_time = current_day.replace(hour=hour)
                end_time = start_time + timedelta(hours=1)
                
                is_taken = random.choice([True, False, False]) # 33% chance of being taken
                
                slot = {
                    "pro_id": pro_id,
                    "start_time": start_time,
                    "end_time": end_time,
                    "is_taken": is_taken
                }
                slots.append(slot)
    
    if slots:
        await slots_collection.insert_many(slots)
        print(f"✅ Created {len(slots)} slots.")

async def create_leads(pro_ids):
    print("💼 Creating Rich Leads...")
    
    leads_to_create = []
    messages_to_create = []
    
    # --- Past Completed/Rated Leads (Revenue) ---
    print("   Generating 10 Completed/Rated leads...")
    for _ in range(10):
        pro_id = random.choice(pro_ids)
        status = random.choice([LeadStatus.COMPLETED, "rated"]) # 'rated' usually maps to completed with review
        created_at = datetime.now(timezone.utc) - timedelta(days=random.randint(1, 7))
        chat_id = f"9725{fake.random_number(digits=8)}@c.us"
        
        lead = {
            "chat_id": chat_id,
            "pro_id": pro_id,
            "status": status,
            "issue_type": random.choice(["נזילה", "קצר חשמלי", "התקנת גוף תאורה", "סתימה בכיור"]),
            "full_address": f"{fake.street_address()}, {fake.city()}",
            "created_at": created_at,
            "details": "תוקן בהצלחה"
        }
        leads_to_create.append(lead)
    
    # --- Active Booking (Tomorrow) ---
    print("   Generating 1 Active Booking...")
    pro_id = pro_ids[0] # Assign to Yossi
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    chat_id_booked = f"9725{fake.random_number(digits=8)}@c.us"
    
    lead_booked = {
        "chat_id": chat_id_booked,
        "pro_id": pro_id,
        "status": LeadStatus.BOOKED,
        "issue_type": "החלפת ברז",
        "full_address": "רחוב הרצל 15, תל אביב",
        "created_at": datetime.now(timezone.utc) - timedelta(hours=2), # Booked 2 hours ago
        "appointment_time": tomorrow.strftime("%Y-%m-%d 10:00"),
        "details": "תיאום למחר בבוקר"
    }
    leads_to_create.append(lead_booked)
    
    # --- The 'SOS' Test Case ---
    print("   Generating 1 SOS Stale Lead (2 hours old, NEW)...")
    chat_id_sos = f"9725{fake.random_number(digits=8)}@c.us"
    
    lead_sos = {
        "chat_id": chat_id_sos,
        "status": LeadStatus.NEW,
        "issue_type": "דוד לא מחמם",
        "full_address": "סוקולוב 20, חולון",
        # Created 2 hours ago -> Should trigger SOS (Timeout is 60 mins)
        "created_at": datetime.now(timezone.utc) - timedelta(hours=2), 
        "details": "הלקוח מחכה לתשובה..."
    }
    leads_to_create.append(lead_sos)
    
    # Insert Leads
    inserted_leads = []
    for lead in leads_to_create:
        res = await leads_collection.insert_one(lead)
        lead["_id"] = res.inserted_id
        inserted_leads.append(lead)
        
        # Generate Chat History for this lead
        msgs = create_chat_history(lead["chat_id"], lead["created_at"])
        messages_to_create.extend(msgs)
        
    # Insert Messages
    if messages_to_create:
        await messages_collection.insert_many(messages_to_create)
        print(f"✅ Created {len(leads_to_create)} leads and {len(messages_to_create)} messages.")

def create_chat_history(chat_id, start_time):
    history = []
    
    # User: Hi
    history.append({
        "chat_id": chat_id,
        "role": "user",
        "text": "היי, יש לי בעיה בבית",
        "timestamp": start_time
    })

    # Assistant: details?
    history.append({
        "chat_id": chat_id,
        "role": "model",
        "text": "היי! אני כאן לעזור. מה בדיוק הבעיה ואיפה?",
        "timestamp": start_time + timedelta(seconds=15)
    })

    # User: Issue
    history.append({
        "chat_id": chat_id,
        "role": "user",
        "text": "יש לי נזילה במטבח מתחת לכיור",
        "timestamp": start_time + timedelta(seconds=45)
    })

    # Assistant: searching
    history.append({
        "chat_id": chat_id,
        "role": "model",
        "text": "הבנתי, אני בודק איזה אינסטלטור פנוי באזור שלך...",
        "timestamp": start_time + timedelta(seconds=60)
    })
    
    return history

async def create_pending_pro():
    print("👷 Creating Pending Professional (for approval flow testing)...")
    pending_pro = {
        "business_name": "דוד חשמל - ממתין לאישור",
        "phone_number": "972500000099",
        "role": "professional",
        "type": "electrician",
        "service_areas": ["Jerusalem"],
        "categories": ["electrician"],
        "is_active": False,
        "is_verified": False,
        "social_proof": {"rating": 0, "review_count": 0},
        "plan": "basic",
        "created_at": datetime.now(timezone.utc),
    }
    result = await users_collection.insert_one(pending_pro)
    print(f"   Created Pending Pro: {pending_pro['business_name']} ({result.inserted_id})")


async def create_consent_records(pro_phones: list[str]):
    """Seed consent records for test pro phones so they skip the consent gate."""
    print("✅ Creating Consent Records for test pros...")
    records = [
        {
            "chat_id": f"{phone}@c.us",
            "accepted": True,
            "timestamp": datetime.now(timezone.utc),
        }
        for phone in pro_phones
    ]
    await consent_collection.insert_many(records)
    print(f"   Created {len(records)} consent records.")


async def create_staging_admin():
    """Create a staging admin account in the DB (username: admin, password: admin123)."""
    print("🔐 Creating Staging Admin Account...")
    existing = await admins_collection.find_one({"username": "admin"})
    if existing:
        print("   Admin already exists, skipping.")
        return
    password_hash = bcrypt.hashpw(b"admin123", bcrypt.gensalt()).decode()
    await admins_collection.insert_one({
        "username": "admin",
        "password_hash": password_hash,
        "role": "owner",
        "created_at": datetime.now(timezone.utc),
    })
    print("   Created admin / admin123 (staging only — change for production!)")


async def seed():
    print("Starting Seed Process...")

    await clear_db()
    pro_ids = await create_pros()
    await create_slots(pro_ids)
    await create_leads(pro_ids)
    await create_pending_pro()
    await create_consent_records(["972524828796", "972500000002"])
    await create_staging_admin()

    print("Seed Complete! Staging environment is ready.")
    print("  Admin login: username=admin  password=admin123")

if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(seed())
