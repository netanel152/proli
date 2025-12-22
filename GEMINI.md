# Fixi Backend & Admin Panel - AI Developer Context

## 1. Project Overview

**Fixi** is an AI-powered CRM and scheduling automation platform for service professionals. It uses WhatsApp as the primary interface for both customers and professionals ("Pros").

- **User Interface:** WhatsApp (via Green API).
- **Admin Interface:** Streamlit Dashboard.
- **Backend:** FastAPI (Async).
- **AI Engine:** Google Gemini (Generative AI).
- **Database:** MongoDB Atlas.

## 2. Architecture & Data Flow

### Core Message Flow (Active)

The system currently uses a class-based service architecture:

1.  **Webhook:** `app/main.py` receives `POST /webhook` from Green API.
2.  **Routing:** Messages are routed to `app.services.workflow.process_incoming_message`.
3.  **Processing:**
    - **`LeadManager`**: Logs messages and manages lead state in MongoDB.
    - **`AIEngine`**: Sends conversation history to Gemini to generate responses and detect `[DEAL]` tags.
    - **`WhatsAppClient`**: Handles sending text, buttons, and location links back to users.

### Scheduling & Background Tasks

- **`app/scheduler.py`**: Runs periodic tasks (Daily Reminders, Stale Job Monitor).
- **Hybrid Dependency:** The scheduler currently imports helper functions (`send_pro_reminder`, `send_whatsapp_message`) from the **legacy** `app/services/logic.py` file, creating a split logic state.

### Key Directories

```text
app/
├── main.py                     # Entry point (FastAPI)
├── scheduler.py                # APScheduler tasks
├── services/
│   ├── workflow.py             # MAIN ENTRY for message logic
│   ├── ai_engine.py            # Gemini integration (Class-based)
│   ├── lead_manager.py         # DB operations for Leads/History
│   ├── whatsapp_client.py      # Green API wrapper
│   └── logic.py                # LEGACY/HYBRID: Contains advanced logic not yet ported to workflow
└── core/                       # Config, Database, Logger
admin_panel/                    # Streamlit Admin Dashboard
scripts/                        # Operational scripts (Seeding, Simulation)
```

## 3. Setup & Commands

### Prerequisites

- Python 3.10+
- MongoDB Atlas URI
- Green API Credentials
- Google Gemini API Key

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

- **Run Tests:** `pytest tests/`
- **Seed Database:** `python scripts/seed_db.py` (Resets DB with test pros and slots).

## 4. Current State & Known Issues

- **Refactoring in Progress:** The project is moving from a monolithic `logic.py` to modular services (`workflow.py`, `ai_engine.py`).
- **Logic Split:** Incoming messages use `workflow.py`, but the scheduler uses `logic.py`. Future tasks should focus on unifying these to use `WhatsAppClient` everywhere.
- **AI Context:** Currently, `AIEngine` uses a static system prompt. The dynamic prompts stored in `users_collection` (populated by `seed_db.py`) are not fully utilized in the active `workflow.py` path yet.

## 5. Development Conventions

- **Async/Sync:** Prefer `async` for all I/O (Database, API calls).
- **Logging:** Use `app.core.logger` (Loguru).
- **Timezones:** Store UTC in DB, display `Asia/Jerusalem` (IL_TZ) to users.
- **Validation:** Use Pydantic models (see `app/schemas`).
