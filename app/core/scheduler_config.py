"""The ``settings.scheduler_config`` document: one id, one set of defaults.

PRO-249. The worker (``app/scheduler.py``) and the admin panel
(``admin_panel/views/settings.py``) both read and write this document, and
each used to carry its own defaults. The panel inserted a partial document
(so the worker's ``$setOnInsert`` never applied) and showed a missing
``sos_healer_active`` as **on** while the worker treated it as **off**: the
operator saw "active" on a job that did not run, and a Save from that screen
switched it on.

The two jobs that message a customer because a chat went silent
(``sos_healer_active``, ``lead_janitor_active``) default **off** until an
operator enables them once the WhatsApp number is warmed up (PRO-73); every
other toggle defaults on, ``sla_monitor_active`` included: the SLA deflection
answers a customer who asked for a human minutes earlier, so their window is
open. A default applies only where the document lacks the key, so a
``scheduler_config`` already stored with ``sla_monitor_active: False`` stays
off until an operator sets it. All three customer-facing jobs still run only
inside business hours. Both processes read a toggle through
:func:`scheduler_toggle` and insert the document through
:data:`SCHEDULER_CONFIG_DEFAULTS`, so a missing key means the same thing on
both sides.
"""

from types import MappingProxyType

SCHEDULER_CONFIG_ID = "scheduler_config"

SCHEDULER_CONFIG_DEFAULTS = MappingProxyType(
    {
        # Daily agenda (daily_reminders_job).
        "run_time": "08:00",
        "is_active": True,
        "last_run_date": None,
        "trigger_now": False,
        # Safety & monitoring.
        "stale_monitor_active": True,
        "sos_reporter_active": True,
        "whatsapp_monitor_active": True,
        # PRO-73: cold customer-facing jobs stay dark until switched on.
        "sos_healer_active": False,
        "lead_janitor_active": False,
        # On by default: it answers a customer who asked for a human minutes
        # earlier (their window is open), so it is not a cold re-engagement.
        "sla_monitor_active": True,
    }
)

SCHEDULER_TOGGLES = tuple(k for k in SCHEDULER_CONFIG_DEFAULTS if k.endswith("_active"))


def scheduler_toggle(config: dict | None, key: str) -> bool:
    """Whether ``key`` is on in ``config``; a missing document or key reads as
    its default. Raises ``KeyError`` for a key with no default, so a typo
    cannot silently read as off."""
    default = SCHEDULER_CONFIG_DEFAULTS[key]
    return bool((config or {}).get(key, default))
