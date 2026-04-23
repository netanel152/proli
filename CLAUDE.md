# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## CRITICAL: Green API limitation

Do **not** use interactive buttons (`send_interactive_buttons`) — Green API does not support them and the helper was removed in April 2026. All WhatsApp menus must be text-based (numeric or keyword replies). Example: instead of Approve/Reject buttons, send `"Reply '1' to approve, '2' to reject."`

## Commands

### Local Development (run all three in separate terminals)

```bash
# Backend API (FastAPI)
uvicorn app.main:app --reload --port 8000

# Background Worker (ARQ + APScheduler)
python -m app.worker

# Admin Panel (Streamlit)
streamlit run admin_panel/main.py
```

### Docker (recommended)

```bash
docker-compose up --build -d
# Backend: http://localhost:8000
# Admin:   http://localhost:8501
# Worker logs: docker-compose logs -f worker
```

### Database

```bash
python scripts/seed_db.py          # Seed initial data
python scripts/create_indexes.py   # Create MongoDB indexes
python scripts/clear_history.py    # Clear chat history
```

### Testing

```bash
# Run all unit tests (uses mongomock — no real DB needed)
pytest

# Run a single test file
pytest tests/test_matching_service.py

# Run only integration tests (requires MONGO_TEST_URI in .env)
pytest -m integration

# Run with verbose output
pytest -v
```

Expected: **162 passed, 6 skipped** (integration tests skipped without `MONGO_TEST_URI`).

### Linting / Formatting

```bash
black .
flake8 .
```

## Architecture

Proli is an AI-powered WhatsApp CRM for Israeli service professionals (plumbers, electricians, etc.). It runs as three cooperating processes:

### Process 1: FastAPI Backend (`app/`)

Entry point for Green API webhooks. Its only job is to validate the incoming payload, enqueue a task to Redis via ARQ, and immediately return `200 OK`. All heavy lifting is deferred to the Worker. Routes: `POST /webhook` and `GET /health`.

### Process 2: ARQ Worker (`app/worker.py` + `app/core/arq_worker.py`)

Picks up `process_message_task` jobs from Redis and calls `workflow_service.process_incoming_message`. Also hosts APScheduler for periodic jobs (SOS healer every 10 mins, stale monitor every 30 mins, daily agenda at 08:00 Israel time).

### Process 3: Streamlit Admin Panel (`admin_panel/`)

Protected by bcrypt cookie-based auth. Views for lead management, professional profiles, and schedule management.

### Service Layer (`app/services/`)

| Service | Responsibility |
|---|---|
| `workflow_service.py` | Central orchestrator — routes messages, manages FSM states, delegates to customer/pro flows |
| `customer_flow.py` | Customer completion checks, ratings, reviews |
| `pro_flow.py` | Professional text commands (approve, reject, pause, resume, finish) — text-only, no button handlers |
| `media_handler.py` | Media type detection and download (images, audio, video) |
| `ai_engine_service.py` | Gemini 2.5 Flash with adaptive fallback (Flash Lite → Flash → Flash 1.5); multimodal; 5-turn context window; non-blocking token accounting |
| `matching_service.py` | Progressive `$geoNear` aggregation (10 km → 20 km → 30 km); falls back to regex city match; load-balances by max 3 active leads per pro |
| `state_manager_service.py` | Redis-backed FSM per `chat_id` (`UserStates` enum); supports custom TTL per state |
| `context_manager_service.py` | Stores last 20 messages per `chat_id` in Redis |
| `lead_manager_service.py` | CRUD for leads in MongoDB |
| `notification_service.py` | Sends WhatsApp notifications to pros; SOS alerts |
| `monitor_service.py` | Stale job detection, reassignment, and escalation to PENDING_ADMIN_REVIEW |
| `whatsapp_client_service.py` | Green API HTTP client — text-only messages (interactive buttons not supported by Green API) |
| `cloudinary_client_service.py` | Media upload/retrieval |
| `security_service.py` | Rate limiting via Redis |

### Data Layer

- **MongoDB**: Primary store — `users` (pros + customers), `leads`, `slots`, `messages`, `settings`, `reviews`, `consent`, `audit_log`, `admins`
- **Redis**: ARQ task queue + context cache (chat history) + state machine (FSM)

### Key Constants (`app/core/constants.py`)

- `LeadStatus`: `contacted → new → booked → completed/rejected/closed/cancelled/pending_admin_review`
- `UserStates`: `IDLE`, `PRO_MODE`, `CUSTOMER_MODE`, `AWAITING_INTENT_CONFIRMATION`, `AWAITING_ADDRESS`, `AWAITING_PRO_APPROVAL`, `PAUSED_FOR_HUMAN`, `ONBOARDING_*`
- `WorkerConstants.MAX_PRO_LOAD = 3`: max concurrent leads per professional
- `WorkerConstants.SOS_TIMEOUT_MINUTES = 60`: reassignment trigger threshold
- `WorkerConstants.GEO_RADIUS_STEPS = [10000, 20000, 30000]`: progressive geo search radii
- `WorkerConstants.PAUSE_TTL_SECONDS = 900`: 15-minute rolling TTL for PAUSED_FOR_HUMAN state
- `ISRAEL_CITIES_COORDS`: static dict mapping Hebrew/English city names to `[lon, lat]` for geo queries

### Testing Conventions

Unit tests use `mongomock_motor` (in-memory MongoDB) and mock `whatsapp` and `ai` instances via `monkeypatch`. Integration tests (marked `@pytest.mark.integration`) connect to a real `MONGO_TEST_URI` test database and clear it before each run. `conftest.py` auto-applies the mock fixtures to all non-integration tests via `autouse=True`. `asyncio_mode = strict` is set in `pytest.ini`.

`$geoNear` is not supported by mongomock — matching service geo tests mock `users_collection.aggregate` as async generators directly.

`customer_flow.py` and `pro_flow.py` functions receive `whatsapp`/`lead_manager` as parameters (dependency injection) so `workflow_service.py` passes its shared instances.

### Configuration

All config is in `app/core/config.py` via `pydantic-settings`. Required env vars: `GREEN_API_INSTANCE_ID`, `GREEN_API_TOKEN`, `GEMINI_API_KEY`, `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET`. Optional: `MONGO_URI` (defaults to localhost), `REDIS_URL`, `MONGO_TEST_URI` (for integration tests), `ADMIN_PASSWORD`, `ADMIN_PHONE` (defaults to hardcoded), `WEBHOOK_TOKEN` (enables webhook auth).

## Session Guidelines

- Skip files over 100KB unless explicitly required.
- Suggest `/cost` when a session is running long to monitor cache ratio.
- Recommend starting a new session when switching to an unrelated task.
