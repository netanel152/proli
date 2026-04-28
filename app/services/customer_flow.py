from app.core.database import users_collection, leads_collection, reviews_collection, slots_collection
from app.core.logger import logger
from app.core.messages import Messages
from app.core.constants import LeadStatus, Defaults
from app.services.context_manager_service import ContextManager
from app.services.state_manager_service import StateManager
from bson import ObjectId
from datetime import datetime, timezone
import pytz

_IL_TZ = pytz.timezone("Asia/Jerusalem")


async def send_customer_completion_check(lead_id: str, whatsapp, triggered_by: str = "auto"):
    """Asks the customer if the job has been completed using interactive buttons."""
    try:
        lead = await leads_collection.find_one({"_id": ObjectId(lead_id)})
        if not lead or lead.get("status") != LeadStatus.BOOKED:
            logger.warning(f"send_customer_completion_check called for invalid/non-booked lead: {lead_id}")
            return

        customer_chat_id = lead["chat_id"]
        pro = await users_collection.find_one({"_id": lead["pro_id"]})
        pro_name = pro.get("business_name", Defaults.GENERIC_PRO_NAME) if pro else Defaults.GENERIC_PRO_NAME

        await whatsapp.send_message(
            customer_chat_id,
            Messages.Customer.COMPLETION_CHECK.format(pro_name=pro_name),
        )
        logger.success(f"Sent customer completion check for lead {lead_id} (Trigger: {triggered_by})")
    except Exception as e:
        logger.error(f"Error in send_customer_completion_check for lead {lead_id}: {e}")


async def handle_customer_completion_text(chat_id: str, text: str, whatsapp):
    """Checks if the user confirmed job completion via text."""
    stripped = text.strip()
    normalized = stripped.lower()

    yes_tokens = {"1", "כן", "כן הסתיים", "כן, הסתיים", "הסתיים", "yes", "done"}
    is_completion = (
        normalized in yes_tokens
        or Messages.Keywords.CUSTOMER_COMPLETION_INDICATOR in stripped
    )

    if not is_completion:
        return None

    lead = await leads_collection.find_one({
        "chat_id": chat_id,
        "status": LeadStatus.BOOKED
    }, sort=[("created_at", -1)])

    if not lead:
        return None

    pro = await users_collection.find_one({"_id": lead["pro_id"]})

    await leads_collection.update_one(
        {"_id": lead["_id"]},
        {
            "$set": {
                "status": LeadStatus.COMPLETED,
                "completed_at": datetime.now(timezone.utc),
                "waiting_for_rating": True
            }
        }
    )
    logger.success(f"✅ Lead {lead['_id']} marked as COMPLETED by customer.")

    # Clear cached context — lead is done, next conversation starts fresh
    await ContextManager.clear_context(chat_id)

    if pro and pro.get("phone_number"):
        pro_chat_id = f"{pro['phone_number']}@c.us" if not pro['phone_number'].endswith('@c.us') else pro['phone_number']
        await whatsapp.send_message(pro_chat_id, Messages.Pro.CUSTOMER_REPORTED_COMPLETION)

    pro_name = pro.get('business_name', Defaults.EXPERT_NAME) if pro else Defaults.EXPERT_NAME
    return Messages.Customer.COMPLETION_ACK.format(pro_name=pro_name)


async def handle_customer_rating_text(chat_id: str, text: str):
    """Checks if the user sent a rating (1-5)."""
    text = text.strip()
    if text not in Messages.Keywords.RATING_OPTIONS:
        return None

    rating = int(text)

    lead = await leads_collection.find_one({
        "chat_id": chat_id,
        "waiting_for_rating": True
    })

    if not lead:
        return None

    try:
        pro_id = lead["pro_id"]
        pro = await users_collection.find_one({"_id": pro_id})

        if not pro:
            logger.error(f"Pro {pro_id} not found for rating on lead {lead['_id']}")
            return None

        # Compute new rating in Python to avoid $round aggregation pipeline (not supported by all drivers/mocks)
        current_count = pro.get("social_proof", {}).get("review_count") or 0
        current_rating = pro.get("social_proof", {}).get("rating") or 5.0
        new_count = current_count + 1
        new_rating = round((current_rating * current_count + rating) / new_count, 1)

        await users_collection.update_one(
            {"_id": pro_id},
            {"$set": {
                "social_proof.rating": new_rating,
                "social_proof.review_count": new_count
            }}
        )

        await leads_collection.update_one(
            {"_id": lead["_id"]},
            {"$set": {
                "waiting_for_rating": False,
                "rating_given": rating,
                "waiting_for_review_comment": True
            }}
        )

        business_name = pro.get('business_name', Defaults.GENERIC_PRO_NAME)
        logger.success(f"⭐ Rating {rating} saved for {business_name}")
        return Messages.Customer.REVIEW_REQUEST
    except Exception as e:
        logger.error(f"Error handling rating for lead {lead['_id']}: {e}")
        return None


async def handle_customer_review_comment(chat_id: str, text: str):
    """Checks if the user sent a textual review after rating."""
    lead = await leads_collection.find_one({
        "chat_id": chat_id,
        "waiting_for_review_comment": True
    })

    if not lead:
        return None

    pro_id = lead.get("pro_id")
    if not pro_id:
        logger.warning(f"handle_customer_review_comment: lead {lead['_id']} has no pro_id, skipping review")
        return None

    rating_given = lead.get("rating_given", 5)

    review_doc = {
        "pro_id": pro_id,
        "customer_chat_id": chat_id,
        "rating": rating_given,
        "comment": text,
        "created_at": datetime.now(timezone.utc)
    }

    await reviews_collection.insert_one(review_doc)

    await leads_collection.update_one(
        {"_id": lead["_id"]},
        {"$set": {"waiting_for_review_comment": False}}
    )

    # Clear context so the next conversation starts fresh
    await ContextManager.clear_context(chat_id)

    logger.success(f"📝 Review comment saved for lead {lead['_id']}")
    return Messages.Customer.REVIEW_SAVED


async def handle_reschedule_selection(chat_id: str, user_text: str, whatsapp) -> None:
    normalized = user_text.strip().lower()

    if any(kw in normalized for kw in Messages.Keywords.CANCEL_KEYWORDS):
        await StateManager.clear_state(chat_id)
        await whatsapp.send_message(chat_id, Messages.Customer.RESCHEDULE_CANCELLED)
        return

    meta = await StateManager.get_metadata(chat_id)
    slots_context = meta.get("reschedule_slots_context", {})
    pick = user_text.strip()

    if pick not in slots_context:
        await whatsapp.send_message(chat_id, Messages.Customer.RESCHEDULE_INVALID_CHOICE)
        return  # state preserved — let customer retry

    slot_id = ObjectId(slots_context[pick])

    lead = await leads_collection.find_one(
        {"chat_id": chat_id, "status": LeadStatus.BOOKED},
        sort=[("created_at", -1)],
    )
    if not lead:
        await StateManager.clear_state(chat_id)
        await whatsapp.send_message(chat_id, Messages.Errors.GENERIC_ERROR)
        return

    # Atomically claim the chosen slot (guards against race conditions)
    chosen_slot = await slots_collection.find_one_and_update(
        {"_id": slot_id, "is_taken": False},
        {"$set": {"is_taken": True}},
    )
    if not chosen_slot:
        await whatsapp.send_message(chat_id, Messages.Customer.RESCHEDULE_INVALID_CHOICE)
        return  # state preserved — let customer retry

    # Free previously booked slot if we have a reference to it
    old_slot_id = lead.get("booked_slot_id")
    if old_slot_id:
        await slots_collection.update_one(
            {"_id": old_slot_id, "is_taken": True},
            {"$set": {"is_taken": False}},
        )

    old_time = lead.get("appointment_time", "לא ידוע")
    new_time = chosen_slot["start_time"].astimezone(_IL_TZ).strftime("%d/%m/%Y %H:%M")

    await leads_collection.update_one(
        {"_id": lead["_id"]},
        {"$set": {
            "appointment_time": new_time,
            "booked_slot_id": slot_id,
            "rescheduled_at": datetime.now(timezone.utc),
            "rescheduled_count": lead.get("rescheduled_count", 0) + 1,
        }},
    )

    await StateManager.clear_state(chat_id)
    await whatsapp.send_message(chat_id, Messages.Customer.RESCHEDULE_SUCCESS.format(new_time=new_time))

    pro = await users_collection.find_one({"_id": lead["pro_id"]})
    if pro and pro.get("phone_number"):
        pro_phone = pro["phone_number"]
        if not pro_phone.endswith("@c.us"):
            pro_phone = f"{pro_phone}@c.us"
        await whatsapp.send_message(
            pro_phone,
            Messages.Pro.CUSTOMER_RESCHEDULED_SUCCESS.format(
                customer_name=lead.get("customer_name") or "הלקוח",
                address=lead.get("full_address") or "לא ידועה",
                old_time=old_time,
                new_time=new_time,
            ),
        )
    logger.success(f"📅 Lead {lead['_id']} rescheduled to {new_time} by customer {chat_id}")
