"""scripts/submit_whatsapp_templates.py against a fake Graph API. No real call."""

import dataclasses
import importlib.util
import pathlib
import re

import httpx
import pytest

from app.core.messages import Messages
from app.providers.whatsapp.template_registry import TEMPLATES, meta_body

_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "scripts"
    / "submit_whatsapp_templates.py"
)
_spec = importlib.util.spec_from_file_location("submit_whatsapp_templates", _PATH)
submit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(submit)

TOKEN = "super-secret-token-value"
LIVE_WABA = "111"


class FakeGraph:
    def __init__(
        self, name="Proli", templates=None, post_status=200, post_category=None
    ):
        self.name = name
        self.templates = templates or []
        self.post_status = post_status
        self.post_category = post_category
        self.next_page = None
        self.requests = []

    def _handle(self, request):
        self.requests.append(request)
        if request.method == "POST":
            if self.post_status >= 400:
                return httpx.Response(
                    self.post_status, json={"error": {"code": 100, "message": "bad"}}
                )
            created = {"id": "1", "status": "PENDING"}
            if self.post_category:
                created["category"] = self.post_category
            return httpx.Response(200, json=created)
        if request.url.path.endswith("/message_templates"):
            page = {"data": self.templates}
            if self.next_page:
                page["paging"] = {"next": self.next_page}
            return httpx.Response(200, json=page)
        return httpx.Response(200, json={"name": self.name})

    def client(self):
        return httpx.Client(
            base_url="https://graph.test/v23.0",
            headers={"Authorization": "Bearer " + TOKEN},
            transport=httpx.MockTransport(self._handle),
        )

    @property
    def posts(self):
        return [r for r in self.requests if r.method == "POST"]


def _meta(spec, status="APPROVED", **overrides):
    return {
        "name": spec.meta_name,
        "language": spec.language,
        "status": status,
        "category": spec.category,
        "components": [{"type": "BODY", "text": meta_body(spec)}],
        **overrides,
    }


def test_dry_run_prints_every_template_and_makes_no_call(capsys):
    graph = FakeGraph()

    assert submit.main([], client=graph.client()) == 0

    out = capsys.readouterr().out
    assert graph.requests == []
    for spec in submit.to_submit():
        assert spec.meta_name in out
    assert "proli_early_lead" not in out.split("Left out:")[0]
    assert "nothing was sent" in out


def test_early_lead_is_not_submitted():
    assert "early_lead" not in {spec.key for spec in submit.to_submit()}


@pytest.mark.parametrize("flag", ["--apply", "--status"])
def test_waba_id_must_be_explicit(flag):
    graph = FakeGraph()

    with pytest.raises(SystemExit) as stopped:
        submit.main([flag], client=graph.client())

    assert graph.requests == []
    # argparse's own code. The category check must not share it, or a mistyped
    # command line reads as "Meta holds a template as MARKETING".
    assert stopped.value.code == 2 != submit.EXIT_NOT_UTILITY


@pytest.mark.parametrize(
    "name",
    ["Test WhatsApp Business Account", "my test account", "TEST", "", None],
)
def test_apply_refuses_a_test_or_unnamed_account(name, capsys):
    graph = FakeGraph(name=name)

    assert (
        submit.main(
            ["--apply", "--waba-id", "1", "--confirm-name", name or "x"],
            client=graph.client(),
        )
        == 1
    )

    assert graph.posts == []
    assert "Refusing" in capsys.readouterr().err


def test_apply_creates_what_is_missing_and_skips_what_exists(capsys):
    existing = submit.to_submit()[0]
    graph = FakeGraph(templates=[_meta(existing, status="PENDING")])

    assert (
        submit.main(
            ["--apply", "--waba-id", LIVE_WABA, "--confirm-name", "Proli"],
            client=graph.client(),
        )
        == 0
    )

    assert len(graph.posts) == len(submit.to_submit()) - 1
    assert all(
        r.url.path == f"/v23.0/{LIVE_WABA}/message_templates" for r in graph.posts
    )
    out = capsys.readouterr().out
    assert f"{existing.meta_name}: already on Meta (PENDING)" in out
    assert f"Account: 'Proli', id {LIVE_WABA}" in out


def test_apply_needs_the_account_name_typed_back(capsys):
    graph = FakeGraph(name="Proli")

    with pytest.raises(SystemExit):
        submit.main(["--apply", "--waba-id", LIVE_WABA], client=graph.client())
    assert graph.requests == []

    assert (
        submit.main(
            ["--apply", "--waba-id", LIVE_WABA, "--confirm-name", "Other"],
            client=graph.client(),
        )
        == 1
    )
    assert graph.posts == []
    assert "Refusing" in capsys.readouterr().err


def test_apply_hard_refuses_the_test_account_id_whatever_it_is_called(capsys):
    graph = FakeGraph(name="Proli")

    args = ["--apply", "--waba-id", "1910054379665469", "--confirm-name", "Proli"]
    assert submit.main(args, client=graph.client()) == 1

    assert graph.requests == []
    assert "Refusing" in capsys.readouterr().err


def test_a_transport_error_is_reported_without_a_traceback_or_the_token(capsys):
    def boom(request):
        raise httpx.ConnectError("no route, Bearer " + TOKEN)

    client = httpx.Client(
        base_url="https://graph.test/v23.0", transport=httpx.MockTransport(boom)
    )

    assert submit.main(["--status", "--waba-id", LIVE_WABA], client=client) == 1

    err = capsys.readouterr().err
    assert "ConnectError" in err
    assert TOKEN not in err


def test_status_asks_for_the_fields_it_compares():
    graph = FakeGraph()

    submit.main(["--status", "--waba-id", LIVE_WABA], client=graph.client())

    request = graph.requests[0]
    assert request.url.params["fields"] == "name,language,status,category,components"


def test_apply_reports_a_failed_template_and_exits_nonzero(capsys):
    graph = FakeGraph(post_status=400)

    assert (
        submit.main(
            ["--apply", "--waba-id", LIVE_WABA, "--confirm-name", "Proli"],
            client=graph.client(),
        )
        == 1
    )

    assert "FAILED" in capsys.readouterr().err


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--status", "--waba-id", LIVE_WABA],
        ["--apply", "--waba-id", LIVE_WABA, "--confirm-name", "Proli"],
    ],
    ids=["dry-run", "status", "apply"],
)
def test_the_token_is_never_printed(args, capsys):
    submit.main(args, client=FakeGraph(post_status=400).client())

    captured = capsys.readouterr()
    assert TOKEN not in captured.out + captured.err


@pytest.mark.parametrize("spec", submit.to_submit(), ids=lambda s: s.key)
def test_payload_has_a_sample_for_every_variable_and_sequential_numbers(spec):
    payload = submit.build_payload(spec)

    assert payload["name"] == spec.meta_name
    assert payload["language"] == "he"
    assert payload["category"] == "UTILITY"
    assert len(payload["components"]) == 1
    body = payload["components"][0]
    numbers = [int(n) for n in re.findall(r"\{\{(\d+)\}\}", body["text"])]
    assert numbers == list(range(1, len(spec.params) + 1))
    assert not re.search(r"\{[a-z_]+\}", body["text"])
    if spec.params:
        samples = body["example"]["body_text"]
        assert samples == [
            [Messages.Templates.PARAM_SAMPLES[name] for name in spec.params]
        ]
        assert all(samples[0])
    else:
        assert "example" not in body


def test_lead_offer_numbers_its_variables_by_the_declared_order():
    text = meta_body(TEMPLATES["lead_offer"])

    assert "*לקוח:* {{1}}" in text
    assert "*דחיפות:* {{6}}" in text


def test_a_body_out_of_order_with_its_params_cannot_be_submitted(monkeypatch, capsys):
    import dataclasses

    spec = TEMPLATES["sos_alert"]
    broken = dataclasses.replace(spec, params=tuple(reversed(spec.params)))
    monkeypatch.setattr(submit, "TEMPLATES", {**TEMPLATES, spec.key: broken})
    graph = FakeGraph()

    assert (
        submit.main(
            ["--apply", "--waba-id", LIVE_WABA, "--confirm-name", "Proli"],
            client=graph.client(),
        )
        == 1
    )

    assert graph.requests == []
    assert "sequence" in capsys.readouterr().err


def test_status_reports_no_drift_when_meta_matches_a_draft_registry_pending():
    specs = submit.to_submit()

    lines = submit.drift(specs, [_meta(spec, status="PENDING") for spec in specs])

    assert lines == []


def test_status_names_each_kind_of_drift():
    a, b, c, d, *_ = submit.to_submit()
    approved_here = __import__("dataclasses").replace(
        b, status=submit.TemplateStatus.APPROVED
    )

    lines = submit.drift(
        [a, approved_here, c, d],
        [
            _meta(a),  # approved on Meta, draft here
            _meta(b, status="REJECTED"),  # the dangerous direction
            _meta(c, status="PENDING", category="MARKETING"),  # recategorised
        ],
    )

    assert any(a.meta_name in x and "flip the registry" in x for x in lines)
    assert any(b.meta_name in x and "REJECTED on Meta" in x for x in lines)
    assert any(c.meta_name in x and "MARKETING" in x for x in lines)
    assert any(d.meta_name in x and "not on Meta" in x for x in lines)


def test_status_notices_a_body_that_changed_after_approval():
    spec = submit.to_submit()[0]
    meta = _meta(spec, status="PENDING", components=[{"type": "BODY", "text": "אחר"}])

    assert any("body differs" in x for x in submit.drift([spec], [meta]))


def test_status_exits_nonzero_on_drift(capsys):
    graph = FakeGraph()

    assert submit.main(["--status", "--waba-id", LIVE_WABA], client=graph.client()) == 1

    assert graph.posts == []
    assert "NOT SUBMITTED" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# The category Meta holds: arming a marketing template is never a default
# ---------------------------------------------------------------------------


def _status(templates, capsys):
    graph = FakeGraph(templates=templates)
    code = submit.main(["--status", "--waba-id", LIVE_WABA], client=graph.client())
    assert graph.posts == []
    return code, capsys.readouterr()


def test_status_prints_the_category_meta_holds_for_every_template(capsys):
    specs = submit.to_submit()

    code, printed = _status([_meta(spec, status="PENDING") for spec in specs], capsys)

    assert code == 0
    for spec in specs:
        assert f"{spec.meta_name}: Meta=PENDING, category=UTILITY" in printed.out


def test_status_prints_no_category_for_a_template_meta_does_not_have(capsys):
    code, printed = _status([], capsys)

    assert code == submit.EXIT_DRIFT  # not submitted is drift, and nothing worse
    assert "Meta=NOT SUBMITTED, category=-" in printed.out
    assert "UTILITY" not in printed.err


@pytest.mark.parametrize("status", ["APPROVED", "PENDING"])
def test_status_exits_2_when_meta_holds_one_template_as_marketing(status, capsys):
    first, *rest = submit.to_submit()
    templates = [_meta(first, status=status, category="MARKETING")]
    templates += [_meta(spec, status="PENDING") for spec in rest]

    code, printed = _status(templates, capsys)

    assert code == submit.EXIT_NOT_UTILITY == 3
    assert f"{first.meta_name}: Meta={status}, category=MARKETING" in printed.out
    assert f"{first.meta_name}: MARKETING on Meta, do not flip" in printed.err
    assert "1 template(s) Meta does not hold as UTILITY" in printed.err
    assert all(spec.meta_name not in printed.err for spec in rest)


def test_status_never_says_to_flip_a_template_meta_approved_as_marketing(capsys):
    """The default this closes: reading APPROVED and arming a marketing template."""
    marketing, utility, *rest = submit.to_submit()
    templates = [_meta(marketing, category="MARKETING"), _meta(utility)]

    _, printed = _status(templates, capsys)

    assert f"{utility.meta_name}: APPROVED on Meta, flip the registry" in printed.out
    assert f"{marketing.meta_name}: APPROVED on Meta, flip" not in printed.out
    assert (
        f"{marketing.meta_name}: APPROVED on Meta as MARKETING, "
        "do not flip the registry"
    ) in printed.out


def test_a_registry_entry_edited_to_marketing_still_fails_the_category_check():
    """It agrees with Meta, so it is not drift. It must stop here regardless."""
    spec = dataclasses.replace(submit.to_submit()[0], category="MARKETING")
    meta = [_meta(spec, status="PENDING")]

    assert not any("category" in line for line in submit.drift([spec], meta))
    assert submit.not_utility([spec], meta) == [
        f"{spec.meta_name}: MARKETING on Meta, do not flip the registry"
    ]


def test_an_armed_template_meta_moved_to_marketing_is_named_as_being_sent():
    """Meta recategorises without revoking, so status alone would look healthy."""
    spec = dataclasses.replace(
        submit.to_submit()[0], status=submit.TemplateStatus.APPROVED
    )

    (line,) = submit.not_utility([spec], [_meta(spec, category="MARKETING")])

    assert "APPROVED in the registry" in line
    assert "being sent in that category" in line


@pytest.mark.parametrize("category", [None, "", "AUTHENTICATION", "marketing"])
def test_anything_but_utility_counts_including_a_missing_category(category):
    spec = submit.to_submit()[0]

    assert submit.not_utility([spec], [_meta(spec, category=category)])


def test_a_utility_template_in_any_letter_case_passes():
    spec = submit.to_submit()[0]

    assert submit.not_utility([spec], [_meta(spec, category="utility")]) == []


def test_apply_prints_the_category_meta_created_each_template_in(capsys):
    graph = FakeGraph(post_category="UTILITY")

    code = submit.main(
        ["--apply", "--waba-id", LIVE_WABA, "--confirm-name", "Proli"],
        client=graph.client(),
    )

    assert code == 0
    printed = capsys.readouterr()
    for spec in submit.to_submit():
        assert f"{spec.meta_name}: PENDING, category=UTILITY" in printed.out
    assert "outside UTILITY" not in printed.err


def test_apply_says_so_at_once_when_meta_creates_a_template_as_marketing(capsys):
    graph = FakeGraph(post_category="MARKETING")

    code = submit.main(
        ["--apply", "--waba-id", LIVE_WABA, "--confirm-name", "Proli"],
        client=graph.client(),
    )

    assert code == submit.EXIT_NOT_UTILITY
    printed = capsys.readouterr()
    assert "category=MARKETING" in printed.out
    assert "Do not flip them in the registry" in printed.err


def test_the_token_is_not_carried_to_another_host_by_a_next_link(capsys):
    """An absolute link is requested as written, bearer header and all."""
    graph = FakeGraph()
    graph.next_page = "https://elsewhere.test/v23.0/111/message_templates?after=x"

    code = submit.main(["--status", "--waba-id", LIVE_WABA], client=graph.client())

    assert code == 1
    assert [r.url.host for r in graph.requests] == ["graph.test"]
    assert "another host" in capsys.readouterr().err


def test_a_next_link_on_the_same_host_is_followed():
    pages = iter(["https://graph.test/v23.0/111/message_templates?after=x", None])
    seen = []

    def handler(request):
        seen.append(str(request.url))
        nxt = next(pages)
        page = {"data": [{"name": f"t{len(seen)}"}]}
        if nxt:
            page["paging"] = {"next": nxt}
        return httpx.Response(200, json=page)

    client = httpx.Client(
        base_url="https://graph.test/v23.0", transport=httpx.MockTransport(handler)
    )

    assert len(submit.fetch_templates(client, LIVE_WABA)) == 2
    assert seen[1].endswith("after=x")
