import time
import asyncio
from datetime import datetime, timedelta
import pytz
from app.core.database import users_collection, leads_collection, settings_collection
from app.services.logic import send_whatsapp_message

# Set Timezone
IL_TZ = pytz.timezone('Asia/Jerusalem')

def run_async(coro):
    """Helper to run async function in a synchronous context"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(coro)
    finally:
        loop.close()

async def send_daily_reminders_async():
    print(f"⏰ [Scheduler] Starting daily reminders check at {datetime.now()}")
    
    # 1. Get all active professionals
    active_pros = list(users_collection.find({"is_active": True}))
    if not active_pros:
        print("   No active professionals found.")
        return

    # 2. Define "Today" range (Local Time -> UTC)
    now_il = datetime.now(IL_TZ)
    today_start = now_il.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    
    start_utc = today_start.astimezone(pytz.utc)
    end_utc = today_end.astimezone(pytz.utc)

    count_sent = 0
    for pro in active_pros:
        # 3. Find new leads for this pro today
        booked_jobs = list(leads_collection.find({
            "pro_id": pro["_id"],
            "status": "New",
            "created_at": {"$gte": start_utc, "$lt": end_utc}
        }).sort("created_at", 1))
        
        if booked_jobs:
            print(f"   Sending reminder to {pro['business_name']} ({len(booked_jobs)} jobs)")
            
            msg = f"☀️ *בוקר טוב {pro['business_name']}!* \nהנה העבודות שלך להיום ({today_start.strftime('%d/%m')}):"
            
            for job in booked_jobs:
                job_time = job["created_at"].replace(tzinfo=pytz.utc).astimezone(IL_TZ)
                time_str = job_time.strftime("%H:%M")
                client_phone = job["chat_id"].replace("@c.us", "")
                details = job.get("details", "פרטים חסרים")
                msg += f"\n🛠️ *{time_str}* - {details}\n   📞 {client_phone}\n"
            
            msg += "\nשיהיה יום מוצלח! 💪"
            
            # Send WhatsApp
            if pro.get("phone_number"):
                chat_id = f"{pro['phone_number']}@c.us" if not pro['phone_number'].endswith("@c.us") else pro['phone_number']
                try:
                    await send_whatsapp_message(chat_id, msg)
                    count_sent += 1
                except Exception as e:
                    print(f"   ❌ Failed to send to {pro['business_name']}: {e}")
    
    print(f"✅ [Scheduler] Cycle complete. Sent {count_sent} reminders.")

def get_scheduler_config():
    """Fetches or creates default config"""
    config = settings_collection.find_one({"_id": "scheduler_config"})
    if not config:
        default_config = {
            "_id": "scheduler_config",
            "run_time": "08:00",
            "is_active": True,
            "trigger_now": False,
            "last_run_date": None
        }
        settings_collection.insert_one(default_config)
        return default_config
    return config

if __name__ == "__main__":
    print("🚀 Scheduler Service Started (Dynamic Mode)...")
    
    while True:
        try:
            # 1. Load Config
            config = get_scheduler_config()
            
            now_il = datetime.now(IL_TZ)
            current_time_str = now_il.strftime("%H:%M")
            current_date_str = now_il.strftime("%Y-%m-%d")
            
            # 2. Check "Trigger Now" (Manual Run)
            if config.get("trigger_now", False):
                print("⚠️ Manual trigger detected!")
                run_async(send_daily_reminders_async())
                # Reset flag
                settings_collection.update_one(
                    {"_id": "scheduler_config"}, 
                    {"$set": {"trigger_now": False}}
                )
                # Optional: Update last_run_date to prevent double auto-run today?
                # Let's NOT update last_run_date for manual triggers, allowing auto-run to still happen if configured.
            
            # Check Auto-Run
            elif config.get("is_active", True):
                last_run = config.get("last_run_date")
                target_time = config.get("run_time", "08:00")
                
                # Check if it matches the minute (simple check) and hasn't run today
                if current_time_str == target_time and last_run != current_date_str:
                    print(f"⏰ Auto-run triggered at {current_time_str}")
                    run_async(send_daily_reminders_async())
                    
                    # Update last_run_date
                    settings_collection.update_one(
                        {"_id": "scheduler_config"}, 
                        {"$set": {"last_run_date": current_date_str}}
                    )
            
            # Sleep for 30 seconds to avoid missing the minute window but not busy-looping
            time.sleep(30)
            
        except Exception as e:
            print(f"❌ Scheduler Error: {e}")
            time.sleep(60) # Sleep longer on error