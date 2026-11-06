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


def test_early_lead_is_left_out_of_submission():
    assert not TEMPLATES["early_lead"].submit
    assert not TEMPLATES["early_lead"].body


def test_sos_alert_pending_has_no_phone():
    """PRO-59: before approval the pro must not get the customer's number."""
    assert "phone" not in TEMPLATES["sos_alert_pending"].params
    assert "phone" in TEMPLATES["sos_alert"].params


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
    assert WorkerConstants.PAUSE_TTL_SECONDS == 15 * 60


def test_pro_found_ends_on_words_with_the_phone_as_the_last_variable():
    spec = TEMPLATES["pro_found"]
    assert spec.params[-1] == "pro_phone"
    assert meta_body(spec).rstrip().endswith("בקרוב.")
    assert "*טלפון:* {pro_phone}\n" in spec.body


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
# customer_flow's completion menu reads these digits, not a Keywords list.
COMPLETION_DIGITS = {"1", "2", "3"}


@pytest.mark.parametrize("spec", SUBMITTED, ids=lambda s: s.key)
def test_every_advertised_reply_is_a_real_keyword(spec):
    # `*token* — text` is the menu format. A bold label ends in a colon instead.
    for token in re.findall(r"^\*([^*:]+)\* —", spec.body, flags=re.M):
        if token in COMPLETION_DIGITS:
            assert spec.key == "completion_check"
            continue
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


def _call_sites():
    for path in sorted((ROOT / "app").rglob("*.py")):
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
    """The defect this exists for: an approved template no send can reach.

    The admin panel's sends wait for the open panel PR, so the onboarding verdicts
    are named here until they move.
    """
    used = {k.value for _, k, _ in _call_sites() if isinstance(k, ast.Constant)}
    used |= {"lead_offer_reassigned"}  # chosen by _offer_template
    panel_only = {"onboarding_approved", "onboarding_rejected"}
    assert {spec.key for spec in SUBMITTED} - used == panel_only


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
