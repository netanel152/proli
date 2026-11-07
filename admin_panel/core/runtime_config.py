"""Reports the runtime settings the panel's sends and save paths depend on."""

from streamlit import config

from app.core.config import settings
from app.core.logger import logger
from app.core.startup_checks import warn_if_cannot_transmit
from app.providers.whatsapp import get_whatsapp

_reported = False
_reported_egress = False


def report_fast_reruns(get_option=config.get_option):
    """Log the effective ``runner.fastReruns`` once per server process.

    ``.streamlit/config.toml`` turns it off, but a start command flag or a
    ``STREAMLIT_RUNNER_FAST_RERUNS`` variable outranks that file, and neither is
    visible from the repo. This line is how a deployment confirms the value.
    """
    global _reported
    if _reported:
        return
    _reported = True

    value = get_option("runner.fastReruns")
    if value:
        logger.error(
            "[admin_panel] runner.fastReruns is ON: a rerun during a WhatsApp send "
            "will lose the outcome shown to the operator. Check for a start command "
            "override, a STREAMLIT_RUNNER_FAST_RERUNS variable, or a working "
            "directory that is not the repo root."
        )
    else:
        logger.info(f"[admin_panel] runner.fastReruns={value}")


def report_whatsapp_egress(whatsapp_factory=get_whatsapp):
    """Report once per server process whether this panel can send at all.

    Never raises: this runs at the top of ``main.py``, and a report must not cost
    the operator the panel.
    """
    global _reported_egress
    if _reported_egress:
        return
    _reported_egress = True

    try:
        warn_if_cannot_transmit(settings, whatsapp_factory().provider)
    except Exception as e:
        logger.error(f"[admin_panel] Could not check the WhatsApp provider: {e}")
