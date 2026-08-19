"""PRO-89 — the Meta Cloud API webhook.

Mounted alongside the legacy ``/webhook`` route at its own path. Deliberately
live even when ``WHATSAPP_PROVIDER=dryrun``: Meta's subscription handshake and
inbound delivery can then be verified during PRO-87 onboarding while outbound
stays muted — receiving was never the dangerous direction.

Authentication is the ``X-Hub-Signature-256`` HMAC over the **raw** body,
which is why this route reads ``request.body()`` instead of taking a pydantic
model: the signature is computed over bytes, and any re-serialization breaks
it. In prod-like environments an unset ``META_APP_SECRET`` rejects every POST
(fail closed) — the Settings validator already refuses that combination at
boot for the cloud provider, and this guard covers the dryrun-provider case
where the route is exposed but the secret was never configured.

Like the legacy route, this endpoint does no processing: verify, dedupe,
open the service window, enqueue. Meta retries on non-2xx with backoff and
disables webhooks only after sustained failure, so the response policy is:
200 for everything we *decided* about (processed, duplicate, ignored,
malformed body — our problem to log, not Meta's to retry), 503 only when an
infrastructure failure kept us from enqueueing — those are the deliveries a
retry genuinely recovers, and the idempotency claim is released first so the
redelivery can land.
"""

import hashlib
import hmac
import json

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app.core.config import settings
from app.core.constants import APIStatus
from app.core.logger import logger
from app.core.phone import to_chat_id
from app.core.redis_client import get_arq_pool, get_redis_client
from app.providers.whatsapp import delivery
from app.providers.whatsapp.cloud_api import (
    iter_meta_messages,
    normalize_meta_message,
    parse_status_events,
)
from app.providers.whatsapp.window import open_service_window
from app.services.security_service import SecurityService

router = APIRouter()


@router.get("/webhook/meta")
async def verify_subscription(
    mode: str | None = Query(default=None, alias="hub.mode"),
    verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    challenge: str | None = Query(default=None, alias="hub.challenge"),
):
    """Meta's one-time subscription handshake: echo the challenge iff the
    operator-chosen verify token matches."""
    token = settings.META_VERIFY_TOKEN
    if (
        token is not None
        and mode == "subscribe"
        and hmac.compare_digest(
            (verify_token or "").encode(), token.get_secret_value().encode()
        )
    ):
        return PlainTextResponse(challenge or "")
    logger.warning("Meta webhook verification attempt with wrong/missing token")
    return JSONResponse(status_code=403, content={"status": "forbidden"})


def _signature_valid(raw_body: bytes, header: str | None) -> bool:
    """Constant-time check of ``X-Hub-Signature-256`` against META_APP_SECRET."""
    if not header:
        return False
    expected = (
        "sha256="
        + hmac.new(
            settings.META_APP_SECRET.get_secret_value().encode(),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
    )
    # Compare bytes, not str: compare_digest raises TypeError on a non-ASCII
    # str, and header values reach us latin-1-decoded straight off the wire —
    # one byte ≥ 0x80 in a forged header must fail auth, not 500 the route.
    return hmac.compare_digest(header.encode("latin-1", "replace"), expected.encode())


@router.post("/webhook/meta")
async def meta_webhook_endpoint(request: Request):
    raw_body = await request.body()

    if settings.META_APP_SECRET is not None:
        if not _signature_valid(raw_body, request.headers.get("X-Hub-Signature-256")):
            logger.warning(
                "Security Alert: Meta webhook POST with invalid or missing "
                "X-Hub-Signature-256"
            )
            return JSONResponse(status_code=403, content={"status": "forbidden"})
    elif settings.is_prod_like:
        # No secret configured in staging/production: nothing can authenticate
        # this call, so nothing may act on it (same posture as PRO-86's
        # WEBHOOK_TOKEN requirement on the legacy route).
        logger.warning(
            "Meta webhook POST rejected: META_APP_SECRET is not configured "
            "in a prod-like environment."
        )
        return JSONResponse(status_code=403, content={"status": "forbidden"})

    try:
        payload = json.loads(raw_body)
    except (ValueError, UnicodeDecodeError):
        logger.warning("Meta webhook POST with non-JSON body — ignored")
        return {"status": APIStatus.IGNORED_TYPE}

    try:
        # Delivery statuses first: they are cheap, carry no user content, and
        # a batch can hold both statuses and messages.
        for event in parse_status_events(payload):
            await delivery.apply_status_event(event)

        enqueued = 0
        arq_pool = None
        for value, message in iter_meta_messages(payload):
            # PRO-89: Meta opens the 24h window on ANY inbound message —
            # stickers and reactions included, which normalize to None below.
            # Open it first, off the raw sender, or the tracker under-reports
            # the window and the outbound side blocks deliverable replies.
            # Running before the idempotency check means a redelivery of an
            # old message re-extends the window slightly past Meta's anchor —
            # over-reporting is the safe direction (131047 is the backstop).
            # (No @g.us group filter here, unlike the legacy route: Cloud API
            # does not deliver group traffic to business numbers.)
            sender = message.get("from")
            if sender:
                await open_service_window(to_chat_id(sender))

            normalized = normalize_meta_message(message, value)
            if normalized is None:
                continue
            chat_id = normalized.chat_id

            # Idempotency — same Redis pattern (and key namespace) as the
            # legacy route: Meta redelivers on slow/failed responses.
            if normalized.message_id:
                redis = await get_redis_client()
                is_new = await redis.set(
                    f"webhook:{normalized.message_id}", "processed", ex=86400, nx=True
                )
                if not is_new:
                    logger.info(
                        f"Idempotency: skipping duplicate Meta message "
                        f"{normalized.message_id}"
                    )
                    continue

            # Coarse DDoS shield only — the precise per-customer limit with
            # pro/admin exemptions runs in the worker (see workflow_service).
            if not await SecurityService.check_rate_limit(
                chat_id, limit=50, window_seconds=60
            ):
                logger.warning(f"⛔ Webhook DDoS shield tripped for ...{chat_id[-8:]}")
                continue

            try:
                if arq_pool is None:
                    arq_pool = await get_arq_pool()
                await arq_pool.enqueue_job(
                    "process_message_task",
                    chat_id,
                    normalized.text,
                    normalized.media_url,
                    message_id=normalized.message_id,
                )
            except Exception:
                # Release the idempotency claim: the outer handler answers 200
                # (Meta must not retry-storm us), so a claimed-but-unqueued
                # wamid would otherwise be a permanently lost message.
                if normalized.message_id:
                    await redis.delete(f"webhook:{normalized.message_id}")
                raise
            enqueued += 1

        if enqueued:
            return {"status": APIStatus.PROCESSING}
        return {"status": APIStatus.IGNORED_TYPE}

    except Exception as e:
        logger.error(f"Meta webhook error: {e}")
        # Non-2xx so Meta retries: the idempotency claim was released in the
        # enqueue guard above, so the redelivery can actually land. This is
        # for transient infra failures (Redis/ARQ down); a deterministic
        # parsing bug would 503 repeatedly and Meta only disables a
        # subscription after *sustained* failure — by which point the same
        # bug with a 200 would have been silently discarding every message.
        return JSONResponse(status_code=503, content={"status": APIStatus.ERROR})
