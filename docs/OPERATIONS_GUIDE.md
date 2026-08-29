# Proli Operations Guide

## Running the System

### Docker (recommended)

```bash
docker-compose up --build -d

# View logs
docker-compose logs -f          # all services
docker-compose logs -f worker   # worker only
docker-compose logs -f api      # API only

# Restart a service
docker-compose restart worker

# Stop everything
docker-compose down
```

### Local development (three terminals)

```bash
# Terminal 1 — API
uvicorn app.main:app --reload --port 8000

# Terminal 2 — Worker (ARQ + APScheduler)
python -m app.worker

# Terminal 3 — Admin panel
streamlit run admin_panel/main.py
```

---

## Logs & Monitoring

Proli uses **Loguru** with PII masking applied to all sinks.

| environment | log of record | how to read it |
|---|---|---|
| local (bare processes) | `logs/proli.log` — rotating at 10 MB, retained 10 days | `Read`/`Grep` the file |
| local (docker-compose) | container stdout | `docker-compose logs --tail=100 worker` / `… api` |
| staging / production | container **stdout**, captured by Railway | Railway dashboard → service → Logs, or the `railway` MCP's `get_logs` |

**There is no `logs/proli.log` in staging or production** (PRO-174). The file sink is development-only: on Railway it wrote to an ephemeral container filesystem that is discarded on every deploy and restart and is unreachable while the service runs, so it bought an archive nobody could read. Prod-like environments emit structured JSON on stdout instead.

- **Console:** Human-readable colored output in development; JSON in production.
- **Correlation (PRO-174):** every line emitted while handling one inbound message carries a `trace_id` — a 12-hex-char id minted at the webhook and forwarded to the ARQ job, so the API's lines and the worker's lines for the same turn share it. Random, tied to nothing — never the customer's phone. Renders as a `trace=<id>` column in development, `"trace_id": "<id>"` in the staging/production JSON.
- **PII masking:** Israeli phone numbers are masked in all environments: `972521234567` → `97252****567`.
- **Address masking (PRO-174):** Hebrew street addresses are replaced with `***ADDRESS***`, leaving the trailing `, <city>` intact as triage context. Latin-script addresses are a known uncovered gap.
- **Secret redaction (PRO-80):** known secret values are replaced with `***REDACTED***` wherever they appear in a log line — a URL query string (e.g. the uvicorn access line `POST /webhook?token=…`), a URL path, an exception string, etc. Complements PRO-79, which suppresses `httpx`/`httpcore` INFO request logs at the source. Since PRO-94 the redaction list is sourced automatically from every `SecretStr` field on `Settings`, so the PRO-89 `META_ACCESS_TOKEN`/`META_APP_SECRET`/`META_VERIFY_TOKEN` are covered without any redaction-list change.
- **Token Accounting (FinOps):** AI token usage is tracked per `pro_id` and stored in the `total_tokens_used` field of the `users` collection. This is handled by a fire-and-forget background task.

Log patterns to watch:

| Pattern | Meaning |
|---------|---------|
| `Model ... failed` | AI fallback triggered |
| `All AI models failed` | All Gemini models exhausted — check API key and quota |
| `$geoNear` / `Expanding search radius` | Geo search expanding to next radius step |
| `No professional found within 30km` | Escalating to PENDING_ADMIN_REVIEW |
| `[SOS Healer]` | Auto-recovery running |
| `[Janitor]` | Cleaning up unassigned stale leads |
| `worker:heartbeat` | Worker liveness key (120 s expiry) |
| `[WA Monitor]` | WhatsApp provider account state watchdog (deauth); skips its tick entirely for a non-transmitting provider (dry-run) |
| `⛔ Outbound halted` | Circuit breaker suppressing sends — instance not authorized |
| `Geocoding unavailable — circuit opened` | `page_critical` → Sentry page; Google Geocoding is failing transiently (missing key, quota, network) and the `geo:unavailable` breaker is open (PRO-19) |

### Incident runbooks

- **WhatsApp outbound outage** → [`RUNBOOK_WHATSAPP_OUTAGE.md`](RUNBOOK_WHATSAPP_OUTAGE.md) — detection, the automatic fail-closed circuit breaker, the manual kill switch, and prevention.

---

## Scheduler Jobs

All jobs run in the Worker process. Individual jobs can be toggled via MongoDB:

```python
# Disable the SOS healer (e.g. during maintenance)
db.settings.update_one(
    {"_id": "scheduler_config"},
    {"$set": {"sos_healer_active": False}},
    upsert=True
)
```

Toggle fields: `sos_healer_active`, `sos_reporter_active`, `stale_monitor_active`, `whatsapp_monitor_active`, `lead_janitor_active`, `sla_monitor_active`.

**PRO-73 pilot posture:** `sos_healer_active`, `lead_janitor_active`, and `sla_monitor_active` gate *cold, customer-facing* re-engagement jobs and **default OFF** in the config `$setOnInsert` — they stay dark until an operator turns them on after the WhatsApp number is warmed up. Even when on, these three only run inside business hours (08:00–21:00 Israel time; see `within_business_hours()` in `app/core/datetime_utils.py`). Enable one via the same `settings_collection.update_one` pattern above, e.g. `{"$set": {"lead_janitor_active": True}}`.

---

## SOS & Healer System

### How it works

The **SOS Healer** (every 10 min; PRO-73: business hours + `sos_healer_active` toggle, default OFF) finds leads in `new` or `contacted` status older than 60 minutes (`pending_admin_review` is excluded — it's already a terminal state for the Healer):

1. If max reassignments (3) already reached → escalates the lead to `PENDING_ADMIN_REVIEW`, alerts the admin immediately, and notifies the customer a human will call back within the hour (PRO-63 — a human takes over, the lead is never closed by this)
2. Notifies customer of the delay
3. Searches for a replacement pro (excluding the current one)
4. If found → reassigns the lead, notifies both pros, clears customer state (a failed offer to the new pro escalates the lead to `PENDING_ADMIN_REVIEW` and pages the operator instead of reporting success)
5. If not found → sets lead to `PENDING_ADMIN_REVIEW`, sends customer a `PENDING_REVIEW` message, clears context

The **Stale Lead Nudger** (every 4 h) finds leads in `BOOKED` status older than 24 hours:
1. Sends a reminder to the professional to close the job if finished.
2. Helps prevent `MAX_PRO_LOAD` (3) issues by ensuring completed jobs are cleared from the system.
3. Limits reminders to `MAX_PRO_REMINDERS` (3) per lead.

The **SLA Monitor** (every 5 min; PRO-73: business hours + `sla_monitor_active` toggle, default OFF) checks chats in the `PAUSED_FOR_HUMAN` state:
1. If 15 minutes of silence pass, the bot sends `Messages.Customer.SLA_DEFLECTION_MESSAGE`.
2. This proactive "wake up" offers the customer a telephone call escalation if the Pro is unresponsive.

The **Pro-Approval SLA** monitor (every 5 min) chases a silent pro on a `NEW` lead awaiting approval, timed from `pro_notified_at`, instead of waiting for the 60-min SOS Healer:
1. At T+10 min (`APPROVAL_NUDGE_MINUTES`), nudges the pro once.
2. At T+25 min (`APPROVAL_REASSIGN_OFFER_MINUTES`), offers the customer a reassignment once — gated to business hours (PRO-73); the pro nudge in step 1 is not gated.
3. Emergency leads use half of both thresholds. Both steps are idempotent via boolean flags on the lead.

The **SOS Reporter** (every 4 h) pages the operator via `page_operator` (Sentry → email, not WhatsApp) once per newly-stuck lead, atomically stamping `admin_reported_at` so the same lead is muted for `SOS_REPORT_REPAGE_HOURS` (24h) afterward; the standing already-paged backlog is logged, not re-paged. A quiet tick therefore means "already paged," not "resolved" — check the admin panel, not the absence of new pages, to know whether a stuck lead is still open.

### Customer-triggered pause

A customer sending "אני צריך נציג" (or similar):
1. Sets their state to `PAUSED_FOR_HUMAN` (15-minute dynamic rolling window)
2. Alerts admin and the assigned pro
3. Sends customer `BOT_PAUSED_BY_CUSTOMER` message
4. All subsequent messages reset the 15-minute timer.
5. Bot auto-resumes when TTL expires, or when the pro sends "המשך"

---

## Pro Approval Flow

When a deal is finalized by the AI:

1. Customer enters `AWAITING_PRO_APPROVAL` state (1h TTL) — bot replies with "still waiting" if they message again
2. Pro receives a text-based approval request (reply "אשר" or "1"):
   - **Approve** (reply "1" or "אשר") → lead becomes `BOOKED`, customer state cleared. System enforces strict scoping (pro must have a pending lead assigned).
   - **Pause Bot** (reply "השהה") → customer enters `PAUSED_FOR_HUMAN` (15m rolling), direct chat begins
   - **Reject** (reply "2" or "דחה") → lead becomes `REJECTED` then immediately handed to `reassign_lead`: reassigned → `NEW` under the next pro (customer told, approval SLA re-armed); no replacement, or a found pro whose offer fails to send → `PENDING_ADMIN_REVIEW` (admin paged, customer told)
3. Pro can manage availability:
   - **Vacation Mode** (reply "חופשה" or "הפסקה") → sets `is_active: False`, pro stops receiving new leads.
   - **Resume** (reply "זמין") → sets `is_active: True`.
4. Finishing Jobs:
   - **Finish** (reply "סיימתי" or "3") → if single job, marks as `COMPLETED`. If multiple, pro picks from a numbered list.
5. **Dynamic Dashboard:** Any unknown command or "תפריט" sends the pro a real-time status summary (rating, active jobs, status).

---

## Customer Self-Service: Status Query

Customers can send any of the following at any time to receive an instant status reply:

| Trigger | Match rule |
|---------|-----------|
| `סטטוס` | whole message equals (case-insensitive) |
| `status` | whole message equals (case-insensitive) |
| `?` | **exact** — the entire trimmed message must be `?` |

**Behaviour:**

- Runs before the AI dispatcher — deterministic, zero token cost, works in every state except `PRO_MODE` and `ADMIN_*`.
- If an active lead exists (`NEW`, `CONTACTED`, `BOOKED`, `PENDING_ADMIN_REVIEW`): returns a formatted status message including issue type, assigned pro (if any), and appointment time.
- If no active lead but a recent terminal lead exists (`COMPLETED`, `CANCELLED`, `REJECTED`, `CLOSED`): returns that lead's terminal-state message.
- If no leads at all: returns a friendly "no active request" message prompting the customer to open a new one.
- State is **not changed** by this command. Logs the reply via `lead_manager.log_message`.


---

## Admin Panel

Access at `http://localhost:8501` (local) or via nginx proxy at port 8080 (Docker).

### RBAC Roles

| Role | Permissions |
|------|------------|
| Owner | Full access — manage admins, view audit log, all edits |
| Editor | Edit leads, professionals, schedules |
| Viewer | Read-only dashboard |

Manage admins under **Settings → Admin Users** (Owner only). All actions are logged to the audit log.

**Fallback auth:** If no admins exist in the DB, the system accepts the `ADMIN_PASSWORD` env var.

### Lead Management

- Edit lead fields directly in the data table
- Change status, assigned pro, issue details
- Click **Save Changes** to persist to MongoDB

### Professional Management

- View all pros, toggle `is_active`, edit profiles
- Approve pending registrations (from WhatsApp self-signup)
- Set `system_prompt`, `price_list`, `service_areas`

---

## Pro Onboarding (Self-Signup)

Professionals can register directly via WhatsApp:

1. Send "הרשמה" to the bot
2. Complete 5-step questionnaire: business name → service type → service areas → pricing → confirm
3. Profile submitted for admin approval
4. Admin approves/rejects from the "Pending Approval" section in the admin panel
5. Pro receives WhatsApp notification of the decision

---

## Backup & Restore

### Decision of record (PRO-111)

The primary backup mechanism is the **in-app nightly `mongodump` → Cloudflare R2** path (via R2's S3-compatible API — plain AWS S3 works too by leaving `BACKUP_S3_ENDPOINT` unset). Atlas continuous backups are not available on the current cluster tier (M10+ only), so the in-app job is not a redundancy — it is the only durable backup. Treat its health accordingly.

### Automated backup

Runs daily at 02:00 IL via APScheduler (`run_daily_backup` on the worker) — **production only** (PRO-127): `start_scheduler` registers the job only when `settings.is_production`; staging/development log an info line instead (PRO-127's decision of record; staging *does* hold working R2 credentials — they were used for the PRO-111 verification runs — but the job stays production-only so staging can never page about an environment that isn't meant to back up). Creates a gzipped `mongodump` and uploads it to `s3://$BACKUP_S3_BUCKET/proli-backups/`.

**S3 upload is mandatory, not optional** — `backup.py --upload-s3` exits non-zero if `BACKUP_S3_BUCKET` is unset or the upload fails, because a local archive on Railway's ephemeral filesystem is wiped on every redeploy. The local `backups/` copy is a working artifact, not a backup.

Required on the Railway **production worker** service only (PRO-127) — staging must not set these:

| Variable | Purpose |
|---|---|
| `BACKUP_S3_BUCKET` | Destination bucket (objects land under `proli-backups/`) |
| `BACKUP_S3_ENDPOINT` | S3-compatible endpoint — for Cloudflare R2: `https://<account_id>.r2.cloudflarestorage.com`. Unset = regular AWS S3 |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | R2 API token credentials (or an AWS IAM user scoped to `s3:PutObject`/`s3:GetObject`/`s3:ListBucket` on the bucket) |
| `AWS_REGION` (or `AWS_DEFAULT_REGION`) | With R2: set `AWS_REGION=auto`. With AWS S3: the bucket region (e.g. `eu-west-1`) |

The runtime Docker image installs `mongodb-database-tools` from MongoDB's official apt repo (PRO-111) — `mongodump`/`mongorestore` are available in every container built from `Dockerfile`.

### The live R2 target (provisioned 2026-08-15, PRO-111)

| What | Value |
|---|---|
| Bucket | `proli-backups`, location **EEUR** (closest R2 region to Israel/Atlas), Standard class |
| Endpoint | `https://01aad977bca59c60adf697873a5dece8.r2.cloudflarestorage.com` |
| Object keys | `proli-backups/proli_<YYYYMMDD_HHMMSS>_<weekday>.gz` (the `proli-backups/` prefix inside the bucket is redundant but harmless — expect it in listings) |
| Lifecycle rule 1 | `expire-backups-after-35-days` — objects deleted after 35 days (≈ the documented 7 daily + 4 weekly coverage) |
| Lifecycle rule 2 | `abort-stuck-multipart-uploads-after-1-day` — interrupted uploads can't linger as billable parts |

**Retention is bucket-side, deliberately.** `scripts/backup.py::cleanup_old_backups` deletes **local files only**; on Railway's ephemeral disk that path is a no-op in practice, and nothing app-side ever deletes uploaded objects. The lifecycle rules above are the real retention mechanism — enforced by the storage provider rather than by a job that must succeed in order to clean up. Local retention (relevant for local/dev runs only): 7 daily + 4 weekly.

### Failure escalation (PRO-111)

- **1 failed nightly run** → `ERROR` log (visible in Railway logs; assumed transient).
- **2 consecutive failures** (`WorkerConstants.BACKUP_FAILURE_ESCALATION_THRESHOLD`) → `CRITICAL` log → **Sentry pages the operator** (backups are now >48h stale).
- The streak counter lives in Redis (`backup:consecutive_failures`), is shared across worker replicas, survives redeploys, and is cleared by the first successful run. If Redis itself is down the job fails open: the failure is logged at ERROR and the job never crashes.

> **Known gap:** escalation only fires when the job *runs*. A worker that is down at 02:00 (or an APScheduler misfire) produces no failure and no page. Freshness monitoring (`backup:last_success` + a >48h watchdog) is tracked separately.

### Verification procedure (run after any change to backup config)

1. On the Railway worker: `railway run python scripts/backup.py --upload-s3` (or wait for the 02:00 run) — expect exit 0 and `Uploaded to s3://...` in the log.
2. Confirm the object exists in the bucket (S3 console or `aws s3 ls s3://$BACKUP_S3_BUCKET/proli-backups/`).
3. **Restore drill** against a scratch DB — a backup that was never restored is a hypothesis, not a backup: point `MONGO_URI` at a scratch cluster/database, then `python scripts/restore.py --from-s3 proli-backups/<latest-key>` and spot-check `users`/`leads` counts.
4. Attach the run log / S3 listing to the relevant Linear issue when this is a launch-gate item.

### Manual commands

```bash
# Create backup
python scripts/backup.py

# Create and upload to S3
python scripts/backup.py --upload-s3

# Restore from latest local backup
python scripts/restore.py --latest

# Restore from S3
python scripts/restore.py --from-s3 <key>

# Restore without dropping existing data
python scripts/restore.py --no-drop
```

---

## Troubleshooting

### Bot responds as "Proli Support" (default persona)

The routing engine found no matching pro.

- Check: are any pros with `is_active: True` in the DB?
- Check: does the customer's city match any pro's `service_areas`?
- Fix: add service areas or activate pros in the admin panel.

### Bot doesn't respond to images / audio / video

- Check: is the media URL publicly accessible?
- Check logs for: `Error downloading media` or `Gemini File Processing Failed`
- For video: Gemini waits up to 120 s for processing — timeouts are logged as errors.

### Leads not appearing in the dashboard

- Click **Refresh Leads** in the admin panel.
- Verify you're connected to the correct MongoDB instance.

### AI always failing

- Check logs for: `All AI models failed`
- Verify `GEMINI_API_KEY` is valid and has quota remaining

---

## Security

### Webhook

Configure the full URL, including the token, at your WhatsApp provider: `https://your-domain/webhook?token=<value>`. Requests without a valid token receive `403 Forbidden`. `WEBHOOK_TOKEN` is **required** (the app refuses to boot without it) whenever `ENVIRONMENT` is `staging`/`production` — PRO-86 removed the sender instance-id check that used to be the other half of webhook authentication, so an unset token would otherwise mean no authentication at all.

The PRO-89 Meta Cloud API route (`GET`/`POST /webhook/meta`) uses a separate auth model, not `WEBHOOK_TOKEN`: the `GET` handshake checks `hub.verify_token` against `META_VERIFY_TOKEN`, and every `POST` is authenticated by an `X-Hub-Signature-256` HMAC over the raw body keyed by `META_APP_SECRET` (both required for `cloud` in staging/production).

### Admin authentication

Passwords are never stored in plaintext. The system uses bcrypt with a random salt. Sessions use `secrets.token_hex(32)` tokens validated server-side on each request. Logout invalidates the token immediately.

Generate a password hash:
```bash
python scripts/generate_admin_hash.py
```

### Slot booking atomicity

`book_slot_for_lead` uses MongoDB `find_one_and_update` to atomically find a free slot and mark it taken — preventing double-booking even under concurrent requests. It returns the reserved slot's `_id` (`Optional[ObjectId]`, `None` if no slot was available), which the approve path persists as the lead's `booked_slot_id` so release/reschedule always free the exact reserved slot rather than a sibling slot from another active job.

---

## Environment Variables Reference

### Required

| Variable | Description |
|----------|------------|
| `GEMINI_API_KEY` | Google Gemini API key |
| `CLOUDINARY_CLOUD_NAME` | Cloudinary account name |
| `CLOUDINARY_API_KEY` | Cloudinary API key |
| `CLOUDINARY_API_SECRET` | Cloudinary API secret |

### Optional

| Variable | Default | Description |
|----------|---------|------------|
| `MONGO_URI` | `mongodb://localhost:27017/proli_db` | MongoDB connection string |
| `MONGO_TEST_URI` | — | Separate DB for integration tests |
| `REDIS_HOST` | `redis` | Redis hostname |
| `REDIS_PORT` | `6379` | Redis port |
| `REDIS_URL` | — | Full Redis DSN (overrides HOST/PORT) |
| `ADMIN_PASSWORD` | — | Plain-text password (hashed on startup) |
| `ADMIN_PHONE` | `972524828796` | Admin WhatsApp number for SOS alerts |
| `WEBHOOK_TOKEN` | — | Enables `?token=<value>` webhook auth. **Required** (boot fails without it) when `ENVIRONMENT` is `staging`/`production` — PRO-86 removed the other half of webhook authentication (the sender instance-id check) |
| `WHATSAPP_PROVIDER` | `dryrun` | Outbound transport: `dryrun` (logs, never transmits) or `cloud` (the PRO-89 `CloudAPIProvider` — Meta Graph API, code-complete but not yet onboarded, see PRO-87). An unrecognised value falls back to `dryrun` |
| `META_ACCESS_TOKEN` | — | Secret. Meta Graph API System User token; required once `WHATSAPP_PROVIDER=cloud` and `WHATSAPP_DRY_RUN` is not `true` |
| `META_APP_SECRET` | — | Secret. Signs inbound `/webhook/meta` (`X-Hub-Signature-256`); required for `cloud` in staging/production |
| `META_VERIFY_TOKEN` | — | Secret. Echoed back during the Meta subscription handshake (`GET /webhook/meta`); required for `cloud` in staging/production |
| `META_PHONE_NUMBER_ID` | — | Not secret. Graph API phone-number node id; required once `WHATSAPP_PROVIDER=cloud` and `WHATSAPP_DRY_RUN` is not `true` |
| `META_GRAPH_API_VERSION` | `v23.0` | Graph API version pinned in every outbound request URL |
| `ENVIRONMENT` | `development` | One of `development` \| `staging` \| `production` — any other value raises at startup. `staging` and `production` are both "prod-like": JSON logs + PII masking on stdout, `diagnose=False`, and `MONGO_URI` required by the admin panel. `production` additionally blocks `scripts/seed_db.py` |
| `LOG_LEVEL` | `INFO` | Loguru log level |
| `MAX_CHAT_HISTORY` | `20` | Max messages stored per chat in Redis |
| `AI_MODELS` | Flash Lite 3.1, Flash 3.5, Flash 2.5, Flash 1.5 | Gemini model fallback chain |
| `BACKUP_S3_BUCKET` | — | Bucket for the nightly backup — **required on the production worker only** (PRO-127: the job is not scheduled at all in staging/development, so it does not need this there; PRO-111: the nightly job fails without it in production) |
| `BACKUP_S3_ENDPOINT` | — | S3-compatible endpoint (Cloudflare R2); unset = AWS S3 |
| `AWS_ACCESS_KEY_ID` | — | AWS credentials for S3 (required on the production worker with `BACKUP_S3_BUCKET`) |
| `AWS_SECRET_ACCESS_KEY` | — | AWS credentials for S3 (required on the production worker with `BACKUP_S3_BUCKET`) |
| `AWS_REGION` | — | Region — boto3 has **no** default; unset → `NoRegionError` → backup failure. With R2 set `auto` |
| `GOOGLE_MAPS_API_KEY` | — | Google Geocoding API key; falls back to static city dict if unset |
| `GEOCODING_NEGATIVE_TTL_SECONDS` | `86400` | How long a **definitive** geocoding miss is cached (Google answered `ZERO_RESULTS`, or the match fell outside Israel) |
| `GEOCODING_TRANSIENT_TTL_SECONDS` | `60` | How long a **transient** geocoding failure is cached (missing key, `REQUEST_DENIED`, `OVER_QUERY_LIMIT`, network error). Deliberately short: these say nothing about the city, so inheriting the 24 h TTL would keep every name attempted during an outage unresolvable for a day after the fix (PRO-19) |
| `SENTRY_DSN` | — | Sentry error reporting DSN, set on all three services (api/worker/admin) via the shared `app/core/sentry.py` `init_sentry()`; disabled if unset |
| `SENTRY_TRACES_SAMPLE_RATE` | `0.0` | Sentry performance tracing sample rate (0.0 = off) |
