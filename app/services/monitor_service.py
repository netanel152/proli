import time
from datetime import datetime, timedelta, timezone
from app.core.database import leads_collection, users_collection
from app.core.constants import LeadStatus, UserStates, WorkerConstants, Defaults, Actor
from app.services.lead_manager_service import set_lead_status
from app.core.config import settings
from app.core.logger import logger
from app.core.redis_client import get_redis_client
from app.services.whatsapp_client_service import WhatsAppClient
from app.services import matching_service
from app.services.notification_service import send_oncall_alert
from app.core.messages import Messages
from app.services.context_manager_service import ContextManager
from app.services.state_manager_service import StateManager
from bson import ObjectId

whatsapp = WhatsAppClient()


async def check_and_reassign_stale_leads():
    """
    AUTO-RECOVERY ("The Healer"):
    Runs frequently (e.g., every 10 mins).
    Finds stale leads and automatically re-assigns them to a new pro.
    """
    logger.info("🕵️ [SOS Healer] Checking for stale leads to reassign...")

    timeout_minutes = WorkerConstants.SOS_TIMEOUT_MINUTES
    threshold_time = datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)

    # Patch #3: Exclude PENDING_ADMIN_REVIEW from the Healer query.
    # PENDING_ADMIN_REVIEW is a *terminal* state for the Healer — it means the
    # Healer already gave up on this lead and handed it to a human. Re-running
    # the reassignment flow on it just re-notifies the customer with
    # CUSTOMER_REASSIGNING, re-fails the match, and re-sets the status to
    # PENDING_ADMIN_REVIEW on every 10-minute tick (see logs 2026-04-18).
    query = {
        "status": {"$in": [LeadStatus.NEW, LeadStatus.CONTACTED]},
        "created_at": {"$lt": threshold_time},
    }

    try:
        cursor = leads_collection.find(query)
        stale_leads = await cursor.to_list(length=WorkerConstants.DB_QUERY_LIMIT)

        if not stale_leads:
            logger.info("✅ [SOS Healer] No stale leads found.")
            return

        logger.warning(
            f"🕵️ [SOS Healer] Found {len(stale_leads)} stale leads. Attempting reassignment..."
        )

        for lead in stale_leads:
            lead_id = lead["_id"]
            chat_id = lead["chat_id"]
            current_pro_id = lead.get("pro_id")

            # Patch #5: Skip leads without a real, usable location before
            # attempting reassignment. A lead with full_address missing, empty,
            # or equal to the legacy UNKNOWN_ADDRESS magic string will always
            # fail geo matching and escalate to PENDING_ADMIN_REVIEW — burning
            # a CUSTOMER_REASSIGNING notification on the customer every cycle.
            # Let the admin take it over directly.
            raw_location = lead.get("full_address")
            if not raw_location or raw_location == Defaults.UNKNOWN_ADDRESS:
                logger.info(
                    f"⏭️ [SOS Healer] Skipping lead {lead_id} for {chat_id} — "
                    f"no usable location (full_address={raw_location!r}). "
                    f"Escalating to PENDING_ADMIN_REVIEW without retry loop."
                )
                await set_lead_status(
                    lead_id,
                    LeadStatus.PENDING_ADMIN_REVIEW,
                    Actor.SYSTEM,
                    extra_set={"escalation_reason": "no_usable_location"},
                )
                await StateManager.clear_state(chat_id)
                continue

            # 1. Notify Customer
            try:
                await whatsapp.send_message(chat_id, Messages.SOS.CUSTOMER_REASSIGNING)
            except Exception as e:
                logger.error(f"Failed to notify customer {chat_id}: {e}")

            # 2. Find Replacement (Excluding current pro)
            excluded_ids = [current_pro_id] if current_pro_id else []
            # Also exclude previously attempted pros if we tracked them (future improvement)

            new_pro = await matching_service.determine_best_pro(
                issue_type=lead.get("issue_type"),
                location=raw_location,
                excluded_pro_ids=excluded_ids,
            )

            reassignment_count = lead.get("reassignment_count", 0)

            # Hard stop: if max reassignments reached, close the lead
            if reassignment_count >= WorkerConstants.MAX_REASSIGNMENTS:
                await set_lead_status(
                    lead_id,
                    LeadStatus.CLOSED,
                    Actor.SYSTEM,
                    extra_set={"closed_reason": "max_reassignments"},
                )
                try:
                    await whatsapp.send_message(
                        chat_id, Messages.SOS.MAX_REASSIGNMENTS_REACHED
                    )
                except Exception as e:
                    logger.error(f"Failed to notify customer {chat_id} of closure: {e}")
                await ContextManager.clear_context(chat_id)
                logger.warning(
                    f"🚫 [SOS Healer] Lead {lead_id} closed after {reassignment_count} reassignments."
                )
                continue

            if new_pro:
                new_pro_id = new_pro["_id"]

                # 3. Update Lead — increment counter, reset timer
                await set_lead_status(
                    lead_id,
                    LeadStatus.NEW,
                    Actor.SYSTEM,
                    extra_set={
                        "pro_id": new_pro_id,
                        "created_at": datetime.now(timezone.utc),
                        "reassigned_from": current_pro_id,
                        "reassignment_count": reassignment_count + 1,
                    },
                )

                # 4. Notify New Pro
                pro_phone = new_pro.get("phone_number")
                if pro_phone:
                    if not pro_phone.endswith("@c.us"):
                        pro_phone = f"{pro_phone}@c.us"

                    header = (
                        Messages.Pro.EMERGENCY_LEAD_HEADER
                        if lead.get("is_emergency")
                        else Messages.Pro.NEW_LEAD_HEADER
                    )
                    msg_to_pro = header + "\n\n"
                    msg_to_pro += Messages.Pro.NEW_LEAD_DETAILS.format(
                        customer_name=lead.get("customer_name") or "לקוח",
                        full_address=lead.get("full_address") or "Unknown",
                        extra_info=f"קומה {lead.get('floor') or '-'}, דירה {lead.get('apartment') or '-'}",
                        issue_type=lead.get("issue_type", "Unknown"),
                        appointment_time=lead.get("appointment_time", "Pending"),
                    )
                    msg_to_pro += Messages.Pro.NEW_LEAD_FOOTER

                    # Send all collected media
                    all_media = lead.get("media_urls", [])
                    if all_media:
                        for i, m_url in enumerate(all_media):
                            caption = msg_to_pro if i == 0 else ""
                            await whatsapp.send_file_by_url(
                                pro_phone, m_url, caption=caption
                            )
                    else:
                        await whatsapp.send_message(pro_phone, msg_to_pro)

                    if lead.get("full_address"):
                        await whatsapp.send_location_link(
                            pro_phone, lead["full_address"], Messages.Pro.NAVIGATE_TO
                        )

                # 5. Notify Old Pro
                if current_pro_id:
                    old_pro = await users_collection.find_one({"_id": current_pro_id})
                    if old_pro and old_pro.get("phone_number"):
                        old_phone = old_pro["phone_number"]
                        if not old_phone.endswith("@c.us"):
                            old_phone = f"{old_phone}@c.us"
                        await whatsapp.send_message(
                            old_phone, Messages.SOS.PRO_LOST_LEAD
                        )

                # Clear any stuck customer state (e.g. AWAITING_PRO_APPROVAL)
                await StateManager.clear_state(chat_id)
                logger.info(
                    f"✅ [SOS Healer] Lead {lead_id} reassigned from {current_pro_id} to {new_pro_id} (attempt {reassignment_count + 1})"
                )

            else:
                logger.warning(
                    f"⚠️ [SOS Healer] Could not find replacement for lead {lead_id} — escalating to PENDING_ADMIN_REVIEW."
                )
                await set_lead_status(
                    lead_id, LeadStatus.PENDING_ADMIN_REVIEW, Actor.SYSTEM
                )
                try:
                    await whatsapp.send_message(
                        chat_id, Messages.Customer.PENDING_REVIEW
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to notify customer {chat_id} of pending review: {e}"
                    )
                # Release the customer from AWAITING_PRO_APPROVAL (or any other
                # FSM state) so the next message they send is routed normally
                # instead of hitting STILL_WAITING for 4 hours.
                await StateManager.clear_state(chat_id)
                await ContextManager.clear_context(chat_id)

    except Exception as e:
        logger.error(f"❌ [SOS Healer] Error: {e}")


async def auto_reject_unassigned_leads():
    """
    AUTO-REJECTION ("The Janitor"):
    Finds CONTACTED leads that have no assigned pro_id and are older than
    UNASSIGNED_LEAD_TIMEOUT_HOURS. Closes them and notifies the customer.
    Prevents leads from accumulating forever with no escape path.
    """
    logger.info("🧹 [Janitor] Checking for unassigned stale leads...")

    timeout_hours = WorkerConstants.UNASSIGNED_LEAD_TIMEOUT_HOURS
    threshold_time = datetime.now(timezone.utc) - timedelta(hours=timeout_hours)

    query = {
        "status": LeadStatus.CONTACTED,
        "pro_id": {"$exists": False},
        "created_at": {"$lt": threshold_time},
    }

    try:
        cursor = leads_collection.find(query)
        stale_unassigned = await cursor.to_list(length=WorkerConstants.DB_QUERY_LIMIT)

        if not stale_unassigned:
            logger.info("✅ [Janitor] No unassigned stale leads found.")
            return

        logger.warning(
            f"🧹 [Janitor] Closing {len(stale_unassigned)} unassigned leads."
        )

        for lead in stale_unassigned:
            lead_id = lead["_id"]
            chat_id = lead.get("chat_id")

            await set_lead_status(
                lead_id,
                LeadStatus.CLOSED,
                Actor.SYSTEM,
                extra_set={"closed_reason": "no_pro_available"},
            )

            if chat_id:
                try:
                    await whatsapp.send_message(chat_id, Messages.SOS.NO_PRO_AVAILABLE)
                except Exception as e:
                    logger.error(f"Failed to notify customer {chat_id} of closure: {e}")
                await ContextManager.clear_context(chat_id)

            logger.info(
                f"🧹 [Janitor] Closed unassigned lead {lead_id} (chat: {chat_id})"
            )

    except Exception as e:
        logger.error(f"❌ [Janitor] Error: {e}")


async def send_periodic_admin_report():
    """
    ADMIN REPORTING ("The Reporter"):
    Runs periodically (e.g., every 4 hours).
    Sends a batched summary of leads that are STILL stuck (reassignment failed).
    """
    logger.info("🕵️ [SOS Reporter] Generating admin report...")

    timeout_minutes = WorkerConstants.SOS_TIMEOUT_MINUTES
    threshold_time = datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)

    # Same query as Healer - if they are still here, it means Healer failed or no one accepted.
    query = {
        "status": {
            "$in": [
                LeadStatus.NEW,
                LeadStatus.CONTACTED,
                LeadStatus.PENDING_ADMIN_REVIEW,
            ]
        },
        "created_at": {"$lt": threshold_time},
    }

    try:
        cursor = leads_collection.find(query)
        stuck_leads = await cursor.to_list(length=WorkerConstants.DB_QUERY_LIMIT)

        if not stuck_leads:
            logger.info("✅ [SOS Reporter] No stuck leads to report.")
            return

        count = len(stuck_leads)
        logger.warning(f"🕵️ [SOS Reporter] Found {count} stuck leads.")

        # Build Message
        message_lines = [
            Messages.SOS.ADMIN_REPORT_HEADER,
            Messages.SOS.ADMIN_REPORT_BODY.format(count=count, timeout=timeout_minutes),
        ]

        for lead in stuck_leads:
            chat_id = lead.get("chat_id", "Unknown").split("@")[0]
            issue = lead.get("issue_type", "Unknown Issue")
            city = lead.get("full_address") or "Unknown City"
            created_at = lead.get("created_at")
            time_str = created_at.strftime("%H:%M") if created_at else "??"

            message_lines.append(
                f"- {chat_id}: {issue} in {city} (Waiting since {time_str})"
            )

        message_lines.append(Messages.SOS.ADMIN_REPORT_FOOTER)
        full_message = "\n".join(message_lines)

        # Send to Admin
        admin_chat_id = f"{settings.ADMIN_PHONE}@c.us"
        await whatsapp.send_message(admin_chat_id, full_message)
        logger.info(f"✅ [SOS Reporter] Sent report to Admin: {settings.ADMIN_PHONE}")

    except Exception as e:
        logger.error(f"❌ [SOS Reporter] Error: {e}")


async def check_whatsapp_instance_state():
    """PRO-20 — Green API deauth watchdog (SPOF protection).

    Polls getStateInstance. The WhatsApp instance is a single point of failure:
    if it loses authorization (phone offline, ban, session drop) no customer or
    pro message is processed, silently. This pages the on-call operator once the
    instance has been non-authorized for longer than the threshold.

    Redis-backed so the alert survives across the short polling interval without
    flapping or re-paging every tick:
      * ``wa:instance:down_since`` — unix ts of first non-authorized probe.
      * ``wa:instance:alerted``    — set once we actually page; gates the
        recovery notice so a brief blip that never crossed the threshold stays
        quiet.
      * ``wa:instance:last_alert`` — TTL dedup so we re-page at most once per
        WA_STATE_REALERT_MINUTES while the instance stays down.

    Fail-open: any Redis error degrades to a single log line and returns —
    a monitoring job must never take down the worker.
    """
    state = await whatsapp.get_state_instance()
    is_authorized = state == "authorized"

    try:
        redis = await get_redis_client()
    except Exception as e:
        logger.warning(f"[WA Monitor] Redis unavailable, skipping tick: {e}")
        return

    DOWN_SINCE_KEY = "wa:instance:down_since"
    ALERTED_KEY = "wa:instance:alerted"
    LAST_ALERT_KEY = "wa:instance:last_alert"

    try:
        if is_authorized:
            # Recovery path: only announce if we previously paged.
            down_since = await redis.get(DOWN_SINCE_KEY)
            alerted = await redis.get(ALERTED_KEY)
            await redis.delete(DOWN_SINCE_KEY, ALERTED_KEY, LAST_ALERT_KEY)
            if down_since and alerted:
                logger.info(
                    "✅ [WA Monitor] Green API instance recovered (authorized)."
                )
                await send_oncall_alert(Messages.Alerts.WHATSAPP_RECOVERED)
            return

        # Non-authorized (or unreachable → state is None).
        now = time.time()
        down_since_raw = await redis.get(DOWN_SINCE_KEY)
        if not down_since_raw:
            # First detection — start the clock, don't page yet.
            await redis.set(DOWN_SINCE_KEY, str(now), ex=86400)
            logger.warning(
                f"[WA Monitor] Green API instance not authorized (state={state}). "
                "Starting deauth timer."
            )
            return

        # Still down — refresh the timer's TTL so a multi-day outage doesn't
        # let down_since expire and silently reset the clock mid-incident.
        await redis.expire(DOWN_SINCE_KEY, 86400)
        downtime_minutes = (now - float(down_since_raw)) / 60
        threshold = WorkerConstants.WA_STATE_ALERT_THRESHOLD_MINUTES
        if downtime_minutes < threshold:
            logger.warning(
                f"[WA Monitor] Instance still not authorized (state={state}) "
                f"for ~{downtime_minutes:.1f}m (< {threshold}m threshold)."
            )
            return

        # Threshold crossed — page, deduped to once per realert window.
        realert_ttl = WorkerConstants.WA_STATE_REALERT_MINUTES * 60
        is_new_alert = await redis.set(LAST_ALERT_KEY, "1", ex=realert_ttl, nx=True)
        if not is_new_alert:
            return  # already paged within the realert window

        await redis.set(ALERTED_KEY, "1", ex=86400)
        # logger.critical → forwarded to Sentry as an issue (worker is CRITICAL-only).
        logger.critical(
            f"🚨 [WA Monitor] Green API instance NON-AUTHORIZED for "
            f"~{downtime_minutes:.0f}m (state={state}) — no messages are being "
            "processed. Paging on-call."
        )
        await send_oncall_alert(
            Messages.Alerts.WHATSAPP_DOWN.format(
                state=state or "unreachable", minutes=int(downtime_minutes)
            )
        )
    except Exception as e:
        logger.error(f"[WA Monitor] Error during instance-state check: {e}")


async def check_sla_deflection():
    """
    SLA MONITOR:
    Finds leads where bot is paused for human, checks if it's been silent for 15 mins.
    """
    logger.info("🕵️ [SLA Monitor] Checking for silent human handoffs...")

    # We use WorkerConstants.PAUSE_TTL_SECONDS as the inactivity threshold (currently 900s / 15m)
    threshold_time = datetime.now(timezone.utc) - timedelta(
        seconds=WorkerConstants.PAUSE_TTL_SECONDS
    )

    # Find leads that are paused and haven't had activity for the threshold time
    query = {
        "is_paused": True,
        "paused_at": {"$lt": threshold_time},
        "status": {"$in": [LeadStatus.NEW, LeadStatus.BOOKED]},
    }

    try:
        cursor = leads_collection.find(query)
        paused_leads = await cursor.to_list(length=WorkerConstants.DB_QUERY_LIMIT)

        if not paused_leads:
            logger.info("✅ [SLA Monitor] No silent handoffs found.")
            return

        for lead in paused_leads:
            chat_id = lead["chat_id"]

            # Double check with Redis state
            state = await StateManager.get_state(chat_id)
            if state != UserStates.PAUSED_FOR_HUMAN:
                # State already cleared or changed, just cleanup the DB flag
                await leads_collection.update_one(
                    {"_id": lead["_id"]}, {"$set": {"is_paused": False}}
                )
                continue

            # It's been 15 mins of silence. Trigger deflection.
            logger.warning(
                f"⏰ [SLA Monitor] SLA exceeded for {chat_id}. Deflecting to phone check."
            )

            # 1. Clear state
            await StateManager.clear_state(chat_id)

            # 2. Update lead doc
            await leads_collection.update_one(
                {"_id": lead["_id"]},
                {"$set": {"is_paused": False, "sla_deflected": True}},
            )

            # 3. Send Deflection Message
            await whatsapp.send_message(
                chat_id, Messages.Customer.SLA_DEFLECTION_MESSAGE
            )

            logger.info(
                f"✅ [SLA Monitor] Deflected customer {chat_id} after inactivity."
            )

    except Exception as e:
        logger.error(f"❌ [SLA Monitor] Error: {e}")


async def remind_stale_booked_leads():
    """
    STALE LEAD NUDGER:
    Finds leads in BOOKED status that are older than STALE_BOOKED_LEAD_HOURS.
    Sends a reminder to the pro to close the job, preventing MAX_PRO_LOAD issues.
    """
    logger.info("⏰ [Stale Lead Nudger] Checking for stale booked leads...")

    threshold_time = datetime.now(timezone.utc) - timedelta(
        hours=WorkerConstants.STALE_BOOKED_LEAD_HOURS
    )

    # Query for BOOKED leads older than 24 hours with reminders < max
    # We use $and to ensure both the time threshold and the reminder count are checked
    query = {
        "status": LeadStatus.BOOKED,
        "$and": [
            {
                "$or": [
                    {"appointment_datetime": {"$lt": threshold_time}},
                    {"updated_at": {"$lt": threshold_time}},
                ]
            },
            {
                "$or": [
                    {"reminders_sent": {"$exists": False}},
                    {"reminders_sent": {"$lt": WorkerConstants.MAX_PRO_REMINDERS}},
                ]
            },
        ],
    }

    try:
        cursor = leads_collection.find(query)
        stale_leads = await cursor.to_list(length=WorkerConstants.DB_QUERY_LIMIT)

        if not stale_leads:
            logger.info("✅ [Stale Lead Nudger] No stale booked leads found.")
            return

        logger.warning(
            f"⏰ [Stale Lead Nudger] Found {len(stale_leads)} stale leads. Sending reminders..."
        )

        for lead in stale_leads:
            lead_id = lead["_id"]
            pro_id = lead.get("pro_id")
            customer_name = lead.get("customer_name") or "לקוח"

            if not pro_id:
                continue

            pro = await users_collection.find_one({"_id": pro_id})
            if not pro or not pro.get("phone_number"):
                continue

            pro_name = pro.get("business_name") or pro.get("name") or "איש מקצוע"
            pro_phone = pro["phone_number"]
            if not pro_phone.endswith("@c.us"):
                pro_phone = f"{pro_phone}@c.us"

            # Send Message
            message = Messages.Pro.STALE_LEAD_REMINDER.format(
                pro_name=pro_name, customer_name=customer_name
            )

            try:
                await whatsapp.send_message(pro_phone, message)

                # Update lead
                await leads_collection.update_one(
                    {"_id": lead_id},
                    {
                        "$inc": {"reminders_sent": 1},
                        "$set": {"last_reminder_at": datetime.now(timezone.utc)},
                    },
                )
                logger.info(
                    f"✅ [Stale Lead Nudger] Sent reminder to pro {pro_id} for lead {lead_id}"
                )
            except Exception as e:
                logger.error(
                    f"❌ [Stale Lead Nudger] Failed to send reminder to {pro_phone}: {e}"
                )

    except Exception as e:
        logger.error(f"❌ [Stale Lead Nudger] Error: {e}")
