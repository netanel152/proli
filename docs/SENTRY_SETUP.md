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
  both `_init_sentry` copies pass `disabled_integrations=[LoguruIntegration]`
  to close that side door (PRO-113 follow-up). Before this, every page was
  duplicated as a second issue and loguru `ERROR`+ text reached Sentry outside
  `_pii_filter`'s guarantee (message scrubbing depended on sink registration
  order; exception values and `extra` were never scrubbed). Consequence:
  breadcrumbs on Sentry events come from **stdlib-origin** records only
  (uvicorn, arq, libraries) — loguru-native application lines do not appear as
  breadcrumbs. A post-init self-check warns if the integration is somehow
  still active (a different `sentry_sdk.init` earlier in the same process).
- **No-op when `SENTRY_DSN` is unset.** Tests, local dev, and the open-source
  checkout never touch the Sentry API. `_init_sentry()` logs
  `"Sentry disabled (SENTRY_DSN not set)."` and returns early.
- **Small, PII-free payloads.** `send_default_pii=False`, no request bodies,
  `attach_stacktrace=True`; `include_local_variables=False` (PRO-113) — the
  default `True` would otherwise ship unscrubbed frame locals (PII/secrets)
  alongside the stack trace.

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
   - disconnect / deauthorize the WhatsApp account and wait for the
     state monitor to page, **or**
   - run `railway run python scripts/fire_test_page.py --service worker` (or
     `--service api`) — the operator verification tool built for this purpose.
3. **Confirm the email arrives.** If it does not, nothing is wired — check, in order:
   the DSN is on the right service, `sentry-sdk` installed, and the alert-rule level
   filter is `fatal`.

---

## Related issues

- **PRO-18** — Configure `SENTRY_DSN` for worker error paging (the ops half of this doc).
- **PRO-71** — yellowCard circuit breaker: halts outbound when the instance is not
  authorized. The alert is outbound, so it must go via Sentry, not WhatsApp.
- **PRO-75** — Delete SMS, page via Sentry email: removes the dead SMS-first
  fallback and makes `notification_service` emit `page_critical` (→ this alert)
  instead of sending over the flagged number.
