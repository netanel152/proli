"""Submit the template registry to Meta and report what Meta has.

    python scripts/submit_whatsapp_templates.py                         # dry run
    python scripts/submit_whatsapp_templates.py --status --waba-id <id>
    python scripts/submit_whatsapp_templates.py --apply --waba-id <id> \\
        --confirm-name "<account name>"

The default prints every payload and touches nothing. ``--status`` reads Meta's
templates, prints the status and the category Meta holds for each, and reports
drift from the registry. It exits 2 if Meta holds any of them in a category other
than UTILITY, whatever else it found, 1 on any other drift, and 0 when the two
agree. ``--apply`` creates whatever Meta
does not already have. It prints the account's name and id, needs that name typed
back as ``--confirm-name``, and refuses an account whose name contains "Test" and
the id 1910054379665469 outright: that is Meta's test Messaging account, the one
configured today, and templates created there are not the ones production will
send. No id is read from the environment, so it has to be typed.

This calls Graph's *management* API and sends no message, so it is outside what
the single-egress rule protects: the circuit breaker and the kill switch guard a
phone number, and nothing here reaches one. Same shape as
``scripts/meta_healthcheck.py``. The token goes in a header and is never printed.

Nothing here changes the registry. When ``--status`` says to flip a template,
flip its entry in ``app/providers/whatsapp/template_registry.py``. It says so
only for one Meta approved *as UTILITY*. Meta can approve a template as MARKETING,
or move an approved one there later and leave it APPROVED, and a marketing
template costs several times a utility one and may not be delivered at all. So
arming one is a decision somebody makes knowingly, never what follows from
reading "APPROVED".

Meta's docs, read 2026-11-06, under
developers.facebook.com/documentation/business-messaging/whatsapp/:
  reference/whatsapp-business-account/template-api/ (create and list),
  templates/overview/ (positional parameters, ``example.body_text``),
  templates/template-categorization/ (recategorised stays APPROVED).
"""

import argparse
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings  # noqa: E402
from app.core.messages import Messages  # noqa: E402
from app.providers.whatsapp.template_registry import (  # noqa: E402
    TEMPLATES,
    TemplateSpec,
    TemplateStatus,
    body_rule_violations,
    meta_body,
)

GRAPH_ROOT = "https://graph.facebook.com"
TEST_WABA_ID = "1910054379665469"
TEMPLATE_FIELDS = "name,language,status,category,components"
UTILITY = "UTILITY"
EXIT_DRIFT = 1
EXIT_NOT_UTILITY = 2


def to_submit() -> list[TemplateSpec]:
    return [spec for spec in TEMPLATES.values() if spec.submit]


def build_payload(spec: TemplateSpec) -> dict:
    """The body of ``POST /{waba-id}/message_templates``."""
    body: dict = {"type": "BODY", "text": meta_body(spec)}
    if spec.params:
        body["example"] = {
            "body_text": [[Messages.Templates.PARAM_SAMPLES[n] for n in spec.params]]
        }
    return {
        "name": spec.meta_name,
        "language": spec.language,
        "category": spec.category,
        "components": [body],
    }


def invalid_bodies() -> list[str]:
    return [
        f"{spec.meta_name}: {problem}"
        for spec in to_submit()
        for problem in body_rule_violations(meta_body(spec), spec.params)
    ]


def account_refusal(account_name: str | None, confirm_name: str) -> str | None:
    """Why ``--apply`` must not touch this account, or ``None``."""
    if not account_name:
        return "Meta returned no account name, so it cannot be checked."
    if "test" in account_name.lower():
        return f"{account_name!r} is a test account."
    if account_name != confirm_name:
        return f"the account is {account_name!r}, but --confirm-name said otherwise."
    return None


def fetch_templates(client: httpx.Client, waba_id: str) -> list[dict]:
    """Every template on the account, across Graph's pages."""
    templates: list[dict] = []
    url = f"/{waba_id}/message_templates"
    params = {"fields": TEMPLATE_FIELDS}
    while url:
        response = client.get(url, params=params)
        response.raise_for_status()
        page = response.json()
        templates.extend(page.get("data") or [])
        url = (page.get("paging") or {}).get("next")
        params = None  # Graph's next link already carries them
    return templates


def meta_category(meta: dict) -> str:
    """The category Meta holds a template in. A missing one is not UTILITY."""
    return str(meta.get("category") or "UNKNOWN").upper()


def not_utility(specs: list[TemplateSpec], meta_templates: list[dict]) -> list[str]:
    """Each template Meta holds in a category other than UTILITY, one line each.

    Whatever the registry says: a spec edited to ``MARKETING`` would agree with
    Meta and so not be drift, and it must still stop here.
    """
    by_name = {(t.get("name"), t.get("language")): t for t in meta_templates}
    lines = []
    for spec in specs:
        meta = by_name.get((spec.meta_name, spec.language))
        if meta is None or meta_category(meta) == UTILITY:
            continue
        armed = (
            "it is APPROVED in the registry, so it is being sent in that category"
            if spec.status is TemplateStatus.APPROVED
            else "do not flip the registry"
        )
        lines.append(f"{spec.meta_name}: {meta_category(meta)} on Meta, {armed}")
    return lines


def drift(specs: list[TemplateSpec], meta_templates: list[dict]) -> list[str]:
    """Every way the registry and Meta disagree, one line each."""
    by_name = {(t.get("name"), t.get("language")): t for t in meta_templates}
    lines = []
    for spec in specs:
        meta = by_name.get((spec.meta_name, spec.language))
        if meta is None:
            if spec.status is TemplateStatus.APPROVED:
                lines.append(f"{spec.meta_name}: APPROVED here, not on Meta")
            else:
                lines.append(f"{spec.meta_name}: not on Meta")
            continue
        status = str(meta.get("status") or "").upper()
        if status == "APPROVED" and spec.status is not TemplateStatus.APPROVED:
            if meta_category(meta) == UTILITY:
                lines.append(f"{spec.meta_name}: APPROVED on Meta, flip the registry")
            else:
                lines.append(
                    f"{spec.meta_name}: APPROVED on Meta as {meta_category(meta)}, "
                    "do not flip the registry"
                )
        if status != "APPROVED" and spec.status is TemplateStatus.APPROVED:
            lines.append(f"{spec.meta_name}: APPROVED here, {status} on Meta")
        if meta.get("category") != spec.category:
            # Recategorising leaves a template APPROVED, so status cannot show it.
            lines.append(
                f"{spec.meta_name}: category {meta.get('category')} on Meta, "
                f"{spec.category} here"
            )
        bodies = [
            c.get("text")
            for c in meta.get("components") or []
            if str(c.get("type")).upper() == "BODY"
        ]
        if bodies != [meta_body(spec)]:
            lines.append(f"{spec.meta_name}: body differs from the registry")
    return lines


def _graph_error(response: httpx.Response) -> str:
    try:
        error = response.json().get("error") or {}
    except ValueError:
        error = {}
    return (
        f"HTTP {response.status_code}, code={error.get('code')}, "
        f"subcode={error.get('error_subcode')}, {error.get('message')!r}"
    )


def show_dry_run() -> int:
    for spec in to_submit():
        body = build_payload(spec)["components"][0]
        print(f"\n{spec.meta_name} [{spec.language}, {spec.category}]")
        print(f"  params: {', '.join(spec.params) or '(none)'}")
        print(f"  samples: {body.get('example', {}).get('body_text', [[]])[0]}")
        print("  body:")
        for line in body["text"].split("\n"):
            print(f"    {line}")
    skipped = [spec.meta_name for spec in TEMPLATES.values() if not spec.submit]
    print(f"\n{len(to_submit())} would be submitted. Left out: {', '.join(skipped)}.")
    print("Dry run: nothing was sent to Meta.")
    return 0


def show_status(client: httpx.Client, waba_id: str) -> int:
    meta_templates = fetch_templates(client, waba_id)
    by_name = {(t.get("name"), t.get("language")): t for t in meta_templates}
    for spec in to_submit():
        meta = by_name.get((spec.meta_name, spec.language)) or {}
        category = meta_category(meta) if meta else "-"
        print(
            f"{spec.meta_name}: Meta={meta.get('status', 'NOT SUBMITTED')}, "
            f"category={category}, registry={spec.status.value}"
        )
    lines = drift(to_submit(), meta_templates)
    print(f"\n{len(lines)} drift line(s).")
    for line in lines:
        print(f"  {line}")
    wrong = not_utility(to_submit(), meta_templates)
    if wrong:
        print(
            f"\n{len(wrong)} template(s) Meta does not hold as UTILITY. Arming one "
            "is a decision to pay marketing rates for it, not a default:",
            file=sys.stderr,
        )
        for line in wrong:
            print(f"  {line}", file=sys.stderr)
        return EXIT_NOT_UTILITY
    return EXIT_DRIFT if lines else 0


def apply(client: httpx.Client, waba_id: str, confirm_name: str) -> int:
    if waba_id == TEST_WABA_ID:
        print(f"Refusing to submit: {waba_id} is the test account.", file=sys.stderr)
        return 1
    account = client.get(f"/{waba_id}", params={"fields": "name"})
    if account.status_code >= 400:
        print(f"Could not read the account: {_graph_error(account)}", file=sys.stderr)
        return 1
    name = account.json().get("name")
    print(f"Account: {name!r}, id {waba_id}")
    refusal = account_refusal(name, confirm_name)
    if refusal:
        print(f"Refusing to submit: {refusal}", file=sys.stderr)
        return 1

    existing = {
        (t.get("name"), t.get("language")): t.get("status")
        for t in fetch_templates(client, waba_id)
    }
    failures = 0
    for spec in to_submit():
        if (spec.meta_name, spec.language) in existing:
            status = existing[(spec.meta_name, spec.language)]
            print(f"{spec.meta_name}: already on Meta ({status}), skipped")
            continue
        response = client.post(
            f"/{waba_id}/message_templates", json=build_payload(spec)
        )
        if response.status_code >= 400:
            failures += 1
            print(
                f"{spec.meta_name}: FAILED, {_graph_error(response)}", file=sys.stderr
            )
            continue
        print(f"{spec.meta_name}: {response.json().get('status')}")
    return 1 if failures else 0


def graph_client() -> httpx.Client:
    token = settings.META_ACCESS_TOKEN
    if token is None:
        raise SystemExit("META_ACCESS_TOKEN is not configured.")
    return httpx.Client(
        base_url=f"{GRAPH_ROOT}/{settings.META_GRAPH_API_VERSION}",
        # Concatenation: the PRO-94 convention rejects a secret on an f-string line.
        headers={"Authorization": "Bearer " + token.get_secret_value()},
        timeout=30.0,
    )


def main(argv: list[str] | None = None, client: httpx.Client | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--status", action="store_true", help="report Meta's status")
    mode.add_argument("--apply", action="store_true", help="create the templates")
    parser.add_argument("--waba-id", help="required by --status and --apply")
    parser.add_argument("--confirm-name", help="required by --apply: the account name")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Hebrew on a Windows console

    problems = invalid_bodies()
    if problems:
        print("Meta would reject these bodies:", file=sys.stderr)
        print("\n".join(problems), file=sys.stderr)
        return 1

    if not (args.status or args.apply):
        return show_dry_run()
    if not args.waba_id:
        parser.error("--status and --apply need an explicit --waba-id")
    if args.apply and not args.confirm_name:
        parser.error("--apply needs --confirm-name")

    with client or graph_client() as graph:
        try:
            if args.status:
                return show_status(graph, args.waba_id)
            return apply(graph, args.waba_id, args.confirm_name)
        except httpx.HTTPStatusError as e:
            print(f"Graph refused: {_graph_error(e.response)}", file=sys.stderr)
            return 1
        except httpx.HTTPError as e:
            # The class name only: a transport error's text can quote the request.
            print(f"Could not reach Graph: {type(e).__name__}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    sys.exit(main())
