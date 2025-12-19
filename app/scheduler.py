from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from app.core.database import users_collection, leads_collection, settings_collection
from app.services.logic import send_whatsapp_message, send_pro_reminder, send_customer_completion_check
from datetime import datetime, timedelta
import pytz
import asyncio
from app.core.logger import logger

IL_TZ = pytz.timezone('Asia/Jerusalem')

async def send_daily_reminders():
    print(f"⏰ [Scheduler] Starting daily reminders check at {datetime.now(IL_TZ)}")
    
    active_pros = await users_collection.find({"is_active": True}).to_list(length=None)
    
    now_il = datetime.now(IL_TZ)
    today_start = now_il.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    
    start_utc = today_start.astimezone(pytz.utc)
    end_utc = today_end.astimezone(pytz.utc)
    
    for pro in active_pros:
        booked_jobs_cursor = leads_collection.find({
            "pro_id": pro["_id"],
            "status": "booked",
            "created_at": {"$gte": start_utc, "$lt": end_utc}
        }).sort("created_at", 1)
        
        booked_jobs = await booked_jobs_cursor.to_list(length=None)
        
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

async def monitor_unfinished_jobs():
    """
    Monitors 'booked' leads and takes action based on their age.
    - 4-6 hours: Remind pro.
    - 6-24 hours: Check with customer.
    - >24 hours: Flag for admin.
    """
    now_il = datetime.now(IL_TZ)
    if not (8 <= now_il.hour < 21):
        return  # Safety: Only run during business hours

    print(f"🕵️ [Scheduler] Running Stale Job Monitor at {now_il.strftime('%H:%M')}...")
    now_utc = datetime.now(pytz.utc)

    # Tier 1: 4-6 hours old -> Remind Pro
    t1_start = now_utc - timedelta(hours=6)
    t1_end = now_utc - timedelta(hours=4)
    t1_leads_cursor = leads_collection.find({
        "status": "booked",
        "created_at": {"$gte": t1_start, "$lt": t1_end}
    })
    async for lead in t1_leads_cursor:
        print(f"[Monitor] T1: Sending pro reminder for lead {lead['_id']}")
        await send_pro_reminder(str(lead["_id"]))

    # Tier 2: 6-24 hours old -> Check with Customer
    t2_start = now_utc - timedelta(hours=24)
    t2_end = now_utc - timedelta(hours=6)
    t2_leads_cursor = leads_collection.find({
        "status": "booked",
        "created_at": {"$gte": t2_start, "$lt": t2_end}
    })
    async for lead in t2_leads_cursor:
        print(f"[Monitor] T2: Sending customer check for lead {lead['_id']}")
        await send_customer_completion_check(str(lead["_id"]))

    # Tier 3: >24 hours old -> Flag for Admin
    t3_end = now_utc - timedelta(hours=24)
    update_result = await leads_collection.update_many(
        {"status": "booked", "created_at": {"$lt": t3_end}, "flag": {"$ne": "requires_admin"}},
        {"$set": {"flag": "requires_admin"}}
    )
    if update_result.modified_count > 0:
        print(f"[Monitor] T3: Flagged {update_result.modified_count} leads for admin review.")


async def scheduler_manager():
    """
    Race-Condition Free Manager for daily reminders.
    Uses atomic 'find_one_and_update' to check availability and lock the run in one step.
    """
    try:
        # 1. Ensure Config Exists (Upsert)
        await settings_collection.update_one(
            {"_id": "scheduler_config"},
            {"$setOnInsert": {"run_time": "08:00", "is_active": True, "last_run_date": None}},
            upsert=True
        )

        # 2. Check for Manual Trigger (Atomic)
        manual_trigger = await settings_collection.find_one_and_update(
            {"_id": "scheduler_config", "trigger_now": True},
            {"$set": {"trigger_now": False, "last_run_date": datetime.now(IL_TZ).strftime("%Y-%m-%d")}})
        
        if manual_trigger:
            print("🚀 [Scheduler] Manual trigger locked & executing daily reminders!")
            await send_daily_reminders()
            return

        # 3. Scheduled Run (Atomic Check-and-Lock)
        now_il = datetime.now(IL_TZ)
        current_time_str = now_il.strftime("%H:%M")
        today_str = now_il.strftime("%Y-%m-%d")
        
        result = await settings_collection.find_one_and_update(
            {
                "_id": "scheduler_config",
                "is_active": True,
                "last_run_date": {"$ne": today_str},
                "run_time": {"$lte": current_time_str}
            },
            {"$set": {"last_run_date": today_str}})

        if result:
            print(f"⏰ [Scheduler] Time to run daily reminders! Locked job for {today_str}.")
            await send_daily_reminders()

    except Exception as e:
        print(f"❌ [Scheduler Manager Error] {e}")

def start_scheduler():
    scheduler = AsyncIOScheduler(timezone=IL_TZ)
    
    # Job 1: Daily "Good Morning" Reminders
    scheduler.add_job(
        scheduler_manager,
        IntervalTrigger(seconds=60),
        id="scheduler_manager",
        replace_existing=True
    )

    # Job 2: Stale Job Monitor
    scheduler.add_job(
        monitor_unfinished_jobs,
        IntervalTrigger(minutes=30), # Run every 30 minutes
        id="stale_job_monitor",
        replace_existing=True
    )

    scheduler.start()
    print("🚀 APScheduler Started with all jobs!")
