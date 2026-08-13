"""PRO-89 — the template registry: which business-initiated sends have a
Meta-approved template behind them.

The inventory itself is PRO-88 (docs/WHATSAPP_TEMPLATE_CATALOG.md); this module
is its machine-readable half. Every entry below mirrors one catalog row. All of
them are ``DRAFT`` until PRO-87 creates the Business Portfolio and the
templates clear Meta review — flipping an entry to ``APPROVED`` (with the exact
name Meta approved) is the single code change that arms it.

Two lookups, two callers:

* ``resolve(key)`` — ``CloudAPIProvider.send_template`` refuses to transmit a
  template Meta has not approved; an unknown or draft key is a structured
  error, never a silent drop.
* ``freeform_fallback(chat_id_kind)`` — the 131047 retry path asks whether a
  *free-form* send that died on a closed window has a template equivalent to
  retry with. Returns ``None`` for everything today (no template is approved),
  which routes the failure to an operator page instead.
"""

from dataclasses import dataclass
from enum import Enum


class TemplateStatus(str, Enum):
    DRAFT = "draft"  # designed in PRO-88, not yet submitted/approved
    APPROVED = "approved"  # cleared Meta review — sendable


@dataclass(frozen=True)
class TemplateSpec:
    key: str  # Proli's stable logical name (catalog row)
    meta_name: str  # the name registered with Meta (final on approval)
    status: TemplateStatus = TemplateStatus.DRAFT
    language: str = "he"  # templates are approved per language (catalog §5)


# Keyed by logical name. The keys are the PRO-88 catalog rows that survived the
# consolidation pass (P2 folded into P1, O1–O3 deleted in favor of operator
# paging), so this is the full set of sends that can ever need a template.
TEMPLATES: dict[str, TemplateSpec] = {
    spec.key: spec
    for spec in (
        # Professional-facing
        TemplateSpec("lead_offer", "proli_lead_offer"),  # P1 (+P2 folded in)
        TemplateSpec("lead_offer_reassigned", "proli_lead_offer_reassigned"),  # P3
        TemplateSpec("early_lead", "proli_early_lead"),  # P4 (fate open — PRO-88 §3)
        TemplateSpec("approval_nudge", "proli_approval_nudge"),  # P5
        TemplateSpec("daily_agenda", "proli_daily_agenda"),  # P6
        TemplateSpec("stale_lead_reminder", "proli_stale_lead_reminder"),  # P7
        TemplateSpec("finish_reminder", "proli_finish_reminder"),  # P8
        TemplateSpec("pro_lost_lead", "proli_pro_lost_lead"),  # P9
        TemplateSpec("bot_paused", "proli_bot_paused"),  # P10
        TemplateSpec("sos_alert", "proli_sos_alert"),  # P11
        TemplateSpec("customer_cancelled", "proli_customer_cancelled"),  # P12
        TemplateSpec("onboarding_verdict", "proli_onboarding_verdict"),  # P13
        # Customer-facing
        TemplateSpec("no_pro_available", "proli_no_pro_available"),  # C1
        TemplateSpec("completion_check", "proli_completion_check"),  # C2
        TemplateSpec("reassignment_notice", "proli_reassignment_notice"),  # C3
    )
}


def resolve(key: str) -> TemplateSpec | None:
    """The spec for ``key`` **iff** it is approved and therefore sendable.

    Draft and unknown keys both return ``None`` — the caller cannot tell them
    apart and must not: neither can be transmitted, and the distinction only
    matters to a human reading the catalog.
    """
    spec = TEMPLATES.get(key)
    if spec is None or spec.status is not TemplateStatus.APPROVED:
        return None
    return spec


def freeform_fallback(kind: str) -> TemplateSpec | None:
    """The approved template to retry a window-closed *free-form* send with.

    ``kind`` is the provider's send kind (``text``/``file``/``interactive``).
    A free-form send carries no template identity, so there is nothing to map
    from until the PRO-88 migration converts business-initiated call sites to
    ``send_template`` — at which point they stop needing a fallback at all.
    Kept as the explicit hook the 131047 retry path calls, so "no fallback →
    operator page" is a decision recorded here rather than an accident of a
    missing dict.
    """
    return None
