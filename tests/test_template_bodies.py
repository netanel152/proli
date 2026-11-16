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
    DELIVERY_PROBE_KEY,
    TEMPLATES,
    QuickReply,
    TemplateSpec,
    TemplateStatus,
    advertised_replies,
    body_rule_violations,
    button_rule_violations,
    meta_body,
    payload_for_label,
    sanitize_param,
    unread_write_prompts,
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


# Deleted from the production account on 2026-11-17: the first submission, in the
# pre-#243 wording. A name Meta has just deleted cannot be reused straight away.
DELETED_ON_META = {
    f"proli_{key}"
    for key in (
        "lead_offer",
        "lead_offer_reassigned",
        "approval_nudge",
        "daily_agenda",
        "stale_lead_reminder",
        "finish_reminder",
        "pro_lost_lead",
        "bot_paused",
        "sos_alert",
        "sos_alert_pending",
        "customer_cancelled",
        "onboarding_approved",
        "onboarding_rejected",
        "pro_lead_outside_trade",
        "pro_found",
        "no_pro_available",
        "completion_check",
        "reassigning",
        "max_reassignments",
        "pending_review",
        "customer_rescheduled",
        "customer_existing_job_query",
        "customer_reported_no_show",
        "customer_reported_completion",
        "pro_cancelled_booking",
        "rate_service",
        "reassign_offer",
        "awaiting_approval",
        "bot_paused_by_pro",
    )
}


def test_no_template_reuses_a_name_deleted_on_meta():
    assert len(DELETED_ON_META) == 29
    assert {spec.meta_name for spec in TEMPLATES.values()} & DELETED_ON_META == set()


@pytest.mark.parametrize(
    "spec", [s for s in TEMPLATES.values() if s.body], ids=lambda s: s.key
)
def test_every_worded_template_is_submitted_under_its_v2_name(spec):
    assert spec.meta_name == f"proli_{spec.key}_v2"


def test_every_template_is_a_draft_utility_template_in_hebrew():
    """PRO-232: the delivery probe is Meta's own English sample, not a Proli
    template, so it is the one entry that is neither DRAFT nor Hebrew."""
    for spec in TEMPLATES.values():
        if spec.key == DELIVERY_PROBE_KEY:
            continue
        assert spec.status is TemplateStatus.DRAFT
        assert spec.category == "UTILITY"
        assert spec.language == "he"


def test_the_entries_left_out_of_submission():
    """The owner's decisions: early_lead would need a media template, and
    rate_service is a survey, which Meta is likely to hold as MARKETING
    (2026-11-17); rate_service keeps a body, so submitting it later is one
    flag. The probe is already approved on Meta's side, so submitting it
    would be wrong."""
    assert {spec.key for spec in TEMPLATES.values() if not spec.submit} == {
        "early_lead",
        "rate_service",
        DELIVERY_PROBE_KEY,
    }
    assert not TEMPLATES["early_lead"].body
    assert TEMPLATES["rate_service"].body == Messages.Templates.RATE_SERVICE
    assert len(SUBMITTED) == 28


def test_the_wrong_trade_notice_is_one_template_for_both_free_form_notices():
    """Approved 2026-11-14. It tells a pro the lead is no longer theirs, offered
    or not, so it can ask for nothing: no אשר or דחה, and "no need to act"."""
    spec = TEMPLATES["pro_lead_outside_trade"]
    assert spec.params == ("issue_type",)
    assert spec.body == (
        "עדכון מפרולי: הפנייה בנושא {issue_type} כבר לא אצלך, "
        "כי היא לא בתחום המקצוע שלך.\nאין צורך לעשות כלום."
    )
    assert "*" not in spec.body
    assert advertised_replies(spec.body) == []


def test_every_submitted_template_has_a_body():
    assert all(spec.body for spec in SUBMITTED)


def test_sos_alert_pending_has_no_variable_at_all():
    """PRO-59: before approval the pro must not get the customer's number.
    PRO-212: nor the customer's words, where they can type it."""
    assert TEMPLATES["sos_alert_pending"].params == ()
    assert "{" not in Messages.Templates.SOS_ALERT_PENDING
    assert TEMPLATES["sos_alert"].params == ("phone", "last_message")


def test_the_two_openings_the_owner_approved_for_utility_review():
    """Approved 2026-11-14: "הצעה" and the megaphone read as promotion. Reworded
    in the owner's plain voice 2026-11-17, still with neither."""
    assert Messages.Templates.LEAD_OFFER_REASSIGNED.startswith(
        "יש פנייה חדשה מפרולי שמחכה לאישור שלך.\n\n"
    )
    assert Messages.Templates.ONBOARDING_APPROVED.split("\n")[1] == (
        "מעכשיו פניות של לקוחות יגיעו אליך לכאן, לוואטסאפ."
    )


# The owner's voice for template bodies, 2026-11-17: plain text a person would
# type. The free-form open-window texts keep their own conventions.
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]")
WITH_BODY = [spec for spec in TEMPLATES.values() if spec.body]


@pytest.mark.parametrize("spec", WITH_BODY, ids=lambda s: s.key)
def test_a_template_body_has_no_emoji_em_dash_bold_or_slashed_gender(spec):
    assert not _EMOJI.search(spec.body)
    assert "—" not in spec.body
    assert "*" not in spec.body
    assert not re.search(r"\w/\w", spec.body), "slashed gender form"


@pytest.mark.parametrize("spec", WITH_BODY, ids=lambda s: s.key)
def test_proli_speaks_as_we_never_i(spec):
    """The bot's own verbs are plural: "מצאנו", "נעדכן", never "מצאתי"/"אעדכן"."""
    first_person = r"(?<!\w)(אני|אעדכן|מצאתי|הצלחתי|קיבלתי|העברתי)(?!\w)"
    assert not re.search(first_person, spec.body)


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
    """The pro gets in touch soon to arrange the visit: worded differently in the
    two channels since 2026-11-17, promising the same thing."""
    assert Messages.Customer.PRO_FOUND.endswith(
        "{pro_name} ייצור איתך קשר בקרוב לתיאום ההגעה."
    )
    assert Messages.Templates.PRO_FOUND.endswith(
        "איש המקצוע יתקשר אליך בקרוב כדי לתאם הגעה."
    )
    assert "לתיאום ההגעה" in Messages.Pro.CONTACT_CARD_CLOSING


def test_pro_found_ends_on_words_with_the_phone_as_the_last_variable():
    spec = TEMPLATES["pro_found"]
    assert spec.params[-1] == "pro_phone"
    assert meta_body(spec).rstrip().endswith("כדי לתאם הגעה.")
    assert "טלפון: {pro_phone}\n" in spec.body
    assert spec.params.index("price_estimate") == len(spec.params) - 2


def test_the_two_max_reassignment_messages_promise_the_same_thing():
    """Style guide §7: nothing measures an hour, so neither claims one."""
    assert "נחזור אליך בהקדם בשעות הפעילות" in Messages.SOS.MAX_REASSIGNMENTS_REACHED
    assert "ונחזור אליך בשעות הפעילות." in Messages.Templates.MAX_REASSIGNMENTS
    for text in (
        Messages.SOS.MAX_REASSIGNMENTS_REACHED,
        Messages.Templates.MAX_REASSIGNMENTS,
    ):
        assert "תוך שעה" not in text


def test_the_rejected_copy_says_the_same_thing_in_both_channels():
    """Word for word until 2026-11-17, when the template took the owner's plain
    wording and the free-form text kept its own. Both still say not approved at
    this stage, and where to ask."""
    for text in (
        Messages.Onboarding.REJECTED_NOTIFICATION,
        Messages.Templates.ONBOARDING_REJECTED,
    ):
        assert "הפרופיל שלך בפרולי לא אושר בשלב" in text
        assert "info@proli.me" in text


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
# customer_flow's completion menu, and for the reassignment offer
# `customer_flow.handle_reassign_offer_reply` (PRO-238), which the soft hold and
# the pipeline both call, so the digits work whether or not the customer is still
# AWAITING_PRO_APPROVAL.
DIGIT_MENUS = {
    "completion_check": ["1", "2", "3"],
    "reassign_offer": ["1", "2"],
}


@pytest.mark.parametrize("spec", SUBMITTED, ids=lambda s: s.key)
def test_every_advertised_reply_is_a_real_keyword(spec):
    tokens = advertised_replies(spec.body)
    if spec.key in DIGIT_MENUS:
        assert tokens == DIGIT_MENUS[spec.key]
        return
    for token in tokens:
        assert token in ADVERTISED, f"{spec.key} advertises {token!r}"
        assert token in ADVERTISED[token]


def test_every_keyword_in_the_table_is_really_advertised():
    advertised = {t for spec in SUBMITTED for t in advertised_replies(spec.body)}
    assert set(ADVERTISED) <= advertised


@pytest.mark.parametrize("spec", WITH_BODY, ids=lambda s: s.key)
def test_every_write_prompt_in_a_body_is_read_as_a_reply(spec):
    """A "כתוב …" the parser cannot read would advertise a reply with no button
    and slip past the parity rule. "לכתוב לנו" is the one exception."""
    assert unread_write_prompts(spec.body) == []


@pytest.mark.parametrize(
    "body, replies",
    [
        ("לאישור כתוב אשר, לדחייה כתוב דחה.", ["אשר", "דחה"]),
        ("אם סיימת, כתוב סיימתי ונסגור אותה.", ["סיימתי"]),
        ("כתוב סיימתי. אם לא, כתוב עדיין עובד ונפסיק.", ["סיימתי", "עדיין עובד"]),
        ("אפשר לכתוב לנו כאן, או לכתוב נציג.", ["נציג"]),
        ("כתוב עבודות.", ["עבודות"]),
        ("כתוב אשרר.", []),  # a misspelling is no reply ...
        ("כתבו אשר.", []),  # ... nor is another verb form
        ("לנציג אנושי", []),
        ("במספר:\n1 - כן\n2 - לא", ["1", "2"]),
        ("מ-1 (גרוע) עד 5 (מצוין)", []),
        ("שורה 1 - באמצע", []),
    ],
)
def test_advertised_replies_reads_the_plain_wording(body, replies):
    assert advertised_replies(body) == replies


@pytest.mark.parametrize(
    "body, unread",
    [
        ("כתוב אשרר ונמשיך.", ["כתוב אשרר ונמשיך."]),
        ("לכתוב משהו", ["לכתוב משהו"]),
        ("לכתוב לנו כאן, כתוב אשר", []),
    ],
)
def test_unread_write_prompts_finds_what_the_parser_skipped(body, unread):
    assert unread_write_prompts(body) == unread


# ---------------------------------------------------------------------------
# PRO-230: quick-reply buttons
# ---------------------------------------------------------------------------

# Sent to a professional. Their payloads are keywords: a bare digit opens a
# numbered list rather than acting once outside one (PRO-212).
PRO_BUTTONED = [
    "lead_offer",
    "lead_offer_reassigned",
    "approval_nudge",
    "sos_alert_pending",
    "stale_lead_reminder",
    "finish_reminder",
    "daily_agenda",
]


@pytest.mark.parametrize("spec", SUBMITTED, ids=lambda s: s.key)
def test_buttons_pass_metas_rules_and_match_the_body(spec):
    assert button_rule_violations(spec) == []


def _spec(buttons, advertised):
    """A spec whose body advertises exactly ``advertised``, as numbered lines."""
    body = "\n".join(f"{token} - טקסט" for token in advertised) or "בלי תפריט"
    return TemplateSpec(
        "probe",
        "proli_probe",
        body=body,
        buttons=tuple(QuickReply(label, payload) for label, payload in buttons),
    )


@pytest.mark.parametrize(
    "buttons, advertised, problem",
    [
        (
            [(f"label{i}", str(i)) for i in range(11)],
            [str(i) for i in range(11)],
            "more than",
        ),
        ([("א" * 26, "1")], ["1"], "longer than"),
        ([("כן, הסתיים", "1")], ["1"], "letters, digits"),
        ([("אשר ✅", "1")], ["1"], "letters, digits"),
        ([("כן  הסתיים", "1")], ["1"], "letters, digits"),
        ([("אשר\n", "1")], ["1"], "letters, digits"),
        ([("אשר", "1"), ("אשר", "2")], ["1", "2"], "share a label"),
        ([("אשר", "1"), ("דחה", "1")], ["1", "1"], "share a payload"),
        ([("אשר", "1")], [], "advertises"),
        ([], ["1"], "advertises"),
    ],
    ids=[
        "eleven",
        "26-chars",
        "comma",
        "emoji",
        "double-space",
        "trailing-newline",
        "duplicate-label",
        "duplicate-payload",
        "button-without-reply",
        "reply-without-button",
    ],
)
def test_the_button_rules_catch_what_they_claim_to(buttons, advertised, problem):
    assert any(
        problem in found for found in button_rule_violations(_spec(buttons, advertised))
    )


def test_every_label_in_the_registry_stands_for_one_payload():
    """`payload_for_label` maps a tap that arrived with only its label back to a
    reply, which is sound only if no two templates give a label two payloads."""
    payloads_by_label: dict[str, set[str]] = {}
    for spec in TEMPLATES.values():
        for button in spec.buttons:
            payloads_by_label.setdefault(button.label, set()).add(button.payload)

    assert payloads_by_label
    for label, payloads in payloads_by_label.items():
        assert len(payloads) == 1, f"{label!r} stands for {payloads}"
        assert payload_for_label(label) == next(iter(payloads))


def test_payload_for_label_knows_nothing_about_a_label_no_template_carries():
    assert payload_for_label("תווית לא מוכרת") is None
    assert payload_for_label("") is None


def test_a_label_two_templates_disagree_about_has_no_payload(monkeypatch):
    """The ambiguous case is refused rather than guessed. A second template sends
    `אשר` as a different payload; the map is read per call, so patching the
    registry is seen."""
    from app.providers.whatsapp import template_registry

    clash = TemplateSpec(
        "clash",
        "proli_clash",
        buttons=(QuickReply(Messages.Templates.Buttons.APPROVE, "9"),),
    )
    monkeypatch.setattr(template_registry, "TEMPLATES", {**TEMPLATES, "clash": clash})

    assert payload_for_label(Messages.Templates.Buttons.APPROVE) is None
    assert (
        payload_for_label(Messages.Templates.Buttons.REJECT)
        == Messages.Keywords.REJECT_COMMANDS[0]
    )


def test_a_label_of_exactly_25_letters_and_a_single_space_is_accepted():
    assert (
        button_rule_violations(_spec([("א" * 12 + " " + "ב" * 12, "1")], ["1"])) == []
    )


@pytest.mark.parametrize("key", PRO_BUTTONED)
def test_a_pro_button_sends_the_keyword_never_a_bare_digit(key):
    """The payload is what the pro would type: in `Messages.Keywords`, and not a
    digit that outside a numbered list only opens one (PRO-212)."""
    spec = TEMPLATES[key]
    assert spec.buttons
    for button in spec.buttons:
        assert not button.payload.isdigit(), key
        assert button.payload in ADVERTISED, f"{key} sends {button.payload!r}"
        assert button.payload in ADVERTISED[button.payload]


def test_the_templates_sent_to_a_customer_send_their_menu_digits():
    customer = {spec.key for spec in SUBMITTED if spec.buttons} - set(PRO_BUTTONED)
    # Two answer a numbered menu, two ask for a person (a keyword in
    # `Messages.Keywords.SOS_COMMANDS`).
    assert customer == {
        "completion_check",
        "reassign_offer",
        "no_pro_available",
        "pro_cancelled_booking",
    }
    for key in DIGIT_MENUS:
        assert [b.payload for b in TEMPLATES[key].buttons] == DIGIT_MENUS[key]
    for key in ("no_pro_available", "pro_cancelled_booking"):
        (button,) = TEMPLATES[key].buttons
        assert button.payload in Messages.Keywords.SOS_COMMANDS


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
PROVIDER_PACKAGE = pathlib.Path("app/providers/whatsapp")


def _call_sites():
    for root in SEND_ROOTS:
        for path in sorted((ROOT / root).rglob("*.py")):
            if PROVIDER_PACKAGE in path.relative_to(ROOT).parents:
                # The package being called, not a caller: `DryRunProvider` passes
                # `template_key=` on to a log entry, and no send is made from here.
                continue
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


# Named at a send site though not submitted, each with its reason. A draft key
# resolves to None, so the send behaves exactly as one naming no template.
NAMED_BUT_NOT_SUBMITTED = {
    # Left out 2026-11-17 as a MARKETING risk; worded and named so submitting it
    # later is the one `submit` flag.
    "rate_service",
}


def test_no_call_site_names_a_template_that_is_not_submitted():
    """A key with no body can never be approved, so naming it would be a promise
    the registry cannot keep. The one exception is listed with its reason."""
    used = {k.value for _, k, _ in _call_sites() if isinstance(k, ast.Constant)}
    assert used - {spec.key for spec in SUBMITTED} == NAMED_BUT_NOT_SUBMITTED
    for key in NAMED_BUT_NOT_SUBMITTED:
        assert TEMPLATES[key].body


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


def test_the_rating_template_offers_what_the_free_form_prompt_offers():
    """Word for word until 2026-11-17. Not submitted now, but kept honest: its
    1 to 5 and דלג are read by the same handler as the free-form prompt's."""
    spec = TEMPLATES["rate_service"]
    assert not spec.submit and spec.params == ("pro_name",)
    for text in (Messages.Templates.RATE_SERVICE, Messages.Customer.RATE_SERVICE):
        assert "1" in text and "5" in text and "דלג" in text
    assert advertised_replies(spec.body) == ["דלג"]
    assert "דלג" in Messages.Keywords.SKIP_TOKENS


def test_only_two_templates_carry_the_customers_phone():
    """PRO-59: both go to the pro of a BOOKED lead. A third needs the same proof."""
    with_phone = sorted(k for k, spec in TEMPLATES.items() if "phone" in spec.params)
    assert with_phone == ["customer_existing_job_query", "sos_alert"]
