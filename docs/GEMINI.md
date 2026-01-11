# Proli Backend & Admin Panel - AI Developer Context

## 1. Project Overview
**Proli** is an AI-powered CRM and scheduling automation platform for service professionals. It transforms WhatsApp into a powerful business tool using a multi-agent approach.

*   **User Interface:** WhatsApp (via Green API).
*   **Admin Interface:** Streamlit Dashboard.
*   **Backend:** FastAPI (Async).
*   **AI Engine:** Google Gemini 2.5 (Multimodal - Text, Images, Audio).
*   **Database:** MongoDB Atlas.
*   **Core Logic:** Intelligent Pro Routing, Load Balancing & Calendar Booking.

## 2. Technical Architecture & Tech Stack

### Core Technologies
*   **Language:** Python 3.12+
*   **Web Framework:** **FastAPI** (Async, handling Webhooks)
*   **Admin UI:** **Streamlit** (Rapid data app development) with `extra_streamlit_components` for cookie management.
*   **Database:** **MongoDB** (Atlas) - using `motor` (Async) for the app and `pymongo` (Sync) for scripts.
*   **Cache & State:** **Redis** (Context Caching & User State Management).
*   **AI Engine:** **Google Gemini Adaptive** (Flash Lite 2.5 -> Flash 2.5 -> Flash 1.5) - Supports Vision, Video & Audio.
*   **HTTP Client:** **HTTPX** (Async requests for media downloading).
*   **Media Storage:** **Cloudinary** (For future media handling/persistence).
*   **Messaging Provider:** **Green API** (WhatsApp wrapper with Interactive Buttons).
*   **Task Queue:** **ARQ** (Async Redis Queue) for offloading heavy message processing.
*   **Scheduling:** **APScheduler** (Cron Jobs) running alongside ARQ in the Worker process.
*   **Logging:** **Loguru** (intercepts standard logging, structured file output).
*   **Infrastructure:** **Docker** & **Docker Compose**.

### Data Flow
1.  **Inbound:** WhatsApp Webhook -> `POST /webhook` (FastAPI `app/api/routes/webhook.py`).
2.  **Task Enqueue:** Webhook immediately enqueues a job (`process_message_task`) to **ARQ** and returns 200 OK.
3.  **Worker Processing (`app/worker.py`):**
    *   **Context:** `ContextManager` fetches chat history from Redis.
    *   **State:** `StateManager` checks user state (Idle, Pro Mode, etc.).
    *   **Dispatcher:** `AIEngine` analyzes input to route the lead or continue conversation.
4.  **Routing Engine (`app.services.matching_service_service.determine_best_pro`):**
    *   **Filter:** Active Pros (`is_active: True`).
    *   **Location:** Checks if extracted `city` matches Pro's `service_areas`.
    *   **Ranking:** Sorts by `social_proof.rating` (Descending).
    *   **Load Balancing:** Skips pros with > `WorkerConstants.MAX_PRO_LOAD` active ("booked") jobs.
5.  **Action & Notification:**
    *   **Database:** Updates Lead with linked `pro_id`.
    *   **Booking:** `matching_service.book_slot_for_lead` ensures atomic slot reservation.
    *   **Notification:** Sends details to Pro and confirmation to User via `notification_service`.
6.  **Background Monitoring (APScheduler):**
    *   **Healer:** Reassigns stale leads (`NEW`/`CONTACTED` > 30m).
    *   **Reporter:** Alerts admin of stuck leads (> 24h).
    *   **Reminders:** Sends daily agenda to Pros.

## 3. Project Structure

```text
./
├── app/                            # FastAPI Backend Core
│   ├── main.py                     # Entry point: App Config & Startup
│   ├── scheduler.py                # APScheduler Config (Periodic Tasks)
│   ├── worker.py                   # ARQ Worker Entry Point
│   ├── api/                        # API Routes
│   │   └── routes/
│   │       └── webhook.py          # Green API Webhook Endpoint
│   ├── services/
│   │   ├── workflow_service.py         # Main Orchestrator (Business Logic)
│   │   ├── matching_service.py         # Pro Routing & Booking Logic
│   │   ├── notification_service.py     # WhatsApp Notification Logic
│   │   ├── ai_engine_service.py        # Gemini Wrapper (Multimodal)
│   │   ├── lead_manager_service.py     # DB Operations (CRUD)
│   │   ├── monitor_service.py          # SOS Recovery & Stale Lead Monitor
│   │   ├── whatsapp_client_service.py  # Green API Wrapper
│   │   ├── context_manager_service.py  # Redis Chat History Manager
│   │   └── state_manager_service.py    # Redis User State Manager
│   └── core/
│       ├── arq_worker.py           # ARQ Settings & Startup Hooks
│       ├── database.py             # MongoDB Connection (Async/Sync)
│       ├── config.py               # Env Vars & Settings
│       ├── constants.py            # Enums & System Constants (Status, Limits)
│       ├── messages.py             # UI/Notification Text Templates
│       ├── prompts.py              # AI System Prompts
│       └── redis_client.py         # Redis Connection Helper
├── admin_panel/                    # Streamlit Admin Dashboard
│   ├── main.py                     # Main Entry Point
│   ├── core/                       # Core Logic
│   │   ├── auth.py                 # Authentication (Bcrypt + Cookies)
│   │   ├── config.py               # Translations (HE/EN)
│   │   └── utils.py                # DB & Helpers
│   ├── views/                      # View Logic
│   │   ├── home.py                 # Dashboard View
│   │   ├── professionals.py        # Pros View
│   │   ├── schedule.py             # Schedule Editor
│   │   └── settings.py             # System Settings
│   └── ui/                         # UI Components
│       └── components.py           # Widgets & Styles
├── tests/                          # Test Suite
├── scripts/                        # Operational Scripts
├── Dockerfile                      # Container definition
├── docker-compose.yml              # Multi-container orchestration
└── entrypoint.sh                   # Startup script
```

## 4. Key Conventions & Rules
*   **Service Layer:** All business logic resides in `app/services/`. Files should be suffixed with `_service.py` where appropriate, but imports should be clean.
*   **State Management:** Use `StateManager.get_state(chat_id)` to determine if a user is in a specific flow (e.g., `REQUIRE_MORE_INFO`).
*   **Context:** Use `ContextManager.get_history(chat_id)` to retrieve conversation context for the AI.
*   **Schema Standardization:**
    *   `full_address` (was `address`)
    *   `issue_type` (was `issue`)
    *   `appointment_time` (was `time_preference`)
*   **Date/Time:** Store all datetimes in **UTC**. Convert to **Asia/Jerusalem** (configured in `settings.TIMEZONE`) for display.
*   **AI Context:** Always inject the *specific* Pro's system prompt using `app.core.prompts.Prompts`.
*   **Security:** 
    *   Use `admin_panel.auth.make_hash` for passwords.
    *   Never commit secrets.
*   **Concurrency:** Use `find_one_and_update` for scheduler and booking locks.

## 5. Audit Findings & Resolution Status
1.  **Performance (Motor):** ✅ Resolved. Core app uses `motor`. `pymongo` reserved for scripts.
2.  **Security (Auth):** ✅ Resolved. `admin_panel/auth.py` uses salted `bcrypt`.
3.  **Concurrency (Scheduler):** ✅ Resolved. Atomic DB locks implemented in `scheduler.py`.
4.  **Error Handling:** ✅ Improved. Background tasks (ARQ) used for heavy lifting.
5.  **Infrastructure:** ✅ Resolved. Dockerized application with structured logging.
6.  **AI Fallback:** ✅ Resolved. Implemented adaptive hierarchy via `settings.AI_MODELS`.
7.  **Refactoring:** ✅ Resolved. Modularized `workflow.py` by extracting logic into `ai_engine_service`, `matching_service`, and `lead_manager_service`.
8.  **New Features:** ✅ Implemented. Interactive buttons and calendar booking logic.
9.  **SOS Recovery:** ✅ Implemented. Automated reassignment and admin reporting.
10. **Test Coverage:** ✅ Improved. Added comprehensive suite for SOS monitor, AI parsing, and full lifecycle.
11. **Worker Resilience:** ✅ Resolved. Added DB connectivity check on startup (ping) to prevent silent failures.
12. **Architecture:** ✅ Resolved. Moved Scheduler from API to Worker process (bundled with ARQ) for better separation of concerns.
13. **Context Caching:** ✅ Resolved. Implemented Redis "Write-Through" cache for chat history (`ContextManager`).
14. **State Management:** ✅ Resolved. Implemented Redis-based `StateManager` to control user flows.

### Environment Variables (`.env`)
```env
# Core
MONGO_URI=mongodb+srv://...
MONGO_TEST_URI=mongodb+srv://... (Optional)
ADMIN_PASSWORD=...
PROJECT_NAME="Proli Bot Server"
ENVIRONMENT="development"

# APIs
GEMINI_API_KEY=...
GREEN_API_ID=...
GREEN_API_TOKEN=...
CLOUDINARY_CLOUD_NAME=...
CLOUDINARY_API_KEY=...
CLOUDINARY_API_SECRET=...

# Redis (Defaults shown)
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_DB=0
```

### Running the Application
1.  **Backend:** `uvicorn app.main:app --reload --port 8000`
2.  **Worker (ARQ + Scheduler):** `python -m app.worker`
3.  **Admin:** `streamlit run admin_panel/main.py`

```