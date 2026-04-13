# Proli — AI Automation for Service Professionals

**[English](#english) | [עברית](#hebrew)**

---

<a name="english"></a>

**Proli** is an AI-powered WhatsApp CRM and scheduling platform for Israeli service professionals (plumbers, electricians, technicians). It combines a multimodal WhatsApp bot for customer interactions with a full-featured admin panel for business management.

## Core Features

### WhatsApp Bot (Customer-facing)

- **Progressive Geo Routing** — Finds the best pro within 10 km, expanding to 20 km then 30 km if needed. Falls back to city-name matching for non-geo queries.
- **Rating + Load Balancing** — Prioritizes highest-rated pros; skips any pro carrying 3 or more active leads.
- **Pro Approval Flow** — Every deal requires explicit pro consent. Customers wait in a soft-hold state (`AWAITING_PRO_APPROVAL`) while the pro reviews and approves, pauses, or rejects via interactive WhatsApp buttons.
- **Live Handoff** — Customer or pro can pause the bot for direct conversation (`PAUSED_FOR_HUMAN`, 2-hour auto-expiry via Redis TTL). Bot resumes automatically when the TTL expires.
- **Multimodal AI** — Analyzes photos, transcribes voice notes, and watches video clips. Mandatory media collection step before estimates.
- **Dynamic Pro Personas** — The AI adopts the assigned pro's pricing, tone, and custom rules during the booking conversation.
- **SOS Auto-Recovery (Healer)** — Detects leads stuck > 60 min and reassigns to a new pro. If no replacement is found, escalates to `PENDING_ADMIN_REVIEW` and notifies the customer.
- **Stale Monitor** — Follows up with pros (4–6 h) and customers (6–24 h) on open leads.
- **Pro Onboarding** — Self-service WhatsApp signup flow for new professionals.

### Admin Panel (Manager-facing)

- **Live Dashboard** — Real-time metrics on leads, professionals, and revenue.
- **RBAC** — Owner, Editor, and Viewer roles with full audit logging.
- **Analytics** — Lead funnels, daily volume, pro performance charts.
- **Lead Management** — Full CRUD with inline editing.
- **Schedule Management** — Daily editor, bulk generator, and recurring weekly templates.
- **Privacy Tools** — GDPR data export and user deletion.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.12+ |
| API | FastAPI + Uvicorn |
| Worker | ARQ (Redis task queue) + APScheduler |
| AI | Google Gemini (Flash Lite 2.5 → Flash 2.5 → Flash 1.5 fallback) |
| Database | MongoDB Atlas (async Motor) |
| Cache / State | Redis |
| Admin UI | Streamlit |
| Media | Cloudinary |
| WhatsApp | Green API |
| SMS Fallback | InforUMobile |
| Security | Bcrypt, webhook token verification, session cookies |
| Infrastructure | Docker Compose / Railway |

---

## Installation & Setup

### 1. Clone and create environment

```bash
git clone https://github.com/netanel152/proli.git
cd proli
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure `.env`

```env
# Required
GREEN_API_INSTANCE_ID=...
GREEN_API_TOKEN=...
GEMINI_API_KEY=...
CLOUDINARY_CLOUD_NAME=...
CLOUDINARY_API_KEY=...
CLOUDINARY_API_SECRET=...

# Optional
MONGO_URI=mongodb+srv://...          # default: mongodb://localhost:27017/proli_db
MONGO_TEST_URI=mongodb+srv://...     # required only for integration tests
ADMIN_PASSWORD=...                   # generate hash: python scripts/generate_admin_hash.py
ADMIN_PHONE=972501234567             # WhatsApp number for SOS alerts
WEBHOOK_TOKEN=...                    # enables ?token=<value> webhook auth
ENVIRONMENT=development              # production | development
```

### 3. Seed the database

```bash
python scripts/seed_db.py
python scripts/create_indexes.py
```

---

## Running the App

### Option A: Docker (recommended)

```bash
docker-compose up --build -d
```

| Service | URL |
|---------|-----|
| API | http://localhost:8000 |
| Admin Panel | http://localhost:8501 |
| Worker logs | `docker-compose logs -f worker` |

### Option B: Local development (three terminals)

```bash
# Terminal 1 — FastAPI backend
uvicorn app.main:app --reload --port 8000

# Terminal 2 — Background worker
python -m app.worker

# Terminal 3 — Admin panel
streamlit run admin_panel/main.py
```

---

## Testing

```bash
# All unit tests (no real DB required)
pytest

# Verbose output
pytest -v

# Single file
pytest tests/test_matching_service.py

# Integration tests (requires MONGO_TEST_URI)
pytest -m integration
```

Expected result: **162 passed, 6 skipped** (integration tests skipped when `MONGO_TEST_URI` is not set).

---

## Documentation

| Doc | Description |
|-----|-------------|
| [Architecture](docs/ARCHITECTURE.md) | System design, data flow, component overview |
| [Logic Flow](docs/DOCUMENTATION_FLOW.md) | Lead lifecycle, AI phases, scheduling |
| [API Docs](docs/API_DOCS.md) | Webhook and health endpoints |
| [Testing Guide](docs/TESTING.md) | Test structure, mocking strategy, writing tests |
| [Operations Guide](docs/OPERATIONS_GUIDE.md) | Running, monitoring, troubleshooting |
| [Scripts](docs/SCRIPTS.md) | Database and simulation scripts |
| [Railway Setup](docs/RAILWAY_SETUP.md) | Cloud deployment guide |
| [Production Readiness](docs/PRODUCTION_READINESS.md) | Pre-launch checklist |
| [Scaling Guide](docs/SCALING_GUIDE.md) | Horizontal scaling strategies |

---

<a name="hebrew"></a>

<div dir="rtl">

## סקירה בעברית

**Proli** היא פלטפורמת CRM ואוטומציה מבוססת AI לבעלי מקצוע ישראלים (אינסטלטורים, חשמלאים, טכנאים). המערכת משלבת בוט וואטסאפ חכם לשיחה עם לקוחות ופאנל ניהול מלא.

### פיצ'רים מרכזיים

#### בוט וואטסאפ

- **ניתוב גיאוגרפי פרוגרסיבי** — מחפש איש מקצוע ברדיוס 10 ק"מ, ומרחיב ל-20 ו-30 ק"מ בהתאם לצורך.
- **אישור איש מקצוע** — כל עסקה מחייבת אישור מפורש של איש המקצוע דרך כפתורי וואטסאפ (אשר/השהה/דחה).
- **מעבר אנושי** — לקוח או איש מקצוע יכולים להשהות את הבוט לשיחה ישירה (פג תוקף אוטומטי לאחר שעתיים).
- **AI מולטי-מודאלי** — ניתוח תמונות, תמלול הודעות קוליות, צפייה בסרטונים.
- **SOS Healer** — ניתוב מחדש אוטומטי של לידים תקועים, ואסקלציה למנהל אם לא נמצא מחליף.

#### פאנל ניהול

- דשבורד בזמן אמת, ניהול לידים ואנשי מקצוע, ניהול יומן, אנליטיקה, הרשאות RBAC.

</div>
