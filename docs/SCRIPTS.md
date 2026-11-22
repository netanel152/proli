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

Against staging or production, run it through the **`🗺️ Check pro service areas`** workflow rather than by hand: the branch picks the environment (`dev` → staging, `production` → production), the connection settings come from repo secrets, the job exits with the script's own code, and the report is written to the run's job summary — which is the artifact to paste onto the issue. It also prints the database name it connected to, so a `Checked 0 pros` result can be told apart from a run that reached the wrong database.

### `normalize_pro_phones.py`

PRO-224 backfill. Reports every pro whose `phone_number` is not the bare `9725…` form the bot looks a pro up by (phones masked to the last four digits), and with `--apply` rewrites it via `app.core.phone.to_il_mobile`. A phone that cannot be an Israeli mobile is reported as `INVALID` and never touched. Refuses to write anything if two users would end up with the same number (`phone_number` is unique). Dry run by default.

```bash
python scripts/normalize_pro_phones.py           # report only
python scripts/normalize_pro_phones.py --apply   # write the normalised phones
```

Exit `0` when every pro is already in the stored form, `1` when some need fixing (dry run) or are invalid, `2` on a would-be duplicate (nothing written), `3` when there is no pro on record.

### `backfill_pro_trades.py`

PRO-234 backfill. Routing now offers a pro only the trades in its `type` + `categories`, so every handyman / general pro without `trades_set_at` gets the list that reproduces the old rule (every trade except electrical, plus electrician where `handles_electrical` was set) and is stamped `trades_set_at`. It also upserts the `תחומי עבודה:` line into a non-empty `system_prompt` and `$unset`s `handles_electrical` from every pro. Idempotent via `trades_set_at`; dry run by default.

```bash
python scripts/backfill_pro_trades.py           # report only
python scripts/backfill_pro_trades.py --apply   # write the lists
```

Exit `0` when nothing is left to do, `1` when some pros need it (dry run), `3` when there is no pro on record. Against staging or production, run the **`🧰 Backfill pro trades`** workflow (branch picks the environment, report in the job summary). Run it with `apply` ticked right after the deploy, or handyman/general pros are offered nothing but handyman work.

### `create_indexes.py`

Creates every MongoDB index in the `INDEX_SPECS` table. A CLI wrapper: the table and the logic are `app/db/indexes.py`, which the API also runs on every boot (`create_all_indexes(silent=True)`), so a manual run only primes a brand-new database or shows the list.

A failed index (a unique index blocked by duplicates, an options conflict such as a changed TTL) is always logged at ERROR, `silent` or not, and returned; the API boot then logs how many failed instead of "✅ MongoDB indexes verified." An index the database holds but the table does not list (a superseded one) is logged at WARNING and never dropped (PRO-250).

```bash
python scripts/create_indexes.py
```

### `clear_history.py`

Deletes every document in `messages`, `leads` and `reviews` (pros and slots stay), after a `y/n` prompt. Refuses to run unless `ENVIRONMENT` is `development` or `staging`, checked before the prompt.

```bash
python scripts/clear_history.py
```

### `generate_admin_hash.py`

Interactive — prompts for a plain-text password and outputs the bcrypt hash to paste into `.env` as `ADMIN_PASSWORD`.

```bash
python scripts/generate_admin_hash.py
```

### `backup.py`

Creates a gzipped MongoDB backup via `mongodump`. A CLI wrapper: the backup itself is `app/db/backup.py`, which the worker runs daily at 02:00 IL (production only) as `python -m app.db.backup --upload-s3`. Archives still land in the repository root's `backups/`.

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

Interactive webhook simulator. Prompts for a message, builds a Meta Cloud API `messages` payload carrying it, and POSTs it to `http://localhost:8000/webhook/meta`. When `META_APP_SECRET` is set in its environment it signs the body with `X-Hub-Signature-256`, as the route requires whenever the backend has that secret; without it the body goes unsigned, which only a development backend accepts. Lives under `tests/`, not `scripts/`.

```bash
python tests/simulate_webhook.py
```

### `fire_test_page.py`

Fires one clearly-marked test CRITICAL through the `page_critical` → Sentry paging path (PRO-113 verification) using the shared `app/core/sentry.py` `init_sentry()`. Run once per service, in that service's environment, to confirm the DSN, integration wiring, and alert rule work end-to-end.

```bash
railway run python scripts/fire_test_page.py --service worker
railway run python scripts/fire_test_page.py --service api
```

### `check_health_leads.py`

One poll of production's `/health/leads`: applies the alert rules and pages through Sentry. Stdlib only, configured entirely by environment, and run by the `🩺 Lead pipeline monitor` workflow rather than by hand. Runbook: "Lead pipeline monitor" in `docs/OPERATIONS_GUIDE.md`.

### `simulate_sla_deflection.py`

Simulates a 15-minute silence for a specific `chat_id` by setting its Redis state to `PAUSED_FOR_HUMAN` and backdating its `paused_at` timestamp in MongoDB. This triggers the SLA Monitor on its next run — provided it's business hours (08:00–21:00 IL) and `sla_monitor_active` is enabled (defaults ON; a stored `False` keeps it off).

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

## WhatsApp / Meta

### `submit_whatsapp_templates.py`

Submits the template registry (`app/providers/whatsapp/template_registry.py`) to Meta and reports what Meta holds. The default is a dry run that prints every payload and touches nothing; `--status --waba-id <id>` prints each template's status and category and reports drift from the registry (exit 3 if Meta holds any template outside UTILITY, 1 on any other drift, 0 when they agree); `--apply --waba-id <id> --confirm-name "<account name>"` creates what Meta does not have, and refuses Meta's test account. It never edits the registry. See `docs/WHATSAPP_TEMPLATE_CATALOG.md`.

```bash
python scripts/submit_whatsapp_templates.py
python scripts/submit_whatsapp_templates.py --status --waba-id <id>
```

### `probe_template_delivery.py`

Staging's proof of a closed-window template delivery (PRO-232): sends one message to `--to <number>` through the facade, as Meta's `hello_world` template when the device's 24h window is closed, as plain text when it is open. Exit 0 sent as the template, 3 sent as text (the window was open, so nothing was proven), 4 not sent.

```bash
python scripts/probe_template_delivery.py --to 9725XXXXXXXX
```

### `meta_healthcheck.py`

Read-only Meta Cloud API connectivity check (PRO-89): config presence (masked), access-token and app-secret validity, the phone-number node, the WABA's phone numbers, webhook configuration and subscribed apps, and optionally a deployed webhook's handshake with `--staging-url`. Exit 0 only when every non-optional check passed.

```bash
python scripts/meta_healthcheck.py
```

---

## Analytics & Reports

### `verify_analytics.py`

Manual smoke-check of the admin panel's analytics aggregations (`admin_panel/core/analytics_queries`) against whatever database `MONGO_URI` points at.

```bash
python scripts/verify_analytics.py
```

### `finops_report.py`

Generates a summary of total Google Gemini tokens used per professional, sorted by highest consumption.

```bash
python scripts/finops_report.py
```

### `reset_test.py`

Clears test state: deletes test leads/messages from MongoDB, wipes Redis state/context/webhook keys. Refuses to run unless `ENVIRONMENT` is `development` or `staging`, checked before it connects to anything.

```bash
python scripts/reset_test.py --all                        # Full environment wipe
python scripts/reset_test.py --customer 972501234567      # Wipe specific customer only
```

### `init_production.py`

One-time production initialization: creates indexes, seeds required settings documents, and verifies connectivity.

```bash
python scripts/init_production.py
```
