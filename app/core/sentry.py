"""Shared Sentry initialization + payload scrubbing for all three services.

Replaces the two hand-synced ``_init_sentry`` copies (``app/main.py`` /
``app/worker.py``) and the third mirror in ``scripts/fire_test_page.py``.
The PRO-113 hardening (LoguruIntegration disable + post-init self-check,
``include_local_variables=False``) lives here once instead of rotting
asymmetrically across copies.

Two things this module adds beyond the dedup:

* ``auto_enabling_integrations=False``. sentry-sdk 2.x auto-enables every
  integration whose package is importable — FastApi, Starlette, Arq, PyMongo,
  Redis, Httpx — while the old copies only disabled Loguru. In production
  that meant unhandled API exceptions and failed ARQ jobs were already
  reaching Sentry *unscrubbed*: exception values can echo a credentialed
  Mongo URI, httpx breadcrumbs can carry a token in a URL (the PRO-79
  class). Integrations are now an explicit per-service allowlist.

* ``before_send=_scrub_event``. ``_pii_filter`` is a loguru sink filter and
  can never touch a Sentry payload; ``page_critical`` compensates by
  scrubbing inline, but that only covers its own message string. The
  ``before_send`` hook applies the same ``redact_secrets`` → ``mask_pii``
  pipeline (plus structural URI-credential stripping) to every string leaf
  of every outgoing event, so the PII guarantee holds for payloads that
  never went through ``page_critical`` — exception values, breadcrumbs,
  request context.

Import hygiene: no ``sentry_sdk`` import at module scope. The no-DSN path
must never import the SDK at all — ``tests/test_fire_test_page.py`` asserts
this, and it keeps tests/local dev fully Sentry-free.
"""

import logging
import re
import time

from app.core.config import settings
from app.core.logger import logger, mask_pii, redact_secrets

# Userinfo in ANY URL: mongodb+srv://user:p%40ss@host, redis://:secret@host…
# redact_secrets only matches the exact SecretStr values from Settings; a
# driver error can echo a *variant* (percent-encoded password, reordered
# params) that no longer contains the literal secret. Stripping the userinfo
# section structurally catches every variant without knowing the secret.
_URI_CREDS = re.compile(r"://[^/@\s]+@")

# Cap for the event walker. Sentry events are shallow (~6 levels in
# practice); 12 is generous headroom, and anything deeper is not worth
# scrubbing string-by-string — the outer guard in _scrub_event decides
# whether such an event may ship at all.
_MAX_WALK_DEPTH = 12

_initialized = False
_active = False


def sentry_active() -> bool:
    """True only when ``init_sentry`` ran and a DSN-backed client exists."""
    return _active


def _scrub_string(value: str) -> str:
    """Secrets first, then PII, then structural URI-credential stripping —
    same ordering rationale as ``_pii_filter`` (a secret containing a
    ``972…`` digit run would be mangled by ``mask_pii`` and no longer match
    ``redact_secrets``)."""
    return _URI_CREDS.sub("://***@", mask_pii(redact_secrets(value)))


def _walk(obj, depth: int = 0, seen: set | None = None):
    """Scrub every string leaf of a Sentry event in place-agnostic fashion.

    Only string *leaves* are rewritten — keys and non-string scalars pass
    through untouched — so the walk cannot break event structure, and it is
    immune to SDK schema drift (fields added by future sentry-sdk versions
    are covered automatically). A leaf that fails to scrub is replaced with
    a placeholder rather than kept: guarantee over visibility.
    """
    if seen is None:
        seen = set()
    if isinstance(obj, str):
        try:
            return _scrub_string(obj)
        except Exception:
            return "[scrub-error]"
    if depth >= _MAX_WALK_DEPTH:
        return obj
    if isinstance(obj, dict):
        if id(obj) in seen:
            return obj
        seen.add(id(obj))
        for key in obj:
            obj[key] = _walk(obj[key], depth + 1, seen)
        return obj
    if isinstance(obj, (list, tuple)):
        if id(obj) in seen:
            return obj
        seen.add(id(obj))
        items = [_walk(item, depth + 1, seen) for item in obj]
        return items if isinstance(obj, list) else tuple(items)
    return obj


def _scrub_event(event, hint):
    """``before_send``: last line of defense before a payload leaves the
    process. On a catastrophic walker failure the event is dropped — unless
    it came from the paging logger, whose message ``page_critical`` already
    scrubbed inline; a page must never be lost to its own safety net."""
    try:
        return _walk(event)
    except Exception:
        if event.get("logger") == "proli.paging":
            return event
        return None


# In-process throttle for non-paging captures (scheduler job errors, the
# loguru bridge). Per-replica is deliberate: this is a monitoring throttle,
# not a correctness lock — adding a Redis dependency to the logging path
# would invert the fail-open ordering everything else here maintains.
_THROTTLE_TTL_SECONDS = 3600  # matches the WA_STATE_REALERT_MINUTES precedent
_THROTTLE_MAX_KEYS = 512
_throttle: dict = {}


def should_send(fingerprint: str, ttl_seconds: int = _THROTTLE_TTL_SECONDS) -> bool:
    """True at most once per ``ttl_seconds`` per fingerprint. Keeps a
    perpetually failing scheduler job (every 2-min tick, forever) at ≤24
    Sentry events/day instead of 720."""
    now = time.monotonic()
    last = _throttle.get(fingerprint)
    if last is not None and now - last < ttl_seconds:
        return False
    if len(_throttle) >= _THROTTLE_MAX_KEYS:
        # Crude size bound: dropping the whole map risks one early re-send
        # per fingerprint, never unbounded growth.
        _throttle.clear()
    _throttle[fingerprint] = now
    return True


# Global ceiling on bridge events per process per rolling day, on top of the
# per-fingerprint hourly throttle. Worst case (many distinct failing sites)
# stays inside a free-tier Sentry quota instead of consuming it in one bad
# night. When the cap trims, the per-site throttle still guarantees each
# site was reported at least once recently.
_BRIDGE_DAILY_CAP = 50
_bridge_window_start = 0.0
_bridge_count = 0


def _bridge_budget_ok() -> bool:
    global _bridge_window_start, _bridge_count
    now = time.monotonic()
    if now - _bridge_window_start >= 86400:
        _bridge_window_start = now
        _bridge_count = 0
    if _bridge_count >= _BRIDGE_DAILY_CAP:
        return False
    _bridge_count += 1
    return True


def _bridge_filter(record) -> bool:
    """Band + opt-outs for the loguru→Sentry bridge sink.

    - CRITICAL stays exclusively on the stdlib paging path (`page_critical`
      → LoggingIntegration at `fatal`); the bridge must not double-report it.
    - ``_stdlib=True`` records came *through* ``InterceptHandler`` from
      stdlib logging (uvicorn, arq, apscheduler) — those either already
      reach Sentry via their integration or are third-party noise; the
      bridge exists for loguru-native app code only.
    - ``sentry_skip=True`` is the explicit opt-out for a ``logger.error``
      whose exception propagates and is captured elsewhere (e.g. the task
      wrapper's log-then-raise, which ArqIntegration captures).
    """
    if record["level"].no >= logging.CRITICAL:
        return False
    extra = record["extra"]
    if extra.get("_stdlib") or extra.get("sentry_skip"):
        return False
    return True


def _sentry_bridge_sink(message) -> None:
    """One controlled door for loguru ERROR visibility (registered only when
    Sentry is active). Chosen over ``LoggingIntegration(event_level=ERROR)``
    — a no-op for loguru app code, which emits no stdlib LogRecord — and
    over per-site ``capture_exception`` calls, the "remember to add it at
    the next site" anti-pattern PRO-94 exists to kill.

    Events go out at level `error`; the fatal-only alert rule never pages on
    them. The payload still passes through ``before_send`` scrubbing like
    everything else (the raw loguru message may not have been through
    ``_pii_filter``, whose sink-filter mutation order is not guaranteed).
    """
    try:
        record = message.record
        site = f"{record['module']}:{record['function']}:{record['line']}"
        if not should_send(f"bridge:{site}"):
            return
        if not _bridge_budget_ok():
            return
        import sentry_sdk

        with sentry_sdk.isolation_scope() as scope:
            scope.set_tag("log_site", site)
            scope.set_tag("via", "loguru-bridge")
            sentry_sdk.capture_message(record["message"], level="error")
    except Exception:
        # The bridge must never become the failure it reports — and it sits
        # on the logging path of documented fail-open code.
        pass


def _integrations_for(service: str) -> list:
    """Explicit integration allowlist per service (replaces auto-enabling).

    Each import sits behind its own try/except (``DidNotEnable`` is a plain
    Exception, not ImportError) so a broken integration degrades that one
    capture surface instead of disabling Sentry. Deliberately absent:
    PyMongo/Redis/Httpx breadcrumb integrations — the scrubber is
    defense-in-depth, not an excuse to ship driver payloads.

    - proli-api: Starlette+FastApi — capture unhandled request exceptions
      (no custom exception handler; adding one would *hide* errors from the
      integration).
    - proli-worker: Arq — captures every exception propagating out of a job.
      Verified against arq 0.26+: WorkerSettings hooks receive only ``ctx``
      (no exception, no success flag) and the try-exhaustion path returns
      before any hook runs, so the integration is the only reliable seam.
      It skips ``Retry``/``JobExecutionFailed`` as control flow.
    - proli-admin: none — Streamlit swallows exceptions into its own error
      UI; the admin entrypoint calls ``capture_exception`` explicitly.
    """
    integrations = []
    if service == "proli-api":
        try:
            from sentry_sdk.integrations.fastapi import FastApiIntegration
            from sentry_sdk.integrations.starlette import StarletteIntegration

            integrations += [StarletteIntegration(), FastApiIntegration()]
        except Exception:
            logger.warning(
                "Starlette/FastApi Sentry integrations unavailable — "
                "unhandled API exceptions will not reach Sentry."
            )
    elif service == "proli-worker":
        try:
            from sentry_sdk.integrations.arq import ArqIntegration

            integrations.append(ArqIntegration())
        except Exception:
            logger.warning(
                "Arq Sentry integration unavailable — failed jobs will not "
                "reach Sentry."
            )
    return integrations


def init_sentry(service: str) -> bool:
    """Initialize Sentry for ``service`` (``proli-api`` / ``proli-worker`` /
    ``proli-admin``). Returns True when a DSN-backed client is active.

    Idempotent: a second call returns the first call's outcome without
    re-initializing. This is also the Streamlit rerun guard — the admin
    panel re-executes its script on every interaction, but module state
    persists in the server process, so the flag holds.
    """
    global _initialized, _active
    if _initialized:
        return _active
    _initialized = True

    if not settings.SENTRY_DSN:
        logger.info("Sentry disabled (SENTRY_DSN not set).")
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
    except ImportError:
        logger.warning(
            "SENTRY_DSN is set but sentry-sdk is not installed. "
            "Run `pip install -r requirements.txt`. Continuing without Sentry."
        )
        return False

    # PRO-113 follow-up, kept as belt-and-suspenders even though
    # auto_enabling_integrations=False already prevents auto-enable in *this*
    # init: disabled_integrations also blocks the integration if some other
    # sentry_sdk.init ran earlier in the process, and the post-init
    # self-check below catches the case where even that was too late.
    # Paging is stdlib-only by design (page_critical); loguru must not reach
    # Sentry. Imported separately from sentry_sdk itself so a failure of this
    # hardening helper degrades to a warning instead of silently disabling
    # ALL paging — if this module can't import, sentry can't auto-enable it
    # either.
    disabled_integrations: list = []
    try:
        from sentry_sdk.integrations.loguru import LoguruIntegration

        disabled_integrations.append(LoguruIntegration)
    except Exception:  # DidNotEnable is a plain Exception, not ImportError
        LoguruIntegration = None
        logger.warning(
            "LoguruIntegration unavailable; it cannot be auto-enabled either. "
            "Continuing with stdlib-only Sentry."
        )

    # LoggingIntegration: breadcrumbs at INFO, but only CRITICAL creates
    # issues. Python's CRITICAL maps to Sentry's `fatal` — the alert rule
    # pages on `fatal` only, so nothing below page_critical ever pages.
    logging_integration = LoggingIntegration(
        level=logging.INFO,  # breadcrumb threshold
        event_level=logging.CRITICAL,  # issue-creation threshold
    )

    sentry_sdk.init(
        dsn=settings.SENTRY_DSN.get_secret_value(),
        # PRO-34: validated + normalized to development|staging|production, so
        # staging reports into its own Sentry environment rather than
        # fragmenting across casing/typo variants of the same label.
        environment=settings.ENVIRONMENT,
        integrations=[logging_integration, *_integrations_for(service)],
        disabled_integrations=disabled_integrations,
        # sentry-sdk 2.x otherwise auto-enables FastApi/Starlette/Arq/
        # PyMongo/Redis/Httpx the moment those packages are importable —
        # every one a scrub-bypassing side door (same class as the Loguru
        # one PRO-113 closed). Integrations are allowlist-only.
        auto_enabling_integrations=False,
        before_send=_scrub_event,
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        send_default_pii=False,
        attach_stacktrace=True,
        # PRO-113: send_default_pii does NOT cover frame locals —
        # include_local_variables defaults to True and attach_stacktrace
        # would ship every frame's locals with each event (raw phone
        # numbers, whole lead documents, a Mongo exception carrying the
        # URI). _scrub_event only walks the event dict, not what never
        # enters it. Locals stay off. Non-negotiable.
        include_local_variables=False,
    )
    # Self-check, not positional trust: disabled_integrations is a no-op if a
    # different sentry_sdk.init already installed the integration earlier in
    # this process (sentry caches installs in module globals).
    if (
        LoguruIntegration is not None
        and sentry_sdk.get_client().get_integration(LoguruIntegration) is not None
    ):
        logger.warning(
            "LoguruIntegration active despite disabled_integrations — Sentry "
            "was initialized earlier in this process; loguru may reach Sentry "
            "unscrubbed."
        )
    sentry_sdk.set_tag("service", service)
    # loguru ERROR → Sentry bridge (band: ERROR ≤ level < CRITICAL after the
    # filter). Registered only on a successful DSN-backed init, so the no-DSN
    # path stays sink-free and tests see unchanged loguru behavior.
    logger.add(_sentry_bridge_sink, level="ERROR", filter=_bridge_filter)
    logger.info(
        f"Sentry initialized (service={service}, "
        f"environment={settings.ENVIRONMENT}, paging=CRITICAL-only, "
        "error-visibility=bridged+throttled)."
    )
    _active = True
    return True
