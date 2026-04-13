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

- **Console:** Human-readable colored output in development; JSON in production.
- **File:** `logs/proli.log` — rotating at 10 MB, retained 10 days, gzip-compressed.
- **PII masking:** Israeli phone numbers are masked in all environments: `972521234567` → `97252****567`.

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

Toggle fields: `sos_healer_active`, `sos_reporter_active`, `stale_monitor_active`.

---

## SOS & Healer System

### How it works

The **SOS Healer** (every 10 min) finds leads in `new`, `contacted`, or `pending_admin_review` status older than 60 minutes:

1. Notifies customer of the delay
2. Searches for a replacement pro (excluding the current one)
3. If found → reassigns the lead, notifies both pros, clears customer state
4. If not found → sets lead to `PENDING_ADMIN_REVIEW`, sends customer a `PENDING_REVIEW` message, clears context
5. If max reassignments (3) reached → closes the lead, notifies customer

The **SOS Reporter** (every 4 h) sends a batched WhatsApp summary of all still-stuck leads to the admin number (`ADMIN_PHONE`).

### Customer-triggered pause

A customer sending "אני צריך נציג" (or similar):
1. Sets their state to `PAUSED_FOR_HUMAN` (2-hour auto-expiry via Redis TTL)
2. Alerts admin and the assigned pro
3. Sends customer `BOT_PAUSED_BY_CUSTOMER` message
4. All subsequent messages are logged silently — no bot response
5. Bot auto-resumes when TTL expires, or when the pro sends "המשך"

---

## Pro Approval Flow

When a deal is finalized by the AI:

1. Customer enters `AWAITING_PRO_APPROVAL` state — bot replies with "still waiting" if they message again
2. Pro receives an approval message with 3 buttons:
   - **Approve** → lead becomes `BOOKED`, customer state cleared
   - **Pause** → customer enters `PAUSED_FOR_HUMAN` (2 h), direct chat begins
   - **Reject** → lead becomes `REJECTED`, system may re-route
3. Pro can resume the bot with "המשך" → clears customer pause state

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

### Automated backup

Runs daily at 02:00 IL via APScheduler. Creates a gzipped `mongodump`, saved to `backups/`. Optionally uploads to S3 if `BACKUP_S3_BUCKET` and AWS credentials are configured.

Retention: 7 daily + 4 weekly backups.

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
- Check available models: `python scripts/check_models.py`

---

## Security

### Webhook

If `WEBHOOK_TOKEN` is set, configure the full URL in Green API: `https://your-domain/webhook?token=<value>`. Requests without a valid token receive `403 Forbidden`.

### Admin authentication

Passwords are never stored in plaintext. The system uses bcrypt with a random salt. Sessions use `secrets.token_hex(32)` tokens validated server-side on each request. Logout invalidates the token immediately.

Generate a password hash:
```bash
python scripts/generate_admin_hash.py
```

### Slot booking atomicity

`book_slot_for_lead` uses MongoDB `find_one_and_update` to atomically find a free slot and mark it taken — preventing double-booking even under concurrent requests.

---

## Environment Variables Reference

### Required

| Variable | Description |
|----------|------------|
| `GREEN_API_INSTANCE_ID` | WhatsApp Business API instance ID |
| `GREEN_API_TOKEN` | Green API authentication token |
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
| `WEBHOOK_TOKEN` | — | Enables `?token=<value>` webhook auth |
| `ENVIRONMENT` | `development` | `production` enables JSON logs + PII masking on stdout |
| `LOG_LEVEL` | `INFO` | Loguru log level |
| `MAX_CHAT_HISTORY` | `20` | Max messages stored per chat in Redis |
| `AI_MODELS` | Flash Lite 2.5, Flash 2.5, Flash 1.5 | Gemini model fallback chain |
| `BACKUP_S3_BUCKET` | — | S3 bucket for automated backup upload |
| `AWS_ACCESS_KEY_ID` | — | AWS credentials for S3 |
| `AWS_SECRET_ACCESS_KEY` | — | AWS credentials for S3 |
| `AWS_REGION` | `eu-west-1` | AWS region |
| `SMS_API_KEY` | — | InforUMobile SMS API key (optional fallback) |
| `SMS_SENDER_ID` | `Proli` | SMS sender name |
