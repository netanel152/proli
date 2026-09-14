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


def pro_option_label(T, pro):
    """The name a selector or card shows for a professional — never blank.

    Self-onboarding writes ``business_name: ""`` (``pro_onboarding_service``),
    and ``dict.get(key, default)`` only fires on a *missing* key, so every
    ``p.get("business_name", T["unnamed_pro"])`` in the panel rendered the empty
    string: a bold nothing on the pro card, a blank row in the assign dropdown,
    and — because the maps were keyed by that name — every unnamed pro collapsing
    onto one dict entry, so picking the blank row wrote whichever document Mongo
    returned last. The tail of the phone number keeps two unnamed pros apart.
    """
    name = (pro.get("business_name") or "").strip()
    if name:
        return name
    tail = str(pro.get("phone_number") or pro.get("_id") or "")[-4:]
    return f"{T['unnamed_pro']} · {tail}" if tail else T["unnamed_pro"]


def pro_option_map(T, pros):
    """``{label: pro}`` with one entry per pro, labels made unique.

    Two pros can share a business name (two "אבי שרברב"s); a name-keyed dict
    silently drops one of them from every selector that reads it. A duplicate
    gets the id tail appended so both stay reachable and the selector still
    reads as a name.
    """
    out = {}
    for pro in pros:
        label = pro_option_label(T, pro)
        if label in out:
            label = f"{label} · {str(pro.get('_id', ''))[-4:]}"
        out[label] = pro
    return out
