# Fixi Backend & Admin Panel - AI Developer Context

## 1. Project Overview
**Fixi** is an AI-powered CRM and scheduling automation platform for service professionals. It uses WhatsApp as the primary interface for both customers and professionals ("Pros").

*   **User Interface:** WhatsApp (via Green API).
*   **Admin Interface:** Streamlit Dashboard.
*   **Backend:** FastAPI (Async).
*   **AI Engine:** Google Gemini (Generative AI).
*   **Database:** MongoDB Atlas.

## 2. Architecture & Data Flow

### Core Message Flow
The system uses a modular service architecture:
1.  **Webhook:** `app/main.py` receives `POST /webhook` from Green API.
2.  **Routing:** Messages are routed to `app.services.workflow.process_incoming_message`.
3.  **Processing:**
    *   **`LeadManager`**: Logs messages and manages lead state in MongoDB.
    *   **`AIEngine`**: Sends conversation history to Gemini to generate responses and detect `[DEAL]` tags.
    *   **`WhatsAppClient`**: Handles sending text, buttons, and location links back to users.

### Scheduling & Background Tasks
*   **`app/scheduler.py`**: Runs periodic tasks (Daily Reminders, Stale Job Monitor).
*   **Dependencies:** Uses `WhatsAppClient` for notifications and `LeadManager` (via DB) for job tracking.

### Key Directories
```text
app/
├── main.py                     # Entry point (FastAPI)
├── scheduler.py                # APScheduler tasks
├── services/
│   ├── workflow.py             # MAIN ENTRY for message logic
│   ├── ai_engine.py            # Gemini integration (Class-based)
│   ├── lead_manager.py         # DB operations for Leads/History
│   └── whatsapp_client.py      # Green API wrapper
└── core/                       # Config, Database, Logger
admin_panel/                    # Streamlit Admin Dashboard
scripts/                        # Operational scripts (Seeding, Simulation)
```

## 3. Setup & Commands

### Prerequisites
*   Python 3.10+
*   MongoDB Atlas URI
*   Green API Credentials
*   Google Gemini API Key

### Running the System
1.  **Backend (FastAPI):**
    ```bash
    uvicorn app.main:app --reload --port 8000
    ```
2.  **Admin Panel (Streamlit):**
    ```bash
    streamlit run admin_panel/app.py
    ```

### Testing
*   **Run Tests:** `pytest tests/`
*   **Seed Database:** `python scripts/seed_db.py` (Resets DB with test pros and slots).

## 5. Development Conventions
*   **Async/Sync:** Prefer `async` for all I/O (Database, API calls).
*   **Logging:** Use `app.core.logger` (Loguru).
*   **Timezones:** Store UTC in DB, display `Asia/Jerusalem` (IL_TZ) to users.
*   **Validation:** Use Pydantic models (see `app/schemas`).