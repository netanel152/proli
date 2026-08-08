"""PRO-86 — Meta Cloud API provider (stub).

The real implementation is PRO-89: send/receive via the Meta Graph API plus 24h
service-window tracking in Redis. This file exists so the facade has a second,
*transmitting* provider to select — which is what makes the fail-closed breaker
path (PRO-82) real and testable rather than theoretical.

Every method raises. Selecting ``WHATSAPP_PROVIDER=cloud`` before PRO-89 lands
is a misconfiguration and should fail loudly at the first send, not degrade to
silence.
"""

from typing import Any, NoReturn

from app.providers.whatsapp.base import NormalizedMessage, WhatsAppProvider

_NOT_YET = (
    "CloudAPIProvider is not implemented yet — see PRO-89. "
    "Set WHATSAPP_PROVIDER=dryrun until it lands."
)


class CloudAPIProvider(WhatsAppProvider):
    name = "cloud"

    # Can reach real handsets once PRO-89 fills it in, so the facade holds it to
    # the fail-closed breaker contract from day one.
    transmits = True

    def _unimplemented(self) -> NoReturn:
        raise NotImplementedError(_NOT_YET)

    async def send_text(self, chat_id: str, text: str) -> dict[str, Any] | None:
        self._unimplemented()

    async def send_file(
        self,
        chat_id: str,
        url: str,
        caption: str = "",
        file_name: str = "media.jpg",
    ) -> dict[str, Any] | None:
        self._unimplemented()

    async def send_template(
        self,
        chat_id: str,
        template_name: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        self._unimplemented()

    async def send_interactive(
        self,
        chat_id: str,
        body: str,
        options: list[str],
    ) -> dict[str, Any] | None:
        self._unimplemented()

    async def get_state(self) -> str | None:
        """``None`` rather than a raise.

        The PRO-20 watchdog polls this on a timer and treats ``None`` as "not
        known-good". Raising here would turn an unconfigured provider into a
        crashing scheduler job; returning None makes the breaker fail closed,
        which is the safe reading of "we cannot confirm this account is fine".
        """
        return None

    def parse_webhook(self, payload: Any) -> NormalizedMessage | None:
        self._unimplemented()
