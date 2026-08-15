"""Meta Cloud API (PRO-89) connectivity health-check — CLI verification.

Read-only. Confirms, without sending anything, that the credentials in the
environment can actually talk to Meta and that the pieces line up:

    python scripts/meta_healthcheck.py                 # local .env
    railway run python scripts/meta_healthcheck.py     # a deployed env

    # Also probe a deployed webhook's public reachability + handshake:
    python scripts/meta_healthcheck.py --staging-url https://api-staging-e66f.up.railway.app

What it checks:
  1. Config presence (masked — never prints a secret value).
  2. Access token validity, type and expiry (Graph ``/debug_token``).
  3. App secret validity (mints an app access token).
  4. Phone-number node: status / quality / display name.
  5. WABA phone numbers + the webhook_configuration Meta has on file.
  6. WABA subscribed_apps — is our app actually subscribed?
  7. (optional) The deployed webhook: GET handshake with the real verify token,
     wrong-token 403, and unsigned-POST 403.

Every credential is read with ``.get_secret_value()`` at the point of use and
never logged (PRO-94). Exit code is 0 iff every non-optional check passed.
"""

import argparse
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings  # noqa: E402

GRAPH_ROOT = "https://graph.facebook.com"

OK = "✅"
BAD = "❌"
WARN = "⚠️"


def _mask(value: str | None) -> str:
    if not value:
        return "(unset)"
    if len(value) <= 8:
        return "set (short)"
    return f"set ({value[:4]}…{value[-4:]}, len={len(value)})"


def _waba_from_dotenv() -> str | None:
    """Fallback: pull META_WABA_ID out of the .env file directly, since Settings
    drops it as an unknown extra."""
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"
    )
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("META_WABA_ID="):
                    return line.split("=", 1)[1].strip() or None
    except OSError:
        pass
    return None


def _secret(name: str) -> str | None:
    val = getattr(settings, name, None)
    if val is None:
        return None
    return val.get_secret_value() if hasattr(val, "get_secret_value") else str(val)


def main() -> int:
    parser = argparse.ArgumentParser(description="Meta Cloud API health-check.")
    parser.add_argument(
        "--staging-url",
        default=None,
        help="Base URL of a deployed API to probe /webhook/meta (handshake + auth).",
    )
    args = parser.parse_args()

    token = _secret("META_ACCESS_TOKEN")
    app_secret = _secret("META_APP_SECRET")
    verify_token = _secret("META_VERIFY_TOKEN")
    pnid = settings.META_PHONE_NUMBER_ID
    # META_WABA_ID is console/template bookkeeping only — Settings ignores it as
    # an extra (see CLAUDE.md), so read it straight from the environment.
    waba = os.environ.get("META_WABA_ID") or _waba_from_dotenv()
    version = settings.META_GRAPH_API_VERSION
    failures = 0

    print("=" * 60)
    print("Meta Cloud API health-check")
    print("=" * 60)
    print(f"  ENVIRONMENT         : {settings.ENVIRONMENT}")
    print(f"  WHATSAPP_PROVIDER   : {settings.WHATSAPP_PROVIDER}")
    print(
        f"  WHATSAPP_DRY_RUN    : {settings.WHATSAPP_DRY_RUN}  "
        f"({'MUTED - will not transmit' if settings.WHATSAPP_DRY_RUN else 'LIVE'})"
    )
    print(f"  META_GRAPH_API_VER  : {version}")
    print(f"  META_PHONE_NUMBER_ID: {pnid or '(unset)'}")
    print(f"  META_WABA_ID        : {waba or '(unset)'}")
    print(f"  META_ACCESS_TOKEN   : {_mask(token)}")
    print(f"  META_APP_SECRET     : {_mask(app_secret)}")
    print(f"  META_VERIFY_TOKEN   : {_mask(verify_token)}")
    print()

    if not token or not pnid:
        print(f"{BAD} META_ACCESS_TOKEN and META_PHONE_NUMBER_ID are required. Stop.")
        return 1

    with httpx.Client(timeout=15.0) as client:
        # 2. token debug
        print("[2] Access token — /debug_token")
        r = client.get(
            f"{GRAPH_ROOT}/{version}/debug_token",
            params={"input_token": token, "access_token": token},
        )
        data = r.json().get("data", {})
        if data.get("is_valid"):
            exp = data.get("expires_at")
            exp_str = "never" if exp == 0 else str(exp)
            print(
                f"    {OK} valid | type={data.get('type')} | expires={exp_str} "
                f"| scopes={','.join(data.get('scopes', []))}"
            )
        else:
            failures += 1
            print(f"    {BAD} invalid token: {r.json()}")

        # 3. app secret
        if app_secret:
            print("[3] App secret — mint app access token")
            # client_id is not stored in settings; derive it from debug_token.
            app_id = data.get("app_id")
            r = client.get(
                f"{GRAPH_ROOT}/{version}/oauth/access_token",
                params={
                    "client_id": app_id,
                    "client_secret": app_secret,
                    "grant_type": "client_credentials",
                },
            )
            if r.status_code == 200 and r.json().get("access_token"):
                print(f"    {OK} app secret valid (app_id={app_id})")
            else:
                failures += 1
                print(f"    {BAD} app secret rejected: {r.json()}")
        else:
            print(
                f"[3] {WARN} META_APP_SECRET unset — skipping (webhook POST auth off)"
            )

        # 4. phone-number node
        print("[4] Phone number status")
        r = client.get(
            f"{GRAPH_ROOT}/{version}/{pnid}",
            params={
                "fields": "status,quality_rating,display_phone_number,"
                "verified_name,name_status,platform_type",
                "access_token": token,
            },
        )
        pn = r.json()
        if r.status_code == 200 and pn.get("status"):
            print(
                f"    {OK} {pn.get('display_phone_number')} "
                f"({pn.get('verified_name')}) | status={pn.get('status')} "
                f"| quality={pn.get('quality_rating')} | {pn.get('platform_type')}"
            )
        else:
            failures += 1
            print(f"    {BAD} could not read phone number: {pn}")

        # 5 + 6. WABA-scoped checks
        if waba:
            print("[5] WABA phone numbers + webhook_configuration")
            r = client.get(
                f"{GRAPH_ROOT}/{version}/{waba}/phone_numbers",
                params={
                    "fields": "display_phone_number,quality_rating,"
                    "webhook_configuration",
                    "access_token": token,
                },
            )
            body = r.json()
            if r.status_code == 200:
                for num in body.get("data", []):
                    hook = (num.get("webhook_configuration") or {}).get(
                        "application", "(none)"
                    )
                    print(
                        f"    {OK} {num.get('display_phone_number')} "
                        f"-> webhook: {hook}"
                    )
            else:
                failures += 1
                print(f"    {BAD} {body}")

            print("[6] WABA subscribed_apps")
            r = client.get(
                f"{GRAPH_ROOT}/{version}/{waba}/subscribed_apps",
                params={"access_token": token},
            )
            body = r.json()
            apps = [
                a.get("whatsapp_business_api_data", {}).get("name")
                for a in body.get("data", [])
            ]
            if apps:
                print(f"    {OK} subscribed apps: {', '.join(filter(None, apps))}")
            else:
                failures += 1
                print(f"    {BAD} no app subscribed to this WABA: {body}")
        else:
            print(f"[5/6] {WARN} META_WABA_ID unset — skipping WABA checks")

        # 7. optional deployed-webhook probe
        if args.staging_url:
            base = args.staging_url.rstrip("/")
            print(f"[7] Deployed webhook probe — {base}")
            if verify_token:
                r = client.get(
                    f"{base}/webhook/meta",
                    params={
                        "hub.mode": "subscribe",
                        "hub.verify_token": verify_token,
                        "hub.challenge": "healthcheck_123",
                    },
                )
                if r.status_code == 200 and r.text == "healthcheck_123":
                    print(f"    {OK} handshake echoes challenge (verify token matches)")
                else:
                    failures += 1
                    print(f"    {BAD} handshake HTTP {r.status_code}: {r.text!r}")
            r = client.get(
                f"{base}/webhook/meta",
                params={
                    "hub.mode": "subscribe",
                    "hub.verify_token": "WRONG",
                    "hub.challenge": "x",
                },
            )
            print(
                f"    {OK if r.status_code == 403 else BAD} wrong verify token "
                f"-> HTTP {r.status_code} (want 403)"
            )
            r = client.post(
                f"{base}/webhook/meta",
                json={"object": "whatsapp_business_account", "entry": []},
            )
            print(
                f"    {OK if r.status_code == 403 else WARN} unsigned POST "
                f"-> HTTP {r.status_code} (want 403 in prod-like)"
            )

    print()
    print("=" * 60)
    if failures:
        print(f"{BAD} {failures} check(s) FAILED.")
        return 1
    print(f"{OK} All checks passed — Proli can reach Meta.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
