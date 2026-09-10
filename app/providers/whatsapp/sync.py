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


def send_text_sync(chat_id: str, text: str) -> bool:
    """Send one text message from sync code.

    Returns True only when the facade actually handed the message to a provider.
    A suppressed send — breaker engaged, kill switch set, or (PRO-159) a closed
    24h service window with no approved fallback template — returns False, so
    the admin panel can tell the operator "not sent" instead of showing a
    success toast for a message nobody received.

    Never raises: every caller is a best-effort notification hanging off a UI
    action, and a failed courtesy message must not abort the database mutation
    that preceded it.
    """
    try:
        result = _run_blocking(
            get_whatsapp().send_message(chat_id, text), _SEND_TIMEOUT_SECONDS
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
