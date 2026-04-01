# Proli - AI CRM & Automation Platform

Proli is an intelligent CRM and scheduling automation platform designed for service professionals (plumbers, electricians, technicians). It features a multi-modal WhatsApp AI bot for customer interactions and a Streamlit-based admin panel for business management.

## 🚀 Project Overview

- **Core Mission**: Automate lead qualification, routing, and scheduling for service businesses using Generative AI.
- **Key Capabilities**: 
  - **Multi-modal AI**: Processes text, images (Vision), video (issue diagnosis), and audio (voice notes) via Google Gemini.
  - **Smart Routing**: Matches leads to professionals based on location, rating, and current load.
  - **Autonomous Scheduling**: Manages MongoDB-backed calendars with atomic locks for booking.
  - **Auto-Recovery (Healer)**: Automatically reassigns unresponsive leads (SOS logic).

## 🛠️ Tech Stack

- **Backend**: FastAPI (Python 3.12+), Uvicorn, Gunicorn.
- **Admin Panel**: Streamlit (Multi-language: HE/EN).
- **Task Queue**: ARQ (Redis-based) for background message processing and AI tasks.
- **Database**: MongoDB Atlas (Async via `motor`), Redis (Session state & caching).
- **AI Engine**: Google Gemini (Adaptive Fallback: `2.5-flash-lite` → `2.5-flash` → `1.5-flash`).
- **Integrations**: Green API (WhatsApp), Cloudinary (Media storage), Redis.
- **Testing**: Pytest (AsyncIO), Mongomock-motor.

## 🏗️ Architecture

- **`app/api/`**: FastAPI routes. Webhooks (`/webhook`) receive WhatsApp messages and enqueue tasks.
- **`app/worker.py`**: Entry point for the ARQ worker that processes enqueued message tasks.
- **`app/services/`**: Core business logic. 
  - `ai_engine_service.py`: Gemini integration with multi-modal support.
  - `workflow_service.py`: Orchestrates message flow and state transitions.
  - `matching_service.py`: Logic for routing leads to the best professional.
- **`app/scheduler.py`**: Background jobs (run within the worker) for reminders and stale lead monitoring.
- **`admin_panel/`**: Streamlit application for management, analytics, and settings.
- **`app/core/`**: Shared utilities, configuration (`config.py`), and database clients.

## 🚦 Building and Running

### Development
1. **Environment**: Use Python 3.12+ and a virtual environment.
2. **Setup**: `pip install -r requirements.txt`.
3. **Environment Variables**: Create a `.env` file (see `README.md` for required keys).
4. **Seed Database**: `python scripts/seed_db.py`.

### Running Services
- **Backend**: `uvicorn app.main:app --reload --port 8000`
- **Worker**: `python -m app.worker`
- **Admin**: `streamlit run admin_panel/main.py`

### Docker (Recommended)
`docker-compose up --build -d`
- Backend: http://localhost:8000
- Admin: http://localhost:8501

## 🧪 Testing and Validation

- **Run Tests**: `pytest`
- **Simulation**: Use `scripts/simulate_webhook.py` to test the full message flow without actual WhatsApp messages.
- **Linting**: `black .` and `flake8`.

## 📜 Development Conventions

- **Async First**: Use `async/await` for all I/O bound operations (DB, AI, HTTP).
- **Type Safety**: Use Pydantic models for all data structures and configuration.
- **Error Handling**: Use the fallback AI strategy and centralized message templates in `app/core/messages.py`.
- **Logging**: Use `loguru` for structured logging. Avoid bare `print` statements.
- **Hebrew Support**: The bot is Hebrew-centric. System prompts and user-facing messages should respect the Hebrew locale and formatting.
- **Security**: Protect `.env` files. Use `admin_panel/core/auth.py` for admin authentication.
