"""Submit the template registry to Meta and report what Meta has.

    python scripts/submit_whatsapp_templates.py                         # dry run
    python scripts/submit_whatsapp_templates.py --status --waba-id <id>
    python scripts/submit_whatsapp_templates.py --apply --waba-id <id>

The default prints every payload and touches nothing. ``--status`` reads Meta's
templates and reports drift from the registry. ``--apply`` creates whatever Meta
does not already have, and refuses an account whose name contains "Test": the
WABA currently configured, 1910054379665469, is Meta's "Test WhatsApp Business
Account", and templates created there are not the ones production will send.
No id is read from the environment, so it has to be typed.

This calls Graph's *management* API and sends no message, so it is outside what
the single-egress rule protects: the circuit breaker and the kill switch guard a
phone number, and nothing here reaches one. Same shape as
``scripts/meta_healthcheck.py``. The token goes in a header and is never printed.

Nothing here changes the registry. When ``--status`` says a template is APPROVED,
flip its entry in ``app/providers/whatsapp/template_registry.py``.

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


def account_refusal(account_name: str | None) -> str | None:
    """Why ``--apply`` must not touch this account, or ``None``."""
    if not account_name:
        return "Meta returned no account name, so it cannot be checked."
    if "test" in account_name.lower():
        return f"{account_name!r} is a test account."
    return None


def fetch_templates(client: httpx.Client, waba_id: str) -> list[dict]:
    """Every template on the account, across Graph's pages."""
    templates: list[dict] = []
    url = f"/{waba_id}/message_templates"
    while url:
        response = client.get(url)
        response.raise_for_status()
        page = response.json()
        templates.extend(page.get("data") or [])
        url = (page.get("paging") or {}).get("next")
    return templates


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
            lines.append(f"{spec.meta_name}: APPROVED on Meta, flip the registry")
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
        print(
            f"{spec.meta_name}: Meta={meta.get('status', 'NOT SUBMITTED')}, "
            f"registry={spec.status.value}"
        )
    lines = drift(to_submit(), meta_templates)
    print(f"\n{len(lines)} drift line(s).")
    for line in lines:
        print(f"  {line}")
    return 1 if lines else 0


def apply(client: httpx.Client, waba_id: str) -> int:
    account = client.get(f"/{waba_id}", params={"fields": "name"})
    if account.status_code >= 400:
        print(f"Could not read the account: {_graph_error(account)}", file=sys.stderr)
        return 1
    refusal = account_refusal(account.json().get("name"))
    if refusal:
        print(f"Refusing to submit: {refusal}", file=sys.stderr)
        return 1

    existing = {
        (t.get("name"), t.get("language")) for t in fetch_templates(client, waba_id)
    }
    failures = 0
    for spec in to_submit():
        if (spec.meta_name, spec.language) in existing:
            print(f"{spec.meta_name}: already on Meta, skipped")
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

    with client or graph_client() as graph:
        try:
            if args.status:
                return show_status(graph, args.waba_id)
            return apply(graph, args.waba_id)
        except httpx.HTTPStatusError as e:
            print(f"Graph refused: {_graph_error(e.response)}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    sys.exit(main())
