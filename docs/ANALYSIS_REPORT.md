# Proli Code Review — March 2026

## Overview

Full codebase review covering architecture, security, performance, code quality, and deployment (Railway + Cloudinary). 19 issues were identified and all have been resolved.

---

## CRITICAL — All Resolved

### 1. ~~XSS Vulnerability in Admin Panel~~ FIXED
Added `html.escape()` in `render_chat_bubble()` to sanitize user text before HTML rendering.

### 2. ~~Auth Cookie Token is Deterministic/Forgeable~~ FIXED
Replaced static `SHA256(admin_hash)` token with random `secrets.token_hex(32)` per login, stored in-memory with expiry. Logout invalidates server-side token.

### 3. ~~No Webhook Signature Verification~~ FIXED
Added `WEBHOOK_TOKEN` env var. If set, webhook requests must include `?token=<value>` or receive 403. Configure the token URL in Green API dashboard.

### 4. ~~Hardcoded Admin Phone Number~~ FIXED
Moved from `WorkerConstants.ADMIN_PHONE` to `settings.ADMIN_PHONE` (env var with default). Updated `notification_service.py`, `monitor_service.py`, and tests.

### 5. ~~`reviews_collection` Not Patched in Tests~~ FIXED
Added `reviews_collection` monkeypatching in both unit and integration test fixtures in `conftest.py`.

---

## HIGH — All Resolved

### 6. ~~Race Condition in Redis Singleton~~ FIXED
Rewrote `redis_client.py` with `asyncio.Lock()` and double-check locking for both `get_redis_client()` and `get_arq_pool()`.

### 7. ~~httpx.AsyncClient Created Per-Request~~ FIXED
Created shared `http_client.py` module. Updated `whatsapp_client_service.py` (persistent client), `workflow_service.py`, and `ai_engine_service.py`. Added cleanup in `main.py` and `arq_worker.py` shutdown.

### 8. ~~N+1 Query in Professional Routing~~ FIXED
Replaced per-pro `count_documents()` loop with single `$group` aggregation pipeline in `matching_service.py`. 50 pros = 1 query instead of 51.

### 9. ~~Double Gemini API Call Per Message~~ FIXED
Pro persona call now receives only last 4 messages instead of full 20-message history. System prompt already contains all extracted context (city, issue, transcription, pricing).

### 10. ~~Unbounded `to_list(length=None)` Queries~~ FIXED
Added limits: `monitor_service.py` stale/stuck leads capped at `DB_QUERY_LIMIT` (100), `scheduler.py` active pros at 500, booked jobs per pro at 50.

### 11. ~~Railway Deployment: Single Container~~ FIXED
Simplified Dockerfile with per-service CMD overrides. Created `docs/RAILWAY_SETUP.md` with multi-service configuration guide.

### 12. ~~`asyncio.run()` Inside Streamlit~~ FIXED
Replaced with sync `send_completion_check_sync()` in `admin_panel/core/utils.py` using sync PyMongo + sync httpx, matching the admin panel's existing sync pattern.

---

## MEDIUM — All Resolved

### 13. ~~`workflow_service.py` — God Module~~ FIXED
Extracted into focused modules: `customer_flow.py` (completion checks, ratings, reviews), `pro_flow.py` (professional text commands), `media_handler.py` (media type detection and download). `workflow_service.py` is now a slim orchestrator that delegates to these modules via dependency injection.

### 14. ~~Cloudinary Service Uses `print()` Not Logger~~ FIXED
Replaced `print()` with `logger.error()` in `cloudinary_client_service.py`.

### 15. ~~`create_initial_schedule` Hardcodes UTC Offset~~ FIXED
Rewrote to use `pytz.timezone('Asia/Jerusalem')` localization. Slots are now created in Israel time and converted to UTC, correctly handling DST.

### 16. ~~`generate_system_prompt` Only Handles 2 of 7 Professions~~ FIXED
Added `PROFESSION_CONFIG` dict covering all 7 `ProType` values (plumber, electrician, handyman, locksmith, painter, cleaner, general) with role, safety instructions, and keywords.

### 17. ~~Outdated `google-genai==0.3.0`~~ FIXED
Updated `requirements.txt` to `google-genai>=1.0.0`. API surface is backward-compatible.

### 18. ~~`price_list` Schema Mismatch~~ FIXED
`workflow_service.py` now formats dict price lists as `"service: N ILS"` strings before injecting into AI prompts. Handles both dict and string formats.

### 19. ~~Inconsistent Message Roles~~ FIXED
Changed seed data message role from `"assistant"` to `"model"` to match `get_chat_history()` mapping.

---

## Technology Stack Assessment

| Component | Version | Verdict | Notes |
|---|---|---|---|
| FastAPI | 0.109.2 | **Keep** | Excellent async webhook processing |
| ARQ | >=0.25.0 | **Keep** | Lightweight, Redis-based. Right for this scale |
| APScheduler | 3.10.4 | **Caution** | Works for single worker. Add distributed locking for horizontal scaling |
| MongoDB + Motor | 6.0 / 3.3.2 | **Keep** | Good fit for flexible schemas. Async Motor is correct |
| Redis | >=5.0.1 | **Keep** | Right tool for state/context/queue |
| Gemini 2.5 Flash | via google-genai >=1.0.0 | **Good** | Model choice and SDK are solid |
| Streamlit | 1.31.1 | **OK for now** | Works for admin. Consider React if real-time needed |
| Cloudinary | 1.38.0 | **Keep** | Works fine. Sync wrapper for Streamlit is valid |
| Railway | - | **Ready** | Multi-service setup documented in `docs/RAILWAY_SETUP.md` |

## Architecture Assessment

The overall architecture is **solid for a v1**:
- Webhook -> Queue -> Worker pattern is correct
- State machine in Redis is well-designed
- Adaptive AI fallback chain is smart
- Admin panel separation is correct

**Remaining recommendations:**
1. Add distributed locking for scheduler (needed before horizontal scaling)
