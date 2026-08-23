# Railway Multi-Service Setup

Proli requires 3 separate Railway services sharing the same repo, MongoDB, and Redis.

## Step 1: Create a Railway Project

Create a new project in Railway. You will add 3 services to it.

## Step 2: Add Shared Infrastructure

Add these plugins/add-ons to the project:
- **Redis** (Railway Redis plugin) - used for ARQ task queue, state, context cache
- **MongoDB** - use MongoDB Atlas (external) and set `MONGO_URI` as a shared variable

## Step 3: Create 3 Services

All 3 services point to the **same GitHub repo** and are intended to use the **same Dockerfile**. They differ only in the start command.

> ⚠️ **They do not currently use it.** As of 2026-08-22 all four services in both environments report `Builder: RAILPACK`, so `Dockerfile` is never built. That is not cosmetic: the image therefore has **no `mongodb-database-tools`**, so PRO-111's nightly backup fails with `mongodump not found`, and the `RUN mongodump --version` build-time guard added specifically to catch that has never executed. `railway.json` declares the Dockerfile builder, but the service-level setting wins — fixing this is a **dashboard action** (each service → Settings → Build → Builder: Dockerfile), not a repo change. Tracked on PRO-128.

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

## Which branch deploys where

| Railway environment | tracks git branch | promotion |
|---|---|---|
| Staging | `dev` | automatic on every merge |
| Production | `production` | **manual fast-forward** |

Staging auto-deploys reliably on every merge to `dev` (the branch renamed from `master` on 2026-08-22).

> ⚠️ **Production's auto-deploy is unreliable — a fast-forward is not enough.** Measured on 2026-08-22: of three pushes to `production`, only the first triggered a deployment. The GitHub ref advanced correctly all three times (verified via the API), and Railway simply did not pick up the last two — the services stayed on the older commit for 15+ minutes with no queued build. **Never assume a promotion deployed.** Always finish with the verification step below, and trigger the deploy from the dashboard if it did not start on its own. This matters more than it sounds: the whole PRO-128 incident was a production that looked promoted and wasn't.

**The decision (2026-08-22, PRO-128): keep the manual promotion gate.** Production should not ship every merge to `dev` unreviewed, particularly before the pilot. What is *not* acceptable is the gate rotting silently, which is exactly what happened: `production` sat on a 2026-07-08 commit for six weeks while the integration branch moved 132 commits ahead, and nobody noticed because a stale branch looks identical to a quiet one. The stale revision still declared the removed WhatsApp vendor's settings fields, so production crash-looped the entire time.

**Preferred: run the `🚢 Promote dev → production` workflow** (Actions → Run workflow). It asserts the fast-forward, asserts `dev`'s CI is green, pushes the ref, and then polls `/health` until production restarts and reports healthy — failing the build if it never does. `dry_run` previews the commit range without touching anything. Use it rather than the manual commands below; the verification is the part that is easy to skip and expensive to skip.

> There is deliberately **no** `railway up` workflow. That command uploads the runner's working directory as a tarball, so it deploys code corresponding to no commit Railway knows about — and the old version of it had no pinned `ref`, meaning "start production" would have shipped `dev`'s tree. If the git integration is broken, fix it in **Settings → Source** or use **Deployments → Redeploy**; do not paper over it with a tarball.

The manual equivalent — a fast-forward, refused if it would not be one:

```bash
git fetch origin
git merge-base --is-ancestor origin/production origin/dev   # must succeed
git push origin dev:production
```

Then **verify the deploy actually started** — this step is not optional, see the warning above:

```bash
# 1. the git ref moved
gh api repos/netanel152/proli/commits/production --jq .sha

# 2. Railway is running that same commit (all three services)
railway status --environment Production
```

If the services are still on the previous commit after a couple of minutes, Railway did not pick the push up. Trigger it by hand: **dashboard → service → Deployments → Redeploy**, for each of `api`, `worker`, `admin`.

Check the gap at any time; if this is not `0`, production is behind `dev`:

```bash
git rev-list --count origin/production..origin/dev
```

**`connect_service_source`'s branch is service-wide, not per-environment.** Railway environments share the service object (Production api and Staging api report the same service id), and the MCP reconnect mutation sets the *service-level* branch even when called with an environment argument. On 2026-08-23 a Staging-scoped reconnect to `dev` silently flipped **Production** onto `dev` for ~25 minutes (contained: muted, identical tested code, no migrations; reconciled by promoting `production` to the same SHA and reconnecting it). If the two environments must track different branches — they must — the per-environment branch override lives only in the **dashboard** (service → Settings inside the specific environment → Source). After ANY source reconnect, check both environments' next builds, not just the one you meant to fix; the PRO-155 detector covers the staging side automatically.

**The deploy trigger itself is monitored (PRO-155).** The Railway↔GitHub trigger has silently died twice (PRO-128: production; PRO-155: staging — all three services sat a day behind `dev` while variable-triggered restarts kept the dashboard green). The `🔎 Verify staging deployed this commit` workflow now runs on every push to `dev` and fails unless staging's authenticated `/health` reports `commit` equal to the pushed SHA within 12 minutes — so a dead trigger is a red X on the merge, not a later surprise. It needs the `STAGING_HEALTH_TOKEN` repo secret (staging's `HEALTH_TOKEN`). The fix, when it fires: reconnect the service source (`connect_service_source` → `netanel152/proli@dev`) for api/worker/admin. Production's equivalent is the promotion workflow's mandatory verify step.

Note that a **variable change also redeploys**, and that path is not equivalent: it restarts the service with the existing image rather than building the new commit. Use it to apply a config change, never to promote code.

If you would rather drop the gate, point the three production services at `dev` in the Railway dashboard (each service → Settings → Source → Branch) and delete the `production` branch, so there is no stale ref left to mislead anyone.

## Step 4: Environment Variables

Set these as **shared variables** (project-level, *within each environment*) so all 3 services inherit them. Which values must **differ** between Staging and Production is specified in the ownership table below — do not copy them across environments:

```
MONGO_URI=mongodb+srv://...
REDIS_URL=redis://...  (auto-set if using Railway Redis plugin)
GEMINI_API_KEY=...
CLOUDINARY_CLOUD_NAME=...
CLOUDINARY_API_KEY=...
CLOUDINARY_API_SECRET=...
ADMIN_PASSWORD_HASH=...  # Generate with: python scripts/generate_admin_hash.py
ADMIN_PHONE=972501234567  # Admin WhatsApp number for SOS alerts
HEALTH_TOKEN=...  # X-Health-Token header value for the detailed /health payload + /health/leads (PRO-136); unset = those views fail closed in staging/production
WEBHOOK_TOKEN=...  # Random string for webhook auth — required (boot fails without it) once ENVIRONMENT is staging/production (PRO-86)
WHATSAPP_PROVIDER=dryrun  # dryrun (default) | cloud — cloud selects the PRO-89 CloudAPIProvider (Meta Graph API)
META_ACCESS_TOKEN=...        # secret — required once WHATSAPP_PROVIDER=cloud and WHATSAPP_DRY_RUN is not true
META_APP_SECRET=...          # secret — signs inbound /webhook/meta; required for cloud in staging/production
META_VERIFY_TOKEN=...        # secret — echoed back during the Meta subscription handshake; required for cloud in staging/production
META_PHONE_NUMBER_ID=...     # not secret — Graph node id, required once WHATSAPP_PROVIDER=cloud and WHATSAPP_DRY_RUN is not true
ENVIRONMENT=production   # per-environment — see below
```

### Which variables are per-environment, and which are intentionally shared

Staging and Production must not share credentials: a leak or compromise of the
lower-trust environment must never authenticate against production, and staging
traffic must never reach Meta as the production number. Decided 2026-08-23;
when copying variables between environments, this table is the contract —
**never copy the full set from one environment to the other**, that is exactly
how the two silently re-merge.

| variable | per-environment | reasoning |
|---|---|---|
| `MONGO_URI`, `REDIS_URL` | **always distinct** | separate datastores; see the seed-guard invariant below |
| `ENVIRONMENT` | **always distinct** | by definition — see PRO-34 above |
| `WEBHOOK_TOKEN` | **distinct** | a shared token means a staging leak authenticates against the production webhook |
| `HEALTH_TOKEN` | **distinct** | authenticates the detailed `/health` payload and `/health/leads` KPIs (PRO-136); same leak logic as the webhook token |
| `ADMIN_PASSWORD_HASH` | **distinct** | one admin credential must not open both panels |
| `SENTRY_DSN` | **distinct** | separate Sentry project per environment, so staging noise cannot pollute production ingest and a leaked staging DSN is worthless |
| `GEMINI_API_KEY` | **distinct** | a staging leak or quota burn must not affect production |
| `META_ACCESS_TOKEN`, `META_APP_SECRET`, `META_VERIFY_TOKEN`, `META_PHONE_NUMBER_ID` | **distinct** | production holds the real pilot number. Staging runs `WHATSAPP_DRY_RUN=true` until it has its **own** test number / test WABA — never point staging at production's phone-number id with dry-run off, because staging sends are then production sends (same number, same Meta quality rating) |
| `WHATSAPP_DRY_RUN` | **distinct** | Staging `true` (muted), Production `false` |
| `CLOUDINARY_*` | shared *(pilot decision)* | media is non-sensitive and a second account is friction with little pilot-stage payoff; revisit post-pilot |
| `GOOGLE_MAPS_API_KEY` | shared *(pilot decision)* | geocoding only; restrict by API + quota in the Google console; revisit post-pilot |
| `ADMIN_PHONE`, `ONCALL_PHONE` | shared | not secrets — the same operator is paged from both |

Railway's **Shared Variables** feature is scoped *within* one environment. It is
the right tool for "define once, inherit in `api`/`worker`/`admin`" — it cannot
and must not be used to share values across Staging and Production.

### `ENVIRONMENT` per Railway environment (PRO-34)

`ENVIRONMENT` accepts exactly `development | staging | production`; anything else raises at startup. It is **not** a shared project variable — set it per Railway environment so the label always matches reality:

| Railway environment | `ENVIRONMENT` |
|---|---|
| Staging | `staging` |
| Production | `production` |

> Environment **ids** were listed here until PRO-128 and both had gone stale — they resolved to nothing, and `scripts/start_railway_services.sh` was passing the same dead uuids to `railway up`. Address environments by name (`--environment Production`); the CLI accepts a name anywhere it accepts an id, and names don't rot. Run `railway environment` to see the current list if you need an id for something else.

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

### `MONGO_URI` per Railway environment (PRO-97)

Each environment points at its **own database**, and this is load-bearing, not tidiness:

| Railway environment | database | contents |
|---|---|---|
| Staging | `proli_staging_db` | empty and seedable — the target of `scripts/seed_db.py` and `scripts/seed_coverage_matrix.py` |
| Production | `proli_db` | live data |

Between 2026-06-27 and 2026-08-13 **all six services shared `proli_staging_db`**, production included. That is dangerous in a specific way: `seed_coverage_matrix.py`'s guard is `DB_NAME == "proli_staging_db"`, which *passed* — it checks what the database is called, not who is reading it. Running the coverage seed would have injected 27 fake professionals into the dataset production served, and `seed_db.py`'s destructive `clear_db()` was reachable at the same time because production also reported `ENVIRONMENT=staging` (PRO-96).

**The invariant to preserve: the database production reads must never be the one seed scripts accept as a target.** When changing `MONGO_URI`, change the *database path only* and set it per service — copying one URI across environments is exactly how the two got merged, and it hides the merge from anyone reading a variable list.

Local `.env` should point at **`proli_staging_db`**, never `proli_db`. It is empty after the split, so seed it:

```bash
python scripts/seed_db.py            # 3 pros, works with ENVIRONMENT=development
# For the 27-pro coverage matrix, set ENVIRONMENT=staging in .env first —
# assert_seed_allowed() requires exactly "staging". The PRO-96 platform
# cross-check does not interfere locally: RAILWAY_ENVIRONMENT_NAME is absent
# outside Railway, so the check exempts you.
python scripts/seed_coverage_matrix.py
```

## Step 5: Configure the WhatsApp webhook

The legacy WhatsApp vendor is gone (PRO-85 — instance deleted, tariff cancelled). Its replacement, the
Meta Cloud API transport (PRO-89), is code-complete and has its own inbound route,
`/webhook/meta` — but there is still no live Meta account to register it with until
PRO-87 (Business Portfolio + template approval) completes. Once it does, register the
**API service** public domain as the Callback URL in the Meta App dashboard, with a
verify token matching `META_VERIFY_TOKEN`:
```
https://api-production-XXXX.up.railway.app/webhook/meta
```
The legacy `/webhook` route (legacy-vendor payload shape) remains for local/manual testing
(`docs/MANUAL_TEST_PLAN.md`) — no live vendor points at it either:
```
https://api-production-XXXX.up.railway.app/webhook?token=YOUR_WEBHOOK_TOKEN
```

## Notes

- The `start.sh` script is kept for local convenience but is NOT used by Railway or Docker Compose.
- Each service is meant to build from the same Dockerfile (Railway caches the build layer so only the first service triggers a full build) — see the Railpack warning in Step 3 for why that is not what is happening today.
- Build config lives in **`railway.json` only**. A second `railway.toml` declaring the same builder was deleted in PRO-128: two sources of build config made it impossible to tell from the repo which one Railway was honouring, and the answer turned out to be neither.
- To scale: only the API and Worker services can safely have `numReplicas > 1`. The Worker requires distributed locking for scheduler jobs before scaling (see `docs/SCALING_GUIDE.md`).
