# Proli Scripts Guide

All scripts run from the project root:

```bash
python scripts/<script_name>.py
```

---

## Database Management

### `seed_db.py`

Populates MongoDB with sample professionals (plumbers, electricians) and test leads. Clears existing collections first.

```bash
python scripts/seed_db.py
```

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

Interactive webhook simulator. Prompts for a message, builds a valid Green API JSON payload, and POSTs it to `http://localhost:8000/webhook`.

```bash
python scripts/simulate_webhook.py
```

### `simulate_test.py`

Automated E2E scenarios (TC1–TC12) over HTTP. Covers consent flow, pro rejection, SOS logic, media handling, and idempotency. Requires the backend to be running locally.

```bash
python scripts/simulate_test.py          # Interactive (step-by-step)
python scripts/simulate_test.py --auto   # Fully automated
```

### `reset_test.py`

Clears test state: deletes test leads/messages from MongoDB, wipes Redis state/context/webhook keys.

```bash
python scripts/reset_test.py --all            # Full environment wipe
python scripts/reset_test.py 972501234567     # Wipe specific customer only
```

### `test_connection.py`

Checks connectivity to MongoDB, Redis, and Gemini API.

```bash
python scripts/test_connection.py
```

### `check_models.py`

Lists Gemini models available to your API key. Useful for verifying access to `gemini-2.5-flash-lite` etc.

```bash
python scripts/check_models.py
```

### `init_production.py`

One-time production initialization: creates indexes, seeds required settings documents, and verifies connectivity.

```bash
python scripts/init_production.py
```
