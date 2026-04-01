# Proli System Architecture

This document provides a high-level overview of the Proli system architecture, component interactions, and data flow.

## 1. High-Level Overview

Proli acts as an intelligent intermediary between Clients (WhatsApp Users) and Professionals. It utilizes a microservices-like architecture (within a monorepo) orchestrated by Docker Compose or Railway.

### Core Components

1.  **Backend API (FastAPI):**
    *   **Role:** Entry point for external Webhooks (Green API) and health checks.
    *   **Responsibility:** Rapid ingestion of messages, request validation (idempotency, rate limiting, instance verification), and queuing tasks to the Worker via Redis/ARQ.
    *   **Scaling:** Stateless, can be horizontally scaled behind a load balancer.

2.  **Background Worker (ARQ + APScheduler):**
    *   **Role:** The heavy lifter of the system.
    *   **Responsibilities:**
        *   **Message Processing (ARQ):** Handles AI inference (Gemini), DB operations, and WhatsApp API calls asynchronously.
        *   **Periodic Tasks (APScheduler):** Runs daily agenda reminders (08:00 IL), Stale Job Monitor (every 30 min), SOS Auto-Healer (every 10 min), and SOS Admin Reporter (every 4 hours).
    *   **Scaling:** Can be scaled horizontally, BUT requires distributed locking for scheduler jobs to prevent duplicates.

3.  **Admin Panel (Streamlit):**
    *   **Role:** Management Interface.
    *   **Responsibility:** Lead dashboard with inline editing, professional profile CRUD, schedule management (daily editor + bulk generator), system settings.
    *   **Security:** Cookie-based Auth with Bcrypt hashing and random session tokens (`secrets.token_hex(32)`) with server-side validation and expiry. Multi-admin RBAC (owner/editor/viewer roles). Bilingual (Hebrew/English).
    *   **Database:** Uses synchronous PyMongo client (separate from the async Motor client used by the API/Worker).
    *   **Features:** Lead dashboard, professional profiles, schedule management (daily/bulk/weekly templates), analytics dashboard, admin user management, audit log viewer, system health monitoring.

4.  **Database Layer:**
    *   **MongoDB Atlas:** Primary data store.
        *   Collections: `users`, `leads`, `messages`, `slots`, `settings`, `reviews`, `consent`, `audit_log`, `admins`
        *   Indexes: phone_number (unique), location (2dsphere), chat_id, status, pro_id+status (compound), status+created_at (compound)
    *   **Redis:** Fast-access layer for:
        *   **Task Queue:** ARQ job backend
        *   **Context:** Recent chat history per `chat_id` (last 20 messages, 4h TTL)
        *   **State:** User session state via FSM (4h TTL)
        *   **Rate Limiting:** Fixed-window per `chat_id` (10 req/60s)
        *   **Idempotency:** Webhook deduplication via `idMessage` (24h TTL)

## 2. Detailed Data Flow

### A. Inbound Message Flow (The "Fast Path")
1.  **User** sends WhatsApp message.
2.  **Green API** pushes JSON webhook to `POST /webhook` on **Backend API**.
3.  **Backend API:**
    *   Validates webhook token (`?token=<value>`) if `WEBHOOK_TOKEN` is configured.
    *   Checks idempotency via Redis `SET NX` on `webhook:{idMessage}`.
    *   Validates `idInstance` matches configured instance.
    *   Filters group messages, applies rate limiting.
    *   Extracts text/media URL from payload (supports: text, extended text, buttons, location, image, audio, video).
    *   Enqueues `process_message_task` to **Redis** via **ARQ**.
    *   Returns `200 OK` immediately (preventing webhook timeout).

### B. Message Processing (The "Smart Path")
1.  **Worker** picks up `process_message_task`.
2.  **Reset/SOS Check:** Checks for system commands (reset, SOS/human handoff).
3.  **State Manager** checks if user is in a specific flow (PRO_MODE, AWAITING_ADDRESS, etc.).
4.  **Pro Auto-Detection:** If IDLE, checks if sender's phone matches a professional.
5.  **Message Logging:** Saves to MongoDB `messages` + Redis context cache.
6.  **Completion/Rating/Review Flow (`customer_flow.py`):** Checks if user is responding to post-job prompts.
7.  **Media Handling (`media_handler.py`):**
    *   Images: Downloaded as bytes, passed inline to Gemini.
    *   Audio/Video: Streamed to temp file, uploaded via Gemini File API, waited for processing.
8.  **AI Engine (Gemini Adaptive Fallback)** — Two-phase analysis:
    *   **Phase 1 (Dispatcher):** Extracts city + issue from the conversation. *Optimization: Only receives the last 8 messages of history.*
    *   **Phase 2 (Pro Persona):** Generates a response using the pro's custom system prompt, pricing, and social proof. *Optimization: If a pro is already assigned to the active lead, Phase 1 is skipped entirely and processing goes straight to Phase 2.*
    *   Adaptive fallback: Flash Lite 2.5 -> Flash 2.5 -> Flash 1.5.
    *   **Context Clearing:** Redis chat history is wiped upon job completion or rejection to ensure clean states for future requests.
9.  **Matching Service** (if routing):
    *   Geo-spatial `$near` query (10km radius) if city coordinates found.
    *   Fallback to regex matching on `service_areas`.
    *   Load balancing via `$group` aggregation pipeline: skip pros with >= 3 active leads.
    *   Final fallback: all active pros sorted by rating.
10. **Deal Detection:** Checks for `is_deal` flag or `[DEAL:...]` pattern.
11. **Action:**
    *   Updates lead in MongoDB.
    *   Sends WhatsApp reply to customer.
    *   If deal: Sends notification + Waze link to pro.

### C. Periodic Maintenance (The "Safety Net")
*   **Every 10 mins:** `SOS Healer` checks for leads stuck in `NEW`/`CONTACTED` state > 60 mins and reassigns to new pro.
*   **Every 30 mins:** `Stale Monitor` sends reminders to pros (4-6h old) and completion checks to customers (6-24h old). Flags >24h leads for admin.
*   **Every 4 hours:** `SOS Reporter` sends batched summary of stuck leads to admin WhatsApp.
*   **Daily (02:00 IL):** Automated MongoDB backup (mongodump + gzip, optional S3 upload).
*   **Daily (08:00 IL):** Sends daily agenda to each active pro with their booked jobs.
*   **Weekly (Sunday 01:00 IL):** Regenerates appointment slots from recurring weekly templates for all active pros.

## 3. Technology Stack

| Component | Technology | Version | Reasoning |
|---|---|---|---|
| **Language** | Python | 3.12+ | Rich ecosystem for AI and Async Web |
| **Web Framework** | FastAPI | 0.109.2 | High performance, native AsyncIO |
| **Async Worker** | ARQ | >=0.25.0 | Lightweight, built on Redis |
| **Scheduling** | APScheduler | 3.10.4 | Cron-like scheduling for Python |
| **Database** | MongoDB (Motor) | 6.0 / 3.3.2 | Schema-less flexibility |
| **Caching** | Redis | >=5.0.1 | Low latency for context, state, queue |
| **AI Model** | Gemini 2.5 Flash | via google-genai | Multimodal, fast, cost-effective |
| **Media Storage** | Cloudinary | 1.38.0 | CDN-backed image hosting |
| **Messaging** | WhatsApp | via Green API | WhatsApp Business API proxy |
| **Admin Panel** | Streamlit | 1.31.1 | Rapid dashboard development |
| **Infrastructure** | Docker Compose / Railway | - | Container orchestration |

## 4. Directory Structure

*   `app/api/routes/`: Webhook (with token auth) and health endpoints (fast, async).
*   `app/services/`: Business logic layer:
    *   `workflow_service.py`: Central orchestrator, delegates to sub-modules.
    *   `customer_flow.py`: Customer completion checks, ratings, reviews.
    *   `pro_flow.py`: Professional text commands (approve, reject, finish).
    *   `media_handler.py`: Media type detection and download (images, audio, video).
    *   `ai_engine_service.py`: Gemini 2.5 Flash with adaptive fallback.
    *   `matching_service.py`: Geo-spatial routing with `$group` aggregation for load balancing.
    *   `notification_service.py`: WhatsApp notifications with SMS fallback.
    *   `sms_service.py`: SMS fallback via Israeli provider (InforUMobile).
    *   `data_management_service.py`: Privacy compliance — consent, data export/deletion.
    *   `audit_service.py`: Admin action audit logging.
    *   `analytics_service.py`: Lead funnel, daily volume, pro performance aggregations.
    *   `scheduling_service.py`: Recurring weekly templates, slot generation, no-show tracking.
    *   `pro_onboarding_service.py`: WhatsApp-based self-signup flow for new professionals.
    *   `monitor_service.py`, `lead_manager_service.py`, etc.
*   `app/core/`: Infrastructure wrappers (DB, Redis, Config, Logger, Constants, Messages, Prompts).
*   `app/schemas/`: Pydantic models for webhook payload validation.
*   `app/worker.py`: Worker process entry point.
*   `app/scheduler.py`: APScheduler jobs and configuration.
*   `admin_panel/`: Streamlit UI (views, core auth/config/utils, UI components). Includes RBAC (owner/editor/viewer), analytics dashboard, and audit log viewer.
*   `scripts/`: Database seeding, index creation, utilities.
*   `docs/`: Project documentation.
*   `nginx/`: Reverse proxy configuration for Docker Compose.

## 5. Deployment

### Docker Compose (Local/Self-Hosted)
Six services: `api`, `worker`, `admin`, `mongo`, `redis`, `nginx`. Each app service gets its own container with proper health checks and dependency ordering.

### Railway (Cloud)
Split into 3 Railway services pointing to the same Dockerfile with different `command` overrides. Each service shares MongoDB Atlas and Redis via project-level env vars. See `docs/RAILWAY_SETUP.md` for full configuration guide.
