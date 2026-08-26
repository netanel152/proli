"""PRO-86 — provider selection.

``get_whatsapp()`` returns the process-wide facade. Nothing outside this package
should ever construct a provider directly: doing so bypasses the circuit breaker
and the kill switch, which is precisely the class of bug this ticket removes.
"""

from app.core.config import settings
from app.core.logger import logger
from app.providers.whatsapp.base import (
    NormalizedMessage,
    ServiceWindowClosedError,
    TemplateNotRegisteredError,
    WhatsAppProvider,
)
from app.providers.whatsapp.cloud_api import CloudAPIProvider
from app.providers.whatsapp.dry_run import DryRunProvider
from app.providers.whatsapp.facade import WhatsAppFacade, record_account_state

_PROVIDERS: dict[str, type[WhatsAppProvider]] = {
    DryRunProvider.name: DryRunProvider,
    CloudAPIProvider.name: CloudAPIProvider,
}

_facade: WhatsAppFacade | None = None


def build_provider() -> WhatsAppProvider:
    """Resolve the configured provider.

    ``WHATSAPP_DRY_RUN=true`` wins over ``WHATSAPP_PROVIDER`` unconditionally.
    It is the operator's emergency mute (the WhatsApp outage runbook tells them
    to set it during an incident), so it must not be possible to have it set and
    still transmit.
    """
    configured = (settings.WHATSAPP_PROVIDER or DryRunProvider.name).strip().lower()

    if settings.WHATSAPP_DRY_RUN:
        if configured != DryRunProvider.name:
            # The two settings overlap, and a silent override is how a muted
            # service turns into an hour of "why isn't it sending". Say it out
            # loud, at WARNING, naming the setting that has to change.
            logger.warning(
                f"WHATSAPP_DRY_RUN=true overrides WHATSAPP_PROVIDER={configured!r} "
                "— nothing will be transmitted. Unset WHATSAPP_DRY_RUN to send."
            )
        return DryRunProvider()

    name = configured
    provider_cls = _PROVIDERS.get(name)
    if provider_cls is None:
        # Unknown value must not silently fall back to something that transmits.
        logger.error(
            f"Unknown WHATSAPP_PROVIDER={name!r}; falling back to dry-run. "
            f"Valid values: {', '.join(sorted(_PROVIDERS))}"
        )
        return DryRunProvider()
    return provider_cls()


def get_whatsapp() -> WhatsAppFacade:
    """The single egress, built once per process."""
    global _facade
    if _facade is None:
        provider = build_provider()
        logger.info(f"WhatsApp egress using provider {provider.name!r}")
        _facade = WhatsAppFacade(provider)
    return _facade


def reset_whatsapp() -> None:
    """Drop the cached facade so the next call re-reads config. Test seam."""
    global _facade
    _facade = None


__all__ = [
    "CloudAPIProvider",
    "DryRunProvider",
    "NormalizedMessage",
    "ServiceWindowClosedError",
    "TemplateNotRegisteredError",
    "WhatsAppFacade",
    "WhatsAppProvider",
    "build_provider",
    "get_whatsapp",
    "record_account_state",
    "reset_whatsapp",
]
