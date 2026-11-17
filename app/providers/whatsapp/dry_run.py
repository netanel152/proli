"""PRO-86 — the provider that never sends.

Subsumes the old ``WHATSAPP_DRY_RUN`` transport hack (PRO-79/PRO-83). Where that
swapped an ``httpx.MockTransport`` underneath a real Green client, this is simply
a provider that logs instead of transmitting — the same guarantee with no
vendor client in the process at all.

This is what the offline test suite and the PRO-83 E2E harness run against.
"""

from typing import Any, Sequence

from app.core.logger import logger
from app.providers.whatsapp import template_registry
from app.providers.whatsapp.base import NormalizedMessage, WhatsAppProvider


def _pairs(buttons: Sequence[Any]) -> list[tuple[str, str]]:
    return [(b.label, b.payload) for b in buttons]


class DryRunProvider(WhatsAppProvider):
    name = "dryrun"

    # Nothing here can reach a handset, so the facade does not gate it on the
    # circuit breaker — see WhatsAppProvider.transmits.
    transmits = False

    def __init__(self) -> None:
        #: Every call, in order — the E2E harness asserts against this.
        self.sent: list[dict[str, Any]] = []

    def _record(self, kind: str, chat_id: str, **detail: Any) -> dict[str, Any]:
        entry = {"kind": kind, "chat_id": chat_id, **detail}
        self.sent.append(entry)
        # debug, not info: previews are rendered message bodies and routinely
        # contain a customer's or pro's phone number (PRO-80).
        preview = str(detail.get("text") or detail.get("caption") or "")[:100]
        logger.debug(
            f"🧪 [DRY-RUN] {kind} to ...{chat_id[-4:]}: {preview!r} — not transmitted"
        )
        return {"idMessage": f"dry-run-{len(self.sent)}"}

    async def send_text(self, chat_id: str, text: str) -> dict[str, Any] | None:
        return self._record("text", chat_id, text=text)

    async def send_file(
        self,
        chat_id: str,
        url: str,
        caption: str = "",
        file_name: str = "media.jpg",
    ) -> dict[str, Any] | None:
        return self._record(
            "file", chat_id, url=url, caption=caption, file_name=file_name
        )

    async def send_template(
        self,
        chat_id: str,
        template_name: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        return self._record(
            "template", chat_id, template_name=template_name, params=params or {}
        )

    async def send_business_initiated(
        self,
        chat_id: str,
        text: str,
        template_key: str,
        params: dict[str, Any] | None = None,
        *,
        buttons: Sequence[Any] | None = None,
        reply_ref: str | None = None,
    ) -> dict[str, Any] | None:
        """The text, as every provider with no window sends it, plus what the
        template would have carried.

        Still a ``text`` entry, so nothing that reads the log changes. PRO-230
        adds ``template_key`` and ``template_buttons`` — the quick replies that
        template *would* carry, whatever its status, so the harness can assert
        them while every template is still a draft. Named for the template and
        not the send on purpose: on an open window Cloud sends plain text, and
        the recipient of this very send sees no buttons at all.

        PRO-104: a send that offers ``buttons`` also records ``buttons``, the
        ``(label, id)`` pairs an open window would show, with ``reply_ref`` in
        each id, as :meth:`send_buttons` does.
        """
        spec = template_registry.TEMPLATES.get(template_key)
        template_buttons = [(b.label, b.payload) for b in spec.buttons] if spec else []
        extra = {}
        if buttons:
            extra["buttons"] = _pairs(
                template_registry.buttons_with_reply_ref(buttons, reply_ref)
            )
        return self._record(
            "text",
            chat_id,
            text=text,
            template_key=template_key,
            template_buttons=template_buttons,
            **extra,
        )

    async def send_buttons(
        self,
        chat_id: str,
        text: str,
        buttons: Sequence[Any],
    ) -> dict[str, Any] | None:
        """A ``text`` entry, as ``send_text`` records, plus the ``(label, id)``
        pairs of its reply buttons (PRO-105)."""
        return self._record("text", chat_id, text=text, buttons=_pairs(buttons))

    async def send_interactive(
        self,
        chat_id: str,
        body: str,
        options: list[str],
    ) -> dict[str, Any] | None:
        return self._record("interactive", chat_id, text=body, options=list(options))

    async def indicate_typing(self, chat_id: str, message_id: str | None) -> None:
        # Recorded even without a wamid: the harness asserts that the indicator
        # was *asked for* before the reply, whatever the transport would do.
        self._record("typing", chat_id, message_id=message_id)

    async def get_state(self) -> str | None:
        """Always ``authorized``.

        A provider that cannot send also cannot be deauthorized. Reporting
        healthy keeps the PRO-20 watchdog and ``/health`` meaningful offline
        instead of paging about an account that does not exist.
        """
        return "authorized"

    def parse_webhook(self, payload: Any) -> NormalizedMessage | None:
        """Accept an already-normalized payload.

        Used by tests and the offline harness, which construct the internal
        shape directly rather than imitating a vendor envelope.
        """
        if payload is None:
            return None
        if isinstance(payload, NormalizedMessage):
            return payload
        if isinstance(payload, dict):
            chat_id = payload.get("chat_id")
            if not chat_id:
                return None
            return NormalizedMessage(
                chat_id=str(chat_id),
                text=str(payload.get("text") or ""),
                media_url=payload.get("media_url"),
                sender_name=payload.get("sender_name"),
                message_id=payload.get("message_id"),
            )
        return None
