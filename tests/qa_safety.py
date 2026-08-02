"""Preflight safety guard for any QA script that can send REAL WhatsApp.

A script that simulates only the *inbound* leg still makes the worker send a
genuine outbound Green API message to the target number. Pointed at the production
instance with a cold recipient, that is the #1 WhatsApp yellowCard trigger
(PRO-72).

The two scripts this was written for — `smoke_test_railway.py` and
`simulate_test.py` — were **deleted** in PRO-83 and replaced by the offline
harness in `tests/e2e/`, which proves the same logic with zero real sends.
The guard is kept because the policy outlives those scripts: any future live-fire
automation (gated on the separate staging Green API instance, PRO-29) must call
`preflight_or_abort` before its first send. Manual transport verification lives in
`docs/PILOT_E2E_CHECKLIST.md` (PRO-64).

It refuses to let a caller run against the production Green API instance unless
the operator explicitly opts in, and forces confirmation before destructive DB
operations. It touches no app code and imports nothing from `app`.
"""

import os
from typing import Callable, Sequence

_BAR = "=" * 62


def is_production_instance(target_instance_id) -> bool:
    """Return True if the target Green API instance must be treated as production.

    Production is defined as "equals the live GREEN_API_INSTANCE_ID from the
    environment". If that env var is unset we cannot prove the target is a safe
    QA instance, so we fail safe and treat it as production.
    """
    live = (os.getenv("GREEN_API_INSTANCE_ID") or "").strip()
    if not live:
        return True  # cannot verify → fail safe
    return str(target_instance_id).strip() == live


def preflight_or_abort(
    *,
    instance_id,
    base_url: str,
    db_name: str,
    recipients: Sequence[str],
    allow_production: bool = False,
    destructive: bool = False,
    assume_yes: bool = False,
    _input: Callable[[str], str] = input,
    _print: Callable[..., None] = print,
) -> None:
    """Print a preflight banner and abort (SystemExit) unless it is safe to send.

    - Aborts if the target is the production instance and ``allow_production`` is
      False (pass ``--i-know-this-is-production`` to override).
    - If ``destructive`` and not ``assume_yes``, requires an interactive "yes".

    ``_input`` / ``_print`` are injectable so the guard is unit-testable.
    """
    _print(_BAR)
    _print("  PREFLIGHT — this run sends REAL WhatsApp messages")
    _print(f"  Target instance : {instance_id}")
    _print(f"  Base URL        : {base_url}")
    _print(f"  Target DB       : {db_name}")
    _print(f"  Recipients      : {', '.join(str(r) for r in recipients)}")
    if destructive:
        _print("  Destructive     : deletes this customer's leads + messages")
    _print(_BAR)

    if is_production_instance(instance_id) and not allow_production:
        _print(
            "ABORT: target is the PRODUCTION Green API instance "
            f"({instance_id}). Sending test bursts to it risks a WhatsApp yellowCard."
        )
        _print(
            "Point --instance-id at a dedicated QA instance, or pass "
            "--i-know-this-is-production to override deliberately."
        )
        raise SystemExit(3)

    if destructive and not assume_yes:
        resp = _input(
            "This will DELETE the test customer's leads/messages. Type 'yes' to continue: "
        )
        if resp.strip().lower() != "yes":
            _print("ABORT: not confirmed.")
            raise SystemExit(3)
