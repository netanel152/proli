# Fixi - AI Automation for Professionals 🛠️🤖

**[English](#english) | [עברית](#hebrew)**

---

<a name="english"></a>

**Fixi** is a smart CRM and scheduling automation platform designed for service professionals (plumbers, electricians, technicians). It seamlessly combines a **Multimodal WhatsApp AI Bot** for customer interaction with a robust **Admin Panel** for business management.

### 🚀 Core Features

#### 🤖 Intelligent WhatsApp Bot (User Facing)

- **Smart Routing Engine:** Automatically routes leads to the *best* professional based on:
  - **Location:** Matches user city with Pro service areas.
  - **Rating:** Prioritizes top-rated professionals.
  - **Load Balancing:** Distributes work to avoid overloading busy pros.
- **Multimodal AI:**
  - **Vision:** Analyzes photos of issues (e.g., a leaking pipe) to understand the problem.
  - **Video:** Watches clips to identify dynamic issues (e.g., flickering lights, strange noises).
  - **Audio:** Transcribes and interprets voice notes in real-time.
- **Dynamic Personas:** The bot adopts the specific pricing, tone, and rules of the assigned professional.
- **Availability Management:** Checks real-time calendar availability in MongoDB and books appointments autonomously.
- **Stale Job Monitor:** Automatically detects "stuck" leads (no completion after 6-24h) and follows up with the pro or customer.

#### 📊 Admin Panel (Manager Facing)

- **Live Dashboard:** Real-time metrics on leads, active professionals, and revenue.
- **Lead Management:** Full CRUD capabilities (Create, Read, Update, Delete) with standardized fields.
- **Smart Schedule:** Daily Editor and Bulk Generator for managing availability.
- **Professional Profiles:** Manage system prompts, pricing, and service areas for each pro.

### 🛠️ Tech Stack

- **Backend:** Python 3.12+, FastAPI, HTTPX
- **Frontend (Admin):** Streamlit
- **AI Engine:** Google Gemini Adaptive (Flash Lite 2.5 → Flash 2.5 → Flash 1.5 Fallback)
- **Database:** MongoDB Atlas (Async via `motor`)
- **Messaging:** WhatsApp (via Green API)
- **Security:** Bcrypt (Admin Auth)
- **Deployment:** Docker / Heroku ready

---

<a name="hebrew"></a>

<div dir="rtl">

**Fixi** היא פלטפורמת אוטומציה וניהול יומן חכמה המיועדת לבעלי מקצוע (אינסטלטורים, חשמלאים, טכנאים). המערכת משלבת בוט וואטסאפ מולטי-מודאלי חכם לשיחה עם לקוחות יחד עם פאנל ניהול מתקדם.

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

#### 📊 פאנל ניהול (מול המנהל)

- **דשבורד בזמן אמת:** צפייה בלידים, סטטוסים ומדדים.
- **ניהול לידים:** יצירה, עריכה ומחיקה של לידים עם שדות אחידים.
- **ניהול יומן:** עורך יומי ומחולל אוטומטי.
- **פרופילים:** הגדרת הנחיות AI ומחירונים לכל איש מקצוע.

</div>

---

## ⚙️ Installation & Setup

1.  **Clone & Environment:**

    ```bash
    git clone <url>
    cd fixi-backend
    python -m venv venv
    source venv/bin/activate  # Windows: venv\Scripts\activate
    pip install -r requirements.txt
    ```

2.  **Configuration (`.env`):**
    Create a `.env` file with the following:

    ```env
    MONGO_URI=mongodb+srv://...
    GEMINI_API_KEY=...
    GREEN_API_ID=...
    GREEN_API_TOKEN=...
    ADMIN_PASSWORD=admin123
    ```

3.  **Seed Database:**
    ```bash
    python scripts/seed_db.py
    ```

## ▶️ Running the App

### Option A: Docker (Recommended)
This will spin up both the Backend and Admin Panel in isolated containers.

```bash
docker-compose up --build -d
```
*   **Backend:** http://localhost:8000
*   **Admin Panel:** http://localhost:8501

### Option B: Local Development

**1. Backend Server (FastAPI):**
```bash
uvicorn app.main:app --reload --port 8000
```

**2. Admin Panel (Streamlit):**
```bash
streamlit run admin_panel/app.py
```