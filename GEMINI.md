# Fixi Backend & Admin Panel - AI Developer Context

## 1. Project Overview
**Fixi** is an AI-powered CRM and scheduling automation platform for service professionals. It transforms WhatsApp into a powerful business tool using a multi-agent approach.

*   **User Interface:** WhatsApp (via Green API).
*   **Admin Interface:** Streamlit Dashboard.
*   **Backend:** FastAPI (Async).
*   **AI Engine:** Google Gemini 2.5 (Multimodal - Text, Images, Audio).
*   **Database:** MongoDB Atlas.
*   **Core Logic:** Intelligent Pro Routing & Load Balancing.

## 2. Technical Architecture & Tech Stack

### Core Technologies
*   **Language:** Python 3.10+
*   **Web Framework:** **FastAPI** (Async, handling Webhooks)
*   **Admin UI:** **Streamlit** (Rapid data app development) with `extra_streamlit_components` for cookie management.
*   **Database:** **MongoDB** (Atlas) - using `motor` (Async) for the app and `pymongo` (Sync) for scripts.
*   **AI Engine:** **Google Gemini 2.5 Flash Lite** (`google-genai` SDK) - Supports Vision & Audio.
*   **HTTP Client:** **HTTPX** (Async requests for media downloading).
*   **Messaging Provider:** **Green API** (WhatsApp wrapper).
*   **Scheduling:** **APScheduler** with **Atomic Locking** (Race-condition free).

### Data Flow
1.  **Inbound:** WhatsApp Webhook -> `POST /webhook` (FastAPI `app/main.py`).
2.  **Routing Engine (`app.services.workflow.determine_best_pro`):
    *   **Filter:** Active Pros (`is_active: True`).
    *   **Location:** Checks if user text matches Pro's `service_areas`.
    *   **Ranking:** Sorts by `social_proof.rating` (Descending).
    *   **Load Balancing:** Skips pros with > 3 active ("booked") jobs.
    *   **Fallback:** Selects highest-rated pro or "Fixi Support" persona.
3.  **Context Assembly:**
    *   Fetches the selected Pro's **Dynamic System Prompt** and **Price List**.
    *   Downloads media (Images/Audio) via `httpx` if present.
4.  **AI Processing:**
    *   `AIEngine` (Gemini 2.5) analyzes text + media + dynamic prompt.
    *   Detects intent and extracts [DEAL] tags or Structured JSON.
5.  **Action:**
    *   **Database:** Creates Lead with linked `pro_id`.
    *   **Notification:** Sends lead details ONLY to the selected Pro with Action Buttons (Approve/Reject).
6.  **Visualization:** Admin Panel (`admin_panel/dashboard_page.py`) reflects changes immediately.

## 3. Project Structure

```text
D:\Projects\fixi-backend\
├── app/                            # FastAPI Backend Core
│   ├── main.py                     # Entry point: Webhook endpoint
│   ├── scheduler.py                # APScheduler with Atomic Locking
│   ├── services/
│   │   ├── workflow.py             # ROUTING ENGINE & Business Logic
│   │   ├── ai_engine.py            # Gemini Wrapper (Multimodal)
│   │   ├── lead_manager.py         # DB Operations (CRUD)
│   │   └── whatsapp_client.py      # Green API Wrapper
│   └── core/
│       ├── database.py             # MongoDB Connection (Async/Sync)
│       └── config.py               # Env Vars
├── admin_panel/                    # Streamlit Admin Dashboard
│   ├── dashboard_page.py           # Main Dashboard (Tabs: View / Create)
│   ├── auth.py                     # Authentication (Bcrypt + Cookies)
│   ├── components.py               # UI Widgets
│   └── config.py                   # Translations (HE/EN)
└── scripts/                        # Operational Scripts
```

## 4. Key Conventions & Rules
*   **Schema Standardization:**
    *   `full_address` (was `address`)
    *   `issue_type` (was `issue`)
    *   `appointment_time` (was `time_preference`)
*   **Date/Time:** Store all datetimes in **UTC**. Convert to **Asia/Jerusalem** for display.
*   **AI Context:** Always inject the *specific* Pro's system prompt.
*   **Security:** 
    *   Use `admin_panel.auth.make_hash` for passwords.
    *   Never commit secrets.
*   **Concurrency:** Use `find_one_and_update` for scheduler locks.

## 5. Audit Findings & Resolution Status
1.  **Performance (Motor):** ✅ Resolved. Core app uses `motor`. `pymongo` reserved for scripts.
2.  **Security (Auth):** ✅ Resolved. `admin_panel/auth.py` uses salted `bcrypt`.
3.  **Concurrency (Scheduler):** ✅ Resolved. Atomic DB locks implemented in `scheduler.py`.
4.  **Error Handling:** ✅ Improved. Background tasks used for heavy lifting.
5.  **AI Fallback:** ⚠️ Pending/Partial. Code uses `gemini-2.5-flash-lite` but explicit retry/fallback logic to `gemini-1.5` is not currently active in `ai_engine.py`.

### Environment Variables (`.env`)
```env
MONGO_URI=mongodb+srv://...
GEMINI_API_KEY=...
GREEN_API_ID=...
GREEN_API_TOKEN=...
ADMIN_PASSWORD=...
```

### Running the Application
1.  **Backend:** `uvicorn app.main:app --reload --port 8000`
2.  **Admin:** `streamlit run admin_panel/app.py`

```