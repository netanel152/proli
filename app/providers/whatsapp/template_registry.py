"""PRO-89 — the template registry: which business-initiated sends have a
Meta-approved template behind them.

The inventory itself is PRO-88 (docs/WHATSAPP_TEMPLATE_CATALOG.md); this module
is its machine-readable half. Every entry stays ``DRAFT`` until the template
clears Meta review (``scripts/submit_whatsapp_templates.py`` submits it and
reports its status and the category Meta holds it in). Flipping an entry to
``APPROVED``, with the exact name Meta approved, arms it for the call sites that
pass ``template_key`` to the facade's ``send_message``: the worker's, and the
admin panel's through ``sync.send_text_sync``. It does nothing for a send that
names no template. Flip only an entry Meta approved as UTILITY: one it holds as
MARKETING costs several times as much and may not be delivered, so arming it is
a decision to make on purpose (``--status`` exits 2 on one).

A spec stores no body text: the copy has exactly one home
(``app/core/messages.py``, ARCHITECTURE_STANDARD §10). ``body`` is a reference to
the constant itself, and ``source`` names the free-form ``Messages.*`` constant
the template was worded from (PRO-168).

Three lookups, three callers:

* ``resolve(key)`` — the provider refuses to transmit a template Meta has not
  approved; an unknown or draft key is a structured error, never a silent drop.
* ``sanitize_param(value)`` — every runtime variable passes through it on the way
  to Meta.
* ``freeform_fallback(kind)`` — the window-closed path for a free-form send that
  named no template. Returns ``None`` for everything, which routes the failure
  to an operator page instead.
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.core.messages import Messages

_VARIABLE = re.compile(r"\{\{(\d+)\}\}")
# Components doc: "Maximum of 1024 characters" for body text.
_BODY_MAX_CHARS = 1024
# shortcut: Meta rejects "too many variables relative to the message length" but
# publishes no ratio, so this floor is ours. Change it if a review says otherwise.
_MIN_FIXED_CHARS_PER_VARIABLE = 20
_LINE_BREAKS_AND_TABS = re.compile(r"[\r\n\t\v\f\x85\u2028\u2029]+")
_LONG_SPACE_RUN = re.compile(r" {4,}")
# A long customer message must not overflow the body and lose the alert. Meta
# states 1024 characters for a body and no separate figure for a parameter, so
# these are ours, chosen to fit the largest body inside that limit.
_PARAM_MAX_CHARS = {"last_message": 700}
_PARAM_DEFAULT_MAX_CHARS = 120


class TemplateStatus(str, Enum):
    DRAFT = "draft"  # designed in PRO-88, not yet submitted/approved
    APPROVED = "approved"  # cleared Meta review — sendable


@dataclass(frozen=True)
class TemplateSpec:
    key: str  # Proli's stable logical name (catalog row)
    meta_name: str  # the name registered with Meta (final on approval)
    status: TemplateStatus = TemplateStatus.DRAFT
    language: str = "he"  # templates are approved per language (catalog §5)
    # PRO-168: the free-form `Messages.*` constant this template was worded from,
    # so a copy pass can tell which template a rewritten string feeds.
    source: str = ""
    # The fixed text submitted to Meta: a reference to a `Messages` constant, not
    # a second copy of it. Empty only on an entry with `submit=False`.
    body: str = ""
    # The names `body` uses, in the order they appear: `{{1}}`..`{{n}}` to Meta.
    params: tuple[str, ...] = ()
    # Every template is UTILITY: MARKETING is throttled and costs more, and Meta
    # can reject a miscategorised one with INCORRECT_CATEGORY.
    category: str = "UTILITY"
    # False keeps an entry out of scripts/submit_whatsapp_templates.py.
    submit: bool = True


# Keyed by logical name: the catalog rows that survived the consolidation pass,
# with the two rows that were really two bodies split (P11, P13) and C3's three
# messages listed one by one. `params` numbers the variables and is what
# `send_template` sends by, so a caller's dict order never matters.
TEMPLATES: dict[str, TemplateSpec] = {
    spec.key: spec
    for spec in (
        # Professional-facing. PRO-59 unfolded P2 from P1: the navigation link is
        # no longer sent with the offer. The post-approval card needs no entry of
        # its own, since it replies inside the 24h window the pro's own אשר opened.
        TemplateSpec(
            "lead_offer",
            "proli_lead_offer",
            source="Messages.Pro.APPROVAL_REQUEST",
            body=Messages.Templates.LEAD_OFFER,
            params=(
                "customer_name",
                "full_address",
                "issue_type",
                "appointment_time",
                "price_estimate",
                "urgency",
            ),
        ),  # P1, and P3 for an emergency: this body is the one that can say so
        TemplateSpec(
            "lead_offer_reassigned",
            "proli_lead_offer_reassigned",
            source="Messages.Pro.NEW_LEAD_HEADER + Messages.Pro.NEW_LEAD_DETAILS",
            body=Messages.Templates.LEAD_OFFER_REASSIGNED,
            params=(
                "customer_name",
                "full_address",
                "issue_type",
                "appointment_time",
            ),
        ),  # P3
        # P4 sends an image when it has one, which needs a media template, and it
        # is not load-bearing. The owner decided not to submit it; its free-form
        # send stays as it is.
        TemplateSpec(
            "early_lead",
            "proli_early_lead",
            source="Messages.Pro.EARLY_LEAD_HEADER + Messages.Pro.EARLY_LEAD_DETAILS",
            submit=False,
        ),  # P4
        TemplateSpec(
            "approval_nudge",
            "proli_approval_nudge",
            source="Messages.Pro.APPROVAL_NUDGE",
            body=Messages.Templates.APPROVAL_NUDGE,
            params=("issue_type", "minutes"),
        ),  # P5
        TemplateSpec(
            "daily_agenda",
            "proli_daily_agenda",
            source="Messages.Pro.DAILY_AGENDA_HEADER",
            body=Messages.Templates.DAILY_AGENDA,
            params=("pro_name", "date", "job_count"),
        ),  # P6
        TemplateSpec(
            "stale_lead_reminder",
            "proli_stale_lead_reminder",
            source="Messages.Pro.STALE_LEAD_REMINDER",
            body=Messages.Templates.STALE_LEAD_REMINDER,
            params=("pro_name", "customer_name"),
        ),  # P7
        TemplateSpec(
            "finish_reminder",
            "proli_finish_reminder",
            source="Messages.Pro.REMINDER",
            body=Messages.Templates.FINISH_REMINDER,
        ),  # P8
        TemplateSpec(
            "pro_lost_lead",
            "proli_pro_lost_lead",
            source="Messages.SOS.PRO_LOST_LEAD",
            body=Messages.Templates.PRO_LOST_LEAD,
            params=("issue_type",),
        ),  # P9
        TemplateSpec(
            "bot_paused",
            "proli_bot_paused",
            source="Messages.Pro.PAUSE_NOTIFICATION",
            body=Messages.Templates.BOT_PAUSED,
            params=("issue_type",),
        ),  # P10
        TemplateSpec(
            "sos_alert",
            "proli_sos_alert",
            source="Messages.SOS.PRO_ALERT",
            body=Messages.Templates.SOS_ALERT,
            params=("phone", "last_message"),
        ),  # P11, lead BOOKED
        TemplateSpec(
            "sos_alert_pending",
            "proli_sos_alert_pending",
            source="Messages.SOS.PRO_ALERT_PENDING",
            body=Messages.Templates.SOS_ALERT_PENDING,
        ),  # P11, lead NEW: no phone (PRO-59) and no customer text (PRO-212)
        TemplateSpec(
            "customer_cancelled",
            "proli_customer_cancelled",
            source="Messages.Pro.CUSTOMER_CANCELLED",
            body=Messages.Templates.CUSTOMER_CANCELLED,
            params=("customer_name", "full_address"),
        ),  # P12
        TemplateSpec(
            "onboarding_approved",
            "proli_onboarding_approved",
            source="Messages.Onboarding.APPROVED_NOTIFICATION",
            body=Messages.Templates.ONBOARDING_APPROVED,
        ),  # P13
        TemplateSpec(
            "onboarding_rejected",
            "proli_onboarding_rejected",
            source="Messages.Onboarding.REJECTED_NOTIFICATION",
            body=Messages.Templates.ONBOARDING_REJECTED,
        ),  # P13
        TemplateSpec(
            "pro_lead_outside_trade",
            "proli_pro_lead_outside_trade",
            source="Messages.Pro.LEAD_OUTSIDE_TRADE",
            body=Messages.Templates.PRO_LEAD_OUTSIDE_TRADE,
            params=("issue_type",),
        ),  # P14: one body for LEAD_OUTSIDE_TRADE and LEAD_OUTSIDE_TRADE_OFFER
        # Customer-facing
        TemplateSpec(
            "pro_found",
            "proli_pro_found",
            source="Messages.Customer.PRO_FOUND",
            body=Messages.Templates.PRO_FOUND,
            params=(
                "pro_name",
                "issue_type",
                "full_address",
                "appointment_time",
                "price_estimate",
                "pro_phone",
            ),
        ),  # the update REASSIGNING and PENDING_REVIEW promise; no catalog row before
        TemplateSpec(
            "no_pro_available",
            "proli_no_pro_available",
            source="Messages.SOS.NO_PRO_AVAILABLE",
            body=Messages.Templates.NO_PRO_AVAILABLE,
            params=("issue_type",),
        ),  # C1
        TemplateSpec(
            "completion_check",
            "proli_completion_check",
            source="Messages.Customer.COMPLETION_CHECK",
            body=Messages.Templates.COMPLETION_CHECK,
            params=("pro_name",),
        ),  # C2
        TemplateSpec(
            "reassigning",
            "proli_reassigning",
            source="Messages.SOS.CUSTOMER_REASSIGNING",
            body=Messages.Templates.REASSIGNING,
            params=("issue_type",),
        ),  # C3
        TemplateSpec(
            "max_reassignments",
            "proli_max_reassignments",
            source="Messages.SOS.MAX_REASSIGNMENTS_REACHED",
            body=Messages.Templates.MAX_REASSIGNMENTS,
            params=("issue_type",),
        ),  # C3
        TemplateSpec(
            "pending_review",
            "proli_pending_review",
            source="Messages.Customer.PENDING_REVIEW",
            body=Messages.Templates.PENDING_REVIEW,
            params=("issue_type",),
        ),  # C3
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

    ``kind`` is the provider's send kind (``text``/``file``/``interactive``). Only
    a send that named no ``template_key`` reaches this, so there is no template
    identity and no way to rebuild variables from rendered Hebrew: anything
    registered here would have to take no params. Nothing is, so this returns
    ``None`` and "no fallback → operator page" stays a decision recorded here.
    """
    return None


def sanitize_param(value: Any, name: str = "") -> str:
    """One runtime value as Meta may receive it, for the variable ``name``.

    Meta's docs do not say whether a parameter value may hold newlines, so this
    assumes not: newlines, tabs and runs of 4+ spaces become one space. A variable
    cannot be empty either, so an empty result falls back to a Hebrew default. A
    value over its limit is cut, since a cut alert beats one Meta refuses.
    """
    text = _LINE_BREAKS_AND_TABS.sub(" ", "" if value is None else str(value))
    text = _LONG_SPACE_RUN.sub(" ", text).strip()
    limit = _PARAM_MAX_CHARS.get(name, _PARAM_DEFAULT_MAX_CHARS)
    return text[:limit].rstrip() or Messages.Fallbacks.UNKNOWN


def meta_body(spec: TemplateSpec) -> str:
    """``spec.body`` as Meta takes it: each ``{name}`` becomes ``{{n}}``."""
    body = spec.body
    for number, name in enumerate(spec.params, 1):
        body = body.replace("{" + name + "}", "{{" + str(number) + "}}")
    return body


def body_rule_violations(body: str, params: tuple[str, ...]) -> list[str]:
    """What Meta's review would object to in a template body.

    ``body`` is in Meta's form, as ``meta_body`` renders it. The rules are from
    Meta's template review and components docs, read
    2026-11-06:
    developers.facebook.com/documentation/business-messaging/whatsapp/templates/
    (template-review/ and components/). Those pages also mention an emoji limit
    without a number, so none is checked.
    """
    problems = []
    variables = list(_VARIABLE.finditer(body))
    # What remains of a `{{name}}`, `{{#1}}` or a mismatched brace once every
    # well-formed `{{n}}` is gone.
    if re.search(r"[{}]", _VARIABLE.sub("", body)):
        problems.append("a variable is not in {{n}} form")
    if [int(v.group(1)) for v in variables] != list(range(1, len(variables) + 1)):
        problems.append("variables are not numbered in sequence from {{1}}")
    if len(variables) != len(params):
        problems.append(f"{len(variables)} variables but {len(params)} params")
    if len(body) > _BODY_MAX_CHARS:
        problems.append(f"longer than {_BODY_MAX_CHARS} characters")
    if not variables:
        return problems
    if not re.search(r"\w", body[: variables[0].start()]):
        problems.append("starts with a variable")
    if not re.search(r"\w", body[variables[-1].end() :]):
        problems.append("ends with a variable")
    if re.search(r"\}\}\W*\{\{", body):
        problems.append("two variables are adjacent")
    if len(_VARIABLE.sub("", body)) / len(variables) < _MIN_FIXED_CHARS_PER_VARIABLE:
        problems.append("too many variables for its length")
    return problems
