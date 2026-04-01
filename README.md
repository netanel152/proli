# Proli - AI Automation for Professionals 🛠️🤖

**[English](#english) | [עברית](#hebrew)**

---

<a name="english"></a>

**Proli** is a smart CRM and scheduling automation platform designed for service professionals (plumbers, electricians, technicians). It seamlessly combines a **Multimodal WhatsApp AI Bot** for customer interaction with a robust **Admin Panel** for business management.

### 🚀 Core Features

#### 🤖 Intelligent WhatsApp Bot (User Facing)

- **Smart Routing Engine:** Automatically routes leads to the _best_ professional based on:
  - **Location:** Matches user city with Pro service areas.
  - **Rating:** Prioritizes top-rated professionals.
  - **Load Balancing:** Distributes work to avoid overloading busy pros.
- **Multimodal AI:**
  - **Vision:** Analyzes photos of issues (e.g., a leaking pipe) to understand the problem.
  - **Video:** Watches clips to identify dynamic issues (e.g., flickering lights, strange noises).
  - **Audio:** Transcribes and interprets voice notes in real-time.
- **Interactive UI:** Uses native WhatsApp buttons for seamless confirmations and quick actions.
- **Dynamic Personas:** The bot adopts the specific pricing, tone, and rules of the assigned professional.
- **Availability Management:** Checks real-time calendar availability in MongoDB and books appointments autonomously using atomic locks.
- **Pro Onboarding:** Self-service onboarding flow for new professionals via WhatsApp.
- **Stale Job Monitor:** Automatically detects "stuck" leads (no completion after 4-24h) and follows up with the pro or customer.
- **SOS Auto-Recovery (Healer):** Automatically reassigns leads to a new professional if the current one doesn't respond within the timeout.
- **SOS Admin Reporter:** Sends a batched summary of stuck leads to the administrator every 4 hours if reassignment fails.

#### 📊 Admin Panel (Manager Facing)

- **Bilingual Interface:** Full support for Hebrew and English via a language toggle.
- **Live Dashboard:** Real-time metrics on leads, active professionals, and revenue.
- **Role-Based Access Control (RBAC):** Owner, Editor, and Viewer roles with comprehensive audit logging.
- **Analytics:** Visual charts for lead funnels, daily volume, and professional performance.
- **Lead Management:** Full CRUD capabilities with inline editing.
- **Smart Schedule:** Daily Editor and Bulk Generator for managing recurring availability.
- **Data Management:** Privacy compliance tools for user data export and deletion.

### 🛠️ Tech Stack

- **Backend:** Python 3.12+, FastAPI, HTTPX, ARQ (Redis Task Queue)
- **Frontend (Admin):** Streamlit
- **AI Engine:** Google Gemini Adaptive (Flash Lite 2.5 → Flash 2.5 → Flash 1.5 Fallback)
- **Database:** MongoDB Atlas (Async via `motor`), Redis (Context & State)
- **Media & Comms:** Cloudinary (Media), Green API (WhatsApp), InforUMobile (SMS Fallback)
- **Security:** Bcrypt (Admin Auth), Webhook Token Verification, Session-based Cookies
- **Testing:** Pytest (AsyncIO & Mocking), E2E Webhook Simulation, Automated Reset & Seeding Scripts
- **Deployment:** Docker Compose / Railway (multi-service)

---

<a name="hebrew"></a>

<div dir="rtl">

**Proli** היא פלטפורמת אוטומציה וניהול יומן חכמה המיועדת לבעלי מקצוע (אינסטלטורים, חשמלאים, טכנאים). המערכת משלבת בוט וואטסאפ מולטי-מודאלי חכם לשיחה עם לקוחות יחד עם פאנל ניהול מתקדם.

### 🚀 פיצ'רים מרכזיים

#### 🤖 בוט וואטסאפ חכם (מול הלקוח)

- **מנוע ניתוב חכם:** מנתב לידים לאיש המקצוע המתאים ביותר לפי:
  - **מיקום:** התאמת עיר הלקוח לאזורי השירות.
  - **דירוג:** עדיפות לבעלי מקצוע עם דירוג גבוה.
  - **עומס:** איזון עבודה למניעת עומס יתר.
- **בינה מלאכותית מולטי-מודאלית:**
  - **ראייה:** מנתח תמונות של תקלות (למשל נזילה) כדי להבין את הבעיה.
  - **וידאו:** צופה בסרטונים כדי לזהות תקלות דינמיות (רעשים, הבהובים).
  - **שמיעה:** מתמלל ומבין הודעות קוליות בזמן אמת.
- **פרסונות דינמיות:** הבוט מאמץ את המחירון, הסגנון והחוקים של איש המקצוע הנבחר.
- **ניהול יומן:** בדיקת זמינות וקביעת תורים אוטומטית.
- **הרשמת אנשי מקצוע:** תהליך הצטרפות עצמאי דרך וואטסאפ.

#### 📊 פאנל ניהול (מול המנהל)

- **ממשק דו-לשוני:** תמיכה מלאה בעברית ובאנגלית.
- **דשבורד בזמן אמת:** צפייה בלידים, סטטוסים ומדדים.
- **הרשאות וניהול אדמינים (RBAC):** צופה, עורך ומנהל מערכת עם לוג פעולות (Audit).
- **אנליטיקה:** גרפים המציגים ביצועים, משפכי המרה ונפח פעילות.
- **ניהול לידים:** יצירה, עריכה ומחיקה של לידים עם שדות אחידים.
- **ניהול יומן:** עורך יומי ומחולל אוטומטי.
- **פרופילים:** הגדרת הנחיות AI ומחירונים לכל איש מקצוע.

</div>

---

## ⚙️ Installation & Setup

1.  **Clone & Environment:**

    ```bash
    git clone <url>
    cd proli-backend
    python -m venv venv
    source venv/bin/activate  # Windows: venv\Scripts\activate
    pip install -r requirements.txt
    ```

2.  **Configuration (`.env`):**
    Create a `.env` file with the following:

    ```env
    MONGO_URI=mongodb+srv://...
    MONGO_TEST_URI=mongodb+srv://... (Optional, for running tests)
    GEMINI_API_KEY=...
    GREEN_API_INSTANCE_ID=...
    GREEN_API_TOKEN=...
    CLOUDINARY_CLOUD_NAME=...
    CLOUDINARY_API_KEY=...
    CLOUDINARY_API_SECRET=...
    ADMIN_PASSWORD_HASH=...  # Generate with: python scripts/generate_admin_hash.py
    ADMIN_PHONE=972501234567  # Admin WhatsApp number for SOS alerts
    WEBHOOK_TOKEN=...  # Optional: enables webhook auth (?token=<value>)
    ```

3.  **Seed Database:**
    ```bash
    python scripts/seed_db.py
    ```

## ▶️ Running the App

### Option A: Docker (Recommended)

This will spin up the Backend, Worker, and Admin Panel in isolated containers.

```bash
docker-compose up --build -d
```

- **Backend:** http://localhost:8000
- **Admin Panel:** http://localhost:8501
- **Worker:** Runs in background (logs via `docker-compose logs -f worker`)

### Option B: Local Development

**1. Backend Server (FastAPI):**

```bash
uvicorn app.main:app --reload --port 8000
```

**2. Worker (Background Jobs):**

```bash
python -m app.worker
```

**3. Admin Panel (Streamlit):**

```bash
streamlit run admin_panel/main.py
```

## 📚 Documentation

For more detailed information, please refer to the `docs/` folder:

- **[API Documentation](docs/API_DOCS.md)**: API Endpoints and Webhook structure.
- **[Logic Flow](docs/DOCUMENTATION_FLOW.md)**: Detailed explanation of the lead lifecycle and AI decision making.
- **[Operations Guide](docs/OPERATIONS_GUIDE.md)**: Manual for running, monitoring, and troubleshooting the system.
- **[Gemini Context](docs/GEMINI.md)**: Technical architecture and AI context for developers.
- **[Scaling Guide](docs/SCALING_GUIDE.md)**: Strategies for scaling the application.
- **[Architecture](docs/ARCHITECTURE.md)**: System architecture and component interactions.
- **[Railway Setup](docs/RAILWAY_SETUP.md)**: Multi-service Railway deployment guide.
- **[Testing Guide](docs/TESTING.md)**: Test suite structure, mocking strategy, and how to run tests.
- **[Production Readiness](docs/PRODUCTION_READINESS.md)**: Checklist for going to production.
- **[Analysis Report](docs/ANALYSIS_REPORT.md)**: March 2026 code review — all 19 issues resolved.
