# Fixi Scripts Guide 🛠️

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

## 2. Testing & Simulation

### `simulate_webhook.py`
**Purpose:** Simulates an incoming WhatsApp message from Green API.
*   **What it does:**
    *   Prompts for a message text (e.g., "I need a plumber in Tel Aviv").
    *   Constructs a valid JSON payload matching Green API's structure.
    *   Sends a POST request to your local backend (`http://localhost:8000/webhook`).
*   **Usage:**
    ```bash
    python scripts/simulate_webhook.py
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
