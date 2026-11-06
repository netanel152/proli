"""Reports the Streamlit setting the panel's save paths depend on."""

from streamlit import config

from app.core.logger import logger

_reported = False


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
