"""PRO-86 — the WhatsApp provider contract.

Every outbound message in Proli leaves through exactly one place: the facade in
``app/providers/whatsapp/facade.py``. A provider is the transport underneath it
and knows nothing about circuit breakers, kill switches or dry-run — those are
facade concerns, so the guarantees hold identically for any provider.

Green API is gone (PRO-85 operator decision, instance 7105567180 deleted). The
only implementations are :class:`~app.providers.whatsapp.dry_run.DryRunProvider`
and the :class:`~app.providers.whatsapp.cloud_api.CloudAPIProvider` stub that
PRO-89 fills in.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class NormalizedMessage:
    """Provider-agnostic inbound message.

    The internal shape every provider's ``parse_webhook`` must produce, so the
    worker never sees a vendor payload. Inbound wiring (``/webhook`` calling
    through to this) lands in PRO-89 alongside the Cloud API payload shape —
    designing a "neutral" inbound model against a single vendor was judged
    premature, see the PRO-86 plan comment.
    """

    chat_id: str
    text: str = ""
    media_url: str | None = None
    sender_name: str | None = None
    message_id: str | None = None


class WhatsAppProvider(ABC):
    """Transport for WhatsApp traffic. Implementations must not send anything
    the facade has not authorised — they are never called directly."""

    #: Short identifier used in logs and by ``WHATSAPP_PROVIDER``.
    name: str = "abstract"

    #: Whether this provider can physically reach a real recipient.
    #:
    #: The facade uses it to decide whether the circuit breaker applies. A
    #: provider that cannot transmit has nothing to protect a phone number
    #: from, so gating it would only make the offline test suite assert against
    #: a permanently-open breaker. Anything that can reach a real handset must
    #: set this True and accept fail-closed behaviour (PRO-82).
    transmits: bool = True

    @abstractmethod
    async def send_text(self, chat_id: str, text: str) -> dict[str, Any] | None:
        """Deliver a plain text message."""

    @abstractmethod
    async def send_file(
        self,
        chat_id: str,
        url: str,
        caption: str = "",
        file_name: str = "media.jpg",
    ) -> dict[str, Any] | None:
        """Deliver a media file addressed by URL."""

    @abstractmethod
    async def send_template(
        self,
        chat_id: str,
        template_name: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Deliver a pre-approved template (business-initiated).

        The template catalog itself is PRO-88; this is the transport half.
        """

    @abstractmethod
    async def send_interactive(
        self,
        chat_id: str,
        body: str,
        options: list[str],
    ) -> dict[str, Any] | None:
        """Deliver an interactive (button/list) message.

        Green API could not do this at all, which is why the project convention
        was numeric text menus. Cloud API can, but nothing may use it until the
        PRO-88 template catalog and PRO-89 transport exist — see CLAUDE.md.
        """

    async def send_typing(self, chat_id: str) -> None:
        """Optional presence indicator. Best-effort by contract — the facade
        swallows failures, so the default no-op is a valid implementation."""
        return None

    @abstractmethod
    async def get_state(self) -> str | None:
        """Authorization state of the underlying account.

        ``"authorized"`` means healthy. ``None`` means the probe itself failed
        and the caller should treat the account as not known-good — the facade
        does exactly that.
        """

    @abstractmethod
    def parse_webhook(self, payload: Any) -> NormalizedMessage | None:
        """Convert a provider webhook payload into a :class:`NormalizedMessage`.

        Returns ``None`` for payloads that carry no processable message (status
        callbacks, echoes, group traffic).
        """

    async def close(self) -> None:
        """Release transport resources. Safe to call more than once."""
        return None
