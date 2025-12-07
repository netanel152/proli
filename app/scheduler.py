from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from app.core.database import users_collection, leads_collection, settings_collection
from app.services.logic import send_whatsapp_message
from datetime import datetime, timedelta
import pytz
import asyncio

IL_TZ = pytz.timezone('Asia/Jerusalem')

async def send_daily_reminders():
    print(f"⏰ [Scheduler] Starting daily reminders check at {datetime.now(IL_TZ)}")
    
    # Query users_collection for active pros
    active_pros = list(users_collection.find({"is_active": True}))
    
    # Define Today
    now_il = datetime.now(IL_TZ)
    today_start = now_il.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    
    start_utc = today_start.astimezone(pytz.utc)
    end_utc = today_end.astimezone(pytz.utc)
    
    for pro in active_pros:
        # Find leads with status='booked' scheduled for Today
        booked_jobs = list(leads_collection.find({
            "pro_id": pro["_id"],
            "status": "booked",
            "created_at": {"$gte": start_utc, "$lt": end_utc}
        }).sort("created_at", 1))
        
        if booked_jobs:
            msg = f"☀️ *בוקר טוב {pro['business_name']}!* \nהנה העבודות שלך להיום ({today_start.strftime('%d/%m')}):"
            for job in booked_jobs:
                job_time = job["created_at"].replace(tzinfo=pytz.utc).astimezone(IL_TZ)
                time_str = job_time.strftime("%H:%M")
                client_phone = job["chat_id"].replace("@c.us", "")
                details = job.get("details", "פרטים חסרים")
                msg += f"\n🛠️ *{time_str}* - {details}\n   📞 {client_phone}\n"
            
            msg += "\nשיהיה יום מוצלח! 💪"
            
            chat_id = pro.get("phone_number")
            if chat_id:
                chat_id = f"{chat_id}@c.us" if not chat_id.endswith("@c.us") else chat_id
                try:
                    await send_whatsapp_message(chat_id, msg)
                except Exception as e:
                    print(f"❌ Failed to send to {pro['business_name']}: {e}")

async def scheduler_manager():
    """
    Smart manager that checks DB config every minute to handle:
    1. 'Run Now' triggers.
    2. Daily scheduled runs (dynamic time).
    """
    try:
        config = settings_collection.find_one({"_id": "scheduler_config"})
        if not config:
            # Create default config if missing
            config = {"_id": "scheduler_config", "run_time": "08:00", "is_active": True, "last_run_date": None}
            settings_collection.insert_one(config)
            print("⚠️ [Scheduler] Created default config.")

        # Manual Trigger Check
        if config.get("trigger_now", False):
            print("🚀 [Scheduler] Manual trigger detected!")
            await send_daily_reminders()
            settings_collection.update_one(
                {"_id": "scheduler_config"},
                {"$set": {"trigger_now": False, "last_run_date": datetime.now(IL_TZ).strftime("%Y-%m-%d")}}
            )
            return

        # Active Check
        if not config.get("is_active", True):
            return

        # Schedule Check
        now_il = datetime.now(IL_TZ)
        current_time_str = now_il.strftime("%H:%M")
        today_str = now_il.strftime("%Y-%m-%d")
        
        target_time = config.get("run_time", "08:00")
        last_run = config.get("last_run_date")

        # If we haven't run today AND current time >= target time
        # (Using >= handles cases where the scheduler might miss the exact minute slightly)
        if last_run != today_str and current_time_str >= target_time:
            print(f"⏰ [Scheduler] Time to run! ({current_time_str} >= {target_time})")
            await send_daily_reminders()
            
            settings_collection.update_one(
                {"_id": "scheduler_config"},
                {"$set": {"last_run_date": today_str}}
            )

    except Exception as e:
        print(f"❌ [Scheduler Manager Error] {e}")

def start_scheduler():
    scheduler = AsyncIOScheduler()
    # Run the manager every 60 seconds
    scheduler.add_job(
        scheduler_manager,
        IntervalTrigger(seconds=60),
        id="scheduler_manager",
        replace_existing=True
    )
    scheduler.start()
    print("🚀 APScheduler Started (Dynamic Manager Mode)!")