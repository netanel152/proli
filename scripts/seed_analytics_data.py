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
    reviews_collection,
)
from app.core.constants import LeadStatus

fake = Faker('he_IL')

async def seed_analytics():
    print("🧹 Cleaning up old analytics data...")
    # Only clean leads and pros we created for this seeder to avoid wiping everything
    # But for a "seeder" often it's better to start fresh.
    # We'll just delete all to be sure we have clean synthetic data.
    await leads_collection.delete_many({})
    # Keep admins, but refresh pros
    await users_collection.delete_many({"role": "professional"})
    await reviews_collection.delete_many({})

    print("👷 Creating 4 Professionals with token usage...")
    pro_types = ["plumber", "electrician", "locksmith", "handyman"]
    pro_ids = []
    for i in range(4):
        pro_data = {
            "business_name": fake.company(),
            "phone_number": f"9725{random.randint(10000000, 99999999)}",
            "role": "professional",
            "type": pro_types[i],
            "service_areas": ["Tel Aviv", "Ramat Gan", "Holon"],
            "is_active": True,
            "total_tokens_used": random.randint(5000, 50000),
            "created_at": datetime.now(timezone.utc) - timedelta(days=60),
        }
        res = await users_collection.insert_one(pro_data)
        pro_ids.append(res.inserted_id)
        print(f"   Created Pro: {pro_data['business_name']} ({res.inserted_id})")

    print("💼 Generating 80 Leads distributed over 30 days...")
    statuses = [s.value for s in LeadStatus]
    # Weighting statuses for a realistic funnel (8 statuses now)
    # new, contacted, booked, completed, rejected, closed, cancelled, pending_admin_review
    status_weights = [0.15, 0.2, 0.15, 0.2, 0.1, 0.05, 0.05, 0.1]
    
    leads_to_create = []
    now = datetime.now(timezone.utc)
    
    for _ in range(80):
        created_at = now - timedelta(days=random.randint(0, 30), hours=random.randint(0, 23))
        status = random.choices(statuses, weights=status_weights)[0]
        
        # Only assign pro if status is not 'new' (usually)
        pro_id = random.choice(pro_ids) if status != "new" else None
        
        lead = {
            "chat_id": f"9725{random.randint(10000000, 99999999)}@c.us",
            "pro_id": pro_id,
            "status": status,
            "issue_type": random.choice(["סתימה", "קצר", "פריצת מנעול", "תיקון קיר"]),
            "full_address": f"{fake.street_address()}, {fake.city()}",
            "created_at": created_at,
            "updated_at": created_at + timedelta(hours=random.randint(1, 48)),
        }
        leads_to_create.append(lead)

    if leads_to_create:
        await leads_collection.insert_many(leads_to_create)
    
    print(f"✅ Created 80 leads.")

    print("⭐ Creating some reviews for performance metrics...")
    reviews = []
    for pro_id in pro_ids:
        for _ in range(random.randint(3, 8)):
            reviews.append({
                "pro_id": pro_id,
                "rating": random.randint(3, 5),
                "comment": fake.sentence(),
                "created_at": now - timedelta(days=random.randint(0, 20))
            })
    if reviews:
        await reviews_collection.insert_many(reviews)
    
    print(f"✅ Created {len(reviews)} reviews.")
    print("\n🚀 Analytics seeding complete!")

if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(seed_analytics())
