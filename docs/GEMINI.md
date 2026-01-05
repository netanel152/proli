# Fixi Backend & Admin Panel - AI Developer Context

## 1. Project Overview
**Fixi** is an AI-powered CRM and scheduling automation platform for service professionals. It transforms WhatsApp into a powerful business tool using a multi-agent approach.

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
*   **AI Engine:** **Google Gemini Adaptive** (Flash Lite 2.5 -> Flash 2.5 -> Flash 1.5) - Supports Vision, Video & Audio.
*   **HTTP Client:** **HTTPX** (Async requests for media downloading).
*   **Media Storage:** **Cloudinary** (For future media handling/persistence).
*   **Messaging Provider:** **Green API** (WhatsApp wrapper with Interactive Buttons).
*   **Scheduling:** **APScheduler** with **Atomic Locking** (Race-condition free).
*   **Logging:** **Loguru** (intercepts standard logging, structured file output).
*   **Infrastructure:** **Docker** & **Docker Compose**.

### Data Flow
1.  **Inbound:** WhatsApp Webhook -> `POST /webhook` (FastAPI `app/api/routes/webhook.py`).
2.  **Dispatcher Analysis:**
    *   `AIEngine` uses `Prompts.DISPATCHER_SYSTEM` to analyze user text/media.
    *   Extracts `city` and `issue` type.
    *   Creates/Updates a provisional lead (Status: `CONTACTED`).
3.  **Routing Engine (`app.services.matching_service.determine_best_pro`):
    *   **Filter:** Active Pros (`is_active: True`).
    *   **Location:** Checks if extracted `city` matches Pro's `service_areas`.
    *   **Ranking:** Sorts by `social_proof.rating` (Descending).
    *   **Load Balancing:** Skips pros with > `WorkerConstants.MAX_PRO_LOAD` active ("booked") jobs.
    *   **Fallback:** Selects highest-rated pro or "Fixi Support" persona.
4.  **Pro Persona AI Processing:**
    *   Fetches the selected Pro's **Dynamic System Prompt**, **Price List**, and **Social Proof**.
    *   `AIEngine` re-runs analysis with the *specific* Pro Persona (Tone, Pricing, Rules).
    *   Generates a reply for the user.
    *   Detects intent and extracts [DEAL] tags or Structured JSON.
5.  **Action:**
    *   **Database:** Updates Lead with linked `pro_id`.
    *   **Booking:** `matching_service.book_slot_for_lead` ensures atomic slot reservation.
    *   **Notification (`app.services.notification_service`):** Sends lead details ONLY to the selected Pro with instructions to reply 'אשר' (Approve) or 'דחה' (Reject).
6.  **SOS Recovery (`app.services.monitor_service`):**
    *   **Healer:** Automatically reassigns leads that remain in `NEW` or `CONTACTED` status beyond `WorkerConstants.SOS_TIMEOUT_MINUTES`.
    *   **Reporter:** Batches leads that fail reassignment and notifies the admin.
7.  **Visualization:** Admin Panel (`admin_panel/dashboard_page.py`) reflects changes immediately.

## 3. Project Structure

```text
./
├── app/                            # FastAPI Backend Core
│   ├── main.py                     # Entry point: App Config & Startup
│   ├── scheduler.py                # APScheduler with Atomic Locking
│   ├── api/                        # API Routes
│   │   └── routes/
│   │       └── webhook.py          # Green API Webhook Endpoint
│   ├── services/
│   │   ├── workflow.py             # Main Orchestrator (Entry Point for Business Logic)
│   │   ├── matching_service.py     # Pro Routing & Booking Logic
│   │   ├── notification_service.py # WhatsApp Notification Logic
│   │   ├── ai_engine.py            # Gemini Wrapper (Multimodal)
│   │   ├── lead_manager.py         # DB Operations (CRUD)
│   │   ├── monitor_service.py      # SOS Recovery & Stale Lead Monitor
│   │   └── whatsapp_client.py      # Green API Wrapper
│   └── core/
│       ├── database.py             # MongoDB Connection (Async/Sync)
│       ├── config.py               # Env Vars & Settings
│       ├── constants.py            # Enums & System Constants (Status, Limits)
│       ├── messages.py             # UI/Notification Text Templates
│       └── prompts.py              # AI System Prompts
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
│   ├── test_booking_and_messaging.py
│   ├── test_admin_auth.py
│   └── ...
├── scripts/                        # Operational Scripts
├── Dockerfile                      # Container definition
├── docker-compose.yml              # Multi-container orchestration
└── entrypoint.sh                   # Startup script
```

## 4. Key Conventions & Rules
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
*   **Constants:** Use `app.core.constants` for statuses (`LeadStatus`) and configuration (`WorkerConstants`).

## 5. Audit Findings & Resolution Status
1.  **Performance (Motor):** ✅ Resolved. Core app uses `motor`. `pymongo` reserved for scripts.
2.  **Security (Auth):** ✅ Resolved. `admin_panel/auth.py` uses salted `bcrypt`.
3.  **Concurrency (Scheduler):** ✅ Resolved. Atomic DB locks implemented in `scheduler.py`.
4.  **Error Handling:** ✅ Improved. Background tasks used for heavy lifting.
5.  **Infrastructure:** ✅ Resolved. Dockerized application with structured logging.
6.  **AI Fallback:** ✅ Resolved. Implemented adaptive hierarchy via `settings.AI_MODELS`.
7.  **Refactoring:** ✅ Resolved. Modularized `workflow.py` by extracting logic into `ai_engine`, `matching_service`, and `lead_manager`.
8.  **New Features:** ✅ Implemented. Interactive buttons and calendar booking logic.
9.  **SOS Recovery:** ✅ Implemented. Automated reassignment and admin reporting.
10. **Test Coverage:** ✅ Improved. Added comprehensive suite for SOS monitor, AI parsing, and full lifecycle.

### Environment Variables (`.env`)
```env
MONGO_URI=mongodb+srv://...
GEMINI_API_KEY=...
GREEN_API_ID=...
GREEN_API_TOKEN=...
CLOUDINARY_CLOUD_NAME=...
CLOUDINARY_API_KEY=...
CLOUDINARY_API_SECRET=...
ADMIN_PASSWORD=...
```

### Running the Application
1.  **Backend:** `uvicorn app.main:app --reload --port 8000`
2.  **Admin:** `streamlit run admin_panel/main.py`

```