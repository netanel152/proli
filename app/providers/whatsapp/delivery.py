"""PRO-89 — outbound delivery bookkeeping and the 131047 retry path.

Delivery statuses live in their own ``wa_delivery`` collection, deliberately
**not** in ``messages``: ``lead_manager_service.get_chat_history`` rehydrates
the Gemini context from any ``messages`` document matching the chat_id, so a
status document there would be replayed into the AI prompt as a turn of
conversation.

Flow: ``CloudAPIProvider`` records every accepted send here (wamid → chat_id +
kind); the ``/webhook/meta`` route feeds ``statuses`` events back through
``apply_status_event``, which updates the record and — for a ``failed`` with
error 131047 (send rejected because the 24h service window was closed) —
drives the template-retry path: an approved fallback template is re-sent
through the facade, and when none is registered the operator is paged. Either
way the failure is never silent, which is the acceptance criterion.
"""

from datetime import datetime, timezone
from typing import Any

from app.core.constants import META_ERROR_WINDOW_CLOSED
from app.core.database import wa_delivery_collection
from app.core.logger import logger, page_critical
from app.core.phone import mask_chat_id as _mask
from app.providers.whatsapp import template_registry

# Safe at top level: window.py imports nothing from this package. The
# get_whatsapp import further down is deferred for a different reason —
# that name is defined in __init__ itself, so it has no submodule fallback.
from app.providers.whatsapp.window import claim_window_page


async def record_outbound(wa_message_id: str, chat_id: str, kind: str) -> None:
    """Remember an accepted send so its status callbacks can be attributed.

    No message content is stored — the record exists to answer "what kind of
    send was wamid X, to whom", which is all the retry path needs.
    """
    now = datetime.now(timezone.utc)
    await wa_delivery_collection.update_one(
        {"wa_message_id": wa_message_id},
        {
            "$set": {"chat_id": chat_id, "kind": kind, "updated_at": now},
            "$setOnInsert": {"status": "accepted", "created_at": now},
        },
        upsert=True,
    )


async def apply_status_event(event: dict[str, Any]) -> None:
    """Persist one ``statuses`` webhook event; drive the 131047 retry path.

    Upserts rather than updates: a status can arrive for a wamid this process
    never recorded (worker restart, record_outbound failure), and a delivery
    fact should not be dropped because the bookkeeping row is missing.
    """
    wa_message_id = event.get("wa_message_id")
    status = event.get("status")
    if not wa_message_id or not status:
        return

    now = datetime.now(timezone.utc)
    update: dict[str, Any] = {
        "status": status,
        "status_timestamp": event.get("timestamp"),
        "updated_at": now,
    }
    if event.get("error_code") is not None:
        update["error_code"] = event.get("error_code")
        update["error_title"] = event.get("error_title")
    if event.get("chat_id"):
        update["chat_id"] = event.get("chat_id")

    await wa_delivery_collection.update_one(
        {"wa_message_id": wa_message_id},
        {"$set": update, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )

    if status != "failed":
        return

    record = await wa_delivery_collection.find_one({"wa_message_id": wa_message_id})
    chat_id = (record or {}).get("chat_id") or event.get("chat_id") or ""
    error_code = event.get("error_code")

    if error_code == META_ERROR_WINDOW_CLOSED:
        await _retry_as_template(wa_message_id, chat_id, record or {})
        return

    logger.error(
        f"WhatsApp delivery failed for {_mask(chat_id)}: "
        f"code={error_code}, title={event.get('error_title')!r} "
        f"(wamid {wa_message_id})"
    )


async def _retry_as_template(
    wa_message_id: str, chat_id: str, record: dict[str, Any]
) -> None:
    """A send died on a closed window after we thought it was open (window
    tracker fail-open, or the window expired in flight). Re-engage via the
    registered fallback template, or page — never a silent drop."""
    kind = record.get("kind") or "text"
    fallback = template_registry.freeform_fallback(kind)
    if fallback is None:
        # PRO-172: the same condition ``_window_fallback`` reports before the
        # send — "window closed, no approved fallback" — noticed asynchronously
        # instead. Sharing that path's per-recipient daily claim means a
        # recipient is paged once for the condition rather than once per
        # detection path per occurrence. Losing the claim never means going
        # quiet: the drop is still reported, at ERROR, wamid included.
        # The bound is Redis-backed, with a per-process fallback when Redis
        # cannot answer — deliberately, because that outage is also what makes
        # these callbacks arrive en masse (see ``claim_window_page``).
        log = page_critical if await claim_window_page(chat_id) else logger.error
        log(
            f"WhatsApp {kind} to {_mask(chat_id)} was rejected by Meta with "
            f"error {META_ERROR_WINDOW_CLOSED} (24h service window closed) and "
            "no approved fallback template is registered (PRO-89). The "
            f"message is LOST (wamid {wa_message_id}). Approve a re-engagement "
            "template (PRO-87/PRO-88) to close this gap."
        )
        return

    try:
        # Through the facade, so the retry gets the same breaker/kill-switch
        # protection as any other send. Imported here: this module is imported
        # by the provider package's own modules, and the package __init__ is
        # what defines get_whatsapp.
        from app.providers.whatsapp import get_whatsapp

        await get_whatsapp().send_template(chat_id, fallback.key)
        await wa_delivery_collection.update_one(
            {"wa_message_id": wa_message_id},
            {"$set": {"retried_with_template": fallback.key}},
        )
        logger.info(
            f"Window-closed {kind} to {_mask(chat_id)} retried as template "
            f"{fallback.key!r}"
        )
    except Exception as e:
        page_critical(
            f"Template retry for window-closed send to {_mask(chat_id)} "
            f"failed: {e} (wamid {wa_message_id})"
        )
