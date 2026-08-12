# Railway Multi-Service Setup

Proli requires 3 separate Railway services sharing the same repo, MongoDB, and Redis.

## Step 1: Create a Railway Project

Create a new project in Railway. You will add 3 services to it.

## Step 2: Add Shared Infrastructure

Add these plugins/add-ons to the project:
- **Redis** (Railway Redis plugin) - used for ARQ task queue, state, context cache
- **MongoDB** - use MongoDB Atlas (external) and set `MONGO_URI` as a shared variable

## Step 3: Create 3 Services

All 3 services point to the **same GitHub repo** and use the **same Dockerfile**. They differ only in the start command.

### Service 1: API (Backend)
- **Name:** `api`
- **Start Command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- **Port:** Automatically detected by Railway (`$PORT`)
- **Public Domain:** Yes (this is your webhook URL)

### Service 2: Worker
- **Name:** `worker`
- **Start Command:** `python -m app.worker`
- **Port:** None (no HTTP traffic)
- **Public Domain:** No

### Service 3: Admin Panel
- **Name:** `admin`
- **Start Command:** `streamlit run admin_panel/main.py --server.port $PORT --server.address 0.0.0.0`
- **Port:** Automatically detected
- **Public Domain:** Yes (admin dashboard URL)

## Step 4: Environment Variables

Set these as **shared variables** (project-level) so all 3 services inherit them:

```
MONGO_URI=mongodb+srv://...
REDIS_URL=redis://...  (auto-set if using Railway Redis plugin)
GEMINI_API_KEY=...
CLOUDINARY_CLOUD_NAME=...
CLOUDINARY_API_KEY=...
CLOUDINARY_API_SECRET=...
ADMIN_PASSWORD_HASH=...  # Generate with: python scripts/generate_admin_hash.py
ADMIN_PHONE=972501234567  # Admin WhatsApp number for SOS alerts
WEBHOOK_TOKEN=...  # Random string for webhook auth — required (boot fails without it) once ENVIRONMENT is staging/production (PRO-86)
WHATSAPP_PROVIDER=dryrun  # dryrun (default) | cloud — cloud is a stub until PRO-89 implements it
ENVIRONMENT=production   # per-environment — see below
```

### `ENVIRONMENT` per Railway environment (PRO-34)

`ENVIRONMENT` accepts exactly `development | staging | production`; anything else raises at startup. It is **not** a shared project variable — set it per Railway environment so the label always matches reality:

| Railway environment | Environment ID | `ENVIRONMENT` |
|---|---|---|
| Staging | `93e8ad7e-3582-4ab7-8f71-1775bf0bbddc` | `staging` |
| Production | `a8c1fc4c-9434-48c4-9461-afce87651d21` | `production` |

Set it on all 3 services (`api`, `worker`, `admin`) in each environment:

```bash
railway variables --set "ENVIRONMENT=staging"    --service api    --environment Staging
railway variables --set "ENVIRONMENT=staging"    --service worker --environment Staging
railway variables --set "ENVIRONMENT=staging"    --service admin  --environment Staging
railway variables --set "ENVIRONMENT=production" --service api    --environment Production
railway variables --set "ENVIRONMENT=production" --service worker --environment Production
railway variables --set "ENVIRONMENT=production" --service admin  --environment Production
```

What the value actually changes:

- **`staging` and `production` are both prod-like** — structured JSON logs, PII masking on stdout, `diagnose=False` on the file sink, and the admin panel refuses to start without `MONGO_URI` (no silent `localhost` fallback).
- **Sentry** tags every event with the value, so staging errors land in a separate Sentry environment from production.
- **`production` alone blocks `scripts/seed_db.py`**, which begins with a destructive `clear_db()`. Staging (`proli_staging_db`) remains a valid seed target.

> **A rejected `ENVIRONMENT` surfaces as a boot crash, not a Sentry issue.** The value is validated while `app.core.config` is imported — before logging and `sentry_sdk.init()` run — so a typo (`ENVIRONMENT=prod`) or an explicitly empty value produces a pydantic `ValidationError` on stderr and a Railway restart loop. Watch the deploy log, not Sentry, when a service fails to come up after an env change.

#### The value is cross-checked against Railway itself (PRO-96)

A *legal but wrong* value used to pass silently, and it happened twice in both directions: staging services claiming `production` (PRO-92) and production `api`+`worker` claiming `staging` (PRO-96). The second one left `seed_db.py`'s destructive guard — which allow-lists `(development, staging)` — disarmed against the production database, and both were found only by a manual sweep.

`Settings` now refuses to boot when `ENVIRONMENT` disagrees with `RAILWAY_ENVIRONMENT_NAME`, which Railway injects itself and an operator cannot mistype. Setting `ENVIRONMENT=staging` on a service in the Production environment is now a startup failure with the fix command in the error text, not a running service that lies about itself.

Two deliberate exemptions, both meaning "the platform has no opinion":

- **The variable is absent** — local checkouts, `docker-compose`, and CI. There `ENVIRONMENT` is the only source of truth, so there is nothing to contradict.
- **A preview environment** (`pr-42`, a personal branch environment) — its name has no counterpart in the three-value vocabulary, so it maps to whatever `ENVIRONMENT` you set. Failing these closed would block previews and buy no safety.

> Running `pytest` inside `railway run` would otherwise fail every test that builds a `Settings`. `tests/conftest.py` clears both Railway variables for the whole suite — the unit tests are offline by design and must not read the platform they happen to be launched from.

## Step 5: Configure the WhatsApp webhook

Green API is gone (PRO-85 — instance deleted, tariff cancelled) and inbound is not yet
provider-abstracted — that lands with PRO-89's Cloud API wiring. Until then there is no
live vendor to point at `/webhook`; when a provider's console asks for a webhook URL, set
it to the **API service** public domain, including the webhook token:
```
https://api-production-XXXX.up.railway.app/webhook?token=YOUR_WEBHOOK_TOKEN
```

## Notes

- The `start.sh` script is kept for local convenience but is NOT used by Railway or Docker Compose.
- Each service builds from the same Dockerfile. Railway caches the build layer so only the first service triggers a full build.
- To scale: only the API and Worker services can safely have `numReplicas > 1`. The Worker requires distributed locking for scheduler jobs before scaling (see `docs/SCALING_GUIDE.md`).
