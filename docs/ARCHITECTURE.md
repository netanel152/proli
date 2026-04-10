# Proli System Architecture

## 1. High-Level Overview

Proli connects WhatsApp customers with service professionals through a three-process architecture orchestrated by Docker Compose or Railway.

```
Customer (WhatsApp)
       │
       ▼
  Green API ──► POST /webhook
                     │
              FastAPI Backend      ← validates, deduplicates, enqueues
                     │
                  Redis/ARQ
                     │
              Background Worker    ← AI, DB, WhatsApp replies
                     │
           ┌─────────┴─────────┐
         MongoDB             Redis
      (leads, users,       (state, context,
       messages, slots)     rate limit, queue)
                     │
              Admin Panel          ← Streamlit management UI
```

---

## 2. The Three Processes

### Process 1: FastAPI Backend (`app/main.py`)

**Role:** Stateless HTTP ingestion layer.

- Validates incoming Green API webhook (token, instance ID, idempotency via Redis `SET NX`, rate limiting 10 req/60 s per `chat_id`)
- Extracts text/media from payload (text, extended text, buttons, location, image, audio, video)
- Enqueues `process_message_task` to Redis via ARQ
- Returns `200 OK` immediately (prevents Green API webhook timeout)
- Health endpoint (`GET /health`) checks MongoDB, Redis, WhatsApp, and worker heartbeat

**Scaling:** Horizontally scalable behind any load balancer — no shared in-process state.

### Process 2: Background Worker (`app/worker.py` + `app/core/arq_worker.py`)

**Role:** Heavy-lifting — AI inference, database writes, WhatsApp replies, scheduled jobs.

**ARQ task:** `process_message_task` → `workflow_service.process_incoming_message`

**APScheduler jobs (6 total):**

| Job | Schedule | Function |
|-----|----------|----------|
| Daily agendas | 08:00 IL (daily) | Send each pro their booked jobs for the day |
| Stale monitor | Every 30 min | Remind pros (4–6 h), check customers (6–24 h), flag >24 h for admin |
| SOS Healer | Every 10 min | Reassign leads stuck > 60 min; escalate to `PENDING_ADMIN_REVIEW` if no replacement |
| SOS Reporter | Every 4 h | Send batched summary of stuck leads to admin WhatsApp |
| Lead Janitor | Every 6 h | Auto-reject `CONTACTED` leads with no assigned pro after 24 h |
| Slot Regeneration | Sunday 01:00 IL | Regenerate appointment slots from recurring weekly templates |

**Startup/shutdown:** Verifies DB + Redis connectivity, starts APScheduler, updates `worker:heartbeat` key in Redis every 60 s (120 s expiry).

**Scaling:** Multiple worker instances require distributed locking for scheduler jobs (not yet implemented — run a single worker instance).

### Process 3: Admin Panel (`admin_panel/main.py`)

**Role:** Streamlit management interface.

- Lead CRUD with inline editing and bulk operations
- Professional profile management and approval workflow
- Schedule management (daily editor, bulk generator, weekly templates)
- Analytics (lead funnel, daily volume, pro performance)
- RBAC: Owner / Editor / Viewer roles with audit logging
- Bilingual Hebrew/English with RTL support

**Security:** Cookie-based auth with bcrypt password hashing and `secrets.token_hex(32)` session tokens, server-side validated on every request.

**Database:** Synchronous PyMongo client (separate from the async Motor client used by API + Worker).

---

## 3. Message Processing Flow

```
process_incoming_message(chat_id, text, media_url)
        │
        ├─ Reset keyword? → clear state + context → RESET_SUCCESS
        │
        ├─ SOS / human handoff keyword?
        │      └─ set PAUSED_FOR_HUMAN (TTL 7200 s)
        │         send_sos_alert(admin + pro)
        │         notify customer BOT_PAUSED_BY_CUSTOMER
        │
        ├─ State == PAUSED_FOR_HUMAN?
        │      └─ log message silently, return (bot offline, direct chat)
        │
        ├─ State == AWAITING_PRO_APPROVAL?
        │      └─ send STILL_WAITING, return
        │
        ├─ State == PRO_MODE?
        │      └─ handle_pro_text_command(pro, text)
        │            ├─ btn_approve_lead → lead BOOKED, clear customer state
        │            ├─ btn_pause_bot   → customer PAUSED_FOR_HUMAN (TTL 7200 s)
        │            ├─ btn_reject_lead → lead REJECTED, clear customer state
        │            ├─ "המשך"/"resume" → clear PAUSED_FOR_HUMAN
        │            └─ other commands  → approve/reject/finish/status
        │
        ├─ State == AWAITING_ADDRESS? → save address, clear state
        │
        ├─ Phone matches active pro? → set PRO_MODE, handle pro flow
        │
        ├─ Register keyword? → start_onboarding()
        │
        └─ Customer flow:
               ├─ log message
               ├─ customer_flow checks (completion/rating/review)
               ├─ media handling (image bytes / audio+video URL)
               │
               ├─ Phase 1 — Dispatcher AI (last 5 turns = 10 messages)
               │      extracts city + issue
               │      if city+issue found → matching_service.determine_best_pro()
               │
               ├─ Phase 2 — Pro Persona AI (if pro assigned)
               │      adopts pro's system prompt, pricing, social proof
               │      tracks token usage: $inc users.total_tokens_used (fire-and-forget)
               │      is_deal=True → _finalize_deal()
               │
               └─ _finalize_deal():
                      create/update lead (status=NEW)
                      set customer state AWAITING_PRO_APPROVAL
                      send customer AWAITING_APPROVAL
                      send pro 3-button approval message (approve/pause/reject)
```

---

## 4. Matching Service

`matching_service.determine_best_pro(location, issue_type, excluded_pro_ids)`

**Step 1 — Geo search (if city has coordinates):**

Progressive `$geoNear` aggregation over `WorkerConstants.GEO_RADIUS_STEPS = [10000, 20000, 30000]` meters:

```python
pipeline = [
    {"$geoNear": {
        "near": {"type": "Point", "coordinates": [lon, lat]},
        "distanceField": "dist_meters",
        "maxDistance": radius,       # 10 km → 20 km → 30 km
        "spherical": True,
        "query": base_filter,        # is_active, role, optional $nin
    }},
    {"$sort": {"social_proof.rating": -1}},
    {"$limit": 100},
]
```

Breaks on first non-empty result set. If all three radii return empty, falls through to step 2.

**Step 2 — Text fallback (no coordinates or geo empty):**

Regex match on `service_areas` field, sorted by rating.

**Step 3 — Reverse match:**

Checks if the city name appears in any pro's `service_areas` string (broader fuzzy match).

**Load balancing:** Before returning a pro, the service queries `leads_collection` for each candidate's active lead count. Skips any pro with `count >= WorkerConstants.MAX_PRO_LOAD` (default: 3).

**No-pro outcome:** Returns `None`. Caller sets lead to `PENDING_ADMIN_REVIEW` and sends `Messages.Customer.PENDING_REVIEW`.

---

## 5. State Machine (UserStates)

Redis-backed FSM per `chat_id`. Default TTL: 4 hours. `PAUSED_FOR_HUMAN` uses a custom 2-hour TTL.

| State | Description |
|-------|-------------|
| `IDLE` | Default — no active flow |
| `PRO_MODE` | Sender is an active professional |
| `AWAITING_ADDRESS` | Waiting for customer to provide street address |
| `AWAITING_PRO_APPROVAL` | Deal sent to pro, customer on soft hold |
| `PAUSED_FOR_HUMAN` | Bot paused for direct pro-customer chat (2 h auto-expiry) |
| `ONBOARDING_*` | Pro self-signup steps (NAME → TYPE → AREAS → PRICES → CONFIRM) |

---

## 6. Lead Lifecycle

```
CONTACTED → NEW → BOOKED → COMPLETED → (rating) → CLOSED
                ↓                ↓
            REJECTED       CANCELLED
                ↓
       PENDING_ADMIN_REVIEW  (no replacement pro found)
```

| Status | Meaning |
|--------|---------|
| `contacted` | AI opened a conversation, no pro assigned yet |
| `new` | Pro matched and sent approval request |
| `booked` | Pro approved, appointment slot locked |
| `completed` | Work done, awaiting customer rating |
| `rejected` | Pro declined |
| `closed` | Archived after max reassignments |
| `cancelled` | Customer cancelled |
| `pending_admin_review` | No pro found after all radius/fallback attempts |

---

## 7. Data Layer

### MongoDB Collections

| Collection | Purpose |
|-----------|---------|
| `users` | Professionals and customers. Pros have `location` (2dsphere), `service_areas`, `price_list`, `social_proof`, `total_tokens_used` |
| `leads` | Job requests. Fields: `chat_id`, `pro_id`, `status`, `issue_type`, `full_address`, `appointment_time`, `media_url`, `reassignment_count` |
| `messages` | Chat history log per `chat_id` |
| `slots` | Appointment slots per pro with atomic locking (`is_taken`) |
| `settings` | Scheduler config toggles (`sos_healer_active`, etc.) |
| `reviews` | Customer ratings and text reviews |
| `consent` | Privacy consent acknowledgements |
| `audit_log` | Admin action history |
| `admins` | Admin accounts with bcrypt-hashed passwords and RBAC roles |

### Redis Keys

| Pattern | Purpose | TTL |
|---------|---------|-----|
| `arq:queue:*` | ARQ task queue | — |
| `state:{chat_id}` | User FSM state | 4 h (PAUSED_FOR_HUMAN: 2 h) |
| `ctx:{chat_id}` | Chat history (last 20 messages) | 4 h |
| `rate:{chat_id}` | Rate limit counter | 60 s |
| `webhook:{idMessage}` | Idempotency key | 24 h |
| `worker:heartbeat` | Worker liveness | 120 s |

---

## 8. Technology Stack

| Component | Technology | Notes |
|-----------|-----------|-------|
| Language | Python 3.12+ | |
| API framework | FastAPI | Async, OpenAPI built-in |
| Task queue | ARQ | Lightweight, Redis-backed |
| Scheduler | APScheduler | 6 cron/interval jobs |
| AI | Google Gemini (google-genai) | Flash Lite 2.5 → Flash 2.5 → Flash 1.5 fallback |
| Database | MongoDB 6.0 + Motor | Async driver |
| Cache/State | Redis | Context, FSM, rate limit, idempotency |
| Admin UI | Streamlit | |
| Media | Cloudinary | CDN-backed |
| WhatsApp | Green API | |
| SMS fallback | InforUMobile | Israeli provider |
| Logging | Loguru | PII-masked (Israeli phone numbers), JSON in production |
| Infrastructure | Docker Compose / Railway | 6 services: api, worker, admin, mongo, redis, nginx |

---

## 9. Deployment

### Docker Compose (local / self-hosted)

Six services with health checks and proper dependency ordering:
- `api` — FastAPI (4 uvicorn workers, port 8000)
- `worker` — ARQ + APScheduler
- `admin` — Streamlit (port 8501, proxied through nginx)
- `mongo` — MongoDB 6.0 with persistent volume
- `redis` — Redis Alpine with persistent volume
- `nginx` — Reverse proxy (port 8080 → routes /api and /)

```bash
docker-compose up --build -d
```

### Railway (cloud)

Three Railway services pointing to the same Dockerfile with different `command` overrides. All share MongoDB Atlas and Redis via project-level env vars. See [RAILWAY_SETUP.md](RAILWAY_SETUP.md).
