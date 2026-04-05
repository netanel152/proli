# Proli Scripts Guide 🛠️

This document explains the purpose and usage of the operational scripts located in the `scripts/` directory. These scripts are useful for development, testing, and maintenance.

> **Note:** Always run scripts from the project root directory (e.g., `python scripts/seed_db.py`).

## 1. Database Management

### `seed_db.py`
**Purpose:** Populates the MongoDB database with initial sample data.
*   **What it does:**
    *   Clears existing collections (Users, Leads, Slots, Messages).
    *   Creates sample Professionals (Plumbers, Electricians) with service areas and pricing.
    *   Useful for resetting the environment during development.
*   **Usage:**
    ```bash
    python scripts/seed_db.py
    ```

### `clear_history.py`
**Purpose:** Wipes conversation history and logs.
*   **What it does:**
    *   Removes all messages from the `messages` collection.
    *   Clears Redis context keys (optional if configured).
    *   **Warning:** Destructive action. Use with caution.
*   **Usage:**
    ```bash
    python scripts/clear_history.py
    ```

### `create_indexes.py`
**Purpose:** Optimizes database query performance.
*   **What it does:**
    *   Creates MongoDB indexes for frequently queried fields (e.g., `phone_number`, `status`, `created_at`).
    *   Should be run once when setting up a new environment.
*   **Usage:**
    ```bash
    python scripts/create_indexes.py
    ```

### `generate_admin_hash.py`
**Purpose:** Helper to create secure passwords for the Admin Panel.
*   **What it does:**
    *   Takes a plain-text password as input.
    *   Outputs the Bcrypt hash string.
    *   You can then paste this hash into the `users` collection manually if needed.
*   **Usage:**
    ```bash
    python scripts/generate_admin_hash.py
    ```

### `backup.py`
**Purpose:** Creates a compressed MongoDB backup with optional S3 upload.
*   **What it does:**
    *   Runs `mongodump` with gzip compression to `backups/` directory.
    *   Optionally uploads to S3 (requires `BACKUP_S3_BUCKET` + AWS credentials).
    *   Retention policy: keeps 7 daily + 4 weekly backups.
    *   Also runs automatically daily at 02:00 IL time via APScheduler.
*   **Usage:**
    ```bash
    python scripts/backup.py              # Local backup only
    python scripts/backup.py --upload-s3  # Backup + S3 upload
    python scripts/backup.py --cleanup    # Clean old backups per retention policy
    ```

### `restore.py`
**Purpose:** Restores a MongoDB backup from local file or S3.
*   **What it does:**
    *   Runs `mongorestore` from a gzipped backup archive.
    *   Supports restoring the latest local backup or downloading from S3.
    *   Safety confirmation prompt before destructive restore.
*   **Usage:**
    ```bash
    python scripts/restore.py --latest          # Restore most recent local backup
    python scripts/restore.py --from-s3 <key>   # Download and restore from S3
    python scripts/restore.py --no-drop         # Restore without dropping existing data
    ```

## 2. Testing & Simulation

### `simulate_webhook.py`
**Purpose:** Simulates an incoming WhatsApp message from Green API (Interactive).
*   **What it does:**
    *   Prompts for a message text (e.g., "I need a plumber in Tel Aviv").
    *   Constructs a valid JSON payload matching Green API's structure.
    *   Sends a POST request to your local backend (`http://localhost:8000/webhook`).
*   **Usage:**
    ```bash
    python scripts/simulate_webhook.py
    ```

### `simulate_test.py`
**Purpose:** Runs automated end-to-end integration tests over HTTP.
*   **What it does:**
    *   Executes predefined test cases (TC1-TC12) including Consent Flow, Pro Reject, SOS Logic, Media Support, and Idempotency.
    *   Simulates both customer and professional interactions via webhook calls.
    *   Validates correct system state and transitions.
*   **Usage:**
    ```bash
    python scripts/simulate_test.py          # Run all tests interactively
    python scripts/simulate_test.py --auto   # Run all tests automatically (non-interactive)
    ```

### `reset_test.py`
**Purpose:** Clears test data and cache states to provide a clean slate for testing.
*   **What it does:**
    *   Deletes leads, messages, and temporary metadata/pending pros from MongoDB.
    *   Wipes state, context, and webhook keys in Redis.
    *   Can reset a specific customer or perform a full environment wipe.
*   **Usage:**
    ```bash
    python scripts/reset_test.py --all            # Full wipe
    python scripts/reset_test.py 972501234567     # Wipe specific customer
    ```

### `test_connection.py`
**Purpose:** Verifies connectivity to external services.
*   **What it does:**
    *   Pings MongoDB.
    *   Checks Redis connection.
    *   Verifies Gemini API key (optional).
*   **Usage:**
    ```bash
    python scripts/test_connection.py
    ```

### `check_models.py`
**Purpose:** Lists available Gemini models.
*   **What it does:**
    *   Connects to Google GenAI.
    *   Prints a list of models available to your API key.
    *   Helps verify if you have access to `gemini-2.5-flash` etc.
*   **Usage:**
    ```bash
    python scripts/check_models.py
    ```
