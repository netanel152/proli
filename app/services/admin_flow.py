from datetime import datetime, timezone
from bson import ObjectId

from app.core.logger import logger
from app.core.messages import Messages
from app.core.constants import LeadStatus, UserStates, WorkerConstants, Actor
from app.services.lead_manager_service import set_lead_status
from app.core.config import settings
from app.core.database import leads_collection, users_collection
from app.core.phone import to_chat_id
from app.services.notification_service import notify_pro_new_lead

ADMIN_TTL = 900  # 15-minute wizard session


async def handle_admin_message(
    chat_id, user_text, current_state, state_manager, redis_client, whatsapp, db
):
    """
    Entry point for all messages from the admin phone number.
    redis_client is accepted for signature compatibility but unused —
    state_manager owns the Redis connection internally.
    """
    text = (user_text or "").strip()

    if text == "ניהול":
        return await _start_wizard(chat_id, state_manager, whatsapp)

    if current_state == UserStates.ADMIN_SELECTING_LEAD:
        return await _handle_lead_selection(chat_id, text, state_manager, whatsapp)

    if current_state == UserStates.ADMIN_SELECTING_ACTION:
        return await _handle_action_selection(chat_id, text, state_manager, whatsapp)

    if current_state == UserStates.ADMIN_SELECTING_PRO:
        return await _handle_pro_selection(chat_id, text, state_manager, whatsapp)

    # Unknown admin-prefixed state — reset silently
    await state_manager.clear_state(chat_id)


# ---------------------------------------------------------------------------
# Wizard steps
# ---------------------------------------------------------------------------


async def _start_wizard(chat_id, state_manager, whatsapp):
    """List all PENDING_ADMIN_REVIEW leads and enter lead-selection state."""
    cursor = leads_collection.find(
        {"status": LeadStatus.PENDING_ADMIN_REVIEW},
        sort=[("created_at", 1)],
    )
    stuck_leads = await cursor.to_list(length=WorkerConstants.DB_QUERY_LIMIT)

    if not stuck_leads:
        await state_manager.clear_state(chat_id)
        await whatsapp.send_message(chat_id, Messages.Admin.NO_STUCK_LEADS)
        return

    now = datetime.now(timezone.utc)
    lines = [Messages.Admin.STUCK_LEADS_HEADER]
    leads_map = {}

    for i, lead in enumerate(stuck_leads, 1):
        city = lead.get("city") or lead.get("full_address") or "עיר לא ידועה"
        issue = lead.get("issue_type") or "בעיה לא ידועה"
        created_at = lead.get("created_at")
        if created_at:
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            wait_minutes = int((now - created_at).total_seconds() / 60)
            wait_str = Messages.Admin.WAIT_MINUTES.format(wait_minutes=wait_minutes)
        else:
            wait_str = "?"
        lines.append(
            Messages.Admin.STUCK_LEAD_ROW.format(
                num=i, city=city, issue=issue, wait=wait_str
            )
        )
        leads_map[str(i)] = str(lead["_id"])

    lines.append(Messages.Admin.SELECT_PROMPT)

    await state_manager.set_metadata(chat_id, {"admin_leads_context": leads_map})
    await state_manager.set_state(
        chat_id, UserStates.ADMIN_SELECTING_LEAD, ttl=ADMIN_TTL
    )
    await whatsapp.send_message(chat_id, "\n".join(lines))


async def _handle_lead_selection(chat_id, text, state_manager, whatsapp):
    """Validate the admin's numeric lead choice and ask what to do with it."""
    if text in Messages.Keywords.CANCEL_KEYWORDS:
        await state_manager.clear_state(chat_id)
        await whatsapp.send_message(chat_id, Messages.Admin.CANCELLED)
        return

    meta = await state_manager.get_metadata(chat_id)
    leads_map = meta.get("admin_leads_context", {})

    if not text.isdigit() or text not in leads_map:
        await whatsapp.send_message(chat_id, Messages.Admin.INVALID_NUMBER)
        return

    meta["selected_lead_id"] = leads_map[text]
    await state_manager.set_metadata(chat_id, meta)
    await state_manager.set_state(
        chat_id, UserStates.ADMIN_SELECTING_ACTION, ttl=ADMIN_TTL
    )
    await whatsapp.send_message(chat_id, Messages.Admin.ACTION_MENU)


async def _handle_action_selection(chat_id, text, state_manager, whatsapp):
    """Handle self-assign (1) or show available pros (2)."""
    if text in Messages.Keywords.CANCEL_KEYWORDS:
        await state_manager.clear_state(chat_id)
        await whatsapp.send_message(chat_id, Messages.Admin.CANCELLED)
        return

    meta = await state_manager.get_metadata(chat_id)
    lead_id = meta.get("selected_lead_id")

    if text == "1":
        admin_pro = await users_collection.find_one(
            {
                "phone_number": {
                    "$in": [settings.ADMIN_PHONE, to_chat_id(settings.ADMIN_PHONE)]
                },
                "role": "professional",
            }
        )
        if not admin_pro:
            await whatsapp.send_message(chat_id, Messages.Admin.NO_ADMIN_PRO_PROFILE)
            return
        await _assign_lead_to_pro(chat_id, lead_id, admin_pro, state_manager, whatsapp)

    elif text == "2":
        lead = await leads_collection.find_one({"_id": ObjectId(lead_id)})
        if not lead:
            await state_manager.clear_state(chat_id)
            await whatsapp.send_message(chat_id, Messages.Admin.LEAD_NOT_FOUND)
            return

        issue = lead.get("issue_type")
        location = lead.get("full_address") or lead.get("city")

        from app.services.matching_service import determine_best_pro
        from app.core.database import leads_collection as _leads

        pros = []
        excluded = []
        for _ in range(3):
            pro = await determine_best_pro(
                issue_type=issue,
                location=location,
                excluded_pro_ids=excluded,
            )
            if not pro:
                break
            pros.append(pro)
            excluded.append(str(pro["_id"]))

        if not pros:
            await whatsapp.send_message(chat_id, Messages.Admin.NO_AVAILABLE_PROS)
            return

        lines = [Messages.Admin.AVAILABLE_PROS_HEADER]
        pros_map = {}
        for i, p in enumerate(pros, 1):
            name = p.get("business_name", "ללא שם")
            rating = p.get("social_proof", {}).get("rating", "-")
            lines.append(Messages.Admin.PRO_ROW.format(num=i, name=name, rating=rating))
            pros_map[str(i)] = str(p["_id"])

        lines.append(Messages.Admin.SELECT_PROMPT)

        meta["admin_pros_context"] = pros_map
        await state_manager.set_metadata(chat_id, meta)
        await state_manager.set_state(
            chat_id, UserStates.ADMIN_SELECTING_PRO, ttl=ADMIN_TTL
        )
        await whatsapp.send_message(chat_id, "\n".join(lines))

    else:
        await whatsapp.send_message(chat_id, Messages.Admin.INVALID_OPTION)


async def _handle_pro_selection(chat_id, text, state_manager, whatsapp):
    """Validate the admin's pro choice and assign the lead."""
    if text in Messages.Keywords.CANCEL_KEYWORDS:
        await state_manager.clear_state(chat_id)
        await whatsapp.send_message(chat_id, Messages.Admin.CANCELLED)
        return

    meta = await state_manager.get_metadata(chat_id)
    pros_map = meta.get("admin_pros_context", {})
    lead_id = meta.get("selected_lead_id")

    if not text.isdigit() or text not in pros_map:
        await whatsapp.send_message(chat_id, Messages.Admin.INVALID_NUMBER)
        return

    pro_id = pros_map[text]
    pro = await users_collection.find_one({"_id": ObjectId(pro_id)})
    if not pro:
        await state_manager.clear_state(chat_id)
        await whatsapp.send_message(chat_id, Messages.Admin.PRO_NOT_FOUND)
        return

    await _assign_lead_to_pro(chat_id, lead_id, pro, state_manager, whatsapp)


# ---------------------------------------------------------------------------
# Shared assignment helper
# ---------------------------------------------------------------------------


async def _assign_lead_to_pro(chat_id, lead_id, pro, state_manager, whatsapp):
    """Update the lead in Mongo, notify the pro, clear admin wizard state."""
    # A human taking ownership is a fresh start for the lead (PRO-63). Without
    # resetting the counter, a lead escalated for exhausted reassignments comes
    # back with `reassignment_count` still at MAX_REASSIGNMENTS, so the next
    # Healer sweep re-escalates it straight off the pro we just assigned and
    # re-pages the admin. `created_at` is reset too because the Healer keys
    # staleness off it — an hours-old `created_at` would make this freshly
    # assigned lead look stale on the very next 10-min tick and get reassigned
    # away before the new pro's 60-min intent window even opens. Clearing
    # `escalation_reason` lets the lead escalate again if this assignment also
    # fails; the PRO-56 flag/timestamp resets are hygiene for the new pro.
    now = datetime.now(timezone.utc)
    await set_lead_status(
        lead_id,
        LeadStatus.NEW,
        Actor.ADMIN,
        extra_set={
            "pro_id": pro["_id"],
            "assigned_by_admin_at": now,
            "created_at": now,
            "pro_notified_at": now,
            "reassignment_count": 0,
            "approval_nudged": False,
            "reassign_offered": False,
        },
        # PRO-162: `admin_reported_at` goes with `escalation_reason` and the
        # `created_at` reset — the lead has a fresh owner, so a future stuck
        # period is a new incident and must be able to page the operator rather
        # than inherit the previous one's mute.
        extra_unset={"escalation_reason": "", "admin_reported_at": ""},
    )

    lead = await leads_collection.find_one({"_id": ObjectId(lead_id)})
    pro_name = pro.get("business_name") or "איש המקצוע"

    # Tri-state: None = the offer was never *attempted* (lead lookup failed),
    # False = attempted and did not reach the pro. Conflating them would blame
    # the pro's 24h window for a lookup failure and send the operator hunting
    # the wrong problem.
    offer_sent: bool | None = None
    if lead:
        # PRO-159 made this return honest: False means the offer never reached
        # the pro (closed 24h window with no approved template, breaker, or a
        # raised send). No auto-escalation here — the admin IS the review, so
        # escalating back to PENDING_ADMIN_REVIEW would be circular. The
        # assignment stands; the admin is told the truth below and contacts
        # the pro by phone.
        offer_sent = await notify_pro_new_lead(lead, pro, whatsapp)
        # Tell the CUSTOMER a pro was found — mirrors the auto-match path in
        # workflow_service. Without this the customer sat in silence after the
        # PENDING_REVIEW message ("צוות Proli יחזור אליך") until they proactively
        # asked; this path notified only the pro and the admin. Send to the
        # lead's chat_id, never `chat_id` (that is the ADMIN running the wizard).
        # Fail-open: a customer-notify hiccup must not abort the assignment.
        # Only when the pro actually got the offer, though — "X יטפל בך"
        # while X has no idea is the exact false-success this branch removes.
        customer_chat_id = lead.get("chat_id")
        if customer_chat_id and offer_sent:
            try:
                await whatsapp.send_message(
                    customer_chat_id,
                    Messages.Customer.AWAITING_APPROVAL_TRANSPARENT.format(
                        pro_name=pro_name
                    ),
                )
            except Exception as e:
                logger.error(
                    f"[admin_flow] Failed to notify customer "
                    f"...{customer_chat_id[-8:]} of assignment: {e}"
                )

    await state_manager.clear_state(chat_id)
    if offer_sent:
        await whatsapp.send_message(
            chat_id, Messages.Admin.ASSIGN_SUCCESS.format(pro_name=pro_name)
        )
    elif offer_sent is None:
        await whatsapp.send_message(
            chat_id,
            Messages.Admin.ASSIGN_LEAD_LOOKUP_MISSED.format(pro_name=pro_name),
        )
    else:
        await whatsapp.send_message(
            chat_id,
            Messages.Admin.ASSIGN_OFFER_FAILED.format(pro_name=pro_name),
        )
    logger.info(
        f"[admin_flow] Lead {lead_id} assigned to pro {pro.get('_id')} by admin {chat_id}"
    )
