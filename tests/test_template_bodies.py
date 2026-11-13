"""Every template body against Meta's review rules, and every call site against
its spec.

The rules are in ``template_registry.body_rule_violations``, with the docs they
come from. What is pinned here is that the registry passes them, that the check
actually catches each rule, and that nothing a call site passes can fail at
runtime.
"""

import ast
import pathlib
import re

import pytest

from app.core.constants import WorkerConstants
from app.core.messages import Messages
from app.providers.whatsapp.template_registry import (
    TEMPLATES,
    TemplateStatus,
    body_rule_violations,
    meta_body,
    sanitize_param,
)
from app.services import notification_service

SUBMITTED = [spec for spec in TEMPLATES.values() if spec.submit]
ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("spec", SUBMITTED, ids=lambda s: s.key)
def test_body_passes_metas_review_rules(spec):
    assert body_rule_violations(meta_body(spec), spec.params) == []


@pytest.mark.parametrize(
    "body, params, problem",
    [
        ("{{1}} היי לך שם וברוכים הבאים", ("a",), "starts with"),
        ("היי לך שם וברוכים הבאים {{1}}", ("a",), "ends with"),
        ("היי לך שם וברוכים הבאים {{1}}.", ("a",), "ends with"),
        ("היי לך שם {{1}} {{2}} וברוכים הבאים", ("a", "b"), "adjacent"),
        ("היי לך שם {{1}}, {{2}} וברוכים הבאים", ("a", "b"), "adjacent"),
        ("היי {{2}} לך שם {{1}} וברוכים הבאים", ("a", "b"), "sequence"),
        ("היי {{2}} לך שם וברוכים הבאים", ("a",), "sequence"),
        ("היי {{#1}} לך שם וברוכים הבאים", (), "form"),
        ("היי {{$1}} לך שם וברוכים הבאים", (), "form"),
        ("היי {{%1}} לך שם וברוכים הבאים", (), "form"),
        ("היי {name} לך שם וברוכים הבאים", (), "form"),
        ("היי {{1} לך שם וברוכים הבאים", ("a",), "form"),
        ("היי {{1}} לך שם", ("a", "b"), "params"),
        ("א {{1}} ב {{2}} ג {{3}} ד", ("a", "b", "c"), "too many"),
        ("א" * 1025, (), "longer"),
    ],
)
def test_the_rules_catch_what_they_claim_to(body, params, problem):
    assert any(problem in found for found in body_rule_violations(body, params))


def test_bodies_differ_because_meta_rejects_identical_wording():
    bodies = [meta_body(spec) for spec in SUBMITTED]
    assert len(set(bodies)) == len(bodies)


def test_meta_names_are_unique_and_legal():
    names = [spec.meta_name for spec in TEMPLATES.values()]
    assert len(set(names)) == len(names)
    assert all(re.fullmatch(r"[a-z0-9_]+", name) for name in names)


def test_every_template_is_a_draft_utility_template_in_hebrew():
    for spec in TEMPLATES.values():
        assert spec.status is TemplateStatus.DRAFT
        assert spec.category == "UTILITY"
        assert spec.language == "he"


def test_early_lead_is_the_one_entry_left_out_of_submission():
    """The owner's decision: it would need a media template."""
    assert [spec.key for spec in TEMPLATES.values() if not spec.submit] == [
        "early_lead"
    ]
    assert not TEMPLATES["early_lead"].body
    assert len(SUBMITTED) == 29


def test_the_wrong_trade_notice_is_one_template_for_both_free_form_notices():
    """Approved 2026-11-14. It tells a pro the lead is no longer theirs, offered
    or not, so it can ask for nothing: no אשר or דחה, and "no need to act"."""
    spec = TEMPLATES["pro_lead_outside_trade"]
    assert spec.params == ("issue_type",)
    assert spec.body == (
        "עדכון מפרולי: הפנייה בנושא {issue_type} כבר לא משויכת אליך, "
        "כי היא מחוץ לתחום המקצוע שלך.\nאין צורך לפעול."
    )
    assert "*" not in spec.body


def test_every_submitted_template_has_a_body():
    assert all(spec.body for spec in SUBMITTED)


def test_sos_alert_pending_has_no_variable_at_all():
    """PRO-59: before approval the pro must not get the customer's number.
    PRO-212: nor the customer's words, where they can type it."""
    assert TEMPLATES["sos_alert_pending"].params == ()
    assert "{" not in Messages.Templates.SOS_ALERT_PENDING
    assert TEMPLATES["sos_alert"].params == ("phone", "last_message")


def test_the_two_openings_the_owner_approved_for_utility_review():
    """Approved 2026-11-14: "הצעה" and the megaphone read as promotion."""
    assert Messages.Templates.LEAD_OFFER_REASSIGNED.startswith(
        "📋 *פנייה חדשה מפרולי ממתינה לאישורך*\n\n"
    )
    assert Messages.Templates.ONBOARDING_APPROVED.split("\n")[1] == (
        "מעכשיו יישלחו אליך כאן בוואטסאפ פניות של לקוחות."
    )


@pytest.mark.parametrize("spec", SUBMITTED, ids=lambda s: s.key)
def test_no_body_carries_the_promotional_signals_that_were_removed(spec):
    assert "הצע" not in spec.body  # הצעה, הצעת, הצעות
    assert "📢" not in spec.body


@pytest.mark.parametrize(
    "text",
    [
        Messages.Templates.BOT_PAUSED,
        Messages.Pro.PAUSE_NOTIFICATION,
        Messages.Pro.PAUSE_ACK,
    ],
)
def test_every_pause_message_states_the_real_pause_length(text):
    assert "רבע שעה" in text
    assert "שעתיים" not in text
    assert "בוואטסאפ" not in text  # the pro is not in that chat
    assert WorkerConstants.PAUSE_TTL_SECONDS == 15 * 60


def test_both_channels_promise_the_customer_the_same_thing():
    promise = "ייצור איתך קשר בקרוב לתיאום ההגעה."
    assert Messages.Customer.PRO_FOUND.endswith("{pro_name} " + promise)
    assert Messages.Templates.PRO_FOUND.endswith(promise)
    assert "לתיאום ההגעה" in Messages.Pro.CONTACT_CARD_CLOSING


def test_pro_found_ends_on_words_with_the_phone_as_the_last_variable():
    spec = TEMPLATES["pro_found"]
    assert spec.params[-1] == "pro_phone"
    assert meta_body(spec).rstrip().endswith("לתיאום ההגעה.")
    assert "*טלפון:* {pro_phone}\n" in spec.body
    assert spec.params.index("price_estimate") == len(spec.params) - 2


def test_the_two_max_reassignment_messages_promise_the_same_thing():
    """Style guide §7: nothing measures an hour, so neither claims one."""
    for text in (
        Messages.SOS.MAX_REASSIGNMENTS_REACHED,
        Messages.Templates.MAX_REASSIGNMENTS,
    ):
        assert "נחזור אליך בהקדם בשעות הפעילות" in text
        assert "תוך שעה" not in text


def test_the_rejected_copy_is_the_same_in_both_channels():
    assert (
        Messages.Onboarding.REJECTED_NOTIFICATION
        == Messages.Templates.ONBOARDING_REJECTED
    )


# ---------------------------------------------------------------------------
# Keywords: every reply a body advertises has a handler
# ---------------------------------------------------------------------------

_K = Messages.Keywords
ADVERTISED = {
    "אשר": _K.APPROVE_COMMANDS,
    "דחה": _K.REJECT_COMMANDS,
    "סיימתי": _K.FINISH_COMMANDS,
    "עדיין עובד": _K.STILL_WORKING_COMMANDS,
    "עבודות": _K.ACTIVE_JOBS_COMMANDS,
    "נציג": _K.SOS_COMMANDS,
}
# Numbered menus are read as digits by their own handler, not a Keywords list:
# customer_flow's completion menu, and `guard_pro_approval_soft_hold` for the
# reassignment offer (while the customer is still AWAITING_PRO_APPROVAL).
DIGIT_MENUS = {
    "completion_check": ["1", "2", "3"],
    "reassign_offer": ["1", "2"],
}


@pytest.mark.parametrize("spec", SUBMITTED, ids=lambda s: s.key)
def test_every_advertised_reply_is_a_real_keyword(spec):
    # `*token* — text` is the menu format. A bold label ends in a colon instead.
    tokens = re.findall(r"^\*([^*:]+)\* —", spec.body, flags=re.M)
    if spec.key in DIGIT_MENUS:
        assert tokens == DIGIT_MENUS[spec.key]
        return
    for token in tokens:
        assert token in ADVERTISED, f"{spec.key} advertises {token!r}"
        assert token in ADVERTISED[token]


def test_every_keyword_in_the_table_is_really_advertised():
    bodies = "\n".join(spec.body for spec in SUBMITTED)
    for token in ADVERTISED:
        assert f"*{token}* —" in bodies


# ---------------------------------------------------------------------------
# Variables: samples, rendering, and what the call sites supply
# ---------------------------------------------------------------------------


def test_every_variable_has_a_usable_sample():
    samples = Messages.Templates.PARAM_SAMPLES
    used = {name for spec in SUBMITTED for name in spec.params}
    assert used == set(samples)
    for name, sample in samples.items():
        assert sample and sanitize_param(sample) == sample, name
        assert not re.search(r"[#$%]", sample), name


def test_each_name_means_one_thing_across_the_registry():
    """`address` beside `full_address` is how a call site supplies the wrong one."""
    used = {name for spec in SUBMITTED for name in spec.params}
    assert "address" not in used
    assert "full_address" in used


class _SendSites(ast.NodeVisitor):
    def __init__(self):
        self.sites = []

    def visit_Call(self, node):
        kwargs = {k.arg: k.value for k in node.keywords if k.arg}
        key = kwargs.get("template_key")
        if key is not None:
            self.sites.append((key, kwargs.get("params")))
        self.generic_visit(node)


# The worker's sends and the admin panel's: an operator's click is as
# business-initiated as a scheduler tick.
SEND_ROOTS = ("app", "admin_panel")


def _call_sites():
    for root in SEND_ROOTS:
        for path in sorted((ROOT / root).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            visitor = _SendSites()
            visitor.visit(tree)
            for key, params in visitor.sites:
                yield path.relative_to(ROOT).as_posix(), key, params


def test_every_call_site_supplies_exactly_its_spec_params():
    checked = 0
    for path, key, params in _call_sites():
        if not isinstance(key, ast.Constant):
            # `template_key=template_key`: notify_pro_new_lead, tested just below.
            assert path == "app/services/notification_service.py"
            continue
        checked += 1
        spec = TEMPLATES[key.value]
        supplied = (
            {k.value for k in params.keys} if isinstance(params, ast.Dict) else set()
        )
        assert params is None or isinstance(params, ast.Dict), f"{path}: {key.value}"
        assert supplied == set(spec.params), f"{path}: {key.value}"
    assert checked


@pytest.mark.parametrize(
    "lead, expected",
    [
        ({"is_emergency": False}, "lead_offer_reassigned"),
        ({"is_emergency": True}, "lead_offer"),
    ],
)
def test_the_chosen_offer_template_gets_its_params(lead, expected):
    key, params = notification_service._offer_template(lead)
    assert key == expected
    assert set(params) == set(TEMPLATES[key].params)
    assert all(params.values())


def test_every_submitted_template_is_reachable_from_a_call_site():
    """The defect this exists for: an approved template no send can reach."""
    used = {k.value for _, k, _ in _call_sites() if isinstance(k, ast.Constant)}
    used |= {"lead_offer_reassigned"}  # chosen by _offer_template
    assert {spec.key for spec in SUBMITTED} - used == set()


def test_no_call_site_names_a_template_that_is_not_submitted():
    """A key with no body can never be approved, so naming it would be a promise
    the registry cannot keep."""
    used = {k.value for _, k, _ in _call_sites() if isinstance(k, ast.Constant)}
    assert used <= {spec.key for spec in SUBMITTED}


def test_the_admin_panel_names_a_template_on_each_of_its_sends():
    """The panel's three direct sends. Its lead offers go through
    `admin_flow.assign_lead_to_pro`, so `notify_pro_new_lead` names theirs."""
    panel = {
        (path, key.value)
        for path, key, _ in _call_sites()
        if path.startswith("admin_panel/")
    }
    assert panel == {
        ("admin_panel/views/professionals.py", "onboarding_approved"),
        ("admin_panel/views/professionals.py", "onboarding_rejected"),
        ("admin_panel/core/utils.py", "completion_check"),
    }


# ---------------------------------------------------------------------------
# sanitize_param
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("נזילה", "נזילה"),
        ("שורה\nשנייה", "שורה שנייה"),
        ("שורה\r\n\r\nשנייה", "שורה שנייה"),
        ("טאב\tכאן", "טאב כאן"),
        ("הרבה     רווחים", "הרבה רווחים"),
        ("שלושה   רווחים", "שלושה   רווחים"),
        (10, "10"),
    ],
)
def test_sanitize_param_flattens_what_meta_may_refuse(raw, expected):
    assert sanitize_param(raw) == expected


def test_line_break_pattern_is_written_with_visible_escapes():
    source = (ROOT / "app/providers/whatsapp/template_registry.py").read_text(
        encoding="utf-8"
    )
    assert "\u2028" not in source and "\u2029" not in source


@pytest.mark.parametrize(
    "name, limit",
    [("last_message", 700), ("full_address", 120), ("customer_name", 120)],
)
def test_sanitize_param_cuts_a_value_at_its_limit(name, limit):
    assert sanitize_param("א" * 4000, name) == "א" * limit


def test_the_largest_body_fits_with_every_variable_at_its_limit():
    # Meta's 1024 is stated for the template's body text. Staying inside it at
    # runtime as well is the safe reading.
    for spec in SUBMITTED:
        rendered = meta_body(spec)
        for number, name in enumerate(spec.params, 1):
            value = sanitize_param("א" * 4000, name)
            rendered = rendered.replace("{{" + str(number) + "}}", value)
        assert len(rendered) <= 1024, spec.key


@pytest.mark.parametrize("raw", ["", "   ", "\n\t\n", None])
def test_sanitize_param_never_returns_an_empty_value(raw):
    assert sanitize_param(raw) == Messages.Fallbacks.UNKNOWN


# ---------------------------------------------------------------------------
# The nine cross-party notices approved on 2026-11-14
# ---------------------------------------------------------------------------

CROSS_PARTY = {
    "pro_cancelled_booking": (),
    "customer_rescheduled": ("customer_name", "full_address", "old_time", "new_time"),
    "customer_existing_job_query": ("customer_name", "issue_type", "phone"),
    "rate_service": ("pro_name",),
    "customer_reported_no_show": (),
    "reassign_offer": (),
    "awaiting_approval": ("pro_name",),
    "customer_reported_completion": ("customer_name",),
    "bot_paused_by_pro": (),
}


@pytest.mark.parametrize("key, params", CROSS_PARTY.items(), ids=list(CROSS_PARTY))
def test_a_cross_party_notice_is_submitted_with_its_variables_in_body_order(
    key, params
):
    spec = TEMPLATES[key]
    assert spec.submit and spec.params == params
    found = re.findall(r"\{(\w+)\}", spec.body)
    assert tuple(found) == params


def test_the_rating_template_is_the_free_form_prompt_word_for_word():
    """Its digits and *דלג* are read by the same handler either way."""
    assert Messages.Templates.RATE_SERVICE == Messages.Customer.RATE_SERVICE


def test_only_two_templates_carry_the_customers_phone():
    """PRO-59: both go to the pro of a BOOKED lead. A third needs the same proof."""
    with_phone = sorted(k for k, spec in TEMPLATES.items() if "phone" in spec.params)
    assert with_phone == ["customer_existing_job_query", "sos_alert"]
