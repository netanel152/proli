# Fixi - AI Automation for Professionals 🛠️🤖

**[English](#english) | [עברית](#hebrew)**

---

<a name="english"></a>

**Fixi** is a smart CRM and scheduling automation platform designed for service professionals (plumbers, electricians, technicians). It seamlessly combines a **WhatsApp AI Bot** for customer interaction with a robust **Admin Panel** for business management.

### 🚀 Core Features

#### 🤖 AI WhatsApp Bot (User Facing)

- **Smart Routing:** Automatically routes conversations to the correct professional based on the user's location and keywords (powered by Google Gemini).
- **Availability Management:** Checks real-time calendar availability in MongoDB and books appointments autonomously.
- **Media Analysis:** Processes images and voice notes (e.g., a picture of a leak) using Gemini Vision & Audio capabilities.
- **Natural Conversation:** Handles inquiries, scheduling, and job completion commands naturally.

#### 📊 Admin Panel (Manager Facing)

- **Live Dashboard:** Real-time metrics on leads, active professionals, and revenue.
- **Lead Management:** Full CRUD capabilities for leads (status tracking, editing details).
- **Smart Schedule:**
  - **Daily Editor:** Granular control over specific time slots.
  - **Bulk Generator:** Auto-generate schedules for days/weeks with one click.
- **Professional Profiles:** Manage system prompts, pricing, service areas, and license details for each pro.

### 🛠️ Tech Stack

- **Backend:** Python 3.12+, FastAPI
- **Frontend (Admin):** Streamlit
- **AI Engine:** Google Gemini (via new `google-genai` SDK)
- **Database:** MongoDB Atlas (Async via `motor`)
- **Messaging:** WhatsApp (via Green API)
- **Media Storage:** Cloudinary
- **Deployment:** Docker / Heroku ready (Procfile included)

---

<a name="hebrew"></a>

<div dir="rtl">

**Fixi** היא פלטפורמת אוטומציה וניהול יומן חכמה המיועדת לבעלי מקצוע (אינסטלטורים, חשמלאים, טכנאים). המערכת משלבת בוט וואטסאפ חכם לשיחה עם לקוחות יחד עם פאנל ניהול מתקדם לבעל העסק.

### 🚀 פיצ'רים מרכזיים

#### 🤖 בוט וואטסאפ חכם (מול הלקוח)

- **ניתוב חכם:** מזהה אוטומטית את מיקום הלקוח וסוג התקלה ומעביר לבעל המקצוע המתאים (מבוסס Gemini AI).
- **ניהול יומן:** בודק זמינות בזמן אמת בבסיס הנתונים וקובע תורים באופן עצמאי מול הלקוח.
- **ניתוח מדיה:** יודע "לראות" תמונות (למשל נזילה) ו"לשמוע" הודעות קוליות כדי להבין את הבעיה.
- **שיחה טבעית:** מתנהל כמו עוזר אישי אנושי, מנומס ומקצועי.

#### 📊 פאנל ניהול (מול המנהל)

- **דשבורד בזמן אמת:** צפייה בלידים חדשים, סטטוס טיפול וגרפים.
- **ניהול לידים:** עריכה, עדכון סטטוסים ומעקב אחר פניות.
- **ניהול יומן:**
  - **עורך יומי:** שליטה מלאה על כל שעה ביום.
  - **מחולל אוטומטי:** יצירת יומן עבודה לשבוע שלם בלחיצת כפתור.
- **פרופילים:** הגדרת מחירים, אזורי שירות והנחיות מיוחדות לכל בעל מקצוע.

</div>

---

## 📂 Project Structure

```text
fixi-backend/
├── app/                        # FastAPI Backend Application
│   ├── core/                   # Config & Database connections
│   ├── services/               # Core Business Logic (AI, WhatsApp)
│   ├── main.py                 # Server Entry Point (Webhook)
│   └── scheduler.py            # Daily Reminders Task
├── admin_panel/                # Streamlit Admin Dashboard
│   ├── app.py                  # Entry Point
│   ├── page_views/             # UI Views
│   └── auth.py                 # Authentication Logic
├── scripts/                    # Utility Scripts
│   ├── seed_db.py              # Reset & Populate DB with Test Data
│   └── test_connection.py      # Verify API and DB connections
├── tests/                      # Automated Tests (Pytest)
├── GEMINI.md                   # AI Agent Instruction Context
├── Procfile                    # Heroku Deployment Config
└── requirements.txt            # Dependencies
```

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
    CLOUDINARY_CLOUD_NAME=...
    CLOUDINARY_API_KEY=...
    CLOUDINARY_API_SECRET=...
    ADMIN_PASSWORD=admin123
    ```

3.  **Seed Database (Optional):**
    Populate the system with dummy pros (Yossi, Moshe) and slots.
    ```bash
    python scripts/seed_db.py
    ```

## ▶️ Running the App

You need to run **two** separate terminals:

**1. Backend Server (FastAPI):**

```bash
uvicorn app.main:app --reload --port 8000
```

- Listens for WhatsApp Webhooks at `/webhook`.

**2. Admin Panel (Streamlit):**

```bash
streamlit run admin_panel/app.py
```

- Opens the UI in your browser (usually `http://localhost:8501`).

## 🧪 Testing

Run the full automated test suite to verify routing, booking, and logic:

```bash
pytest tests/test_full_flow.py
```

---

## 🤖 AI Context

This project includes a `GEMINI.md` file designed to provide immediate context for AI assistants (like Gemini, Copilot, or Cursor). It contains architectural insights, key commands, and development conventions.