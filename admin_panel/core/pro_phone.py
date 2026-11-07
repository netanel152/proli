"""The pro form's phone check — streamlit-free, collection injected (PRO-224).

The form used to store ``phone_number`` exactly as typed, so a pro added as
``050-123-4567`` was never found by the bot's ``[phone, chat_id]`` lookup.
"""

from app.core.phone import to_chat_id, to_il_mobile, to_local_phone

ERR_INVALID = "invalid_pro_phone"
ERR_TAKEN = "pro_phone_taken"


def check_pro_phone(users, raw, exclude_id=None) -> tuple[str, str | None]:
    """``(phone, None)`` with the bare ``9725…`` form to store, or
    ``("", error_key)`` naming the ``TRANS`` message to show.

    ``exclude_id`` is the pro being edited, so saving it unchanged is no clash.
    """
    phone = to_il_mobile(raw)
    if not phone:
        return "", ERR_INVALID
    forms = [phone, to_chat_id(phone), to_local_phone(phone)]
    query = {"phone_number": {"$in": forms}}
    if exclude_id is not None:
        query["_id"] = {"$ne": exclude_id}
    if users.find_one(query, {"_id": 1}):
        return "", ERR_TAKEN
    return phone, None
