from datetime import datetime, timedelta, timezone
from app.core.database import leads_collection, users_collection
from app.core.constants import LeadStatus, WorkerConstants
from app.core.logger import logger
from app.services.whatsapp_client import WhatsAppClient
from app.services import matching_service
from app.core.messages import Messages
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
        "status": {"$in": [LeadStatus.NEW, LeadStatus.CONTACTED]},
        "created_at": {"$lt": threshold_time}
    }
    
    try:
        cursor = leads_collection.find(query)
        stale_leads = await cursor.to_list(length=None)
        
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
                location=lead.get("full_address") or lead.get("city"), # Check schema
                excluded_pro_ids=excluded_ids
            )
            
            if new_pro:
                new_pro_id = new_pro["_id"]
                
                # 3. Update Lead
                await leads_collection.update_one(
                    {"_id": lead_id},
                    {
                        "$set": {
                            "pro_id": new_pro_id,
                            "status": LeadStatus.NEW,
                            "created_at": datetime.now(timezone.utc), # Reset timer
                            "reassigned_from": current_pro_id
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
                        issue_type=lead.get('issue_type', 'Unknown'),
                        appointment_time=lead.get('appointment_time', 'Pending')
                    )
                    msg_to_pro += Messages.Pro.NEW_LEAD_FOOTER
                    
                    await whatsapp.send_message(pro_phone, msg_to_pro)
                    
                    # Send location link if address exists
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

                logger.info(f"✅ [SOS Healer] Lead {lead_id} reassigned from {current_pro_id} to {new_pro_id}")
            
            else:
                logger.warning(f"⚠️ [SOS Healer] Could not find replacement for lead {lead_id} (Stuck).")

    except Exception as e:
        logger.error(f"❌ [SOS Healer] Error: {e}")

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
        "status": {"$in": [LeadStatus.NEW, LeadStatus.CONTACTED]},
        "created_at": {"$lt": threshold_time}
    }
    
    try:
        cursor = leads_collection.find(query)
        stuck_leads = await cursor.to_list(length=None)
        
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
        admin_chat_id = f"{WorkerConstants.ADMIN_PHONE}@c.us"
        await whatsapp.send_message(admin_chat_id, full_message)
        logger.info(f"✅ [SOS Reporter] Sent report to Admin: {WorkerConstants.ADMIN_PHONE}")

    except Exception as e:
        logger.error(f"❌ [SOS Reporter] Error: {e}")
