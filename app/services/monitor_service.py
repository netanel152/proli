from datetime import datetime, timedelta, timezone
from app.core.database import leads_collection, users_collection
from app.core.constants import LeadStatus, WorkerConstants
from app.core.config import settings
from app.core.logger import logger
from app.services.whatsapp_client_service import WhatsAppClient
from app.services import matching_service
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
    
    query = {
        "status": {"$in": [LeadStatus.NEW, LeadStatus.CONTACTED, LeadStatus.PENDING_ADMIN_REVIEW]},
        "created_at": {"$lt": threshold_time}
    }

    try:
        cursor = leads_collection.find(query)
        stale_leads = await cursor.to_list(length=WorkerConstants.DB_QUERY_LIMIT)

        if not stale_leads:
            logger.info("✅ [SOS Healer] No stale leads found.")
            return

        logger.warning(f"🕵️ [SOS Healer] Found {len(stale_leads)} stale leads. Attempting reassignment...")

        for lead in stale_leads:
            lead_id = lead["_id"]
            chat_id = lead["chat_id"]
            current_pro_id = lead.get("pro_id")
            
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
                location=lead.get("full_address"),
                excluded_pro_ids=excluded_ids
            )
            
            reassignment_count = lead.get("reassignment_count", 0)

            # Hard stop: if max reassignments reached, close the lead
            if reassignment_count >= WorkerConstants.MAX_REASSIGNMENTS:
                await leads_collection.update_one(
                    {"_id": lead_id},
                    {"$set": {"status": LeadStatus.CLOSED, "closed_reason": "max_reassignments"}}
                )
                try:
                    await whatsapp.send_message(chat_id, Messages.SOS.MAX_REASSIGNMENTS_REACHED)
                except Exception as e:
                    logger.error(f"Failed to notify customer {chat_id} of closure: {e}")
                await ContextManager.clear_context(chat_id)
                logger.warning(f"🚫 [SOS Healer] Lead {lead_id} closed after {reassignment_count} reassignments.")
                continue

            if new_pro:
                new_pro_id = new_pro["_id"]

                # 3. Update Lead — increment counter, reset timer
                await leads_collection.update_one(
                    {"_id": lead_id},
                    {
                        "$set": {
                            "pro_id": new_pro_id,
                            "status": LeadStatus.NEW,
                            "created_at": datetime.now(timezone.utc),
                            "reassigned_from": current_pro_id,
                            "reassignment_count": reassignment_count + 1
                        }
                    }
                )

                # 4. Notify New Pro
                pro_phone = new_pro.get("phone_number")
                if pro_phone:
                    if not pro_phone.endswith("@c.us"):
                        pro_phone = f"{pro_phone}@c.us"

                    msg_to_pro = Messages.Pro.NEW_LEAD_HEADER + "\n\n"
                    msg_to_pro += Messages.Pro.NEW_LEAD_DETAILS.format(
                        full_address=lead.get('full_address', 'Unknown'),
                        extra_info=f"קומה {lead.get('floor') or '-'}, דירה {lead.get('apartment') or '-'}",
                        issue_type=lead.get('issue_type', 'Unknown'),
                        appointment_time=lead.get('appointment_time', 'Pending')
                    )
                    msg_to_pro += Messages.Pro.NEW_LEAD_FOOTER

                    await whatsapp.send_message(pro_phone, msg_to_pro)

                    if lead.get('full_address'):
                        await whatsapp.send_location_link(pro_phone, lead['full_address'], Messages.Pro.NAVIGATE_TO)

                # 5. Notify Old Pro
                if current_pro_id:
                    old_pro = await users_collection.find_one({"_id": current_pro_id})
                    if old_pro and old_pro.get("phone_number"):
                        old_phone = old_pro["phone_number"]
                        if not old_phone.endswith("@c.us"):
                            old_phone = f"{old_phone}@c.us"
                        await whatsapp.send_message(old_phone, Messages.SOS.PRO_LOST_LEAD)

                # Clear any stuck customer state (e.g. AWAITING_PRO_APPROVAL)
                await StateManager.clear_state(chat_id)
                logger.info(f"✅ [SOS Healer] Lead {lead_id} reassigned from {current_pro_id} to {new_pro_id} (attempt {reassignment_count + 1})")

            else:
                logger.warning(f"⚠️ [SOS Healer] Could not find replacement for lead {lead_id} — escalating to PENDING_ADMIN_REVIEW.")
                await leads_collection.update_one(
                    {"_id": lead_id},
                    {"$set": {"status": LeadStatus.PENDING_ADMIN_REVIEW}}
                )
                try:
                    await whatsapp.send_message(chat_id, Messages.Customer.PENDING_REVIEW)
                except Exception as e:
                    logger.error(f"Failed to notify customer {chat_id} of pending review: {e}")
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
        "created_at": {"$lt": threshold_time}
    }

    try:
        cursor = leads_collection.find(query)
        stale_unassigned = await cursor.to_list(length=WorkerConstants.DB_QUERY_LIMIT)

        if not stale_unassigned:
            logger.info("✅ [Janitor] No unassigned stale leads found.")
            return

        logger.warning(f"🧹 [Janitor] Closing {len(stale_unassigned)} unassigned leads.")

        for lead in stale_unassigned:
            lead_id = lead["_id"]
            chat_id = lead.get("chat_id")

            await leads_collection.update_one(
                {"_id": lead_id},
                {"$set": {"status": LeadStatus.CLOSED, "closed_reason": "no_pro_available"}}
            )

            if chat_id:
                try:
                    await whatsapp.send_message(chat_id, Messages.SOS.NO_PRO_AVAILABLE)
                except Exception as e:
                    logger.error(f"Failed to notify customer {chat_id} of closure: {e}")
                await ContextManager.clear_context(chat_id)

            logger.info(f"🧹 [Janitor] Closed unassigned lead {lead_id} (chat: {chat_id})")

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
        "status": {"$in": [LeadStatus.NEW, LeadStatus.CONTACTED, LeadStatus.PENDING_ADMIN_REVIEW]},
        "created_at": {"$lt": threshold_time}
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
            Messages.SOS.ADMIN_REPORT_BODY.format(count=count, timeout=timeout_minutes)
        ]

        for lead in stuck_leads:
            chat_id = lead.get("chat_id", "Unknown").split("@")[0]
            issue = lead.get("issue_type", "Unknown Issue")
            city = lead.get("full_address", "Unknown City")
            created_at = lead.get("created_at")
            time_str = created_at.strftime("%H:%M") if created_at else "??"
            
            message_lines.append(f"- {chat_id}: {issue} in {city} (Waiting since {time_str})")

        message_lines.append(Messages.SOS.ADMIN_REPORT_FOOTER)
        full_message = "\n".join(message_lines)
        
        # Send to Admin
        admin_chat_id = f"{settings.ADMIN_PHONE}@c.us"
        await whatsapp.send_message(admin_chat_id, full_message)
        logger.info(f"✅ [SOS Reporter] Sent report to Admin: {settings.ADMIN_PHONE}")

    except Exception as e:
        logger.error(f"❌ [SOS Reporter] Error: {e}")


async def check_sla_deflection():
    """
    SLA MONITOR:
    Finds leads where bot is paused for human, checks if it's been silent for 15 mins.
    """
    logger.info("🕵️ [SLA Monitor] Checking for silent human handoffs...")
    
    # We use WorkerConstants.PAUSE_TTL_SECONDS as the inactivity threshold (currently 900s / 15m)
    threshold_time = datetime.now(timezone.utc) - timedelta(seconds=WorkerConstants.PAUSE_TTL_SECONDS)
    
    # Find leads that are paused and haven't had activity for the threshold time
    query = {
        "is_paused": True,
        "paused_at": {"$lt": threshold_time},
        "status": {"$in": [LeadStatus.NEW, LeadStatus.BOOKED]}
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
                await leads_collection.update_one({"_id": lead["_id"]}, {"$set": {"is_paused": False}})
                continue

            # It's been 15 mins of silence. Trigger deflection.
            logger.warning(f"⏰ [SLA Monitor] SLA exceeded for {chat_id}. Deflecting to phone check.")

            # 1. Clear state
            await StateManager.clear_state(chat_id)
            
            # 2. Update lead doc
            await leads_collection.update_one(
                {"_id": lead["_id"]}, 
                {"$set": {"is_paused": False, "sla_deflected": True}}
            )

            # 3. Send Deflection Message
            await whatsapp.send_message(chat_id, Messages.Customer.SLA_DEFLECTION_MESSAGE)
            
            logger.info(f"✅ [SLA Monitor] Deflected customer {chat_id} after inactivity.")

    except Exception as e:
        logger.error(f"❌ [SLA Monitor] Error: {e}")
