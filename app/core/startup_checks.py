"""Boot-time configuration checks that report rather than refuse.

`app/core/config.py` raises on the misconfigurations that stop a service doing
its job at all — an unauthenticated webhook, a transport that transmits
nothing. This module is for the other kind: a setting that leaves the service
working while quietly degrading something around it, where refusing to boot
would turn a partial failure into a total one.

It lives here rather than in `config.py` because `app/core/logger.py` imports
`settings` at module level, so `config.py` cannot import the logger without a
cycle. Detection stays a property on `Settings`; the message comes from here,
and the boot paths call it.
"""

from app.core.config import Settings
from app.core.legal_links import terms_text_is_final
from app.core.logger import logger


def warn_if_terms_enabled_on_draft(settings: Settings) -> bool:
    """Report ``TERMS_ENABLED`` set while ``terms.html`` is still a draft.

    The page stays hidden either way, so without this the operator's flag would
    look like it did nothing, for no visible reason. Returns whether it reported.
    """
    if not settings.TERMS_ENABLED or terms_text_is_final():
        return False
    logger.error(
        "TERMS_ENABLED is set but app/static/terms.html is still a draft — /terms "
        "stays 404 and no message links to it until the text is finished."
    )
    return True


def warn_if_public_origin_malformed(settings: Settings) -> bool:
    """Report a ``PUBLIC_BASE_ORIGIN`` that was set but cannot be linked to.

    The messages simply go out without a link, so without this the only sign is
    a link that never appears. Never names the value: it can hold credentials.
    Returns whether it reported.
    """
    if not settings.public_base_origin_malformed:
        return False
    logger.error(
        "PUBLIC_BASE_ORIGIN is set but is not a public https origin with no path, "
        "like https://example.co.il — messages carry no policy link until it is fixed."
    )
    return True


def warn_if_waba_id_missing(settings: Settings) -> bool:
    """Report production transmitting with no ``META_WABA_ID``.

    The quality watchdog still reads the rating, but ``/health`` then says
    ``can_send_message: UNCONFIGURED`` and a BLOCKED or LIMITED WABA pages nobody.
    Returns whether it reported.
    """
    if (
        not settings.is_production
        or settings.WHATSAPP_DRY_RUN
        or settings.WHATSAPP_PROVIDER != "cloud"
        or settings.META_WABA_ID
    ):
        return False
    logger.error(
        "META_WABA_ID is not set — the WhatsApp watchdog cannot read the WABA's "
        "health_status, so a BLOCKED or LIMITED account pages nobody."
    )
    return True


def warn_if_admin_phone_unconfigured(settings: Settings) -> bool:
    """Report a prod-like deployment that never set ``ADMIN_PHONE`` (PRO-48).

    Every SOS report, admin alert and on-call page routes to ``ADMIN_PHONE``
    (``ONCALL_PHONE`` falls back to it), and the field carries a real number as
    its default — so a deployment that never set it pages whoever that default
    happens to name, silently, for as long as nobody notices.

    **Emitted at ERROR, not WARNING, and that is the point.** The failure this
    catches is one you only discover when an incident fails to reach anyone, so
    a message that reaches only stdout reproduces the problem it reports. A
    loguru-native ERROR goes through `app/core/sentry.py`'s bridge sink to a
    Sentry `error` event — out-of-band, throttled per site per hour, and the
    fatal-only alert rule never pages on it. CRITICAL (`page_critical`) would
    page, which is too loud for a degraded side channel that is, today, still
    reaching the operator.

    Returns whether it reported, so a caller can assert on the decision without
    capturing logs.
    """
    if not (settings.is_prod_like and settings.admin_phone_unconfigured):
        return False

    # Last 4 only, the project-wide form for a number in a log line
    # (`notification_service.py` masks the on-call number the same way).
    tail = (settings.ADMIN_PHONE or "")[-4:] or "?"
    logger.error(
        f"ADMIN_PHONE is not set in this environment — SOS reports, admin "
        f"alerts and on-call pages route to the built-in default (***{tail}). "
        f"Set it explicitly on this service (PRO-48)."
    )
    return True
