"""PRO-89 — the template registry: which business-initiated sends have a
Meta-approved template behind them.

The inventory itself is PRO-88 (docs/WHATSAPP_TEMPLATE_CATALOG.md); this module
is its machine-readable half. An entry stays ``DRAFT`` until the template
clears Meta review (``scripts/submit_whatsapp_templates.py`` submits it and
reports its status and the category Meta holds it in). Flipping an entry to
``APPROVED``, with the exact name Meta approved, is the code half of arming it
for the call sites that pass ``template_key`` to the facade's ``send_message``:
the worker's, and the admin panel's through ``sync.send_text_sync``. The
environment half is ``WHATSAPP_ARMED_TEMPLATES`` (PRO-232, below). It does
nothing for a send that names no template.

``category`` records what Meta holds, not what was asked for. Meta approved all
28 submitted ``_v2`` templates on the production account by 2026-11-18, three of
them as MARKETING (``reassigning``, ``pro_lead_outside_trade``,
``stale_lead_reminder``). Those are ``APPROVED`` here so the registry agrees with
Meta, and are left out of ``WHATSAPP_ARMED_TEMPLATES``: a MARKETING template
costs several times a UTILITY one and may not be delivered, so arming one is a
decision to make on purpose (``--status`` exits 3 while Meta holds any).

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

PRO-232: ``APPROVED`` is half of arming. The other half is this environment's
``WHATSAPP_ARMED_TEMPLATES``, which names the keys its Meta account actually
holds: templates are approved on the production account only, and the same code
deploys to staging, whose test account holds none of them. ``resolve`` wants
both, so flipping an entry here arms it nowhere until an environment names it.

PRO-230: a template whose body advertises a reply (`כתוב אשר`, or a numbered
line `1 - …`) also carries it as a quick-reply button. The label is submitted
with the template; the payload goes with each send and is exactly the string the
flow accepts when typed, so a tap and a typed reply reach the same handler. The
keywords stay in the body, so typing keeps working on a client that shows no
buttons.

PRO-104/PRO-105: the same buttons (``buttons_for``) also go on the free-form
send of an open window, as interactive reply buttons, for the pro lead offers
and the customer completion check. A pro offer's button carries its lead
(``with_reply_ref``: ``"אשר#<lead_id>"``), so a tap acts on that lead and never
on another waiting offer.
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.core.config import settings
from app.core.logger import logger
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
# Quick-reply buttons, from Meta's docs read 2026-11-15:
# templates/components/ — "Templates are limited to 10 quick reply buttons",
# label "25 characters maximum"; templates/utility-templates/ — quick-reply is a
# supported button type on a UTILITY template, label "Maximum 25 characters.
# Alphanumeric characters only." Letters (any script), digits and single spaces.
_MAX_QUICK_REPLIES = 10
_BUTTON_LABEL_MAX_CHARS = 25
# Matched with `fullmatch`: `$` would also match before a trailing newline.
_BUTTON_LABEL = re.compile(r"[^\W_]+(?: [^\W_]+)*")
# What a body advertises, in the plain wording the owner approved 2026-11-17
# (no bold, no em dash). Two shapes:
# * a keyword after "כתוב"/"לכתוב" ("לאישור כתוב אשר"). A keyword can be two
#   words ("עדיין עובד"), so it is matched against the keywords a template may
#   offer, longest first, and must end at a word boundary. "לכתוב לנו" is the one
#   other thing a body may write after the verb; `unread_write_prompts` finds any
#   other, so a misspelt keyword cannot slip past the parity rule unseen.
# * a numbered line, `1 - text`, at the start of a line. A digit inside a
#   sentence (RATE_SERVICE's "מ-1 ... עד 5") is not one, and gets no button.
_REPLY_KEYWORDS = sorted(
    {
        *Messages.Keywords.APPROVE_COMMANDS,
        *Messages.Keywords.REJECT_COMMANDS,
        *Messages.Keywords.FINISH_COMMANDS,
        *Messages.Keywords.STILL_WORKING_COMMANDS,
        *Messages.Keywords.ACTIVE_JOBS_COMMANDS,
        *Messages.Keywords.SOS_COMMANDS,
        *Messages.Keywords.SKIP_TOKENS,
    }
    - {"1", "2", "3", "4"},  # a digit is offered as a numbered line, never typed
    key=len,
    reverse=True,
)
_WRITE_VERB = r"(?<!\w)ל?כתוב "
_ADVERTISED_REPLY = re.compile(
    rf"{_WRITE_VERB}({'|'.join(map(re.escape, _REPLY_KEYWORDS))})(?!\w)" r"|^(\d+) - ",
    flags=re.M,
)
_WRITE_PROMPT = re.compile(rf"{_WRITE_VERB}(?!לנו(?!\w))")
# PRO-232: what separates the keys in WHATSAPP_ARMED_TEMPLATES.
_ARMED_SEPARATORS = re.compile(r"[\s,;]+")
# Tokens already warned about, so a bad value is reported once per process
# rather than on every send that consults it.
_WARNED_ARMING_TOKENS: set[str] = set()


class TemplateStatus(str, Enum):
    DRAFT = "draft"  # designed in PRO-88, not yet submitted/approved
    APPROVED = "approved"  # cleared Meta review — sendable


@dataclass(frozen=True)
class QuickReply:
    """One quick-reply button: what it says, and what a tap sends back."""

    label: str  # submitted with the template; a `Messages.Templates.Buttons` constant
    payload: str  # sent with each message; the reply the flow already accepts typed


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
    # Submitted as UTILITY: MARKETING is throttled and costs more, and Meta can
    # reject a miscategorised one with INCORRECT_CATEGORY. Once Meta has ruled,
    # this is the category Meta holds, so `--status` reports no drift.
    category: str = "UTILITY"
    # False keeps an entry out of scripts/submit_whatsapp_templates.py.
    submit: bool = True
    # PRO-230: one per reply the body advertises, in the body's order.
    buttons: tuple[QuickReply, ...] = ()


_B = Messages.Templates.Buttons
_K = Messages.Keywords
# Pro templates send the keyword, never a bare digit: a digit is a pick from a
# numbered list, and outside one it opens the list rather than acting (PRO-212).
_APPROVE_OR_REJECT = (
    QuickReply(_B.APPROVE, _K.APPROVE_COMMANDS[0]),
    QuickReply(_B.REJECT, _K.REJECT_COMMANDS[0]),
)
_FINISHED = QuickReply(_B.FINISHED, _K.FINISH_COMMANDS[0])
_HUMAN = QuickReply(_B.HUMAN, _K.SOS_COMMANDS[0])


# PRO-232: the one entry that stands for Meta's own sample template.
DELIVERY_PROBE_KEY = "delivery_probe"


# Keyed by logical name: the catalog rows that survived the consolidation pass,
# with the two rows that were really two bodies split (P11, P13) and C3's three
# messages listed one by one. `params` numbers the variables and is what
# `send_template` sends by, so a caller's dict order never matters.
#
# The `_v2` Meta names: on 2026-11-17 the first submission went out with the
# pre-#243 wording, and the owner had those 29 templates deleted from the
# production account and the current wording resubmitted under new names, since
# a name Meta has just deleted cannot be used again straight away. The keys did
# not change, so nothing that arms or sends by key notices.
TEMPLATES: dict[str, TemplateSpec] = {
    spec.key: spec
    for spec in (
        # Professional-facing. PRO-59 unfolded P2 from P1: the navigation link is
        # no longer sent with the offer. The post-approval card needs no entry of
        # its own, since it replies inside the 24h window the pro's own אשר opened.
        TemplateSpec(
            "lead_offer",
            "proli_lead_offer_v2",
            status=TemplateStatus.APPROVED,
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
            buttons=_APPROVE_OR_REJECT,
        ),  # P1, and P3 for an emergency: this body is the one that can say so
        TemplateSpec(
            "lead_offer_reassigned",
            "proli_lead_offer_reassigned_v2",
            status=TemplateStatus.APPROVED,
            source="Messages.Pro.NEW_LEAD_HEADER + Messages.Pro.NEW_LEAD_DETAILS",
            body=Messages.Templates.LEAD_OFFER_REASSIGNED,
            params=(
                "customer_name",
                "full_address",
                "issue_type",
                "appointment_time",
            ),
            buttons=_APPROVE_OR_REJECT,
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
            "proli_approval_nudge_v2",
            status=TemplateStatus.APPROVED,
            source="Messages.Pro.APPROVAL_NUDGE",
            body=Messages.Templates.APPROVAL_NUDGE,
            params=("issue_type", "minutes"),
            buttons=_APPROVE_OR_REJECT,
        ),  # P5
        TemplateSpec(
            "daily_agenda",
            "proli_daily_agenda_v2",
            status=TemplateStatus.APPROVED,
            source="Messages.Pro.DAILY_AGENDA_HEADER",
            body=Messages.Templates.DAILY_AGENDA,
            params=("pro_name", "date", "job_count"),
            buttons=(QuickReply(_B.ACTIVE_JOBS, _K.ACTIVE_JOBS_COMMANDS[0]),),
        ),  # P6
        TemplateSpec(
            "stale_lead_reminder",
            "proli_stale_lead_reminder_v2",
            status=TemplateStatus.APPROVED,
            category="MARKETING",  # Meta's call, 2026-11-18: never armed
            source="Messages.Pro.STALE_LEAD_REMINDER",
            body=Messages.Templates.STALE_LEAD_REMINDER,
            params=("pro_name", "customer_name"),
            buttons=(_FINISHED,),
        ),  # P7
        TemplateSpec(
            "finish_reminder",
            "proli_finish_reminder_v2",
            status=TemplateStatus.APPROVED,
            source="Messages.Pro.REMINDER",
            body=Messages.Templates.FINISH_REMINDER,
            buttons=(
                _FINISHED,
                QuickReply(_B.STILL_WORKING, _K.STILL_WORKING_COMMANDS[0]),
            ),
        ),  # P8
        TemplateSpec(
            "pro_lost_lead",
            "proli_pro_lost_lead_v2",
            status=TemplateStatus.APPROVED,
            source="Messages.SOS.PRO_LOST_LEAD",
            body=Messages.Templates.PRO_LOST_LEAD,
            params=("issue_type",),
        ),  # P9
        TemplateSpec(
            "bot_paused",
            "proli_bot_paused_v2",
            status=TemplateStatus.APPROVED,
            source="Messages.Pro.PAUSE_NOTIFICATION",
            body=Messages.Templates.BOT_PAUSED,
            params=("issue_type",),
        ),  # P10
        TemplateSpec(
            "sos_alert",
            "proli_sos_alert_v2",
            status=TemplateStatus.APPROVED,
            source="Messages.SOS.PRO_ALERT",
            body=Messages.Templates.SOS_ALERT,
            params=("phone", "last_message"),
        ),  # P11, lead BOOKED
        TemplateSpec(
            "sos_alert_pending",
            "proli_sos_alert_pending_v2",
            status=TemplateStatus.APPROVED,
            source="Messages.SOS.PRO_ALERT_PENDING",
            body=Messages.Templates.SOS_ALERT_PENDING,
            buttons=_APPROVE_OR_REJECT,
        ),  # P11, lead NEW: no phone (PRO-59) and no customer text (PRO-212)
        TemplateSpec(
            "customer_cancelled",
            "proli_customer_cancelled_v2",
            status=TemplateStatus.APPROVED,
            source="Messages.Pro.CUSTOMER_CANCELLED",
            body=Messages.Templates.CUSTOMER_CANCELLED,
            params=("customer_name", "full_address"),
        ),  # P12
        TemplateSpec(
            "onboarding_approved",
            "proli_onboarding_approved_v2",
            status=TemplateStatus.APPROVED,
            source="Messages.Onboarding.APPROVED_NOTIFICATION",
            body=Messages.Templates.ONBOARDING_APPROVED,
        ),  # P13
        TemplateSpec(
            "onboarding_rejected",
            "proli_onboarding_rejected_v2",
            status=TemplateStatus.APPROVED,
            source="Messages.Onboarding.REJECTED_NOTIFICATION",
            body=Messages.Templates.ONBOARDING_REJECTED,
        ),  # P13
        TemplateSpec(
            "pro_lead_outside_trade",
            "proli_pro_lead_outside_trade_v2",
            status=TemplateStatus.APPROVED,
            category="MARKETING",  # Meta's call, 2026-11-18: never armed
            source="Messages.Pro.LEAD_OUTSIDE_TRADE",
            body=Messages.Templates.PRO_LEAD_OUTSIDE_TRADE,
            params=("issue_type",),
        ),  # P14: one body for LEAD_OUTSIDE_TRADE and LEAD_OUTSIDE_TRADE_OFFER
        # Customer-facing
        TemplateSpec(
            "pro_found",
            "proli_pro_found_v2",
            status=TemplateStatus.APPROVED,
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
            "proli_no_pro_available_v2",
            status=TemplateStatus.APPROVED,
            source="Messages.SOS.NO_PRO_AVAILABLE",
            body=Messages.Templates.NO_PRO_AVAILABLE,
            params=("issue_type",),
            buttons=(_HUMAN,),
        ),  # C1
        TemplateSpec(
            "completion_check",
            "proli_completion_check_v2",
            status=TemplateStatus.APPROVED,
            source="Messages.Customer.COMPLETION_CHECK",
            body=Messages.Templates.COMPLETION_CHECK,
            params=("pro_name",),
            buttons=(
                QuickReply(_B.JOB_DONE, "1"),
                QuickReply(_B.NOT_YET, "2"),
                QuickReply(_B.PRO_NO_SHOW, "3"),
            ),
        ),  # C2
        TemplateSpec(
            "reassigning",
            "proli_reassigning_v2",
            status=TemplateStatus.APPROVED,
            category="MARKETING",  # Meta's call, 2026-11-18: never armed
            source="Messages.SOS.CUSTOMER_REASSIGNING",
            body=Messages.Templates.REASSIGNING,
            params=("issue_type",),
        ),  # C3
        TemplateSpec(
            "max_reassignments",
            "proli_max_reassignments_v2",
            status=TemplateStatus.APPROVED,
            source="Messages.SOS.MAX_REASSIGNMENTS_REACHED",
            body=Messages.Templates.MAX_REASSIGNMENTS,
            params=("issue_type",),
        ),  # C3
        TemplateSpec(
            "pending_review",
            "proli_pending_review_v2",
            status=TemplateStatus.APPROVED,
            source="Messages.Customer.PENDING_REVIEW",
            body=Messages.Templates.PENDING_REVIEW,
            params=("issue_type",),
        ),  # C3
        # What one party's message, or a scheduler, sends the other party. The
        # sender is in-window by construction and the recipient is not, so these
        # had no catalog row until the 2026-11-14 audit. To the professional:
        TemplateSpec(
            "customer_rescheduled",
            "proli_customer_rescheduled_v2",
            status=TemplateStatus.APPROVED,
            source="Messages.Pro.CUSTOMER_RESCHEDULED_SUCCESS",
            body=Messages.Templates.CUSTOMER_RESCHEDULED,
            params=("customer_name", "full_address", "old_time", "new_time"),
        ),
        TemplateSpec(
            "customer_existing_job_query",
            "proli_customer_existing_job_query_v2",
            status=TemplateStatus.APPROVED,
            source="Messages.Pro.CUSTOMER_EXISTING_JOB_QUERY",
            body=Messages.Templates.CUSTOMER_EXISTING_JOB_QUERY,
            params=("customer_name", "issue_type", "phone"),
        ),  # lead BOOKED, so the phone is already this pro's to have (PRO-59)
        TemplateSpec(
            "customer_reported_no_show",
            "proli_customer_reported_no_show_v2",
            status=TemplateStatus.APPROVED,
            source="Messages.Pro.CUSTOMER_REPORTED_NO_SHOW",
            body=Messages.Templates.CUSTOMER_REPORTED_NO_SHOW,
        ),
        TemplateSpec(
            "customer_reported_completion",
            "proli_customer_reported_completion_v2",
            status=TemplateStatus.APPROVED,
            source="Messages.Pro.CUSTOMER_REPORTED_COMPLETION",
            body=Messages.Templates.CUSTOMER_REPORTED_COMPLETION,
            params=("customer_name",),
        ),
        # To the customer:
        TemplateSpec(
            "pro_cancelled_booking",
            "proli_pro_cancelled_booking_v2",
            status=TemplateStatus.APPROVED,
            source="Messages.Customer.PRO_CANCELLED_BOOKING",
            body=Messages.Templates.PRO_CANCELLED_BOOKING,
            buttons=(_HUMAN,),
        ),
        TemplateSpec(
            "rate_service",
            "proli_rate_service_v2",
            source="Messages.Customer.RATE_SERVICE",
            body=Messages.Templates.RATE_SERVICE,
            params=("pro_name",),
            # The owner's decision, 2026-11-17: a rating request is a survey,
            # which Meta is likely to hold as MARKETING. Its send site still
            # names it, so a window-closed rating prompt pages as before and
            # submitting it later needs only this flag.
            submit=False,
        ),  # the short line sent while the number is degraded stays free-form
        TemplateSpec(
            "reassign_offer",
            "proli_reassign_offer_v2",
            status=TemplateStatus.APPROVED,
            source="Messages.Customer.REASSIGN_OFFER",
            body=Messages.Templates.REASSIGN_OFFER,
            buttons=(
                QuickReply(_B.FIND_ANOTHER_PRO, "1"),
                QuickReply(_B.KEEP_WAITING, "2"),
            ),
        ),
        TemplateSpec(
            "awaiting_approval",
            "proli_awaiting_approval_v2",
            status=TemplateStatus.APPROVED,
            source="Messages.Customer.AWAITING_APPROVAL_TRANSPARENT",
            body=Messages.Templates.AWAITING_APPROVAL,
            params=("pro_name",),
        ),  # after a reassignment or an admin assignment; the first offer's is a reply
        TemplateSpec(
            "bot_paused_by_pro",
            "proli_bot_paused_by_pro_v2",
            status=TemplateStatus.APPROVED,
            source="Messages.Customer.BOT_PAUSED_BY_PRO",
            body=Messages.Templates.BOT_PAUSED_BY_PRO,
        ),
        # PRO-232: not a Proli message. Meta gives every test account a
        # `hello_world` template, approved and parameterless, and the test
        # account holds nothing else, so this is how staging proves a real
        # closed-window template delivery (scripts/probe_template_delivery.py).
        # No flow names this key; arm it on staging only.
        TemplateSpec(
            DELIVERY_PROBE_KEY,
            "hello_world",
            status=TemplateStatus.APPROVED,
            language="en_US",
            # The text the probe sends on an open window. The template's body is
            # Meta's own, so none is stored here.
            source="Messages.System.DELIVERY_PROBE",
            submit=False,
        ),
    )
}


# PRO-104: a reply can name the lead it answers. A button's id (an interactive
# reply) or its quick-reply payload (a template) is then "<keyword>#<lead_id>",
# and `normalize_meta_message` splits it back into the keyword, which is the
# message text, and the lead id, which travels beside it as `reply_ref`.
REPLY_REF_SEPARATOR = "#"
_REPLY_WITH_REF = re.compile(r"(.+)#([0-9a-fA-F]{24})", flags=re.S)


def buttons_for(key: str) -> tuple[QuickReply, ...]:
    """The quick replies the template ``key`` carries, ``()`` for none or an
    unknown key. Whatever its status: a free-form send of the same message offers
    the same replies on an open window (PRO-104/PRO-105)."""
    spec = TEMPLATES.get(key)
    return spec.buttons if spec else ()


def with_reply_ref(payload: str, reply_ref: str | None) -> str:
    """``payload`` carrying ``reply_ref``, or ``payload`` itself without one."""
    return f"{payload}{REPLY_REF_SEPARATOR}{reply_ref}" if reply_ref else payload


def buttons_with_reply_ref(buttons, reply_ref: str | None) -> tuple[QuickReply, ...]:
    """``buttons`` with ``reply_ref`` appended to each payload (labels unchanged)."""
    return tuple(
        QuickReply(b.label, with_reply_ref(b.payload, reply_ref)) for b in buttons
    )


def split_reply_ref(value: str) -> tuple[str, str | None]:
    """``(keyword, lead_id)`` for ``"<keyword>#<24 hex>"``, else ``(value, None)``.

    Anything else, a malformed suffix included, comes back whole: it is read as
    the words it is, exactly as before PRO-104.
    """
    match = _REPLY_WITH_REF.fullmatch(value or "")
    if not match:
        return value, None
    return match.group(1), match.group(2).lower()


def _payloads_by_label() -> dict[str, str]:
    """Every unambiguous label → payload pair across the registry."""
    pairs: dict[str, set[str]] = {}
    for spec in TEMPLATES.values():
        for button in spec.buttons:
            pairs.setdefault(button.label, set()).add(button.payload)
    return {
        label: next(iter(payloads))
        for label, payloads in pairs.items()
        if len(payloads) == 1
    }


def _warn_once(token: str, why: str) -> None:
    if token in _WARNED_ARMING_TOKENS:
        return
    _WARNED_ARMING_TOKENS.add(token)
    # ERROR in production, so a mistyped key is noticed at deploy rather than
    # as a template that never goes out. It still arms nothing.
    log = logger.error if settings.is_production else logger.warning
    log(
        f"WHATSAPP_ARMED_TEMPLATES names {token!r}, which {why}. It stays "
        "unarmed: sends naming it behave as if it had no template."
    )


def armed_keys() -> frozenset[str]:
    """The keys this environment arms: named in ``WHATSAPP_ARMED_TEMPLATES``
    **and** ``APPROVED`` here.

    Fail-safe by construction: a name the registry does not know, a DRAFT entry,
    a wildcard or a stray character arms nothing, and is reported once at
    WARNING. There is no "all": arming is always a list of names someone typed.
    Parsed per call rather than at import, so the suite can change the setting
    and the registry without reloading this module.
    """
    raw = settings.WHATSAPP_ARMED_TEMPLATES or ""
    armed = set()
    for token in _ARMED_SEPARATORS.split(raw.strip()):
        if not token:
            continue
        spec = TEMPLATES.get(token)
        if spec is None:
            _warn_once(token, "is not a key in the template registry")
        elif spec.status is not TemplateStatus.APPROVED:
            _warn_once(token, "is still DRAFT in the registry")
        else:
            armed.add(token)
    return frozenset(armed)


def armed_specs() -> list[TemplateSpec]:
    """Every armed spec, in registry order. What the quality watchdog checks."""
    keys = armed_keys()
    return [spec for key, spec in TEMPLATES.items() if key in keys]


def resolve(key: str) -> TemplateSpec | None:
    """The spec for ``key`` **iff** it is armed here and therefore sendable.

    Draft, unknown and unarmed keys all return ``None`` — the caller cannot tell
    them apart and must not: none of them can be transmitted from this
    environment, and the distinction only matters to a human reading the
    catalog or the environment's variables.
    """
    spec = TEMPLATES.get(key)
    if spec is None or spec.status is not TemplateStatus.APPROVED:
        return None
    if key not in armed_keys():
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


def advertised_replies(body: str) -> list[str]:
    """The replies a body offers, in order: each keyword it asks to be written,
    and each numbered line's digit."""
    return [m.group(1) or m.group(2) for m in _ADVERTISED_REPLY.finditer(body)]


def unread_write_prompts(body: str) -> list[str]:
    """Every "כתוב …" in ``body`` that ``advertised_replies`` does not read as a
    reply, quoted to the end of its line: a keyword the registry does not know,
    or a misspelling of one. Should be empty for every body."""
    read = {m.start() for m in _ADVERTISED_REPLY.finditer(body)}
    return [
        body[m.start() :].split("\n", 1)[0]
        for m in _WRITE_PROMPT.finditer(body)
        if m.start() not in read
    ]


def payload_for_label(label: str) -> str | None:
    """The reply a quick-reply ``label`` stands for, or ``None``.

    PRO-230: a tap normally arrives as the payload ``send_template`` attached.
    A template sent *without* button parameters — WhatsApp Manager's own test
    send, or any future caller that bypasses ``send_template`` — arrives
    carrying the button's label instead, and two customer labels
    (``REASSIGN_OFFER``'s) match no handler. This maps a label back, so a tap
    is read as the digit or keyword it stands for however it arrived.

    A label two templates disagree about is not resolvable and returns
    ``None``; the message then reaches the dispatcher as the label's own words,
    exactly as it did before this existed.

    The map is registry-global rather than per-template, so a label-only tap is
    read by its label whatever template it came from. That holds while a label
    means one thing, which the parity rule in ``button_rule_violations`` keeps
    true; the day two audiences share a label with different payloads, the
    lookup degrades to ``None`` instead of guessing.

    Built per call, not cached at import: the suite swaps ``TEMPLATES``
    wholesale with ``monkeypatch.setattr``, and a map built at import would not
    see it. Eleven entries, read only when a tap arrives without its payload.
    """
    return _payloads_by_label().get(label)


def button_rule_violations(spec: TemplateSpec) -> list[str]:
    """What Meta would refuse in ``spec``'s buttons, and where they and its body
    disagree.

    The limits are the ones above ``_MAX_QUICK_REPLIES``. The last rule is ours
    (PRO-230): every reply the body advertises has a button and no button exists
    without one, so a tap can never offer what typing could not.
    """
    problems = []
    if len(spec.buttons) > _MAX_QUICK_REPLIES:
        problems.append(f"more than {_MAX_QUICK_REPLIES} quick-reply buttons")
    labels = [b.label for b in spec.buttons]
    payloads = [b.payload for b in spec.buttons]
    for label in labels:
        if len(label) > _BUTTON_LABEL_MAX_CHARS:
            problems.append(
                f"button {label!r} is longer than {_BUTTON_LABEL_MAX_CHARS} characters"
            )
        if not _BUTTON_LABEL.fullmatch(label):
            problems.append(f"button {label!r} is not letters, digits and spaces")
    if len(set(labels)) != len(labels):
        problems.append("two buttons share a label")
    if len(set(payloads)) != len(payloads):
        problems.append("two buttons share a payload")
    if payloads != advertised_replies(spec.body):
        problems.append(
            f"buttons send {payloads} but the body advertises "
            f"{advertised_replies(spec.body)}"
        )
    return problems


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
