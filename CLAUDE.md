# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## CRITICAL: text-only menus, and the single outbound egress

**Every WhatsApp menu must stay text-based** (numeric or keyword replies). Example: instead of Approve/Reject buttons, send `"Reply '1' to approve, '2' to reject."`

The original reason was a Green API limitation. Green API is gone (PRO-85 — instance deleted, tariff cancelled), and the rule now rests on a different footing: `WhatsAppProvider.send_interactive` exists on the ABC and the PRO-89 `CloudAPIProvider` transport can send it, but **nothing in any flow may call it** — no template is approved yet (PRO-87 onboarding, the Business Portfolio and template review, is still open) and adopting buttons over numeric menus is an explicit product decision not yet made (see the PRO-88 catalog). The Green-shaped `send_interactive_buttons` helper was removed in April 2026 and stays removed.

**All outbound traffic goes through `app/providers/whatsapp/` (PRO-86).** Never build a provider directly, never call an HTTP client at a vendor endpoint — both bypass the circuit breaker and the operator kill switch, which is precisely what caused the yellowCard incident. Synchronous callers (the Streamlit admin panel) use `app.providers.whatsapp.sync.send_text_sync`.

A CI step fails the build on any reference to the old vendor's domain (`green` + `-api.com`, either spelling) anywhere in the repo — see the "Guard" step in `.github/workflows/tests.yml`. Write it split like that if you ever need to mention it in prose, or the guard will trip on your own sentence. A second "Guard — all outbound traffic through the provider facade" step fails the build on `httpx`/`requests` imports under `app/services/` (allowlisting `geocoding_service.py`) and on `CloudAPIProvider(`/`DryRunProvider(` construction anywhere in `app/` outside `app/providers/whatsapp/`.

## Commands

### Local Development (run all three in separate terminals)

```bash
# Backend API (FastAPI)
uvicorn app.main:app --reload --port 8000

# Background Worker (ARQ + APScheduler)
python -m app.worker

# Admin Panel (Streamlit)
streamlit run admin_panel/main.py
```

### Docker (recommended)

```bash
docker-compose up --build -d
# Backend: http://localhost:8000
# Admin:   http://localhost:8501
# Worker logs: docker-compose logs -f worker
```

### Database

```bash
python scripts/seed_db.py          # Seed initial data
python scripts/create_indexes.py   # Create MongoDB indexes
python scripts/clear_history.py    # Clear chat history
```

### Testing

```bash
# Run all unit tests (uses mongomock — no real DB needed)
pytest

# Run a single test file
pytest tests/test_matching_service.py

# Run only integration tests (requires MONGO_TEST_URI in .env)
pytest -m integration

# Run with verbose output
pytest -v
```

Expected baseline lives in `docs/TESTING.md` ("Current status" line) — the single source of truth for the pass/skip count, enforced exactly by the "Guard — test baseline" CI step (fails on a regression **or** a stale baseline, so the count and the doc must move in the same PR). Integration tests are skipped without `MONGO_TEST_URI`.

Canonical run is `pytest` inside the project virtualenv (PRO-50, pinned `pydantic`/`pydantic-core`/`pydantic-settings` for deterministic resolution). Unit tests need neither a real MongoDB (in-memory `mongomock`) nor a real Redis (in-memory `fakeredis`, PRO-78) — no external services required.

### Linting / Formatting

```bash
black .
flake8 .
```

## Architecture

Proli is an AI-powered WhatsApp CRM for Israeli service professionals (plumbers, electricians, etc.). It runs as three cooperating processes:       

### Process 1: FastAPI Backend (`app/`)

Entry point for inbound WhatsApp webhooks. Its only job is to validate the incoming payload, enqueue a task to Redis via ARQ, and immediately return `200 OK`. All heavy lifting is deferred to the Worker. Routes: `POST /webhook`, `GET/POST /webhook/meta` (PRO-89 — Meta Cloud API subscription handshake and inbound; live even under `WHATSAPP_PROVIDER=dryrun`), `GET /health`, and `GET /health/leads`.

### Process 2: ARQ Worker (`app/worker.py` + `app/core/arq_worker.py`)

Picks up `process_message_task` jobs from Redis and calls `workflow_service.process_incoming_message`. Also hosts APScheduler for periodic jobs (SOS healer every 10 mins, stale monitor every 30 mins, stale lead nudger every 4h, daily agenda at 08:00 Israel time, pro-approval SLA check every 5 mins, WhatsApp instance deauth watchdog every 2 mins).

### Process 3: Streamlit Admin Panel (`admin_panel/`)

Protected by bcrypt cookie-based auth. Views for lead management, professional profiles, and schedule management.

### Service Layer (`app/services/`)

| Service | Responsibility |
|---|---|
| `workflow_service.py` | Central orchestrator — routes messages, manages FSM states, delegates to customer/pro/admin flows; handles emergency bypass and loyalty checks |
| `customer_flow.py` | Customer completion checks, ratings, reviews, and rescheduling |
| `pro_flow.py` | Professional text commands (approve, reject, pause, resume, finish, cancel booked job, details, summary, **מצא** — rate-limited stuck-lead search) — implements Dynamic Dashboard and availability controls; on finish, captures an optional **final_price** (PRO-33) via a non-blocking `PRO_AWAITING_FINAL_PRICE` prompt and derives `commission_amount` |
| `admin_flow.py` | Admin routing wizard (`ניהול` keyword): list PENDING_ADMIN_REVIEW leads → self-assign or pick a pro; assignment notifies the pro via `notification_service.notify_pro_new_lead` |
| `media_handler.py` | Media type detection and download (images, audio, video) |
| `ai_engine_service.py` | Gemini 2.5 Flash with adaptive fallback (Flash Lite → Flash → Flash 1.5); multimodal; 5-turn context window; non-blocking token accounting |
| `matching_service.py` | Progressive `$geoNear` aggregation (10 km → 20 km → 30 km); falls back to regex city match; load-balances by max 3 active leads per pro |
| `state_manager_service.py` | Redis-backed FSM per `chat_id` (`UserStates` enum); supports custom TTL per state |
| `context_manager_service.py` | Stores last 20 messages per `chat_id` in Redis |
| `lead_manager_service.py` | CRUD for leads in MongoDB |
| `notification_service.py` | Sends WhatsApp notifications to pros; SOS alerts; on-call paging via `send_oncall_alert` (WhatsApp when the instance is authorized, else `logger.critical` → Sentry/email page); owns the shared lead-offer builder — `build_new_lead_message` (pure) and `notify_pro_new_lead` (sends offer + navigation link, fail-open) — used by `monitor_service`'s reassignment path and `admin_flow`'s assignment path so the message and its media/Hebrew-fallback policy can't drift between callers |
| `monitor_service.py` | Stale job detection, reassignment (shared `reassign_lead` helper — escalates to PENDING_ADMIN_REVIEW, with an immediate admin alert, once `MAX_REASSIGNMENTS` is exhausted; PRO-63, never closes the lead; notifies the new pro via `notification_service.notify_pro_new_lead`), stale lead reminders (nudger), pro-approval SLA monitor (`check_pro_approval_sla` — nudges a silent pro, then offers the customer reassignment, gated to business hours per PRO-73), escalation to PENDING_ADMIN_REVIEW, and WhatsApp account deauth detection (`check_whatsapp_instance_state`; skipped for non-transmitting providers) |
| `app/providers/whatsapp/` | Single outbound egress (PRO-86, not under `app/services/`) — `WhatsAppFacade` owns the PRO-71 circuit breaker (fail-closed per PRO-82 on an absent `wa:instance:state` confirmation) and the `wa:instance:paused`/`wa:instance:paused:manual` kill switch, fail-open on Redis error; provider selection via `WHATSAPP_PROVIDER` (`dryrun` default — logs, never transmits; `cloud` — the PRO-89 `CloudAPIProvider`, sends text/file/template/interactive via the Meta Graph API and enforces the 24h customer-service window (`window.py`, Redis-backed, fail-open) — a closed window with no approved fallback template pages the operator rather than dropping silently; `template_registry.py` is the PRO-88 catalog as code, every entry `DRAFT` until PRO-87 lands approvals; `delivery.py` persists per-message delivery statuses in `wa_delivery` and retries a window-closed send as a template); text-only sends (interactive buttons defined on the ABC and sendable by Cloud API, but no flow calls them — see the note above); `app.providers.whatsapp.sync.send_text_sync` bridges the synchronous admin panel |
| `cloudinary_client_service.py` | Media upload/retrieval |
| `security_service.py` | Rate limiting via Redis — coarse fixed-window webhook DDoS shield (`check_rate_limit`), per-customer inbound sliding window (`check_sliding_window`), and daily per-chat AI/multimodal cost cap (`check_and_increment_daily_ai_cap`, Israel-time reset). Pros/admins exempt; all checks fail-open |

### Data Layer

- **MongoDB**: Primary store — `users` (pros + customers), `leads`, `slots`, `messages`, `settings`, `reviews`, `consent`, `audit_log`, `admins`, `wa_delivery` (PRO-89 — per-wamid outbound delivery statuses, kept out of `messages` so status callbacks never get replayed into the AI context)  
- **Redis**: ARQ task queue + context cache (chat history) + state machine (FSM)

### Key Constants (`app/core/constants.py`)

- `LeadStatus`: `contacted → new → booked → completed/rejected/closed/cancelled/pending_admin_review`
- `UserStates`: `IDLE`, `PRO_MODE`, `CUSTOMER_MODE`, `AWAITING_INTENT_CONFIRMATION`, `CUSTOMER_FLOW`, `AWAITING_ADDRESS`, `AWAITING_MEDIA`, `AWAITING_TIME`, `AWAITING_CONSENT`, `SOS`, `AWAITING_PRO_APPROVAL`, `PAUSED_FOR_HUMAN`, `AWAITING_RESCHEDULE_TIME`, `AWAITING_LOYALTY_CONFIRMATION`, `PRO_SELECTING_JOB_TO_FINISH`, `PRO_SELECTING_JOB_TO_CANCEL`, `PRO_AWAITING_FINAL_PRICE`, `ONBOARDING_*`, `ADMIN_MODE_IDLE`, `ADMIN_SELECTING_LEAD`, `ADMIN_SELECTING_ACTION`, `ADMIN_SELECTING_PRO`
- `WorkerConstants.MAX_PRO_LOAD = 3`: max concurrent leads per professional
- `WorkerConstants.SOS_TIMEOUT_MINUTES = 60`: reassignment trigger threshold
- `WorkerConstants.STALE_BOOKED_LEAD_HOURS = 24`: threshold for stale job reminders
- `WorkerConstants.GEO_RADIUS_STEPS = [10000, 20000, 30000]`: progressive geo search radii
- `WorkerConstants.PAUSE_TTL_SECONDS = 900`: 15-minute rolling TTL for PAUSED_FOR_HUMAN state
- `WorkerConstants.PRO_SEARCH_RATE_LIMIT_SECONDS = 600`: 10-minute per-pro cool-down on the `מצא` proactive stuck-lead search
- `WorkerConstants.COMMISSION_RATE = 0.10`: platform take-rate applied to a recorded `final_price` → `commission_amount` (PRO-33; GMV/commission surfaced in the admin analytics Revenue tab)
- `WorkerConstants.FINAL_PRICE_TTL_SECONDS = 600`: 10-minute window for the pro to answer the post-completion "how much did you charge?" prompt (skippable, never gates COMPLETED)
- `WorkerConstants.APPROVAL_NUDGE_MINUTES = 10`: nudge a silent pro this long after a lead was offered (emergency leads: half)
- `WorkerConstants.APPROVAL_REASSIGN_OFFER_MINUTES = 25`: offer the customer a reassignment if the pro is still silent (emergency leads: half)
- `WorkerConstants.APPROVAL_SLA_CHECK_INTERVAL_MINUTES = 5`: how often the pro-approval SLA scheduler job runs
- `WorkerConstants.INBOUND_RATE_LIMIT_MAX = 20` / `INBOUND_RATE_LIMIT_WINDOW_SECONDS = 60`: per-customer inbound sliding-window limit (pros/admins exempt)
- `WorkerConstants.DAILY_AI_CALL_CAP = 40`: per-chat daily ceiling on Gemini/multimodal calls (resets at Israel-time midnight)
- `WorkerConstants.RATE_LIMIT_ABUSE_TRIP_THRESHOLD = 3`: repeated trips within a window escalate from `logger.warning` to `logger.error` (Sentry)
- `WorkerConstants.WA_STATE_CHECK_INTERVAL_MINUTES = 2`: how often the worker polls the WhatsApp provider's account state (`get_state`)
- `WorkerConstants.WA_STATE_ALERT_THRESHOLD_MINUTES = 5`: page on-call only after the instance has been non-authorized > this many minutes
- `WorkerConstants.WA_STATE_REALERT_MINUTES = 60`: re-page interval while the instance stays deauthorized
- `WorkerConstants.WA_STATE_PAUSE_TTL_SECONDS = 360`: TTL on the `wa:instance:paused` outbound-halt key; auto-releases if the monitor dies
- `WorkerConstants.WA_STATE_CONFIRM_TTL_SECONDS = 360`: TTL on `wa:instance:state`, the positive confirmation a probe found the account authorized; the outbound facade fails **closed** (blocks sending) once this key is absent (PRO-82/PRO-86)
- `WorkerConstants.PENDING_REVIEW_SHORTCIRCUIT_HOURS = 24`: how long a PENDING_ADMIN_REVIEW lead short-circuits the customer's chat before their next message proceeds to the normal dispatcher (PRO-63)
- `ISRAEL_CITIES_COORDS`: static dict mapping Hebrew/English city names to `[lon, lat]` for geo queries

### Testing Conventions

Unit tests use `mongomock_motor` (in-memory MongoDB) and mock `whatsapp` and `ai` instances via `monkeypatch`. Integration tests (marked `@pytest.mark.integration`) connect to a real `MONGO_TEST_URI` test database and clear it before each run. `conftest.py` auto-applies the mock fixtures to all non-integration tests via `autouse=True`. `asyncio_mode = strict` is set in `pytest.ini`.

`$geoNear` is not supported by mongomock — matching service geo tests mock `users_collection.aggregate` as async generators directly.

`customer_flow.py` and `pro_flow.py` functions receive `whatsapp`/`lead_manager` as parameters (dependency injection) so `workflow_service.py` passes its shared instances.

### Configuration

All config is in `app/core/config.py` via `pydantic-settings`. Required env vars: `GEMINI_API_KEY`, `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET`. Optional: `ENVIRONMENT` (`development` | `staging` | `production`, defaults to `development`; any other value — including empty — fails validation at startup, PRO-34), `MONGO_URI` (defaults to localhost), `REDIS_URL`, `MONGO_TEST_URI` (for integration tests), `ADMIN_PASSWORD`, `ADMIN_PHONE` (defaults to hardcoded), `ONCALL_PHONE` (on-call number for infra alerts; defaults to `ADMIN_PHONE`), `WEBHOOK_TOKEN` (enables `?token=` webhook auth; **required**, not optional, whenever `ENVIRONMENT` is `staging`/`production` — PRO-86 removed the sender instance-id check that used to be the other half of webhook auth, so an unset token now means no authentication at all, and `Settings` refuses to boot without it in a prod-like environment), `SENTRY_DSN` (enables CRITICAL-only operator paging; unset = no-op), `WHATSAPP_PROVIDER` (`dryrun` default | `cloud`; which transport the outbound facade uses — `cloud` selects the PRO-89 `CloudAPIProvider`, code-complete against the Meta Graph API though PRO-87 onboarding has not yet approved any template or gone live), `WHATSAPP_DRY_RUN` (default `false`; set `true` in local `.env` to force the `DryRunProvider` regardless of `WHATSAPP_PROVIDER`, so dev/simulation never cold-initiates a real message from the pilot number — this is also the operator's emergency mute, see `docs/RUNBOOK_WHATSAPP_OUTAGE.md`), `META_ACCESS_TOKEN`/`META_APP_SECRET`/`META_VERIFY_TOKEN` (`SecretStr | None`) and `META_PHONE_NUMBER_ID`/`META_GRAPH_API_VERSION` (default `v23.0`) — the PRO-89 Cloud API credentials; `require_cloud_provider_config` makes the token + phone-number id mandatory the moment `WHATSAPP_PROVIDER=cloud` is selected without `WHATSAPP_DRY_RUN`, and makes the app secret + verify token (which authenticate `GET`/`POST /webhook/meta`) additionally mandatory in a prod-like environment.

**`ENVIRONMENT` is cross-checked against the platform (PRO-96).** `Settings` refuses to boot when the declared value disagrees with `RAILWAY_ENVIRONMENT_NAME`, which Railway injects and an operator cannot mistype. A *legal but wrong* value used to pass silently — it happened in both directions (PRO-92: staging claiming `production`; PRO-96: production api+worker claiming `staging`, which disarmed `seed_db.py`'s destructive guard against the production database). Exempt when the Railway variable is absent (local, docker-compose, CI) or holds a preview-environment name (`pr-42`) that has no counterpart in the three-value vocabulary. `tests/conftest.py` clears both Railway variables suite-wide so `pytest` under `railway run` still works.

**Every credential-bearing setting is a `pydantic.SecretStr` (PRO-94)** — `GEMINI_API_KEY`, `MONGO_URI`, `MONGO_TEST_URI`, `ADMIN_PASSWORD`, `REDIS_URL`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET`, `WEBHOOK_TOKEN`, `GOOGLE_MAPS_API_KEY`, `SENTRY_DSN`. pydantic's default `__repr__` prints every field value, so any traceback touching `Settings` used to dump the whole secret set into a log, a Sentry event or a terminal. Read them with `.get_secret_value()` **at the point of use** — never into a module-level name, and never into an f-string or a log line. The convention is enforced by field name: anything ending in `TOKEN`, `KEY`, `SECRET`, `PASSWORD`, `DSN`, `_URI` or `_URL` must be `SecretStr` or `tests/test_settings_secret_masking.py` fails the build (this is what already covers PRO-89's `META_ACCESS_TOKEN`, `META_APP_SECRET` and `META_VERIFY_TOKEN`). `app/core/logger.py` sources its redaction list from those fields automatically, so a new credential is scrubbed from logs the moment it is typed correctly. SecretStr only protects an object that already exists: a `ValidationError` raised *during* `Settings` construction (e.g. PRO-96's environment cross-check) fires before any field is wrapped, so pydantic's default error text used to echo the raw input — env vars included — under `input_value=`. `Settings.model_config` now sets `hide_input_in_errors=True` (PRO-99) to close that construction-time gap; the flag covers `__str__`/`__repr__`/tracebacks only — `ValidationError.errors()`/`.json()` still carry the raw input dict, so no boot handler may render either.

## Session Guidelines

- Skip files over 100KB unless explicitly required.
- Suggest `/cost` when a session is running long to monitor cache ratio.
- Recommend starting a new session when switching to an unrelated task.
- After finishing a code-change task, delegate to the **docs-syncer** subagent (incremental mode) to update any `.md` files made stale by the diff.
