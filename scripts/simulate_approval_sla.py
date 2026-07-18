"""Fast-forward the PRO-56 pro-approval SLA clock so scenario 3 of the pilot E2E
checklist is testable in seconds instead of waiting 10–25 minutes.

The approval SLA (``check_pro_approval_sla``) fires off ``pro_notified_at``:
  * T+10 (5 for emergency) → nudge the silent pro.
  * T+25 (12 for emergency) → offer the customer a reassignment.
The ``run_pro_approval_sla`` APScheduler job runs every 5 minutes.

Usage:
    python scripts/simulate_approval_sla.py <customer_phone_or_chat_id> [nudge|offer]

Modes (default: offer):
  * ``nudge`` — ages the clock just past the 10-min mark → the next worker tick
    nudges the assigned pro. Requires a lead that already has a real pro assigned
    (run the normal customer→pro flow first, then this).
  * ``offer`` — ages the clock past the 25-min mark → the next tick sends the
    customer the reassignment offer (1/2). Only fires during Israel business
    hours (08:00–21:00) — the PRO-73 gate.

Prefers the customer's existing NEW lead (so a real pro/phone is used); creates a
throwaway lead only if none exists (the customer offer still fires, but there is
no real pro to nudge).
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import leads_collection  # noqa: E402
from app.core.datetime_utils import within_business_hours  # noqa: E402
from app.services.state_manager_service import StateManager  # noqa: E402
from app.core.constants import UserStates, LeadStatus, WorkerConstants  # noqa: E402


async def simulate_approval_sla(chat_id: str, mode: str = "offer"):
    if not chat_id.endswith("@c.us"):
        chat_id = f"{chat_id}@c.us"

    nudge_min = WorkerConstants.APPROVAL_NUDGE_MINUTES
    offer_min = WorkerConstants.APPROVAL_REASSIGN_OFFER_MINUTES
    # Age the clock just past the target threshold.
    aged_min = (offer_min if mode == "offer" else nudge_min) + 1
    notified_at = datetime.now(timezone.utc) - timedelta(minutes=aged_min)

    print(f"🛠️  Simulating pro-approval SLA ({mode}) for: {chat_id}")
    print(f"    Aging pro_notified_at to ~{aged_min} min ago.")

    sla_fields = {
        "pro_notified_at": notified_at,
        "approval_nudged": False,
        "reassign_offered": False,
    }

    lead = await leads_collection.find_one(
        {"chat_id": chat_id, "status": LeadStatus.NEW},
        sort=[("created_at", -1)],
    )

    if lead:
        if not lead.get("pro_id"):
            print(
                "⚠️  This NEW lead has no assigned pro — the pro nudge can't send. "
                "Run the customer→pro flow first so a pro is assigned."
            )
        await leads_collection.update_one({"_id": lead["_id"]}, {"$set": sla_fields})
        print(f"✅ Aged existing lead {lead['_id']} (pro_id={lead.get('pro_id')}).")
    else:
        dummy = {
            "chat_id": chat_id,
            "status": LeadStatus.NEW,
            "issue_type": "Approval SLA Test",
            "full_address": "Test City, Test St",
            "created_at": notified_at - timedelta(hours=1),
            **sla_fields,
        }
        result = await leads_collection.insert_one(dummy)
        print(
            f"✅ Created throwaway lead {result.inserted_id} (no pro assigned — "
            "the customer offer will fire, but there is no pro to nudge)."
        )

    # The job only acts while the customer is parked in AWAITING_PRO_APPROVAL.
    await StateManager.set_state(
        chat_id,
        UserStates.AWAITING_PRO_APPROVAL,
        ttl=WorkerConstants.PRO_APPROVAL_TTL_SECONDS,
    )
    print("✅ Set Redis state to AWAITING_PRO_APPROVAL.")

    if mode == "offer" and not within_business_hours():
        print(
            "\n⏰ NOTE: it's currently OUTSIDE Israel business hours (08:00–21:00). "
            "The customer offer is gated (PRO-73) and will NOT send until business "
            "hours. Use 'nudge' mode to test now, or re-run in-hours."
        )

    print("\n🚀 Simulation ready — make sure the worker is running.")
    print(
        f"   run_pro_approval_sla runs every "
        f"{WorkerConstants.APPROVAL_SLA_CHECK_INTERVAL_MINUTES} min and will pick this up."
    )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(
            "Usage: python scripts/simulate_approval_sla.py "
            "<customer_phone_or_chat_id> [nudge|offer]"
        )
        sys.exit(1)

    target = sys.argv[1]
    selected_mode = sys.argv[2].lower() if len(sys.argv) > 2 else "offer"
    if selected_mode not in ("nudge", "offer"):
        print("Mode must be 'nudge' or 'offer'.")
        sys.exit(1)

    asyncio.run(simulate_approval_sla(target, selected_mode))
