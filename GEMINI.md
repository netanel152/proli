# Fixi - AI Automation for Professionals

## Project Overview
**Fixi** is a dual-interface platform designed to automate scheduling and customer interaction for service professionals (e.g., plumbers, electricians).
It consists of two main components:
1.  **AI WhatsApp Bot (Backend):** Handles user conversations, identifies issues via text/image/audio using Google Gemini, and autonomously books appointments.
2.  **Admin Panel (Frontend):** A web-based dashboard for professionals to manage leads, schedules, and settings.

## Tech Stack & Architecture
*   **Backend:** Python 3.12+, FastAPI (Async)
*   **Admin Frontend:** Streamlit
*   **Database:** MongoDB Atlas (using `motor` for async access)
*   **AI Engine:** Google Gemini (using the modern `google-genai` v1 SDK)
*   **Messaging:** WhatsApp via Green API
*   **Media:** Cloudinary for storage and management

## Directory Structure
*   `app/`: Main FastAPI backend application.
    *   `main.py`: Entry point for the backend server and webhook handler.
    *   `core/`: Configuration (`config.py`) and database connection (`database.py`).
    *   `services/`: Business logic (AI processing, WhatsApp client, Workflow orchestration).
    *   `schemas/`: Pydantic models.
*   `admin_panel/`: Streamlit-based dashboard.
    *   `app.py`: Entry point for the admin interface.
    *   `page_views/`: UI components for different dashboard pages.
*   `scripts/`: Utility scripts for maintenance and setup.
    *   `seed_db.py`: Populates the DB with dummy data (professionals, slots).
    *   `test_connection.py`: Verifies API keys and DB connectivity.
*   `tests/`: Automated tests (`pytest`).

## Setup & Installation

1.  **Environment Setup:**
    ```bash
    python -m venv venv
    # Windows:
    venv\Scripts\activate
    # Unix/Mac:
    source venv/bin/activate
    ```

2.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

3.  **Configuration:**
    Create a `.env` file in the root directory with the following keys:
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

## Development Workflow

### Running the Application
The system requires two separate processes running simultaneously:

**1. Backend Server (FastAPI):**
Handles WhatsApp webhooks and API logic.
```bash
uvicorn app.main:app --reload --port 8000
```

**2. Admin Panel (Streamlit):**
Provides the visual interface for management.
```bash
streamlit run admin_panel/app.py
```
*   The Admin Panel is typically accessible at `http://localhost:8501`.

### Database Management
*   **Seeding Data:** To reset and populate the database with test professionals and slots:
    ```bash
    python scripts/seed_db.py
    ```

### Testing
Run the full automated test suite using the virtual environment:
```bash
venv\Scripts\python -m pytest tests/test_full_flow.py
```

## Conventions
*   **SDK Usage:** Use `google.genai` and its `types` module. Avoid the deprecated `google.generativeai`.
*   **Async/Await:** The backend uses `async` functions extensively, especially for database operations (`motor`) and external API calls (`httpx`).
*   **Configuration:** All environment variables are managed via `pydantic-settings` in `app/core/config.py`.
*   **Modular Services:** Logic is separated into specific services (`ai_engine.py`, `whatsapp_client.py`) to keep `main.py` clean.