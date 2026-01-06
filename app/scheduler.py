from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from app.core.database import users_collection, leads_collection, settings_collection
from app.services.workflow import send_pro_reminder, send_customer_completion_check, whatsapp
from app.services.monitor_service import check_and_reassign_stale_leads, send_periodic_admin_report
from datetime import datetime, timedelta
from app.core.constants import LeadStatus
import pytz
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
            "status": LeadStatus.BOOKED,
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
                    await whatsapp.send_message(chat_id, msg)
                except Exception as e:
                    print(f"❌ Failed to send to {pro['business_name']}: {e}")

async def monitor_unfinished_jobs():
    """
    Monitors 'booked' leads and takes action based on their age.
    """
    # 1. Check Config
    config = await settings_collection.find_one({"_id": "scheduler_config"})
    if config and not config.get("stale_monitor_active", True):
        return

    now_il = datetime.now(IL_TZ)
    if not (8 <= now_il.hour < 21):
        return  # Safety: Only run during business hours

    print(f"🕵️ [Scheduler] Running Stale Job Monitor at {now_il.strftime('%H:%M')}...")
    now_utc = datetime.now(pytz.utc)

    # Tier 1: 4-6 hours old -> Remind Pro
    t1_start = now_utc - timedelta(hours=6)
    t1_end = now_utc - timedelta(hours=4)
    t1_leads_cursor = leads_collection.find({
        "status": LeadStatus.BOOKED,
        "created_at": {"$gte": t1_start, "$lt": t1_end}
    })
    async for lead in t1_leads_cursor:
        print(f"[Monitor] T1: Sending pro reminder for lead {lead['_id']}")
        await send_pro_reminder(str(lead["_id"]))

    # Tier 2: 6-24 hours old -> Check with Customer
    t2_start = now_utc - timedelta(hours=24)
    t2_end = now_utc - timedelta(hours=6)
    t2_leads_cursor = leads_collection.find({
        "status": LeadStatus.BOOKED,
        "created_at": {"$gte": t2_start, "$lt": t2_end}
    })
    async for lead in t2_leads_cursor:
        print(f"[Monitor] T2: Sending customer check for lead {lead['_id']}")
        await send_customer_completion_check(str(lead["_id"]))

    # Tier 3: >24 hours old -> Flag for Admin
    t3_end = now_utc - timedelta(hours=24)
    update_result = await leads_collection.update_many(
        {"status": LeadStatus.BOOKED, "created_at": {"$lt": t3_end}, "flag": {"$ne": "requires_admin"}},
        {"$set": {"flag": "requires_admin"}}
    )
    if update_result.modified_count > 0:
        print(f"[Monitor] T3: Flagged {update_result.modified_count} leads for admin review.")

# --- Wrappers for Imported Services ---

async def run_sos_healer():
    """Wrapper for SOS Auto-Healer with Toggle Check"""
    config = await settings_collection.find_one({"_id": "scheduler_config"})
    if config and not config.get("sos_healer_active", True):
        return
    await check_and_reassign_stale_leads()

async def run_sos_reporter():
    """Wrapper for SOS Admin Reporter with Toggle Check"""
    config = await settings_collection.find_one({"_id": "scheduler_config"})
    if config and not config.get("sos_reporter_active", True):
        return
    await send_periodic_admin_report()


async def daily_reminders_job():
    """
    Daily Reminders Job (Cron).
    Uses atomic 'find_one_and_update' to check availability and lock the run in one step.
    Runs once per day at the scheduled Cron time.
    """
    try:
        # 1. Ensure Config Exists (Upsert)
        await settings_collection.update_one(
            {"_id": "scheduler_config"},
            {"$setOnInsert": {
                "run_time": "08:00", 
                "is_active": True, 
                "last_run_date": None,
                "stale_monitor_active": True,
                "sos_healer_active": True,
                "sos_reporter_active": True
            }},
            upsert=True
        )

        # 2. Scheduled Run (Atomic Check-and-Lock)
        today_str = datetime.now(IL_TZ).strftime("%Y-%m-%d")
        
        # Only run if active and NOT run today yet
        result = await settings_collection.find_one_and_update(
            {
                "_id": "scheduler_config",
                "is_active": True,
                "last_run_date": {"$ne": today_str}
            },
            {"$set": {"last_run_date": today_str}}
        )

        if result:
            print(f"⏰ [Scheduler] Executing daily reminders for {today_str}.")
            await send_daily_reminders()

    except Exception as e:
        print(f"❌ [Scheduler Error] {e}")

def start_scheduler():
    scheduler = AsyncIOScheduler(timezone=IL_TZ)
    
    # Job 1: Daily "Good Morning" Reminders (Cron)
    scheduler.add_job(
        daily_reminders_job,
        CronTrigger(hour=8, minute=0, timezone=IL_TZ),
        id="daily_reminders_job",
        replace_existing=True
    )

    # Job 2: Stale Job Monitor (Wrapped)
    scheduler.add_job(
        monitor_unfinished_jobs,
        IntervalTrigger(minutes=30),
        id="stale_job_monitor",
        replace_existing=True
    )

    # Job 3: SOS Auto-Healer (Wrapped)
    scheduler.add_job(
        run_sos_healer,
        IntervalTrigger(minutes=10),
        id="sos_auto_healer",
        replace_existing=True
    )

    # Job 4: SOS Admin Reporter (Wrapped)
    scheduler.add_job(
        run_sos_reporter,
        IntervalTrigger(hours=4),
        id="sos_admin_reporter",
        replace_existing=True
    )

    scheduler.start()
    print("🚀 APScheduler Started with all jobs!")
    return scheduler
