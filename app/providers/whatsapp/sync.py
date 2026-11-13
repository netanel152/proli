"""PRO-86 — blocking bridge for the synchronous admin panel.

Streamlit call sites used to reach for raw ``httpx.post`` against the legacy
WhatsApp vendor,
which is exactly how outbound traffic escaped the circuit breaker and helped
earn the yellowCard. They are synchronous, so they cannot ``await`` the facade;
this module is the one sanctioned way across that boundary for outbound
messages. The loop it runs on is ``app.core.sync_bridge`` — shared with
every other sync→async crossing in the admin panel, see that module for why.

It is a bridge, not a second egress: everything still goes through
:class:`~app.providers.whatsapp.facade.WhatsAppFacade`, breaker included.
"""

from typing import Any

from app.core.logger import logger
from app.core.sync_bridge import run_blocking
from app.providers.whatsapp import get_whatsapp

# How long a single admin-panel send may block the Streamlit script run.
_SEND_TIMEOUT_SECONDS = 30.0


def _run_blocking(coro, timeout: float):
    """Kept as the module's seam: the loop itself now lives in
    ``app.core.sync_bridge`` so the geocoding check at pro approval shares it
    (and the cached Redis client) instead of starting a second loop."""
    return run_blocking(coro, timeout)


def can_transmit() -> bool:
    """Whether this process's provider can reach a handset at all.

    For choosing what to tell the operator after a send came back False: a muted
    environment needs its variables changed, and retrying will not help.
    """
    return get_whatsapp().provider.transmits


def send_text_sync(
    chat_id: str,
    text: str,
    *,
    template_key: str | None = None,
    params: dict[str, Any] | None = None,
) -> bool:
    """Send one text message from sync code.

    A panel send is business-initiated, since an operator's click is what
    triggers it, so it names its template as the worker's sends do: a recipient
    whose 24h window is closed gets that template once it is approved. A send
    that names none is exactly the call it was before.

    Returns True only when the facade actually handed the message to a provider.
    A suppressed send — breaker engaged, kill switch set, or (PRO-159) a closed
    24h service window with no approved fallback template — returns False, so
    the admin panel can tell the operator "not sent" instead of showing a
    success toast for a message nobody received. So does a provider that cannot
    transmit, whose ``send_text`` returns a dict without sending anything.

    Never raises: every caller is a best-effort notification hanging off a UI
    action, and a failed courtesy message must not abort the database mutation
    that preceded it.
    """
    try:
        facade = get_whatsapp()
        if not facade.provider.transmits:
            logger.warning(
                f"Admin-panel send to ...{chat_id[-4:]} not sent — provider "
                f"{facade.provider.name!r} cannot transmit."
            )
            return False
        template = (
            {}
            if template_key is None
            else {"template_key": template_key, "params": params}
        )
        result = _run_blocking(
            facade.send_message(chat_id, text, **template), _SEND_TIMEOUT_SECONDS
        )
    except Exception as e:
        logger.error(f"Admin-panel send to ...{chat_id[-4:]} failed: {e}")
        return False
    if result is None:
        logger.warning(
            f"Admin-panel send to ...{chat_id[-4:]} suppressed — outbound halted."
        )
        return False
    return True
