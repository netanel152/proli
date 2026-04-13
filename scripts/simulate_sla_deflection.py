import asyncio
import sys
import os
from datetime import datetime, timedelta, timezone

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import leads_collection
from app.services.state_manager_service import StateManager
from app.core.constants import UserStates, LeadStatus, WorkerConstants

async def simulate_sla_deflection(chat_id: str):
    """
    Simulates a 15-minute silence for a specific chat_id in PAUSED_FOR_HUMAN state.
    This will cause the SLA Monitor (run by the worker) to trigger on its next pass.
    """
    if not chat_id.endswith("@c.us"):
        chat_id = f"{chat_id}@c.us"

    print(f"🛠️ Simulating SLA Deflection for: {chat_id}")

    # 1. Update/Create Lead in DB
    # We look for an active lead or create a dummy one
    lead = await leads_collection.find_one(
        {"chat_id": chat_id, "status": {"$in": [LeadStatus.NEW, LeadStatus.BOOKED]}},
        sort=[("created_at", -1)]
    )

    # Threshold is 15 mins (900s)
    stale_time = datetime.now(timezone.utc) - timedelta(seconds=WorkerConstants.PAUSE_TTL_SECONDS + 60)

    if lead:
        await leads_collection.update_one(
            {"_id": lead["_id"]},
            {"$set": {"is_paused": True, "paused_at": stale_time}}
        )
        print(f"✅ Updated existing lead {lead['_id']} to be stale (paused_at: {stale_time})")
    else:
        # Create a dummy lead if none exists
        new_lead = {
            "chat_id": chat_id,
            "status": LeadStatus.NEW,
            "issue_type": "SLA Test",
            "full_address": "Test City, Test St",
            "created_at": stale_time - timedelta(hours=1),
            "is_paused": True,
            "paused_at": stale_time
        }
        result = await leads_collection.insert_one(new_lead)
        print(f"✅ Created dummy lead {result.inserted_id} to be stale (paused_at: {stale_time})")

    # 2. Set Redis State
    await StateManager.set_state(chat_id, UserStates.PAUSED_FOR_HUMAN, ttl=WorkerConstants.PAUSE_TTL_SECONDS)
    print(f"✅ Set Redis state to PAUSED_FOR_HUMAN for {chat_id}")

    print(f"\n🚀 Simulation ready! Make sure the worker is running.")
    print(f"The SLA Monitor (APScheduler job) runs every 5 minutes and will pick this up.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/simulate_sla_deflection.py <phone_number_or_chat_id>")
        sys.exit(1)

    target = sys.argv[1]
    asyncio.run(simulate_sla_deflection(target))
