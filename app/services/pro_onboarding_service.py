"""
Pro Onboarding Service — WhatsApp-based self-signup flow.

Multi-step FSM collecting business name, type, areas, prices.
Creates user with is_active=False pending admin approval.
"""

from app.core.database import users_collection
from app.core.constants import UserStates, ISRAEL_CITIES_COORDS
from app.core.messages import Messages
from app.core.logger import logger
from app.services.state_manager_service import StateManager
from datetime import datetime, timezone


ONBOARDING_STATES = {
    UserStates.ONBOARDING_NAME,
    UserStates.ONBOARDING_TYPE,
    UserStates.ONBOARDING_AREAS,
    UserStates.ONBOARDING_PRICES,
    UserStates.ONBOARDING_CONFIRM,
}


async def start_onboarding(chat_id: str, whatsapp) -> bool:
    """Start the onboarding flow for a new professional.
    Returns True if onboarding started, False if already registered."""
    phone = chat_id.replace("@c.us", "")

    existing = await users_collection.find_one({
        "phone_number": {"$in": [phone, chat_id]},
        "role": "professional",
    })
    if existing:
        if existing.get("pending_approval"):
            await whatsapp.send_message(chat_id, Messages.Onboarding.PENDING_ALREADY)
        else:
            await whatsapp.send_message(chat_id, Messages.Onboarding.ALREADY_REGISTERED)
        return False

    await StateManager.set_state(chat_id, UserStates.ONBOARDING_NAME)
    await StateManager.set_metadata(chat_id, {"onboarding": {}})
    await whatsapp.send_message(chat_id, Messages.Onboarding.WELCOME)
    return True


async def handle_onboarding_step(chat_id: str, text: str, state: str, whatsapp):
    """Route to the correct onboarding step handler."""
    text = text.strip()

    # Allow cancel at any step
    if text.lower() in ["ביטול", "cancel", "בטל"]:
        await StateManager.clear_state(chat_id)
        await whatsapp.send_message(chat_id, Messages.Onboarding.CANCELLED)
        return

    handlers = {
        UserStates.ONBOARDING_NAME: _handle_name,
        UserStates.ONBOARDING_TYPE: _handle_type,
        UserStates.ONBOARDING_AREAS: _handle_areas,
        UserStates.ONBOARDING_PRICES: _handle_prices,
        UserStates.ONBOARDING_CONFIRM: _handle_confirm,
    }

    handler = handlers.get(state)
    if handler:
        await handler(chat_id, text, whatsapp)


async def _handle_name(chat_id: str, text: str, whatsapp):
    if len(text) < 2 or len(text) > 100:
        await whatsapp.send_message(chat_id, "שם העסק חייב להיות בין 2 ל-100 תווים. נסה שוב:")
        return

    meta = await StateManager.get_metadata(chat_id)
    data = meta.get("onboarding", {})
    data["name"] = text
    await StateManager.set_metadata(chat_id, {"onboarding": data})
    await StateManager.set_state(chat_id, UserStates.ONBOARDING_TYPE)
    await whatsapp.send_message(chat_id, Messages.Onboarding.ASK_TYPE)


async def _handle_type(chat_id: str, text: str, whatsapp):
    type_map = Messages.Onboarding.TYPE_MAP
    pro_type = type_map.get(text) or type_map.get(text.strip().lower())

    if not pro_type:
        await whatsapp.send_message(chat_id, Messages.Onboarding.INVALID_TYPE)
        return

    meta = await StateManager.get_metadata(chat_id)
    data = meta.get("onboarding", {})
    data["type"] = pro_type
    await StateManager.set_metadata(chat_id, {"onboarding": data})
    await StateManager.set_state(chat_id, UserStates.ONBOARDING_AREAS)
    await whatsapp.send_message(chat_id, Messages.Onboarding.ASK_AREAS)


async def _handle_areas(chat_id: str, text: str, whatsapp):
    areas = [a.strip() for a in text.replace("،", ",").split(",") if a.strip()]
    if not areas:
        await whatsapp.send_message(chat_id, "לא זיהיתי ערים. שלח רשימת ערים מופרדות בפסיקים:")
        return

    meta = await StateManager.get_metadata(chat_id)
    data = meta.get("onboarding", {})
    data["areas"] = areas
    await StateManager.set_metadata(chat_id, {"onboarding": data})
    await StateManager.set_state(chat_id, UserStates.ONBOARDING_PRICES)
    await whatsapp.send_message(chat_id, Messages.Onboarding.ASK_PRICES)


async def _handle_prices(chat_id: str, text: str, whatsapp):
    meta = await StateManager.get_metadata(chat_id)
    data = meta.get("onboarding", {})

    if text.lower() in ["דלג", "skip", "לדלג"]:
        data["prices"] = ""
    else:
        data["prices"] = text

    await StateManager.set_metadata(chat_id, {"onboarding": data})
    await StateManager.set_state(chat_id, UserStates.ONBOARDING_CONFIRM)

    type_label = Messages.Onboarding.TYPE_LABELS.get(data.get("type", ""), data.get("type", ""))
    areas_str = ", ".join(data.get("areas", []))
    prices_str = data.get("prices") or "לא צוין"

    confirm_msg = Messages.Onboarding.CONFIRM.format(
        name=data.get("name", ""),
        type=type_label,
        areas=areas_str,
        prices=prices_str,
    )
    await whatsapp.send_message(chat_id, confirm_msg)


async def _handle_confirm(chat_id: str, text: str, whatsapp):
    if text.lower() in ["אשר", "כן", "confirm", "yes", "אישור"]:
        meta = await StateManager.get_metadata(chat_id)
        data = meta.get("onboarding", {})
        await _create_pending_pro(chat_id, data)
        await StateManager.clear_state(chat_id)
        await whatsapp.send_message(chat_id, Messages.Onboarding.SUCCESS)
    elif text.lower() in ["ביטול", "לא", "cancel", "no"]:
        await StateManager.clear_state(chat_id)
        await whatsapp.send_message(chat_id, Messages.Onboarding.CANCELLED)
    else:
        await whatsapp.send_message(chat_id, "השב *אשר* לשליחה או *ביטול* להתחלה מחדש.")


async def _create_pending_pro(chat_id: str, data: dict):
    phone = chat_id.replace("@c.us", "")
    areas = data.get("areas", [])

    # Try to resolve coordinates from first known city
    location = None
    for area in areas:
        coords = ISRAEL_CITIES_COORDS.get(area)
        if coords:
            location = {"type": "Point", "coordinates": coords}
            break

    pro_doc = {
        "business_name": data.get("name", ""),
        "phone_number": phone,
        "role": "professional",
        "type": data.get("type", "general"),
        "categories": [data.get("type", "general")],
        "service_areas": areas,
        "prices_for_prompt": data.get("prices", ""),
        "is_active": False,
        "pending_approval": True,
        "social_proof": {"rating": 5.0, "review_count": 0},
        "plan": "basic",
        "system_prompt": "",
        "keywords": [],
        "created_at": datetime.now(timezone.utc),
        "onboarded_via": "whatsapp",
    }

    if location:
        pro_doc["location"] = location

    result = await users_collection.insert_one(pro_doc)
    logger.info(f"New pending pro created: {result.inserted_id} ({data.get('name')})")
    return result.inserted_id
