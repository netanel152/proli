"""PRO-86 — the provider that never sends.

Subsumes the old ``WHATSAPP_DRY_RUN`` transport hack (PRO-79/PRO-83). Where that
swapped an ``httpx.MockTransport`` underneath a real Green client, this is simply
a provider that logs instead of transmitting — the same guarantee with no
vendor client in the process at all.

This is what the offline test suite and the PRO-83 E2E harness run against.
"""

from typing import Any

from app.core.logger import logger
from app.providers.whatsapp.base import NormalizedMessage, WhatsAppProvider


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

    async def send_interactive(
        self,
        chat_id: str,
        body: str,
        options: list[str],
    ) -> dict[str, Any] | None:
        return self._record("interactive", chat_id, text=body, options=list(options))

    async def send_typing(self, chat_id: str) -> None:
        self._record("typing", chat_id)

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
