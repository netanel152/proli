import time
from datetime import datetime, timedelta, timezone
from app.core.database import leads_collection, users_collection
from app.core.constants import LeadStatus, UserStates, WorkerConstants, Defaults, Actor
from app.core.phone import to_chat_id
from app.services.lead_manager_service import set_lead_status
from app.core.config import settings
from app.core.logger import logger
from app.core.redis_client import get_redis_client
from app.core.datetime_utils import within_business_hours
from app.services.whatsapp_client_service import WhatsAppClient
from app.services import matching_service
from app.services.notification_service import send_oncall_alert
from app.core.messages import Messages
from app.services.context_manager_service import ContextManager
from app.services.state_manager_service import StateManager
from bson import ObjectId

whatsapp = WhatsAppClient()


async def reassign_lead(lead) -> bool:
    """Reassign one lead to the next-best pro, excluding its current pro.

    Notifies the customer, the new pro, and the old pro; honors
    ``MAX_REASSIGNMENTS`` (closes the lead) and escalates to
    ``PENDING_ADMIN_REVIEW`` when no replacement exists. Resets the approval-SLA
    clock (``pro_notified_at`` + flags) for the new pro so PRO-56 re-arms.
    Returns True iff a new pro was assigned.

    Shared by the SOS Healer (60-min stale sweep) and the PRO-56 approval-SLA
    reassignment offer (customer chose "find someone else").
    """
    lead_id = lead["_id"]
    chat_id = lead["chat_id"]
    current_pro_id = lead.get("pro_id")

    # Skip leads without a real, usable location — geo matching would always fail
    # and escalate to PENDING_ADMIN_REVIEW, burning a CUSTOMER_REASSIGNING notice
    # each cycle. Hand to the admin directly.
    raw_location = lead.get("full_address")
    if not raw_location or raw_location == Defaults.UNKNOWN_ADDRESS:
        logger.info(
            f"⏭️ [Reassign] Skipping lead {lead_id} for ...{chat_id[-8:]} — no usable "
            f"location (full_address={raw_location!r}). Escalating to PENDING_ADMIN_REVIEW."
        )
        await set_lead_status(
            lead_id,
            LeadStatus.PENDING_ADMIN_REVIEW,
            Actor.SYSTEM,
            extra_set={"escalation_reason": "no_usable_location"},
        )
        await StateManager.clear_state(chat_id)
        return False

    # 1. Notify customer
    try:
        await whatsapp.send_message(chat_id, Messages.SOS.CUSTOMER_REASSIGNING)
    except Exception as e:
        logger.error(f"Failed to notify customer ...{chat_id[-8:]}: {e}")

    # 2. Find replacement (excluding current pro)
    excluded_ids = [current_pro_id] if current_pro_id else []
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
            await whatsapp.send_message(chat_id, Messages.SOS.MAX_REASSIGNMENTS_REACHED)
        except Exception as e:
            logger.error(f"Failed to notify customer ...{chat_id[-8:]} of closure: {e}")
        # Release the customer's FSM state (not just context) — this branch is now
        # reachable from the PRO-56 "1" reply, so a customer whose lead just closed
        # must not stay parked in AWAITING_PRO_APPROVAL.
        await StateManager.clear_state(chat_id)
        await ContextManager.clear_context(chat_id)
        logger.warning(
            f"🚫 [Reassign] Lead {lead_id} closed after {reassignment_count} reassignments."
        )
        return False

    if new_pro:
        new_pro_id = new_pro["_id"]

        # 3. Update lead — increment counter, reset timers + PRO-56 SLA clock
        await set_lead_status(
            lead_id,
            LeadStatus.NEW,
            Actor.SYSTEM,
            extra_set={
                "pro_id": new_pro_id,
                "created_at": datetime.now(timezone.utc),
                "pro_notified_at": datetime.now(timezone.utc),
                "approval_nudged": False,
                "reassign_offered": False,
                "reassigned_from": current_pro_id,
                "reassignment_count": reassignment_count + 1,
            },
        )

        # 4. Notify new pro
        pro_phone = new_pro.get("phone_number")
        if pro_phone:
            pro_phone = to_chat_id(pro_phone)

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

            all_media = lead.get("media_urls", [])
            if all_media:
                for i, m_url in enumerate(all_media):
                    caption = msg_to_pro if i == 0 else ""
                    await whatsapp.send_file_by_url(pro_phone, m_url, caption=caption)
            else:
                await whatsapp.send_message(pro_phone, msg_to_pro)

            if lead.get("full_address"):
                await whatsapp.send_location_link(
                    pro_phone, lead["full_address"], Messages.Pro.NAVIGATE_TO
                )

        # 5. Notify old pro
        if current_pro_id:
            old_pro = await users_collection.find_one({"_id": current_pro_id})
            if old_pro and old_pro.get("phone_number"):
                old_phone = to_chat_id(old_pro["phone_number"])
                await whatsapp.send_message(old_phone, Messages.SOS.PRO_LOST_LEAD)

        # Clear any stuck customer state (e.g. AWAITING_PRO_APPROVAL)
        await StateManager.clear_state(chat_id)
        logger.info(
            f"✅ [Reassign] Lead {lead_id} reassigned from {current_pro_id} to "
            f"{new_pro_id} (attempt {reassignment_count + 1})"
        )
        return True

    # No replacement — escalate to admin review and release the customer.
    logger.warning(
        f"⚠️ [Reassign] Could not find replacement for lead {lead_id} — "
        f"escalating to PENDING_ADMIN_REVIEW."
    )
    await set_lead_status(lead_id, LeadStatus.PENDING_ADMIN_REVIEW, Actor.SYSTEM)
    try:
        await whatsapp.send_message(chat_id, Messages.Customer.PENDING_REVIEW)
    except Exception as e:
        logger.error(
            f"Failed to notify customer ...{chat_id[-8:]} of pending review: {e}"
        )
    await StateManager.clear_state(chat_id)
    await ContextManager.clear_context(chat_id)
    return False


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
            await reassign_lead(lead)

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
        admin_chat_id = to_chat_id(settings.ADMIN_PHONE)
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
    PAUSED_KEY = "wa:instance:paused"  # PRO-71 outbound circuit breaker

    try:
        if is_authorized:
            # Recovery path: only announce if we previously paged.
            down_since = await redis.get(DOWN_SINCE_KEY)
            alerted = await redis.get(ALERTED_KEY)
            # Release the auto breaker on recovery. The manual kill switch lives in
            # a separate key (wa:instance:paused:manual) the monitor never touches,
            # so an operator-set halt survives instance recovery.
            await redis.delete(DOWN_SINCE_KEY, ALERTED_KEY, LAST_ALERT_KEY, PAUSED_KEY)
            if down_since and alerted:
                logger.info(
                    "✅ [WA Monitor] Green API instance recovered (authorized)."
                )
                await send_oncall_alert(
                    Messages.Alerts.WHATSAPP_RECOVERED, assume_authorized=True
                )
            return

        # Non-authorized (or unreachable → state is None).
        now = time.time()
        # Circuit breaker (PRO-71): halt outbound IMMEDIATELY on the first
        # non-authorized tick — before the paging threshold — so we stop feeding
        # messages into a filtering/blocked instance. The TTL is a safety net: a
        # live monitor refreshes it every tick; if the monitor dies the breaker
        # auto-releases so outbound is never halted forever.
        await redis.set(
            PAUSED_KEY,
            state or "unreachable",
            ex=WorkerConstants.WA_STATE_PAUSE_TTL_SECONDS,
        )
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
        # logger.critical → forwarded to Sentry as an issue (worker is CRITICAL-only),
        # which is the out-of-band operator page. We deliberately do NOT try to
        # send an on-call alert over WhatsApp here: WhatsApp is the down channel,
        # so paging over it would only amplify the outage (PRO-75). The structured
        # context below (state, downtime, instance) makes the Sentry email actionable.
        # yellowCard is the insidious case: Green API returns 200 and the message
        # is silently filtered (accepted, never delivered). notAuthorized/blocked/
        # unreachable stop processing outright. Branch the text so a paged operator
        # looks in the right place.
        if state == "yellowCard":
            impact = "messages are being silently filtered by WhatsApp (accepted, never delivered)"
        else:
            impact = "no messages are being processed"
        logger.critical(
            f"🚨 [WA Monitor] Green API instance NON-AUTHORIZED for "
            f"~{downtime_minutes:.0f}m (state={state or 'unreachable'}, "
            f"instance=***{str(settings.GREEN_API_INSTANCE_ID)[-4:]}) — {impact}. "
            "Outbound is halted (circuit breaker). Paging on-call via Sentry email."
        )
    except Exception as e:
        logger.error(f"[WA Monitor] Error during instance-state check: {e}")


async def check_pro_approval_sla():
    """PRO-56 — chase a silent pro fast instead of waiting for the 60-min Healer.

    Over leads in NEW with an assigned pro whose customer is still parked in
    AWAITING_PRO_APPROVAL, timed from ``pro_notified_at``:
      * T+APPROVAL_NUDGE_MINUTES → nudge the pro once (``approval_nudged`` flag).
      * T+APPROVAL_REASSIGN_OFFER_MINUTES → offer the customer a reassignment once
        (``reassign_offered`` flag); the 1/2 reply is handled in workflow_service.
    Emergency leads use half the thresholds. Idempotent via the boolean flags.
    """
    logger.info("⏰ [Approval SLA] Checking leads awaiting pro approval...")
    now = datetime.now(timezone.utc)
    try:
        query = {
            "status": LeadStatus.NEW,
            "pro_id": {"$ne": None},
            "pro_notified_at": {"$ne": None},
            "$or": [
                {"approval_nudged": {"$ne": True}},
                {"reassign_offered": {"$ne": True}},
            ],
        }
        leads = await leads_collection.find(query).to_list(
            length=WorkerConstants.DB_QUERY_LIMIT
        )
    except Exception as e:
        logger.error(f"❌ [Approval SLA] Query failed: {e}")
        return

    for lead in leads:
        try:
            chat_id = lead["chat_id"]
            # Only act while the customer is genuinely waiting for approval.
            if (
                await StateManager.get_state(chat_id)
                != UserStates.AWAITING_PRO_APPROVAL
            ):
                continue

            notified_at = lead.get("pro_notified_at")
            if not notified_at:
                continue
            # Mongo hands datetimes back tz-naive; make it UTC-aware before the
            # subtraction (matches the guard used across the codebase). Without
            # this the arithmetic raises and the per-lead except swallows it —
            # the whole feature would silently never fire.
            if notified_at.tzinfo is None:
                notified_at = notified_at.replace(tzinfo=timezone.utc)
            waited_min = (now - notified_at).total_seconds() / 60

            nudge_after = WorkerConstants.APPROVAL_NUDGE_MINUTES
            offer_after = WorkerConstants.APPROVAL_REASSIGN_OFFER_MINUTES
            if lead.get("is_emergency"):
                nudge_after //= 2  # 5 min
                offer_after //= 2  # 12 min

            # T+offer: reassignment offer to the customer (once). Claim the flag
            # atomically (gated on status=NEW + not-yet-offered) BEFORE sending, so
            # overlapping ticks — or a Redis-down scheduler lock that fails open —
            # can't double-send. NOTE: quiet-hours / Shabbat gating of this
            # customer-facing message is deferred to PRO-73.
            # PRO-73: the customer-facing offer is gated to business hours — never
            # message a customer at 3am. Outside hours we skip (reassign_offered
            # stays False) so it fires on the next in-hours tick. The pro nudge
            # below is pro-facing and stays ungated.
            if (
                waited_min >= offer_after
                and not lead.get("reassign_offered")
                and within_business_hours()
            ):
                claimed = await leads_collection.update_one(
                    {
                        "_id": lead["_id"],
                        "status": LeadStatus.NEW,
                        "reassign_offered": {"$ne": True},
                    },
                    {"$set": {"reassign_offered": True}},
                )
                if claimed.modified_count == 1:
                    await whatsapp.send_message(
                        chat_id, Messages.Customer.REASSIGN_OFFER
                    )
                    logger.info(
                        f"⏰ [Approval SLA] Offered reassignment to ...{chat_id[-8:]} "
                        f"after ~{waited_min:.0f}m."
                    )
                continue  # don't also nudge on the same tick

            # T+nudge: nudge the silent pro (once) — same atomic-claim pattern.
            if waited_min >= nudge_after and not lead.get("approval_nudged"):
                claimed = await leads_collection.update_one(
                    {
                        "_id": lead["_id"],
                        "status": LeadStatus.NEW,
                        "approval_nudged": {"$ne": True},
                    },
                    {"$set": {"approval_nudged": True}},
                )
                if claimed.modified_count == 1:
                    pro = await users_collection.find_one({"_id": lead.get("pro_id")})
                    if pro and pro.get("phone_number"):
                        pro_phone = to_chat_id(pro["phone_number"])
                        await whatsapp.send_message(
                            pro_phone,
                            Messages.Pro.APPROVAL_NUDGE.format(minutes=nudge_after),
                        )
                    logger.info(
                        f"⏰ [Approval SLA] Nudged pro for lead {lead['_id']} "
                        f"after ~{waited_min:.0f}m."
                    )
        except Exception as e:
            logger.error(f"❌ [Approval SLA] Error on lead {lead.get('_id')}: {e}")


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
            pro_phone = to_chat_id(pro_phone)

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
