# Fixi Backend & Admin Panel - AI Developer Context

## 1. Project Overview
**Fixi** is an AI-powered CRM and scheduling automation platform designed for service professionals (e.g., plumbers, electricians). It facilitates interaction through two primary interfaces:

1.  **WhatsApp Bot (User Facing):**
    *   **Interface:** Users chat via WhatsApp.
    *   **Intelligence:** Powered by **Google Gemini** (Generative AI).
    *   **Functionality:** Handles natural language inquiries, qualifies leads, identifies user intent (e.g., "I have a leak"), and autonomously schedules appointments based on real-time availability.
    *   **Media:** Processes images and audio using Gemini's multimodal capabilities.

2.  **Admin Panel (Manager Facing):**
    *   **Interface:** Web-based dashboard built with **Streamlit**.
    *   **Functionality:** Allows business owners to view leads, manage service professionals' profiles, edit schedules (slots), and oversee system settings.
    *   **Localization:** Supports Hebrew (RTL) and English.

## 2. Technical Architecture & Tech Stack

### Core Technologies
*   **Language:** Python 3.10+
*   **Web Framework:** **FastAPI** (Async, handling Webhooks)
*   **Admin UI:** **Streamlit** (Rapid data app development)
*   **Database:** **MongoDB** (Atlas) - using `pymongo` (sync) and `motor` (if applicable, currently `pymongo` appears primary).
*   **AI Engine:** **Google Gemini** (`google-generativeai` SDK).
*   **Messaging Provider:** **Green API** (WhatsApp wrapper).
*   **Scheduling:** **APScheduler** (Background tasks, reminders).
*   **Media Storage:** **Cloudinary** (Image/Audio hosting).
*   **Logging:** **Loguru** (Structured logging).
*   **Validation:** **Pydantic** (Data validation & settings).

### Data Flow
1.  **Inbound:** WhatsApp Webhook -> `POST /webhook` (FastAPI `app/main.py`).
2.  **Filtering:** Ignores group messages unless specified.
3.  **Processing (Background):**
    *   Message content is passed to `app.services.logic.ask_fixi_ai`.
    *   **Context Assembly:** AI retrieves conversation history (`messages` collection) and relevant context (availability, pro details).
    *   **LLM Call:** Gemini processes text/media and determines the response or tool call (e.g., `book_appointment`).
4.  **Action:**
    *   **Database:** Updates `leads`, `slots`, or `messages`.
    *   **Response:** Sends text/media back via Green API.
5.  **Visualization:** Admin Panel (`admin_panel/app.py`) reflects changes immediately by querying MongoDB.

## 3. Project Structure

```text
D:\Projects\fixi-backend\
├── app/                            # FastAPI Backend Core
│   ├── main.py                     # Entry point: Webhook endpoint, startup events
│   ├── scheduler.py            # APScheduler configuration (Daily reminders)
│   ├── core/
│   │   ├── config.py               # Pydantic Settings (Env vars)
│   │   ├── database.py             # MongoDB connection, collection definitions
│   │   └── logger.py               # Loguru configuration
│   ├── services/
│   │   └── logic.py                # THE BRAIN: Gemini integration, prompt engineering, tool calls
│   └── schemas/
│       └── whatsapp.py             # Pydantic models for Green API webhooks
├── admin_panel/                    # Streamlit Admin Dashboard
│   ├── app.py                      # Admin Entry point (Navigation, Auth check)
│   ├── auth.py                     # Simple authentication logic
│   ├── components.py               # UI widgets (Chat bubbles, styling)
│   ├── config.py                   # Admin-specific config (Translations)
│   ├── pages.py                    # Page Renderers: Dashboard, Schedule, Pros, Settings
│   └── utils.py                    # Admin helper functions
├── scripts/                        # Operational Scripts
│   ├── check_models.py             # Verify Gemini models availability
│   ├── clear_history.py            # Utility to wipe chat history
│   ├── seed_db.py                  # CRITICAL: Populates DB with initial Pros/Slots for testing
│   ├── simulate_webhook.py         # Test bot logic without real WhatsApp messages
│   └── test_connection.py          # Connectivity check (Mongo, etc.)
├── tests/                          # Pytest Suite
│   ├── conftest.py                 # Fixtures (Mock DB, etc.)
│   └── test_full_flow.py           # End-to-end flow testing
├── .env                            # Environment Variables (Secrets)
├── requirements.txt            # Python Dependencies
└── Procfile                        # Deployment command (e.g., for Heroku)
```

## 4. Setup & Development

### Environment Variables (`.env`)
Create a `.env` file in the root directory with the following keys:
```env
# Database
MONGO_URI=mongodb+srv://<user>:<password>@cluster.mongodb.net/?retryWrites=true&w=majority

# AI
GEMINI_API_KEY=AIzaSy...

# WhatsApp (Green API)
GREEN_API_ID=...
GREEN_API_TOKEN=...

# Media (Cloudinary)
CLOUDINARY_CLOUD_NAME=...
CLOUDINARY_API_KEY=...
CLOUDINARY_API_SECRET=...

# Security
ADMIN_PASSWORD=...
```

### Running the Application (Local)
This system requires two concurrent processes:

1.  **Backend (FastAPI):**
    ```powershell
    uvicorn app.main:app --reload --port 8000
    ```
    *Access API docs at: http://127.0.0.1:8000/docs*

2.  **Admin Panel (Streamlit):**
    ```powershell
    streamlit run admin_panel/app.py
    ```
    *Access UI at: http://localhost:8501*

### Testing & Verification
*   **Run Tests:**
    ```powershell
    python -m pytest tests/
    ```
*   **Simulate Message:**
    ```powershell
    python scripts/simulate_webhook.py
    ```
    *Useful for debugging logic without sending real WhatsApp messages.*
*   **Seed Database:**
    ```powershell
    python scripts/seed_db.py
    ```
    *Resets the database with test professionals and slots.*

## 5. Key Conventions & Rules
*   **Date/Time:** Store all datetimes in **UTC**. Convert to **Asia/Jerusalem** only for display in the Admin Panel or WhatsApp messages.
*   **Async/Sync:** FastAPI is async. MongoDB calls via `pymongo` are synchronous (be mindful of blocking). Streamlit is synchronous.
*   **Localization:** The Admin Panel is bilingual. Text strings should use the mapping in `admin_panel/config.py`.
*   **Logging:** Use `loguru` (`from loguru import logger`) instead of standard `logging`.