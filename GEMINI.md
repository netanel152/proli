# Fixi Backend & Admin Panel - Context & Documentation

## 1. Project Overview
**Fixi** is an AI-powered CRM and scheduling automation platform for service professionals. It uses a dual-interface approach:
1.  **WhatsApp Bot (User Facing):** Users interact via WhatsApp. The bot (powered by Google Gemini) handles inquiries, qualifies leads, and schedules appointments.
2.  **Admin Panel (Manager Facing):** A web-based dashboard (Streamlit) for business owners to manage leads, professionals, and schedules.

## 2. Technical Architecture

### Tech Stack
*   **Backend:** FastAPI (Python 3.10+)
*   **Admin UI:** Streamlit
*   **Database:** MongoDB (Atlas)
*   **AI:** Google Gemini (Generative AI) via `google-generativeai`
*   **Messaging:** WhatsApp via Green API
*   **Scheduling:** APScheduler (Async)

### Data Flow
1.  **Incoming Message:** WhatsApp Webhook -> `POST /webhook` (FastAPI)
2.  **Processing:**
    *   Background Task -> `process_message`
    *   AI Logic -> `app.services.logic.ask_fixi_ai` (Gemini)
    *   DB Interaction -> Check availability/leads in MongoDB
3.  **Response:** Send reply via Green API
4.  **Admin View:** Data updates in real-time on Streamlit dashboard

## 3. Project Structure

```text
D:\Projects\fixi-backend\
├── app/                        # FastAPI Backend
│   ├── main.py                 # Entry point, Webhook handler, Health check
│   ├── scheduler.py            # APScheduler setup for daily reminders
│   ├── core/
│   │   ├── config.py           # Settings management (pydantic)
│   │   └── database.py         # MongoDB connection & collections
│   ├── services/
│   │   └── logic.py            # Core AI logic & WhatsApp integration
│   └── schemas/
│       └── whatsapp.py         # Pydantic models for incoming webhooks
├── admin_panel/                # Streamlit Admin Dashboard
│   ├── app.py                  # Entry point (Sidebar, Lang, Auth)
│   ├── pages.py                # UI Views (Dashboard, Schedule, Settings)
│   ├── auth.py                 # Simple password-based auth
│   └── components.py           # UI helpers (CSS, Chat bubbles)
├── scripts/                    # Utility scripts (Seeding, Webhook Sim)
├── requirements.txt            # Dependencies
└── .env                        # Environment Variables (Required)
```

## 4. Key Components

### Backend (`app/`)
*   **`main.py`**: Initializes FastAPI. Handles `POST /webhook`. It filters group messages (`@g.us`) and dispatches processing to background tasks to prevent timeouts.
*   **`services/logic.py`**: The "Brain". Contains `ask_fixi_ai` which builds the prompt for Gemini and handles the tool calls (function calling) for booking slots.

### Admin Panel (`admin_panel/`)
*   **`app.py`**: Handles layout, sidebar navigation, and language switching (HE/EN). Checks authentication.
*   **`pages.py`**: Contains the render functions for each page:
    *   `view_dashboard`: Lead metrics and management.
    *   `view_schedule`: Daily slot editor and bulk generator.
    *   `view_pros`: CRUD for service professionals.

### Database (MongoDB)
Collections defined in `app/core/database.py`:
*   `users`: Service professionals (store prompt, business info).
*   `leads`: Potential clients generated from WhatsApp.
*   `slots`: Calendar availability (Time slots).
*   `messages`: Chat history.
*   `settings`: System config (e.g., Scheduler run time).

## 5. Development & Running

### Prerequisites
*   Python 3.10+
*   MongoDB Instance
*   `.env` file with: `MONGO_URI`, `GEMINI_API_KEY`, `GREEN_API_ID`, `GREEN_API_TOKEN`

### Running the System
The system requires **two** concurrent processes:

1.  **Backend Server:**
    ```bash
    uvicorn app.main:app --reload --port 8000
    ```

2.  **Admin Panel:**
    ```bash
    streamlit run admin_panel/app.py
    ```

### Common Commands
*   **Simulate Webhook:** `python scripts/simulate_webhook.py` (Great for testing without sending real WhatsApp messages)
*   **Seed DB:** `python scripts/seed_db.py` (Populates initial data)

## 6. Conventions
*   **Dates:** Store as UTC in Database. Convert to 'Asia/Jerusalem' for Display.
*   **Async:** The backend is fully async (`async def`). Database calls use synchronous `pymongo` (watch out for blocking if load increases).
*   **Localization:** Admin panel supports HE/EN via `config.TRANS`.
