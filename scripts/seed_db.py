import asyncio
import os
import sys
import random
from datetime import datetime, timedelta, timezone
from faker import Faker

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import (
    users_collection,
    leads_collection,
    slots_collection,
    messages_collection,
    reviews_collection
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
    print("✅ Database Cleared.")

async def create_pros():
    print("👷 Creating Professionals...")
    
    pros = [
        {
            "business_name": "יוסי אינסטלציה",
            "phone_number": "972500000001",
            "service_areas": AREAS_CENTER,
            "categories": ["Plumber"],
            "is_active": True,
            "rating": 4.9,
            "system_prompt": "אתה יוסי, אינסטלטור מנוסה. היה אדיב ומקצועי.",
            "price_list": {"ביקור": 250, "פתיחת סתימה": 400}
        },
        {
            "business_name": "משה חשמל ומיזוג",
            "phone_number": "972500000002",
            "service_areas": AREAS_SOUTH,
            "categories": ["Electrician"],
            "is_active": True,
            "rating": 4.7,
            "system_prompt": "אתה משה, חשמלאי מוסמך. תן דגש על בטיחות.",
            "price_list": {"ביקור": 300, "התקנת שקע": 150}
        }
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
    
    # --- 1. Past Completed/Rated Leads (Revenue) ---
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
    
    # --- 2. Active Booking (Tomorrow) ---
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
    
    # --- 3. The 'SOS' Test Case ---
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
    
    # 1. User: Hi
    history.append({
        "chat_id": chat_id,
        "sender": "user",
        "content": "היי, יש לי בעיה בבית",
        "timestamp": start_time
    })
    
    # 2. Assistant: details?
    history.append({
        "chat_id": chat_id,
        "sender": "assistant",
        "content": "היי! אני כאן לעזור. מה בדיוק הבעיה ואיפה?",
        "timestamp": start_time + timedelta(seconds=15)
    })
    
    # 3. User: Issue
    history.append({
        "chat_id": chat_id,
        "sender": "user",
        "content": "יש לי נזילה במטבח מתחת לכיור",
        "timestamp": start_time + timedelta(seconds=45)
    })
    
    # 4. Assistant: searching
    history.append({
        "chat_id": chat_id,
        "sender": "assistant",
        "content": "הבנתי, אני בודק איזה אינסטלטור פנוי באזור שלך...",
        "timestamp": start_time + timedelta(seconds=60)
    })
    
    return history

async def seed():
    print("🌱 Starting Seed Process...")
    
    await clear_db()
    pro_ids = await create_pros()
    await create_slots(pro_ids)
    await create_leads(pro_ids)
    
    print("🌳 Seed Complete! Environment is ready.")

if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(seed())
