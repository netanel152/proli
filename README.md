# Proli — AI Automation for Service Professionals

**[English](#english) | [עברית](#hebrew)**

---

<a name="english"></a>

**Proli** is an AI-powered WhatsApp CRM and scheduling platform for Israeli service professionals (plumbers, electricians, technicians). It combines a multimodal WhatsApp bot for customer interactions with a full-featured admin panel for business management.

## Core Features

### WhatsApp Bot (Customer-facing)

- **Bulletproof Text-Based Menus** — All interactions use simple numeric (e.g., "1", "2") or keyword (e.g., "אשר", "דחה") replies. No reliance on fragile interactive buttons.
- **Dynamic Pro Dashboard** — Professionals receive a real-time status overview (rating, active jobs, availability) when they access the menu or send unknown commands.
- **Availability & Vacation Mode** — Pros can toggle their status ("זמין" / "הפסקה") to control lead flow directly from WhatsApp.
- **Multi-Job Finish** — Pros with multiple active bookings are guided through a selection menu to close the correct job.
- **Zero-Touch Intent Detection** — AI automatically detects if a registered professional needs a service for their own home and seamlessly toggles them into `CUSTOMER_MODE` without manual switching.
- **Emergency Express Lane** — Urgent issues (leaks, electrical fires) bypass strict address requirements if a city is identified, speeding up help when it matters most.
- **Loyalty Matchmaking** — Returning customers are automatically offered their previous successful professional before new matching begins.
- **Self-Service Rescheduling** — Customers with confirmed bookings can reschedule by picking from the professional's live available slots directly in WhatsApp.
- **Progressive Geo Routing** — Finds the best pro within 10 km, expanding to 20 km then 30 km if needed. Falls back to city-name matching for non-geo queries.
- **Rating + Load Balancing** — Prioritizes highest-rated pros; skips any pro carrying 3 or more active leads.
- **Pro Approval Flow** — Every deal requires explicit pro consent via text reply. Customers wait in a soft-hold state (`AWAITING_PRO_APPROVAL`) while the pro reviews and approves, pauses, or rejects.
- **Transparent Approval** — Customers see the name of the professional being contacted, reducing uncertainty during the wait.
- **Live Handoff (SOS)** — Customer can request a human representative. The bot pauses for a **15-Minute Dynamic Idle Timeout** that resets on every message.
- **SLA Deflection** — If a Pro remains silent for 15 minutes during a handoff, the bot proactively "wakes up" to offer the customer a direct phone call escalation.
- **Multimodal AI** — Analyzes photos, transcribes voice notes, and watches video clips. Mandatory media collection step before estimates.
- **Dynamic Pro Personas** — The AI adopts the assigned pro's pricing, tone, and custom rules during the booking conversation.
- **SOS Auto-Recovery (Healer)** — Detects leads stuck > 60 min and reassigns to a new pro. If no replacement is found, escalates to `PENDING_ADMIN_REVIEW` and notifies the customer.
- **Stale Monitor** — Follows up with pros (4–6 h) and customers (6–24 h) on open leads.
- **Pro Onboarding** — Self-service WhatsApp signup flow for new professionals.
- **Proactive Stuck-Lead Search (`מצא`)** — Pros can pull the oldest `PENDING_ADMIN_REVIEW` lead on demand. Redis-backed per-pro cool-down (10 min) prevents spamming.
- **Admin Routing Wizard (`ניהול`)** — Admin phone number triggers a guided flow to list stuck leads, self-assign, or pick a replacement pro directly from WhatsApp.

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

Expected result: **216 passed, 6 skipped** (integration tests skipped when `MONGO_TEST_URI` is not set).

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

- **תפריטי טקסט חסינים** — כל האינטראקציות מבוססות על תשובות טקסט פשוטות (מספרים כמו "1", "2" או מילות מפתח כמו "אשר", "דחה"). ללא הסתמכות על כפתורי וואטסאפ שבירים.
- **דשבורד איש מקצוע דינמי** — אנשי מקצוע מקבלים תמונת מצב בזמן אמת (דירוג, עבודות פעילות, סטטוס) בכל כניסה לתפריט או שליחת הודעה לא מוכרת.
- **שליטה בזמינות (מצב חופשה)** — אפשרות למעבר בין מצב "זמין" ל"הפסקה" לשליטה בקבלת לידים חדשים ישירות מוואטסאפ.
- **סיום עבודות מרובות** — ממשק בחירה מובנה לסיום עבודה ספציפית כאשר יש לאיש המקצוע מספר עבודות פעילות במקביל.
- **זיהוי כוונות אוטומטי (Zero-Touch)** — ה-AI מזהה באופן אוטומטי אם איש מקצוע רשום זקוק לשירות עבור עצמו ומעביר אותו למצב לקוח (`CUSTOMER_MODE`) בצורה חלקה.
- **נתיב מהיר לחירום** — תקלות דחופות (פיצוץ צנרת, קצר חשמלי) עוקפות את דרישת הכתובת המלאה ומאפשרות חיבור מהיר לאיש מקצוע ברגע שהעיר זוהתה.
- **שימור לקוחות חכם** — לקוחות חוזרים מקבלים הצעה אוטומטית לחזור לאיש המקצוע שטיפל בהם בעבר בהצלחה.
- **שינוי מועד עצמאי** — לקוחות יכולים לשנות מועד לביקור קיים על ידי בחירה מתוך רשימת תורים פנויים של איש המקצוע ישירות בוואטסאפ.
- **שקיפות מלאה** — הלקוח רואה את שם איש המקצוע אליו הועברה הפנייה, מה שמפחית את חוסר הוודאות בזמן ההמתנה.
- **ניתוב גיאוגרפי פרוגרסיבי** — מחפש איש מקצוע ברדיוס 10 ק"מ, ומרחיב ל-20 ו-30 ק"מ בהתאם לצורך.
- **אישור איש מקצוע** — כל עסקה מחייבת אישור מפורש של איש המקצוע דרך תשובת טקסט (אשר/השהה/דחה).
- **מעבר אנושי (SOS)** — לקוח יכול לבקש מענה אנושי. הבוט מושהה לחלון זמן דינמי של 15 דקות שמתאפס עם כל הודעה.
- **SLA Deflection** — אם איש המקצוע לא עונה תוך 15 דקות בזמן שיחה ישירה, הבוט יתעורר ויציע ללקוח מעבר לשיחה טלפונית.
- **AI מולטי-מודאלי** — ניתוח תמונות, תמלול הודעות קוליות, צפייה בסרטונים.
- **SOS Healer** — ניתוב מחדש אוטומטי של לידים תקועים, ואסקלציה למנהל אם לא נמצא מחליף.
- **חיפוש יזום של לידים תקועים (`מצא`)** — איש מקצוע יכול למשוך את הליד התקוע הוותיק ביותר. הגבלת קצב ברדיס (10 דק' לאיש מקצוע) מונעת ספאם.
- **אשף ניתוב למנהל (`ניהול`)** — מספר הטלפון של המנהל מפעיל תהליך מובנה להצגת לידים תקועים, הקצאה עצמית או בחירת איש מקצוע חלופי ישירות מוואטסאפ.

#### פאנל ניהול

- דשבורד בזמן אמת, ניהול לידים ואנשי מקצוע, ניהול יומן, אנליטיקה, הרשאות RBAC.

</div>
