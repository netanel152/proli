from datetime import datetime, timezone
from bson import ObjectId

from app.core.logger import logger
from app.core.messages import Messages
from app.core.constants import LeadStatus, UserStates, WorkerConstants
from app.core.config import settings
from app.core.database import leads_collection, users_collection

ADMIN_TTL = 900  # 15-minute wizard session


async def handle_admin_message(chat_id, user_text, current_state, state_manager, redis_client, whatsapp, db):
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
        await whatsapp.send_message(chat_id, "✅ אין לידים תקועים כרגע")
        return

    now = datetime.now(timezone.utc)
    lines = ["📋 *לידים הממתינים לטיפול:*\n"]
    leads_map = {}

    for i, lead in enumerate(stuck_leads, 1):
        city = lead.get("city") or lead.get("full_address") or "עיר לא ידועה"
        issue = lead.get("issue_type") or "בעיה לא ידועה"
        created_at = lead.get("created_at")
        if created_at:
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            wait_minutes = int((now - created_at).total_seconds() / 60)
            wait_str = f"{wait_minutes}ד'"
        else:
            wait_str = "?"
        lines.append(f"{i}. {city} — {issue} (ממתין {wait_str})")
        leads_map[str(i)] = str(lead["_id"])

    lines.append("\nהשב/י מספר לבחירה או 'ביטול' ליציאה.")

    await state_manager.set_metadata(chat_id, {"admin_leads_context": leads_map})
    await state_manager.set_state(chat_id, UserStates.ADMIN_SELECTING_LEAD, ttl=ADMIN_TTL)
    await whatsapp.send_message(chat_id, "\n".join(lines))


async def _handle_lead_selection(chat_id, text, state_manager, whatsapp):
    """Validate the admin's numeric lead choice and ask what to do with it."""
    if text in Messages.Keywords.CANCEL_KEYWORDS:
        await state_manager.clear_state(chat_id)
        await whatsapp.send_message(chat_id, "בוטל.")
        return

    meta = await state_manager.get_metadata(chat_id)
    leads_map = meta.get("admin_leads_context", {})

    if not text.isdigit() or text not in leads_map:
        await whatsapp.send_message(
            chat_id, "❌ מספר לא חוקי. נסה שוב או שלח 'ביטול'."
        )
        return

    meta["selected_lead_id"] = leads_map[text]
    await state_manager.set_metadata(chat_id, meta)
    await state_manager.set_state(chat_id, UserStates.ADMIN_SELECTING_ACTION, ttl=ADMIN_TTL)
    await whatsapp.send_message(
        chat_id,
        "בחרת בליד. למי להעביר?\n1. קח את הליד לעצמך\n2. הצג רשימת אנשי מקצוע פנויים",
    )


async def _handle_action_selection(chat_id, text, state_manager, whatsapp):
    """Handle self-assign (1) or show available pros (2)."""
    if text in Messages.Keywords.CANCEL_KEYWORDS:
        await state_manager.clear_state(chat_id)
        await whatsapp.send_message(chat_id, "בוטל.")
        return

    meta = await state_manager.get_metadata(chat_id)
    lead_id = meta.get("selected_lead_id")

    if text == "1":
        admin_pro = await users_collection.find_one({
            "phone_number": {"$in": [settings.ADMIN_PHONE, f"{settings.ADMIN_PHONE}@c.us"]},
            "role": "professional",
        })
        if not admin_pro:
            await whatsapp.send_message(
                chat_id,
                "❌ לא נמצא פרופיל פרופסיונלי למנהל. נסה אפשרות 2.",
            )
            return
        await _assign_lead_to_pro(chat_id, lead_id, admin_pro, state_manager, whatsapp)

    elif text == "2":
        lead = await leads_collection.find_one({"_id": ObjectId(lead_id)})
        if not lead:
            await state_manager.clear_state(chat_id)
            await whatsapp.send_message(chat_id, "❌ הליד לא נמצא. אפס עם 'ניהול'.")
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
            await whatsapp.send_message(
                chat_id, "❌ לא נמצאו אנשי מקצוע פנויים לליד זה."
            )
            return

        lines = ["👷 *אנשי מקצוע פנויים:*\n"]
        pros_map = {}
        for i, p in enumerate(pros, 1):
            name = p.get("business_name", "ללא שם")
            rating = p.get("social_proof", {}).get("rating", "-")
            lines.append(f"{i}. {name} (דירוג: {rating})")
            pros_map[str(i)] = str(p["_id"])

        lines.append("\nהשב/י מספר לבחירה או 'ביטול' ליציאה.")

        meta["admin_pros_context"] = pros_map
        await state_manager.set_metadata(chat_id, meta)
        await state_manager.set_state(chat_id, UserStates.ADMIN_SELECTING_PRO, ttl=ADMIN_TTL)
        await whatsapp.send_message(chat_id, "\n".join(lines))

    else:
        await whatsapp.send_message(chat_id, "❌ אפשרות לא חוקית. השב 1 או 2.")


async def _handle_pro_selection(chat_id, text, state_manager, whatsapp):
    """Validate the admin's pro choice and assign the lead."""
    if text in Messages.Keywords.CANCEL_KEYWORDS:
        await state_manager.clear_state(chat_id)
        await whatsapp.send_message(chat_id, "בוטל.")
        return

    meta = await state_manager.get_metadata(chat_id)
    pros_map = meta.get("admin_pros_context", {})
    lead_id = meta.get("selected_lead_id")

    if not text.isdigit() or text not in pros_map:
        await whatsapp.send_message(
            chat_id, "❌ מספר לא חוקי. נסה שוב או שלח 'ביטול'."
        )
        return

    pro_id = pros_map[text]
    pro = await users_collection.find_one({"_id": ObjectId(pro_id)})
    if not pro:
        await state_manager.clear_state(chat_id)
        await whatsapp.send_message(chat_id, "❌ איש המקצוע לא נמצא. אפס עם 'ניהול'.")
        return

    await _assign_lead_to_pro(chat_id, lead_id, pro, state_manager, whatsapp)


# ---------------------------------------------------------------------------
# Shared assignment helper
# ---------------------------------------------------------------------------

async def _assign_lead_to_pro(chat_id, lead_id, pro, state_manager, whatsapp):
    """Update the lead in Mongo, notify the pro, clear admin wizard state."""
    await leads_collection.update_one(
        {"_id": ObjectId(lead_id)},
        {"$set": {
            "pro_id": pro["_id"],
            "status": LeadStatus.NEW,
            "assigned_by_admin_at": datetime.now(timezone.utc),
        }},
    )

    lead = await leads_collection.find_one({"_id": ObjectId(lead_id)})

    pro_phone = pro.get("phone_number", "")
    if pro_phone and not pro_phone.endswith("@c.us"):
        pro_phone = f"{pro_phone}@c.us"

    if pro_phone and lead:
        try:
            customer_phone = (lead.get("chat_id") or "").replace("@c.us", "")
            extra_info = f"קומה {lead.get('floor') or '-'}, דירה {lead.get('apartment') or '-'}"
            header = Messages.Pro.EMERGENCY_LEAD_HEADER if lead.get("is_emergency") else Messages.Pro.NEW_LEAD_HEADER
            msg = (
                header + "\n\n"
                + Messages.Pro.NEW_LEAD_DETAILS.format(
                    customer_name=lead.get("customer_name") or "לקוח",
                    full_address=lead.get("full_address") or "לא ידוע",
                    extra_info=extra_info,
                    issue_type=lead.get("issue_type") or "לא ידוע",
                    appointment_time=lead.get("appointment_time") or "בהקדם",
                )
                + Messages.Pro.NEW_LEAD_FOOTER
            )
            await whatsapp.send_message(pro_phone, msg)
        except Exception as e:
            logger.error(f"[admin_flow] Failed to notify pro {pro_phone} for lead {lead_id}: {e}")

    await state_manager.clear_state(chat_id)
    pro_name = pro.get("business_name") or "איש המקצוע"
    await whatsapp.send_message(chat_id, f"✅ הליד הועבר ל-{pro_name}.")
    logger.info(f"[admin_flow] Lead {lead_id} assigned to pro {pro.get('_id')} by admin {chat_id}")
