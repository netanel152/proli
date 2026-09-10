# Proli Scripts Guide

All scripts run from the project root:

```bash
python scripts/<script_name>.py
```

---

## Database Management

### `seed_db.py`

Populates MongoDB with sample professionals (plumbers, electricians) and test leads. Clears existing collections first. Refuses to run when `ENVIRONMENT=production` (PRO-34) — only `development` and `staging` are permitted targets.

```bash
python scripts/seed_db.py
```

### `seed_coverage_matrix.py`

Staging-only. Seeds 27 deterministic professionals positioned around `WorkerConstants.GEO_RADIUS_STEPS` so `matching_service.determine_best_pro` (radius expansion, load balancing, rating sort, text fallback, coverage gaps) can be exercised with a known-correct answer per scenario (PRO-84, `docs/MANUAL_TEST_PLAN.md` TC-19..TC-28). Every document is tagged `seed_batch: "coverage_v1"`.

Refuses to write anything unless `ENVIRONMENT=staging`, the target database is `proli_staging_db`, the configured WhatsApp provider cannot transmit (`provider.transmits` is `False` — i.e. `WHATSAPP_DRY_RUN=true` or `WHATSAPP_PROVIDER=dryrun`; PRO-86 replaced the legacy vendor's production-instance-id check with this capability check), and no untagged (foreign) professional already exists in the database. All seeded phone numbers fall in `972000000100`-`972000000199` — structurally unreachable, since a valid Israeli MSISDN never has `0` immediately after `972`.

Not safe to run alongside `scripts/seed_db.py` in either direction: `seed_db.py`'s `clear_db()` wipes the matrix, and its own seeded pro outranks the intended TC-20 winner.

```bash
python scripts/seed_coverage_matrix.py                       # seed the base 27 pros (no artificial load)
python scripts/seed_coverage_matrix.py --scenario load-balance     # + overload the top 3 S01 pros (TC-20)
python scripts/seed_coverage_matrix.py --scenario overload-shfela  # + overload every S04 pro (TC-21)
python scripts/seed_coverage_matrix.py --purge                # remove only this batch (seed_batch=coverage_v1)
```

`--scenario` is repeatable and off by default.

### `migrate_unknown_address.py`

One-time migration. Finds all leads with `full_address = "Unknown Address"` (a legacy sentinel value) and clears the field so the geocoding service can resolve it properly on the next interaction.

```bash
python scripts/migrate_unknown_address.py
```

### `check_pro_service_areas.py`

Backfill check for the pro-approval geocode validation. Geocodes every active pro's `service_areas` through the routing pipeline (static dict → Redis cache → Google) and prints, per pro, which areas resolved (✓), which Google does not know (✗ — correct them in the admin panel) and which could not be checked because the geocoder was unavailable (?). Dry run by default.

```bash
python scripts/check_pro_service_areas.py                       # report only
python scripts/check_pro_service_areas.py --include-inactive    # also paused pros
python scripts/check_pro_service_areas.py --apply               # write `location` where missing + verdict fields
python scripts/check_pro_service_areas.py --apply --overwrite-location
```

Exit `0` when every checked pro resolves, `1` when some pro needs a correction (or resolves but carries no `location` — re-run with `--apply`), `2` when the geocoder was unavailable for at least one area, `3` when **no pro matched the filter at all**. That last one is deliberately not `0`: "every checked pro is fine" is vacuously true of an empty set, so a green run that examined nothing would be indistinguishable from one that examined everything — the failure this script exists to catch, aimed at itself. On `3` the report says which population is empty (none on record / all awaiting approval / all paused). Needs `REDIS_URL` and `GOOGLE_MAPS_API_KEY` for anything outside `ISRAEL_CITIES_COORDS`; without the key nothing is ever marked *unresolved*.

Against staging or production, run it through the **`🗺️ Check pro service areas`** workflow rather than by hand: the branch picks the environment (`dev` → staging, `production` → production), `railway run` supplies the credentials, the job exits with the script's own code, and the report is written to the run's job summary — which is the artifact to paste onto the issue.

### `create_indexes.py`

Creates MongoDB indexes for query performance. Run once when setting up a new environment.

Indexes created: `phone_number` (unique), `location` (2dsphere), `chat_id`, `status`, `pro_id+status` (compound), `status+created_at` (compound).

```bash
python scripts/create_indexes.py
```

### `clear_history.py`

Wipes all conversation history from the `messages` collection and clears Redis context keys.

```bash
python scripts/clear_history.py
```

### `generate_admin_hash.py`

Interactive — prompts for a plain-text password and outputs the bcrypt hash to paste into `.env` as `ADMIN_PASSWORD`.

```bash
python scripts/generate_admin_hash.py
```

### `backup.py`

Creates a gzipped MongoDB backup via `mongodump`. Also runs automatically daily at 02:00 IL via APScheduler.

Retention policy: 7 daily + 4 weekly backups.

```bash
python scripts/backup.py              # Local backup only
python scripts/backup.py --upload-s3  # Backup + upload to S3
python scripts/backup.py --cleanup    # Prune old backups per retention policy
```

Requires `BACKUP_S3_BUCKET`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` for S3 upload.

### `restore.py`

Restores MongoDB from a local gzip archive or S3. Prompts for confirmation before dropping data.

```bash
python scripts/restore.py --latest          # Restore most recent local backup
python scripts/restore.py --from-s3 <key>   # Download from S3 and restore
python scripts/restore.py --no-drop         # Restore without dropping existing collections
```

---

## Testing & Simulation

### `simulate_webhook.py`

Interactive webhook simulator. Prompts for a message, builds a valid legacy-envelope JSON payload, and POSTs it to `http://localhost:8000/webhook`. Lives under `tests/`, not `scripts/`.

```bash
python tests/simulate_webhook.py
```

### `fire_test_page.py`

Fires one clearly-marked test CRITICAL through the `page_critical` → Sentry paging path (PRO-113 verification) using the shared `app/core/sentry.py` `init_sentry()`. Run once per service, in that service's environment, to confirm the DSN, integration wiring, and alert rule work end-to-end.

```bash
railway run python scripts/fire_test_page.py --service worker
railway run python scripts/fire_test_page.py --service api
```

### `simulate_sla_deflection.py`

Simulates a 15-minute silence for a specific `chat_id` by setting its Redis state to `PAUSED_FOR_HUMAN` and backdating its `paused_at` timestamp in MongoDB. This triggers the SLA Monitor on its next run — provided it's business hours (08:00–21:00 IL) and `sla_monitor_active` is enabled (defaults OFF, PRO-73).

```bash
python scripts/simulate_sla_deflection.py 972501234567
```

### `simulate_approval_sla.py`

Fast-forwards the PRO-56 pro-approval SLA clock so pilot E2E scenario 3 is testable in seconds instead of waiting 10–25 min. Ages a customer's NEW lead's `pro_notified_at` and sets Redis state to `AWAITING_PRO_APPROVAL`, so the next `run_pro_approval_sla` tick (every 5 min) fires. Prefers the customer's existing lead (real pro assigned); `offer` mode only sends during business hours (PRO-73 gate).

```bash
python scripts/simulate_approval_sla.py 972501234567 nudge   # → nudge the pro (~T+10)
python scripts/simulate_approval_sla.py 972501234567 offer   # → customer reassignment offer (~T+25, in-hours)
```

---

## Analytics & Reports

### `finops_report.py`

Generates a summary of total Google Gemini tokens used per professional, sorted by highest consumption.

```bash
python scripts/finops_report.py
```

### `reset_test.py`

Clears test state: deletes test leads/messages from MongoDB, wipes Redis state/context/webhook keys.

```bash
python scripts/reset_test.py --all            # Full environment wipe
python scripts/reset_test.py 972501234567     # Wipe specific customer only
```

### `init_production.py`

One-time production initialization: creates indexes, seeds required settings documents, and verifies connectivity.

```bash
python scripts/init_production.py
```
