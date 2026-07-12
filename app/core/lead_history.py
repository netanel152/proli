"""Pure helper for building lead ``status_history`` entries.

Shared by the async service layer (``lead_manager_service.set_lead_status``) and
the synchronous Streamlit admin panel so both write identical
``{status, at, by}`` entries. No I/O and no async/motor imports, so it is safe
to import from either process.

``status`` and ``actor`` are stored as-is. ``LeadStatus`` and ``Actor`` are
``(str, Enum)`` subclasses, so their underlying string content ("completed",
"pro", ...) is what MongoDB persists — do NOT wrap them in ``str()``, which
would serialize the Enum repr ("LeadStatus.COMPLETED") instead.
"""

from datetime import datetime, timezone


def status_history_entry(status, actor) -> dict:
    """Build one timestamped status transition record."""
    return {
        "status": status,
        "at": datetime.now(timezone.utc),
        "by": actor,
    }
