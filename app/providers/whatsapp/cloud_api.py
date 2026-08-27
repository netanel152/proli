"""PRO-89 — Meta Cloud API provider.

The transmitting transport underneath the PRO-86 facade: sends via the Graph
API ``/{phone_number_id}/messages`` endpoint, normalizes Meta webhook payloads
into the internal :class:`NormalizedMessage` model, and enforces the 24-hour
customer-service window (see ``window.py``) that Meta enforces anyway — the
difference is that we fail *structured and loud* instead of collecting silent
131047 rejections.

Layering rules this file honors:

* It never checks the circuit breaker or kill switch — those are facade
  concerns and already ran by the time a method here is called.
* It is constructed only by ``build_provider()`` in this package; the CI guard
  fails the build on construction anywhere else.
* Credentials are read with ``.get_secret_value()`` at the point of use and
  never flow into a log line (PRO-94).

The webhook *parsing* functions are module-level on purpose: the
``/webhook/meta`` route calls them without holding a provider instance, which
keeps the PRO-86 "nothing outside the package constructs a provider" rule
intact for the API process even when it runs with ``WHATSAPP_PROVIDER=dryrun``
(e.g. staging receiving real inbound while muted).
"""

import mimetypes
from typing import Any
from urllib.parse import urlparse

from app.core.config import settings
from app.core.messages import Messages
from app.core.constants import WorkerConstants
from app.core.http_client import get_http_client
from app.core.logger import logger, page_critical
from app.core.phone import mask_chat_id as _mask
from app.core.phone import strip_suffix, to_chat_id
from app.providers.whatsapp import template_registry
from app.providers.whatsapp.base import (
    NormalizedMessage,
    ServiceWindowClosedError,
    TemplateNotRegisteredError,
    WhatsAppProvider,
)
from app.providers.whatsapp.window import is_service_window_open

_GRAPH_ROOT = "https://graph.facebook.com"

#: Marker scheme for inbound media. Meta webhooks carry a media *id*, not a
#: URL — the real URL requires an authorized two-step fetch and expires within
#: minutes, so nothing downstream may ever see it. The worker resolves this
#: marker into a permanent Cloudinary URL before the message reaches the
#: dispatcher (see ``app/core/arq_worker.py``).
META_MEDIA_SCHEME = "meta-media://"

_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
_VIDEO_EXTENSIONS = (".mp4", ".3gp")
_AUDIO_EXTENSIONS = (".aac", ".amr", ".m4a", ".mp3", ".ogg", ".opus")

# Meta's hard limits on interactive messages. Above them the message is
# rejected, so the mapping degrades: ≤3 options → reply buttons, ≤10 → a list,
# >10 → the numeric text menu every flow already speaks.
_MAX_BUTTONS = 3
_MAX_LIST_ROWS = 10
_BUTTON_TITLE_LIMIT = 20
_LIST_ROW_TITLE_LIMIT = 24
# Cloud API hard caps. The legacy vendor's ceilings were far higher, so bodies that
# have always worked (the daily agenda, מצא results) can newly overflow: text
# is chunked across sends, an oversized interactive body degrades to text.
_TEXT_BODY_LIMIT = 4096
_INTERACTIVE_BODY_LIMIT = 1024


def _split_text(text: str, limit: int = _TEXT_BODY_LIMIT) -> list[str]:
    """Split a body into ≤limit chunks, preferring newline boundaries.

    Never truncates: a 4097-char agenda arrives as two messages rather than
    losing its last job line to a Graph 400.
    """
    if len(text) <= limit:
        return [text]
    chunks = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n", 1, limit)
        if cut <= 0:
            cut = limit
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    if remaining:
        chunks.append(remaining)
    return chunks


# ``ServiceWindowClosedError`` and ``TemplateNotRegisteredError`` are imported
# from ``base`` above rather than defined here: PRO-159 moved them onto the
# provider contract so the facade can catch them without importing a transport.
# The imports keep every historical
# ``from app.providers.whatsapp.cloud_api import ServiceWindowClosedError``
# working, and both are still raised below by this module.


class CloudAPIProvider(WhatsAppProvider):
    name = "cloud"

    # Reaches real handsets, so the facade holds it to the fail-closed breaker
    # contract (PRO-82).
    transmits = True

    # ------------------------------------------------------------------
    # Graph API plumbing
    # ------------------------------------------------------------------

    def _messages_url(self) -> str:
        return (
            f"{_GRAPH_ROOT}/{settings.META_GRAPH_API_VERSION}"
            f"/{settings.META_PHONE_NUMBER_ID}/messages"
        )

    def _auth_headers(self) -> dict[str, str]:
        token = settings.META_ACCESS_TOKEN
        if token is None:
            raise RuntimeError(
                "META_ACCESS_TOKEN is not configured — Settings validation "
                "should have refused this at boot (require_cloud_provider_config)."
            )
        # Concatenation, not an f-string: the PRO-94 convention test rejects
        # any secret interpolated on an f-string line, log or not.
        return {"Authorization": "Bearer " + token.get_secret_value()}

    async def _post_message(
        self, chat_id: str, payload: dict[str, Any], kind: str
    ) -> dict[str, Any]:
        """POST one message payload; return the legacy-shaped result.

        Raises on any non-2xx — a transmitting provider must never convert a
        vendor rejection into a quiet ``None`` (that was the yellowCard
        lesson's little sibling). The Graph error code/title are logged for
        the operator; the response body never contains message content.
        """
        body = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": strip_suffix(chat_id),
            **payload,
        }
        client = await get_http_client()
        response = await client.post(
            self._messages_url(), json=body, headers=self._auth_headers()
        )
        if response.status_code >= 400:
            error = {}
            try:
                error = response.json().get("error", {})
            except Exception:
                pass
            logger.error(
                f"Graph API rejected {kind} to {_mask(chat_id)}: "
                f"HTTP {response.status_code}, "
                f"code={error.get('code')}, title={error.get('message')!r}"
            )
            response.raise_for_status()

        data = response.json()
        wa_message_id = (data.get("messages") or [{}])[0].get("id")
        await self._record_outbound(wa_message_id, chat_id, kind)
        # Legacy shape: every historical call site reads result["idMessage"].
        return {"idMessage": wa_message_id, "meta": data}

    async def _record_outbound(
        self, wa_message_id: str | None, chat_id: str, kind: str
    ) -> None:
        """Bookkeeping for the delivery-status webhook. Never blocks a send."""
        if not wa_message_id:
            return
        try:
            from app.providers.whatsapp import delivery

            await delivery.record_outbound(wa_message_id, chat_id, kind)
        except Exception as e:
            logger.warning(f"Could not record outbound {kind} for status tracking: {e}")

    async def _window_fallback(
        self, chat_id: str, kind: str
    ) -> template_registry.TemplateSpec | None:
        """``None`` when the free-form send may proceed, a spec when it must be
        re-routed as a template, and a page + raise when neither is possible.

        The structured error is the AC: a closed window with no approved
        fallback must page the operator (``page_critical`` → Sentry) and
        surface to the caller — never a silent drop.

        A returned fallback is sent with **no params**, so only a
        parameterless re-engagement template may ever be registered as one —
        body variables cannot be reconstructed from a rendered Hebrew string.
        Enforce that when PRO-87 arms ``freeform_fallback``.
        """
        if await is_service_window_open(chat_id):
            return None
        fallback = template_registry.freeform_fallback(kind)
        if fallback is not None:
            logger.info(
                f"Service window closed for {_mask(chat_id)} — re-routing "
                f"{kind} via template {fallback.key!r}"
            )
            return fallback
        page = await self._first_window_page_today(chat_id)
        # With every registry entry still DRAFT, a closed window is the
        # *expected* state for business-initiated sends the moment the cloud
        # provider goes live — so only the first block per recipient per day
        # pages (CRITICAL → Sentry → email); the rest stay at ERROR. A page
        # that fires dozens of times a morning stops being a page.
        log = page_critical if page else logger.error
        log(
            f"WhatsApp {kind} to {_mask(chat_id)} blocked: 24h service window "
            "closed and no approved fallback template is registered "
            "(PRO-89). The message was NOT sent. Approve a re-engagement "
            "template (PRO-87/PRO-88) or wait for the recipient to write in."
        )
        raise ServiceWindowClosedError(
            f"service window closed for {_mask(chat_id)}; no fallback template"
        )

    async def _first_window_page_today(self, chat_id: str) -> bool:
        """True once per chat_id per 24h. Fail-loud: if Redis is unreadable we
        cannot dedupe, and paging twice beats not paging."""
        try:
            from app.core.redis_client import get_redis_client

            redis = await get_redis_client()
            return bool(
                await redis.set(f"wa:window:page:{chat_id}", "1", ex=86400, nx=True)
            )
        except Exception:
            return True

    # ------------------------------------------------------------------
    # Outbound
    # ------------------------------------------------------------------

    async def send_text(self, chat_id: str, text: str) -> dict[str, Any] | None:
        fallback = await self._window_fallback(chat_id, "text")
        if fallback is not None:
            return await self.send_template(chat_id, fallback.key)
        # This sentinel must never survive the loop. ``_split_text`` is total —
        # it returns at least one chunk for any input, ``[""]`` included — and
        # PRO-159 made that load-bearing: the facade and its callers now read a
        # ``None`` return as "blocked, not sent". Give ``_split_text`` an empty
        # -input shortcut and a successful send starts reporting itself failed.
        result = None
        for chunk in _split_text(text):
            result = await self._post_message(
                chat_id,
                {"type": "text", "text": {"body": chunk, "preview_url": True}},
                "text",
            )
        return result

    async def send_file(
        self,
        chat_id: str,
        url: str,
        caption: str = "",
        file_name: str = "media.jpg",
    ) -> dict[str, Any] | None:
        fallback = await self._window_fallback(chat_id, "file")
        if fallback is not None:
            return await self.send_template(chat_id, fallback.key)

        path = urlparse(url).path.lower()
        if path.endswith(_IMAGE_EXTENSIONS):
            payload = {"type": "image", "image": {"link": url, "caption": caption}}
        elif path.endswith(_VIDEO_EXTENSIONS):
            payload = {"type": "video", "video": {"link": url, "caption": caption}}
        elif path.endswith(_AUDIO_EXTENSIONS):
            # Audio messages cannot carry a caption; it follows as text below.
            payload = {"type": "audio", "audio": {"link": url}}
        else:
            payload = {
                "type": "document",
                "document": {"link": url, "caption": caption, "filename": file_name},
            }

        result = await self._post_message(chat_id, payload, "file")
        if payload["type"] == "audio" and caption:
            try:
                await self.send_text(chat_id, caption)
            except Exception as e:
                # The audio itself was delivered — losing the caption must not
                # convert a successful send into a raised one.
                logger.warning(
                    f"Audio caption follow-up to {_mask(chat_id)} failed: {e}"
                )
        return result

    async def send_template(
        self,
        chat_id: str,
        template_name: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        spec = template_registry.resolve(template_name)
        if spec is None:
            page_critical(
                f"WhatsApp template send to {_mask(chat_id)} blocked: "
                f"{template_name!r} is not an approved template in the "
                "registry (PRO-88/PRO-89). The message was NOT sent."
            )
            raise TemplateNotRegisteredError(
                f"template {template_name!r} is not registered as approved"
            )
        template: dict[str, Any] = {
            "name": spec.meta_name,
            "language": {"code": spec.language},
        }
        if params:
            # Positional body variables, in the dict's insertion order — the
            # caller owns matching the template's {{1}}..{{n}} ordering.
            template["components"] = [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": str(value)}
                        for value in params.values()
                    ],
                }
            ]
        return await self._post_message(
            chat_id, {"type": "template", "template": template}, "template"
        )

    async def send_interactive(
        self,
        chat_id: str,
        body: str,
        options: list[str],
    ) -> dict[str, Any] | None:
        """Buttons for ≤3 options, a list for ≤10, numeric text beyond that.

        Reply IDs are ``"1"``, ``"2"``, … — exactly the strings a user types in
        the numeric text menus — so to the FSM a button tap and a typed digit
        are the same inbound and no transition changes (the PRO-89 AC).
        Remember CLAUDE.md: no *flow* may call this until PRO-88/89 templates
        land; this is the transport half only.
        """
        options = list(options)
        title_limit = (
            _BUTTON_TITLE_LIMIT
            if len(options) <= _MAX_BUTTONS
            else _LIST_ROW_TITLE_LIMIT
        )
        titles = [opt[:title_limit] for opt in options]
        if (
            not options
            or len(options) > _MAX_LIST_ROWS
            or len(body) > _INTERACTIVE_BODY_LIMIT
            or len(set(titles)) != len(titles)
        ):
            # Meta rejects the whole message for any of these: too many rows,
            # an oversized body, or duplicate (truncation-collided) titles.
            # Fall back to the numeric text menu every flow already renders —
            # never truncate a menu silently, never 400 a menu send.
            menu = "\n".join(f"{i + 1}. {opt}" for i, opt in enumerate(options))
            return await self.send_text(chat_id, f"{body}\n{menu}" if menu else body)

        fallback = await self._window_fallback(chat_id, "interactive")
        if fallback is not None:
            return await self.send_template(chat_id, fallback.key)

        if len(options) <= _MAX_BUTTONS:
            interactive = {
                "type": "button",
                "body": {"text": body},
                "action": {
                    "buttons": [
                        {
                            "type": "reply",
                            "reply": {
                                "id": str(i + 1),
                                "title": opt[:_BUTTON_TITLE_LIMIT],
                            },
                        }
                        for i, opt in enumerate(options)
                    ]
                },
            }
        else:
            interactive = {
                "type": "list",
                "body": {"text": body},
                "action": {
                    "button": Messages.System.LIST_PICKER_BUTTON,
                    "sections": [
                        {
                            "rows": [
                                {"id": str(i + 1), "title": opt[:_LIST_ROW_TITLE_LIMIT]}
                                for i, opt in enumerate(options)
                            ]
                        }
                    ],
                },
            }
        return await self._post_message(
            chat_id, {"type": "interactive", "interactive": interactive}, "interactive"
        )

    # ------------------------------------------------------------------
    # State + inbound
    # ------------------------------------------------------------------

    async def get_state(self) -> str | None:
        """Authorization state read off the phone-number node.

        ``CONNECTED`` maps to ``"authorized"`` so the PRO-20 watchdog, the
        PRO-71/82 breaker and ``record_account_state`` work unchanged for this
        provider. Anything else (``FLAGGED``, ``RESTRICTED``, ``BANNED``, …)
        is passed through lowercased — the watchdog treats any non-authorized
        answer as unhealthy. ``None`` means the probe itself failed.
        """
        if settings.META_ACCESS_TOKEN is None or not settings.META_PHONE_NUMBER_ID:
            return None
        try:
            client = await get_http_client()
            response = await client.get(
                f"{_GRAPH_ROOT}/{settings.META_GRAPH_API_VERSION}"
                f"/{settings.META_PHONE_NUMBER_ID}",
                params={"fields": "status,quality_rating"},
                headers=self._auth_headers(),
            )
            response.raise_for_status()
            data = response.json()
            status = str(data.get("status") or "").strip()
            quality = data.get("quality_rating")
            if quality:
                # Breadcrumb for PRO-90 (quality/tier monitoring). Debug, not
                # info: the PRO-20 watchdog probes every 2 minutes and ~720
                # identical INFO lines/day is noise, not signal.
                logger.debug(f"Meta phone-number quality_rating={quality}")
            if not status:
                return None
            return "authorized" if status.upper() == "CONNECTED" else status.lower()
        except Exception as e:
            logger.warning(f"Meta state probe failed: {e}")
            return None

    def parse_webhook(self, payload: Any) -> NormalizedMessage | None:
        return parse_meta_webhook(payload)


# ----------------------------------------------------------------------
# Webhook parsing — module-level so the route can use it without holding a
# provider instance (see module docstring).
# ----------------------------------------------------------------------


def iter_meta_messages(payload: Any) -> list[tuple[dict, dict]]:
    """Every ``(value, message)`` pair in a Meta webhook envelope.

    Meta batches: one POST can carry several entries, each with several
    changes, each with several messages. The route enqueues one worker task
    per message, mirroring the one-payload-one-task shape of the legacy route.
    """
    pairs: list[tuple[dict, dict]] = []
    if not isinstance(payload, dict):
        return pairs
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            if change.get("field") != "messages":
                continue
            value = change.get("value") or {}
            for message in value.get("messages") or []:
                if isinstance(message, dict):
                    pairs.append((value, message))
    return pairs


def normalize_meta_message(
    message: dict, value: dict | None = None
) -> NormalizedMessage | None:
    """One Meta message object → the internal model. ``None`` if unprocessable.

    Interactive replies normalize to their reply *id* (``"1"``, ``"2"``, …),
    not their title — the id is what ``send_interactive`` assigned, so the FSM
    receives exactly the string a typed numeric reply would have produced.
    """
    sender = message.get("from")
    if not sender:
        return None
    chat_id = to_chat_id(sender)

    sender_name = None
    for contact in (value or {}).get("contacts") or []:
        if contact.get("wa_id") == sender:
            sender_name = (contact.get("profile") or {}).get("name")
            break

    text = ""
    media_url = None
    message_type = message.get("type")

    if message_type == "text":
        text = (message.get("text") or {}).get("body") or ""
    elif message_type == "interactive":
        interactive = message.get("interactive") or {}
        reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
        text = reply.get("id") or ""
    elif message_type == "button":
        # Template quick-reply. The payload is what the template author set —
        # our templates use the numeric ids for the same FSM-parity reason.
        button = message.get("button") or {}
        text = button.get("payload") or button.get("text") or ""
    elif message_type == "location":
        location = message.get("location") or {}
        parts = []
        if location.get("name"):
            parts.append(str(location["name"]))
        if location.get("address"):
            parts.append(str(location["address"]))
        if (
            location.get("latitude") is not None
            and location.get("longitude") is not None
        ):
            parts.append(f"({location['latitude']}, {location['longitude']})")
        text = (
            " ".join(parts)
            if parts
            else Messages.System.LOCATION_AS_TEXT.format(
                latitude=location.get("latitude"), longitude=location.get("longitude")
            )
        )
    elif message_type in ("image", "audio", "video", "document"):
        media = message.get(message_type) or {}
        media_id = media.get("id")
        if media_id:
            media_url = f"{META_MEDIA_SCHEME}{media_id}"
        text = media.get("caption") or ""
    else:
        # reactions, stickers, system events, unsupported — nothing to process
        return None

    return NormalizedMessage(
        chat_id=chat_id,
        text=text,
        media_url=media_url,
        sender_name=sender_name,
        message_id=message.get("id"),
    )


def parse_meta_webhook(payload: Any) -> NormalizedMessage | None:
    """ABC-contract parse: the first processable message in the envelope."""
    for value, message in iter_meta_messages(payload):
        normalized = normalize_meta_message(message, value)
        if normalized is not None:
            return normalized
    return None


def parse_status_events(payload: Any) -> list[dict[str, Any]]:
    """Every delivery-status event (``sent``/``delivered``/``read``/``failed``)
    in the envelope, flattened for ``delivery.apply_status_event``."""
    events: list[dict[str, Any]] = []
    if not isinstance(payload, dict):
        return events
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            if change.get("field") != "messages":
                continue
            value = change.get("value") or {}
            for status in value.get("statuses") or []:
                if not isinstance(status, dict):
                    continue
                errors = status.get("errors") or []
                first_error = errors[0] if errors else {}
                events.append(
                    {
                        "wa_message_id": status.get("id"),
                        "chat_id": to_chat_id(status.get("recipient_id") or ""),
                        "status": status.get("status"),
                        "timestamp": status.get("timestamp"),
                        "error_code": first_error.get("code"),
                        "error_title": first_error.get("title"),
                    }
                )
    return events


async def fetch_meta_media(media_id: str) -> tuple[bytes | None, str | None]:
    """Resolve a Meta media id to ``(bytes, mime_type)``.

    Two authorized hops: ``GET /{media_id}`` returns a short-lived CDN URL,
    which must itself be fetched with the Bearer token. Called by the worker
    (``arq_worker``) to re-host inbound media on Cloudinary — the CDN URL
    expires in minutes, so it must never be stored or forwarded.
    """
    token = settings.META_ACCESS_TOKEN
    if token is None:
        logger.warning("Cannot fetch Meta media: META_ACCESS_TOKEN not configured.")
        return None, None
    headers = {"Authorization": "Bearer " + token.get_secret_value()}
    try:
        client = await get_http_client()
        lookup = await client.get(
            f"{_GRAPH_ROOT}/{settings.META_GRAPH_API_VERSION}/{media_id}",
            headers=headers,
        )
        lookup.raise_for_status()
        info = lookup.json()
        url = info.get("url")
        mime = info.get("mime_type")
        if not url:
            logger.warning(f"Meta media lookup for {media_id} returned no URL.")
            return None, None
        if not str(url).startswith("https://"):
            # The Bearer token is attached to whatever the lookup returned —
            # never hand it to a non-TLS (or otherwise bizarre) destination.
            logger.warning(f"Meta media lookup for {media_id} returned non-HTTPS URL.")
            return None, None
        try:
            # Meta ships numeric fields as strings in these envelopes
            # (statuses[].timestamp already is one) — a str here must not
            # TypeError into the catch-all and silently drop ALL media.
            size = int(info.get("file_size") or 0)
        except (TypeError, ValueError):
            size = 0  # unparseable → let the download proceed; the post-
            # download check below is what makes the cap real anyway
        if size > WorkerConstants.MAX_INBOUND_MEDIA_BYTES:
            # Meta allows up to 100MB; buffering that through the worker and
            # Cloudinary serves nobody. Checked before the second hop so the
            # bytes are never downloaded at all.
            logger.warning(
                f"Meta media {media_id} is {size} bytes — over the "
                f"{WorkerConstants.MAX_INBOUND_MEDIA_BYTES} cap; not fetched."
            )
            return None, None
        download = await client.get(url, headers=headers)
        download.raise_for_status()
        if len(download.content) > WorkerConstants.MAX_INBOUND_MEDIA_BYTES:
            # file_size can be absent or lie; the cap is enforced on what
            # actually arrived, not on what the lookup claimed.
            logger.warning(
                f"Meta media {media_id} downloaded {len(download.content)} "
                f"bytes — over the {WorkerConstants.MAX_INBOUND_MEDIA_BYTES} "
                "cap; discarded."
            )
            return None, None
        return download.content, mime or download.headers.get(
            "Content-Type", mimetypes.guess_type(url)[0]
        )
    except Exception as e:
        logger.error(f"Failed to fetch Meta media {media_id}: {e}")
        return None, None
