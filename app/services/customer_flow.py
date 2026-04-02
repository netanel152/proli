from app.core.database import users_collection, leads_collection, reviews_collection
from app.core.logger import logger
from app.core.messages import Messages
from app.core.constants import LeadStatus, Defaults
from app.services.context_manager_service import ContextManager
from bson import ObjectId
from datetime import datetime, timezone


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

        await whatsapp.send_interactive_buttons(
            to_number=customer_chat_id.replace("@c.us", ""),
            text=Messages.Customer.COMPLETION_CHECK.format(pro_name=pro_name),
            buttons=[
                {"id": Messages.Keywords.BUTTON_CONFIRM_FINISH, "title": Messages.Keywords.BUTTON_TITLE_YES_FINISHED},
                {"id": Messages.Keywords.BUTTON_NOT_FINISHED, "title": Messages.Keywords.BUTTON_TITLE_NO_NOT_YET}
            ]
        )
        logger.success(f"Sent customer completion check (buttons) for lead {lead_id} (Trigger: {triggered_by})")
    except Exception as e:
        logger.error(f"Error in send_customer_completion_check for lead {lead_id}: {e}")


async def handle_customer_completion_text(chat_id: str, text: str, whatsapp):
    """Checks if the user confirmed job completion via text."""
    text = text.strip()

    is_completion = False
    if Messages.Keywords.CUSTOMER_COMPLETION_INDICATOR in text:
        is_completion = True
    elif Messages.Keywords.BUTTON_CONFIRM_FINISH in text or Messages.Keywords.TEXT_YES_FINISHED in text:
        is_completion = True

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

    pro_id = lead["pro_id"]
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
