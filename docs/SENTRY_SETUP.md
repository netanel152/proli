# Sentry Setup — Operator Paging

Sentry serves Proli on two strictly separated tiers:

| Tier | Sentry level | What creates it | Pages the operator? |
|---|---|---|---|
| **Paging** | `fatal` | `page_critical(...)` only (stdlib `proli.paging`) | **Yes** — the alert rule filters on `fatal` |
| **Visibility** | `error` | Unhandled exceptions (FastApi/Starlette/Arq integrations, APScheduler `EVENT_JOB_ERROR` listener, worker-death capture, admin view capture) and the throttled loguru ERROR bridge | No — dashboard only |

Paging runs on infrastructure independent of WhatsApp so it can report a WhatsApp
outage without riding the channel that is down (see PRO-71 / PRO-75). The
visibility tier exists because ~95% of real failures land at ERROR (a lost
webhook enqueue, a monitor job dying on every tick, a fail-open rate limit) and
used to be stdout-only.

This file is the runbook referenced from `app/core/sentry.py` (the single
shared init all services call), `app/worker.py`, `app/main.py`, and
`app/core/config.py`.

---

## Design choices (as implemented)

- **One shared init: `app/core/sentry.py`.** `init_sentry(service)` replaced
  the two hand-synced `_init_sentry` copies (`app/main.py` / `app/worker.py`)
  and the mirror in `scripts/fire_test_page.py`. The worker tags events
  `service=proli-worker`; the API tags `service=proli-api`. The worker is
  where the failures we page on actually surface (stuck leads, reassignment
  loops, SOS/monitor crashes); the API returns `200 OK` immediately and does
  almost no business logic. The init is idempotent (a second call is a no-op).
- **Integrations are an explicit allowlist.** sentry-sdk 2.x *auto-enables*
  every integration whose package is importable — FastApi, Starlette, Arq,
  PyMongo, Redis, Httpx — which silently widened the documented CRITICAL-only
  design: unhandled API exceptions and failed ARQ jobs were reaching Sentry
  **unscrubbed** (exception values can echo a credentialed Mongo URI; httpx
  breadcrumbs can carry a token in a URL — the PRO-79 class).
  `init_sentry` passes `auto_enabling_integrations=False` and enables
  integrations per service, deliberately, in `_integrations_for()`.
- **Every outgoing event is scrubbed (`before_send`).** `_scrub_event` walks
  the whole event and applies `redact_secrets` → `mask_pii` → structural
  URI-credential stripping (`://user:pass@host` → `://***@host`, which also
  catches percent-encoded variants the exact-match secret list can't) to
  every string leaf — exception values, breadcrumbs, extra, request context.
  On a scrubber failure the event is **dropped**, unless it came from
  `proli.paging` (already inline-scrubbed by `page_critical`) — the safety
  net must never eat a page. This is defense-in-depth on top of
  `page_critical`'s inline scrub, not a replacement for it.
- **CRITICAL-only.** A `LoggingIntegration` is configured with
  `level=INFO` (breadcrumbs) and `event_level=CRITICAL` (issue creation). Regular
  `ERROR`/`WARNING` noise stays in stdout/loguru. To page the operator, code calls
  `page_critical(...)` (`app/core/logger.py`) — never raises, so it is also the
  right call from a fail-open path.
  Python's `logging.CRITICAL` maps to Sentry's **`fatal`** level — filter on that
  in the alert rule below.
  > **PRO-113:** `LoggingIntegration` only hooks *stdlib* `logging`, so a loguru
  > `logger.critical(...)` call does not by itself create a Sentry event.
  > `page_critical` is the only paging primitive — it logs through
  > `logging.getLogger("proli.paging")`, the stdlib channel `LoggingIntegration`
  > actually watches, with secrets/PII scrubbed inline before the record is
  > built. Loguru `logger.critical` is stdout-only and is banned under `app/`
  > (enforced by `tests/test_page_critical.py`). To verify paging end-to-end,
  > use `scripts/fire_test_page.py` rather than a one-off `logger.critical` call.
- **Loguru never reaches Sentry.** sentry-sdk *auto-enables* its
  `LoguruIntegration` (at `event_level=ERROR`) whenever loguru is installed;
  `init_sentry` passes `disabled_integrations=[LoguruIntegration]`
  to close that side door (PRO-113 follow-up). Before this, every page was
  duplicated as a second issue and loguru `ERROR`+ text reached Sentry outside
  `_pii_filter`'s guarantee (message scrubbing depended on sink registration
  order; exception values and `extra` were never scrubbed). Consequence:
  breadcrumbs on Sentry events come from **stdlib-origin** records only
  (uvicorn, arq, libraries) — loguru-native application lines do not appear as
  breadcrumbs. A post-init self-check warns if the integration is somehow
  still active (a different `sentry_sdk.init` earlier in the same process).
- **Three services, one init.** `init_sentry("proli-api" | "proli-worker" |
  "proli-admin")` — api enables Starlette+FastApi (unhandled request
  exceptions), worker enables Arq (exceptions propagating out of jobs; arq's
  `WorkerSettings` hooks can't see failures, and the try-exhaustion path
  returns before any hook runs), admin enables none (Streamlit swallows
  exceptions into its own error UI — `admin_panel/main.py` wraps the view
  dispatch and calls `capture_exception` explicitly). The scheduler gets an
  `EVENT_JOB_ERROR` listener (`app/scheduler.py:_on_job_error`), and
  `app/worker.py` captures + flushes on worker-process death.
  *Residual gap (known, accepted):* a job whose worker dies repeatedly
  mid-run until arq's try-exhaustion produces only an `arq.worker` WARNING;
  worker death itself is covered by the heartbeat key + startup paging.
- **loguru ERROR bridge (throttled).** A loguru sink registered only when
  Sentry is active forwards ERROR-level records (`ERROR ≤ level < CRITICAL`)
  as non-paging `error` events. Excluded: stdlib-origin records
  (`_stdlib=True`, bound by `InterceptHandler` — uvicorn/arq/apscheduler
  either have their own integration or are noise) and explicit
  `logger.bind(sentry_skip=True)` opt-outs (log-then-raise sites whose
  exception is captured elsewhere). Spend is bounded twice: one event per
  `module:function:line` per hour (`should_send`), plus a global cap of 50
  bridge events per process per rolling day. A monitor job failing every
  2-min tick forever costs ≤24 events/day.
- **No-op when `SENTRY_DSN` is unset.** Tests, local dev, and the open-source
  checkout never touch the Sentry API. `init_sentry()` logs
  `"Sentry disabled (SENTRY_DSN not set)."` and returns early — without ever
  importing `sentry_sdk` (asserted by `tests/test_fire_test_page.py`).
- **Small, PII-free payloads.** `send_default_pii=False`, no request bodies,
  `attach_stacktrace=True`; `include_local_variables=False` (PRO-113) — the
  default `True` would otherwise ship unscrubbed frame locals (PII/secrets)
  alongside the stack trace.

---

## Environment variables (set in Railway on **all three** services — api, worker, admin)

| Var | Required | Notes |
|-----|----------|-------|
| `SENTRY_DSN` | Yes (to enable) | Sentry → **Project Settings → Client Keys (DSN)**. Unset ⇒ Sentry is fully off. |
| `SENTRY_TRACES_SAMPLE_RATE` | No (default `0.0`) | Leave at `0.0` — no performance tracing; paging only. |
| `ENVIRONMENT` | No (default `development`) | Tags each event (`production` / `staging`). |

> Set `SENTRY_DSN` on the `api`, `worker`, **and** `admin` Railway services.
> If only some have it you are partially blind — most paging events originate
> in the worker; admin crashes are invisible anywhere else.

`sentry-sdk` is already pinned in `requirements.txt`. If `SENTRY_DSN` is set but the
package is missing, `init_sentry()` logs a warning and continues without Sentry
(fail-open — a monitoring dependency never takes down a process).

---

## Alert rule (Sentry dashboard — not in the repo)

WhatsApp is the channel that goes down, so the alert must reach you **off** WhatsApp.
Sentry → email is that path. Configure once:

1. Sentry → **Alerts → Create Alert → Issues**.
2. **Environment:** `production`.
3. **When:** *An issue is created*.
4. **If (filter):** *The event's level equals* **`fatal`** (this is Python `CRITICAL`).
5. **Then:** *Send a notification via* **Email** to the operator address.
6. Leave issue-owner/rate-limit digests off — every critical should page.
7. The `fatal` filter is what keeps the visibility tier (level `error`) from
   paging — do not widen it.
8. Recommended: enable **Spike Protection** (Settings → Subscription) as the
   server-side backstop for the client-side throttles.

Reconstruct this rule from scratch if the Sentry project is ever recreated; it is
the only piece of the paging path that lives outside the repo.

---

## Re-alert dedup (already handled in app code)

Do **not** add a second dedup in Sentry that would swallow re-pages. The app already
throttles: `monitor_service` writes `wa:instance:last_alert` and re-pages a
persistent non-authorized instance only every `WorkerConstants.WA_STATE_REALERT_MINUTES`
(60 min). That keeps a multi-hour outage to ~1 event/hour — well inside Sentry's
free-tier budget — while still re-notifying so the incident can't be silently forgotten.

---

## Verification (the only test that matters)

1. Confirm `SENTRY_DSN` is set on the Railway `worker` service.
2. Trigger a critical event — either:
   - disconnect / deauthorize the WhatsApp account and wait for the
     state monitor to page, **or**
   - run `railway run python scripts/fire_test_page.py --service worker` (or
     `--service api`) — the operator verification tool built for this purpose.
3. **Confirm the email arrives.** If it does not, nothing is wired — check, in order:
   the DSN is on the right service, `sentry-sdk` installed, and the alert-rule level
   filter is `fatal`.
4. Visibility tier: check the events carry masked phones (`97252****567`)
   and stripped URI credentials (`mongodb+srv://***@…`) — the `before_send`
   scrubber's whole-event walk is the guarantee here; a raw phone number in
   any Sentry event is a bug (`tests/test_sentry_scrub.py`).

---

## Related issues

- **PRO-18** — Configure `SENTRY_DSN` for worker error paging (the ops half of this doc).
- **PRO-71** — yellowCard circuit breaker: halts outbound when the instance is not
  authorized. The alert is outbound, so it must go via Sentry, not WhatsApp.
- **PRO-75** — Delete SMS, page via Sentry email: removes the dead SMS-first
  fallback and makes `notification_service` emit `page_critical` (→ this alert)
  instead of sending over the flagged number.
