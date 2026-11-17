"""PRO-86 — the WhatsApp provider contract.

Every outbound message in Proli leaves through exactly one place: the facade in
``app/providers/whatsapp/facade.py``. A provider is the transport underneath it
and knows nothing about circuit breakers, kill switches or dry-run — those are
facade concerns, so the guarantees hold identically for any provider.

The legacy WhatsApp vendor is gone (PRO-85 operator decision, its instance
deleted). The
implementations are :class:`~app.providers.whatsapp.dry_run.DryRunProvider`
(logs, never transmits) and the Meta Cloud API
:class:`~app.providers.whatsapp.cloud_api.CloudAPIProvider` (PRO-89).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Sequence

from app.providers.whatsapp.template_registry import buttons_with_reply_ref


class ServiceWindowClosedError(Exception):
    """A free-form send was attempted outside the recipient's 24h service
    window and no approved fallback template exists. Raised *after* the
    operator page — the raise is for the caller's error handling, the page is
    the guarantee the drop was not silent.

    Part of the provider **contract** rather than of any one provider, so the
    facade above can name it without importing a transport (PRO-159). The
    facade translates it into the same ``None`` return a breaker-blocked send
    already produces: a closed window is a known, already-paged degraded mode,
    and letting it propagate crashed ``process_message_task`` mid-flow — after
    state transitions and DB writes had landed — then had ARQ retry the whole
    handler and re-execute them.
    """


class TemplateNotRegisteredError(Exception):
    """``send_template`` was asked for a template the registry does not list as
    approved. Nothing is transmitted.

    On the contract for the same reason as :class:`ServiceWindowClosedError`,
    and translated by the facade the same way (PRO-159). It is the other door
    into that bug: a closed window sends ``send_text``/``send_file``/
    ``send_interactive`` through ``send_template(fallback.key)``, so an
    unapproved fallback raises from inside an ordinary free-form send and would
    kill ``process_message_task`` mid-flow exactly as the window error did.
    Unreachable today only because ``freeform_fallback()`` returns ``None`` for
    every kind; registering a fallback there that is still ``DRAFT`` triggers it.
    """


class TemplateParamsError(Exception):
    """An approved template was asked for with variables other than the ones its
    spec declares. Nothing is transmitted, since Meta would reject a wrong count
    and a wrong name would put a value in the wrong place. Reported before it is
    raised and translated by the facade like the two errors above.
    """


class TemplateRefusedError(Exception):
    """Meta refused an armed template at send time: paused, disabled, not held by
    this account, or a parameter it would not take (PRO-232). Nothing was
    delivered.

    Reported (paged through the ``TEMPLATE_LOST`` budget) before it is raised,
    and translated by the facade like the errors above, so a refusal never
    aborts the flow whose state change it was announcing. A refused *free-form*
    send is not this: it still propagates (the yellowCard lesson).
    """


@dataclass(frozen=True)
class NormalizedMessage:
    """Provider-agnostic inbound message.

    The internal shape every provider's ``parse_webhook`` must produce, so the
    worker never sees a vendor payload. PRO-89 wired the Cloud API side:
    ``/webhook/meta`` normalizes Meta envelopes through
    ``cloud_api.normalize_meta_message`` into exactly this shape before
    anything is enqueued.
    """

    chat_id: str
    text: str = ""
    media_url: str | None = None
    sender_name: str | None = None
    message_id: str | None = None
    #: PRO-104: the lead a tapped button named (``"<keyword>#<lead_id>"``), as
    #: a 24-hex ObjectId string. ``text`` then holds only the keyword.
    reply_ref: str | None = None


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
        """Deliver a plain text message.

        Raises :class:`ServiceWindowClosedError` when a free-form send is
        blocked by a closed 24h window with no approved fallback template. The
        provider must report that to the operator *before* raising: the facade
        turns the exception into a ``None`` return (PRO-159), so the provider's
        log line is the only record that the message existed.
        """

    @abstractmethod
    async def send_file(
        self,
        chat_id: str,
        url: str,
        caption: str = "",
        file_name: str = "media.jpg",
    ) -> dict[str, Any] | None:
        """Deliver a media file addressed by URL.

        Same window contract as :meth:`send_text` — report, then raise.
        """

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
        """Deliver ``text``, or the approved template when only that can arrive.

        ``buttons`` (``template_registry.QuickReply``) are the replies ``text``
        advertises, offered as reply buttons where the transport can (PRO-104);
        ``reply_ref`` is the lead a tap names, appended to each payload.

        The default is for a provider with no service window to respect, which
        always sends the text. Same window contract as :meth:`send_text`.
        """
        if buttons:
            return await self.send_buttons(
                chat_id, text, buttons_with_reply_ref(buttons, reply_ref)
            )
        return await self.send_text(chat_id, text)

    async def send_buttons(
        self,
        chat_id: str,
        text: str,
        buttons: Sequence[Any],
    ) -> dict[str, Any] | None:
        """Deliver ``text`` with reply buttons mirroring the replies it advertises.

        Each button is a ``template_registry.QuickReply``; a tap sends back its
        ``payload``, the string the flow already accepts typed (PRO-105). The
        default sends the text alone, which is always correct: the text still
        says what to type. Same window contract as :meth:`send_text`.
        """
        return await self.send_text(chat_id, text)

    @abstractmethod
    async def send_interactive(
        self,
        chat_id: str,
        body: str,
        options: list[str],
    ) -> dict[str, Any] | None:
        """Deliver an interactive (button/list) message.

        The legacy vendor could not do this at all, which is why the project convention
        was numeric text menus. The PRO-89 transport exists now, but no *flow*
        calls this: the reply buttons the flows send (PRO-104/PRO-105) go through
        :meth:`send_buttons` and :meth:`send_business_initiated`, whose ids are
        the keywords the text advertises, not ``"1"``, ``"2"`` (see CLAUDE.md).

        Same window contract as :meth:`send_text` — report, then raise.
        """

    async def window_closed_by_design(self, chat_id: str, reason: str) -> bool:
        """True when a send left untemplated **on purpose** cannot arrive (PRO-232).

        ``reason`` says why the send has no template; it is for the log. The
        caller drops the send quietly on True instead of letting it reach the
        "closed window, no approved template" page, which is for a send whose
        missing template is a defect. The default, for a provider with no
        window to respect, is False: the send goes ahead as usual.
        """
        return False

    async def indicate_typing(self, chat_id: str, message_id: str | None) -> None:
        """Mark the inbound ``message_id`` read and show "typing…" (PRO-133).

        Best-effort by contract — the facade swallows failures, so the default
        no-op is a valid implementation. ``message_id`` is the inbound wamid;
        a provider that needs it (Cloud API does) skips the call without one.
        """
        return None

    @abstractmethod
    async def get_state(self) -> str | None:
        """Authorization state of the underlying account.

        ``"authorized"`` means healthy. ``None`` means the probe itself failed
        and the caller should treat the account as not known-good — the facade
        does exactly that.
        """

    async def get_quality(self) -> dict[str, Any] | None:
        """What the vendor says about the number's reputation and limits.

        Not abstract: only a transmitting provider has a number to report on.
        ``None`` means there is no reading, which the watchdog treats as a failed
        poll. Keys are in ``CloudAPIProvider.get_quality``.
        """
        return None

    async def get_template_states(
        self, specs: list[Any]
    ) -> dict[str, dict[str, str]] | None:
        """What the vendor holds for each template spec, keyed by spec key (PRO-232).

        Each value has ``status`` and ``category`` in Meta's upper case; a spec
        the account does not hold maps to ``status: "NOT_FOUND"``. ``None``
        means nothing could be read. Not abstract: only a transmitting provider
        has an account holding templates.
        """
        return None

    @abstractmethod
    def parse_webhook(self, payload: Any) -> NormalizedMessage | None:
        """Convert a provider webhook payload into a :class:`NormalizedMessage`.

        Returns ``None`` for payloads that carry no processable message (status
        callbacks, echoes, group traffic).
        """

    async def close(self) -> None:
        """Release transport resources. Safe to call more than once."""
        return None
