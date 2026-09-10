"""Operator-facing labels for enum-like values — streamlit-free.

PRO-61. The admin panel used to translate lead statuses and profession types
at every call site with ``T.get(x, x.capitalize())``, so a missing key did
not fail: it rendered as capitalised English ("Booked", "Plumber") inside a
Hebrew page, and each view could — and did — resolve the same value
differently (the analytics funnel never translated at all). This module is
the one place those two vocabularies are resolved, so every view renders the
same word for the same value.

Two sources of truth, neither of them here:

* **Profession types** come from ``Messages.Onboarding.TYPE_LABELS`` — the
  catalog the pro-onboarding flow already uses to *name* a profession back to
  the pro. The Hebrew ``type_*`` entries in ``TRANS`` are derived from it at
  import (``config.py``), so the panel cannot call a plumber something the
  bot does not; the English entries stay in ``TRANS``.
* **Lead statuses** are the flat per-status keys in ``TRANS`` (``"booked"``,
  ``"pending_admin_review"``…), one per ``LeadStatus`` member, guarded by
  ``tests/test_admin_translations.py``. The pro-facing
  ``Messages.Pro.STATUS_LABELS`` is deliberately *not* reused wholesale: it
  is written from the pro's side and maps both ``new`` and ``contacted`` to
  "ממתין", which is fine in a WhatsApp job row but unusable in a status
  selector or as two adjacent Kanban column headers.
"""

from app.core.constants import LeadStatus
from app.core.messages import Messages

#: Every lead status the panel can show or set, in lifecycle order. The
#: single source of the selector option lists (table editor, Edit form,
#: Create form) so the three cannot offer different sets.
LEAD_STATUSES = [s.value for s in LeadStatus]

#: Profession codes as stored on a pro document, in the catalog's menu order.
#: The Add/Edit-pro selectbox reads this instead of carrying its own list.
PROFESSION_TYPES = tuple(Messages.Onboarding.TYPE_LABELS)


def lead_status_label(T, status):
    """The operator-facing word for a lead ``status``.

    Falls back to the raw value — never a capitalised one — so a status the
    dict does not know renders as the honest token (``"N/A"``, a stray legacy
    value) rather than as fake English UI copy.
    """
    status = "" if status is None else str(status)
    return T.get(status) or status


def profession_label(T, ptype):
    """The operator-facing name for a profession code (``"plumber"`` → אינסטלטור)."""
    ptype = "" if ptype is None else str(ptype)
    return T.get(f"type_{ptype}") or ptype


def status_label_map(T, statuses=None):
    """``{status: label}`` for the given statuses (default: every LeadStatus)."""
    return {s: lead_status_label(T, s) for s in (statuses or LEAD_STATUSES)}


def status_by_label(T, statuses=None):
    """``{label: status}`` — the inverse, for reading a label-valued cell back.

    ``st.column_config.SelectboxColumn`` has no ``format_func``: the option
    the operator picks *is* the cell value. So the leads editor shows a
    label-valued twin column and maps the choice back through this on save.
    """
    return {label: s for s, label in status_label_map(T, statuses).items()}
