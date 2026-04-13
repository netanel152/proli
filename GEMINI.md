# GEMINI.md - Project Context & Instructions

## Project Overview
**Proli** is an AI-powered WhatsApp CRM and scheduling platform designed for Israeli service professionals (plumbers, electricians, etc.). It automates customer interactions using Google Gemini, manages lead routing based on geography and rating, and provides a full-featured management dashboard.

### Core Architecture
The system operates as three independent but cooperating processes:
1.  **FastAPI Backend (`app/`)**: Entry point for Green API webhooks. Enqueues tasks to Redis and returns immediately.
2.  **ARQ Worker (`app/worker.py`)**: The "brain" of the system. Processes messages, executes AI logic, matches professionals, and runs background maintenance jobs (SOS healer, stale monitors).
3.  **Streamlit Admin Panel (`admin_panel/`)**: A management UI for leads, professionals, schedules, and analytics.

### Key Technologies
- **Language**: Python 3.12+
- **API Framework**: FastAPI + Uvicorn
- **Admin UI**: Streamlit
- **Task Queue**: ARQ (Redis-backed)
- **AI**: Google Gemini (Flash Lite 2.5 → Flash 2.5 → Flash 1.5 fallback)
- **Database**: MongoDB Atlas (Async Motor driver)
- **Messaging**: Green API (WhatsApp), InforUMobile (SMS fallback)
- **Media**: Cloudinary (Image/Audio/Video processing)
- **State Management**: Redis-backed FSM (Finite State Machine)

---

## Building and Running

### Environment Setup
1.  **Install Dependencies**: `pip install -r requirements.txt`
2.  **Configuration**: Create a `.env` file based on the template in `README.md`.
    -   Required: `GREEN_API_INSTANCE_ID`, `GREEN_API_TOKEN`, `GEMINI_API_KEY`, `CLOUDINARY_*`.
3.  **Initialize Database**:
    ```bash
    python scripts/seed_db.py
    python scripts/create_indexes.py
    ```

### Execution Modes
-   **Docker (Recommended)**: `docker-compose up --build -d`
-   **Local Development (3 Terminals)**:
    1.  `uvicorn app.main:app --reload --port 8000` (API)
    2.  `python -m app.worker` (Worker)
    3.  `streamlit run admin_panel/main.py` (Admin)

---

## Testing Strategy
-   **Unit Tests**: Use `pytest`. Employs `mongomock_motor` and mocks for AI/WhatsApp.
-   **Integration Tests**: Run with `pytest -m integration`. Requires `MONGO_TEST_URI`.
-   **Key Commands**:
    -   `pytest` (Run all unit tests)
    -   `pytest tests/test_matching_service.py` (Specific test)
    -   `black .` / `flake8 .` (Linting)

---

## Development Conventions

### Service Layer Design
Logic is strictly decoupled into services in `app/services/`:
-   `workflow_service.py`: Central orchestrator for all incoming messages.
-   `ai_engine_service.py`: Handles multimodal prompts and adaptive model fallback.
-   `matching_service.py`: Implements progressive geo-search (10km → 20km → 30km).
-   `state_manager_service.py`: Manages `UserStates` (FSM) in Redis with custom TTLs.

### State & Lead Lifecycles
-   **Lead Status**: `new` → `contacted` → `booked` → `completed`.
-   **User States**: `IDLE`, `AWAITING_ADDRESS`, `AWAITING_PRO_APPROVAL`, `PAUSED_FOR_HUMAN`.
-   **SOS Healer**: Periodically checks for leads stuck in `AWAITING_PRO_APPROVAL` for >60 mins and reassigns them.

### Data Standards
-   **MongoDB**: Primary persistent storage for `users`, `leads`, `slots`, and `audit_log`.
-   **Redis**: Volatile storage for `chat_history` (last 20 messages) and `fsm_state`.
-   **Timezone**: Always use `Asia/Jerusalem` (`pytz`).

### Coding Style
-   Follow **PEP 8**. Use `black` for formatting.
-   Maintain type hints for all service methods.
-   New features MUST include corresponding unit tests in the `tests/` directory.
-   Avoid circular imports by importing within functions when necessary (e.g., in `admin_panel/auth.py`).
