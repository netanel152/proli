# Sentry Setup — Operator Paging

Sentry is Proli's **out-of-band operator-paging channel**. It is deliberately *not*
a general error-mirroring tool: only operator-actionable events reach it, and it
runs on infrastructure independent of WhatsApp so it can report a WhatsApp outage
without riding the channel that is down (see PRO-71 / PRO-75).

This file is the runbook referenced from `app/worker.py`, `app/main.py`, and
`app/core/config.py`.

---

## Design choices (as implemented)

- **Both processes initialize Sentry.** `app/worker.py` tags events
  `service=proli-worker`; `app/main.py` tags `service=proli-api`. The worker is
  where the failures we page on actually surface (stuck leads, reassignment
  loops, SOS/monitor crashes); the API returns `200 OK` immediately and does
  almost no business logic.
- **CRITICAL-only.** A `LoggingIntegration` is configured with
  `level=INFO` (breadcrumbs) and `event_level=CRITICAL` (issue creation). Regular
  `ERROR`/`WARNING` noise stays in stdout/loguru. To page the operator, code calls
  `logger.critical(...)` (or raises and lets arq's top-level handler catch it).
  Python's `logging.CRITICAL` maps to Sentry's **`fatal`** level — filter on that
  in the alert rule below.
- **No-op when `SENTRY_DSN` is unset.** Tests, local dev, and the open-source
  checkout never touch the Sentry API. `_init_sentry()` logs
  `"Sentry disabled (SENTRY_DSN not set)."` and returns early.
- **Small, PII-free payloads.** `send_default_pii=False`, no request bodies, no
  local variables; `attach_stacktrace=True`.

---

## Environment variables (set in Railway on **both** services)

| Var | Required | Notes |
|-----|----------|-------|
| `SENTRY_DSN` | Yes (to enable) | Sentry → **Project Settings → Client Keys (DSN)**. Unset ⇒ Sentry is fully off. |
| `SENTRY_TRACES_SAMPLE_RATE` | No (default `0.0`) | Leave at `0.0` — no performance tracing; paging only. |
| `ENVIRONMENT` | No (default `development`) | Tags each event (`production` / `staging`). |

> Set `SENTRY_DSN` on **both** the `api` **and** the `worker` Railway services.
> If only one has it you are half-blind — most paging events originate in the worker.

`sentry-sdk` is already pinned in `requirements.txt`. If `SENTRY_DSN` is set but the
package is missing, `_init_sentry()` logs a warning and continues without Sentry
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
   - disconnect / deauthorize the WhatsApp (Green API) instance and wait for the
     state monitor to page, **or**
   - run a one-off `logger.critical("sentry paging test", extra=...)` on the worker.
3. **Confirm the email arrives.** If it does not, nothing is wired — check, in order:
   the DSN is on the right service, `sentry-sdk` installed, and the alert-rule level
   filter is `fatal`.

---

## Related issues

- **PRO-18** — Configure `SENTRY_DSN` for worker error paging (the ops half of this doc).
- **PRO-71** — yellowCard circuit breaker: halts outbound when the instance is not
  authorized. The alert is outbound, so it must go via Sentry, not WhatsApp.
- **PRO-75** — Delete SMS, page via Sentry email: removes the dead SMS-first
  fallback and makes `notification_service` emit `logger.critical` (→ this alert)
  instead of sending over the flagged number.
