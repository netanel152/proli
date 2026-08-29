from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from app.schemas.whatsapp import WebhookPayload
from app.core.logger import logger, new_trace_id
from app.core.config import settings
from app.core.constants import APIStatus
from app.core.messages import Messages
from app.core.redis_client import get_redis_client, get_arq_pool
from app.services.security_service import SecurityService

router = APIRouter()


@router.post("/webhook")
async def webhook_endpoint(payload: WebhookPayload, token: str = Query(default=None)):
    """
    Legacy inbound webhook — the retired vendor's payload envelope.
    """
    # PRO-174: mint and bind the correlation id before anything is logged —
    # above the token check, so a rejected probe's "Security Alert" line is
    # correlatable too. `contextualize` is contextvars-backed, so the binding
    # belongs to this request's task and cannot bleed into a concurrently
    # handled chat. Deliberately random and derived from nothing: see
    # `new_trace_id` for why a digest of the chat id would be a way to undo
    # `mask_pii` rather than a correlation id.
    trace_id = new_trace_id()
    with logger.contextualize(trace_id=trace_id):
        # Webhook Token Verification
        if settings.WEBHOOK_TOKEN:
            if token != settings.WEBHOOK_TOKEN.get_secret_value():
                logger.warning(
                    "Security Alert: Webhook request with invalid or missing token"
                )
                return JSONResponse(status_code=403, content={"status": "forbidden"})

        return await _handle_webhook(payload, trace_id)


async def _handle_webhook(payload: WebhookPayload, trace_id: str):
    """The legacy route's body, split out so the whole of it runs inside the
    ``trace_id`` binding above — including the `except` handler, which is
    exactly the line an operator most needs to correlate. The id is passed
    down as a plain argument rather than read back out of loguru's context:
    the enqueue below has to forward it, and loguru exposes no public reader
    for a contextualized value."""
    try:
        # Idempotency Check (Redis)
        if payload.idMessage:
            redis = await get_redis_client()
            cache_key = f"webhook:{payload.idMessage}"

            # Atomic set-if-not-exists with 24h TTL
            is_new = await redis.set(cache_key, "processed", ex=86400, nx=True)

            if not is_new:
                logger.info(
                    f"Idempotency: Skipping duplicate message {payload.idMessage}"
                )
                return {"status": APIStatus.PROCESSING, "detail": "duplicate"}

        # Basic Filters
        if payload.typeWebhook == "incomingMessageReceived":
            sender_data = payload.senderData
            msg_data = payload.messageData

            if not sender_data or not msg_data:
                return {"status": APIStatus.IGNORED_NO_DATA}

            chat_id = sender_data.chatId

            # Group Filter
            if chat_id.endswith("@g.us"):
                return {"status": APIStatus.IGNORED_GROUP}

            # Rate Limit Check — coarse DDoS shield only. Kept generous (50/60s) so it
            # never hard-blocks a pro/admin; the precise per-customer limit with
            # pro/admin exemptions is enforced in the worker (see workflow_service).
            if not await SecurityService.check_rate_limit(
                chat_id, limit=50, window_seconds=60
            ):
                logger.warning(f"⛔ Webhook DDoS shield tripped for ...{chat_id[-8:]}")
                return {"status": APIStatus.IGNORED_RATE_LIMIT}

            # Extract User Text
            user_text = ""
            media_url = None

            if msg_data.typeMessage == "textMessage":
                user_text = msg_data.textMessageData.textMessage
            elif msg_data.typeMessage == "extendedTextMessage":
                user_text = msg_data.extendedTextMessageData.text or ""
            elif msg_data.typeMessage == "locationMessage":
                # Handle location pin messages
                if msg_data.locationMessageData:
                    loc = msg_data.locationMessageData
                    parts = []
                    if loc.nameLocation:
                        parts.append(loc.nameLocation)
                    if loc.address:
                        parts.append(loc.address)
                    if loc.latitude and loc.longitude:
                        parts.append(f"({loc.latitude}, {loc.longitude})")
                    user_text = (
                        " ".join(parts)
                        if parts
                        else Messages.System.LOCATION_AS_TEXT.format(
                            latitude=loc.latitude, longitude=loc.longitude
                        )
                    )
                    logger.info(f"Location message from {chat_id}: {user_text}")
            elif msg_data.typeMessage in [
                "imageMessage",
                "audioMessage",
                "videoMessage",
            ]:
                if msg_data.fileMessageData:
                    media_url = msg_data.fileMessageData.downloadUrl
                    user_text = msg_data.fileMessageData.caption or ""

            # Process Standard Message via ARQ Worker
            arq_pool = await get_arq_pool()
            await arq_pool.enqueue_job(
                "process_message_task",
                chat_id,
                user_text,
                media_url,
                message_id=payload.idMessage,
                # Sent, not recomputed worker-side: `idMessage` is optional on
                # this envelope, and a recomputed id would silently diverge
                # from the API's every time it is absent (PRO-174).
                trace_id=trace_id,
            )
            return {"status": APIStatus.PROCESSING}

        elif payload.typeWebhook == "incomingBlock":
            pass

        return {"status": APIStatus.IGNORED_TYPE}

    except Exception as e:
        logger.error(f"Webhook Error: {e}")
        return {"status": APIStatus.ERROR}
