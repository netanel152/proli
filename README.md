# Fixi Backend & Admin Panel 🛠️🤖

**Fixi** is an AI-powered CRM and scheduling automation platform designed for service professionals (e.g., plumbers, electricians). It combines a WhatsApp-based AI bot for customer interaction with a powerful web-based Admin Panel for management.

## 🚀 Core Features

### 🤖 AI WhatsApp Bot
*   **Smart Routing:** Automatically routes conversations to the correct professional based on location and keywords (using Gemini AI).
*   **Natural Language Understanding:** Handles availability checks, scheduling, and job completion.
*   **Media Handling:** Processes images and voice notes using Google Gemini & Cloudinary.
*   **Automated Scheduling:** Checks real-time availability and books slots directly in MongoDB.

### 📊 Admin Panel (Streamlit)
*   **Dashboard:** Real-time metrics on leads, active professionals, and new messages.
*   **Lead Management:** View, edit, update status, and delete leads.
*   **Schedule Management:**
    *   **Daily Editor:** Granular control over specific time slots.
    *   **Bulk Generator:** Auto-generate schedules for days/weeks.
*   **Professional Management:** Add new pros with custom prompts, service areas, and pricing.
*   **System Settings:** Configure the auto-scheduler (run time, active status) and trigger manual runs.

## 🛠️ Tech Stack

*   **Language:** Python 3.12+
*   **Backend Framework:** FastAPI
*   **Admin Interface:** Streamlit
*   **Database:** MongoDB (via `pymongo`)
*   **AI Engine:** Google Gemini (Generative AI)
*   **Messaging:** WhatsApp (via Green API)
*   **Media Storage:** Cloudinary
*   **Task Scheduling:** APScheduler (AsyncIO)

## 📂 Project Structure

```text
D:\Projects\fixi-backend\
├── app/                        # FastAPI Backend Application
│   ├── core/                   # Config & Database connections
│   ├── schemas/                # Pydantic models (Webhooks)
│   ├── services/               # Core Business Logic (AI, WhatsApp)
│   ├── main.py                 # Server Entry Point
│   └── scheduler.py            # Dynamic Task Scheduler
├── admin_panel/                # Streamlit Admin Dashboard
│   ├── app.py                  # Admin Entry Point
│   ├── admin_pages.py          # UI Views (Dashboard, Schedule, etc.)
│   └── components.py           # UI Components (Chat bubbles, CSS)
├── requirements.txt            # Python Dependencies
└── .env                        # Environment Variables (Not committed)
```

## ⚙️ Setup & Installation

### 1. Prerequisites
*   Python 3.10 or higher.
*   MongoDB Atlas connection string or local MongoDB instance.
*   API Keys for: Google Gemini, Cloudinary, Green API (WhatsApp).

### 2. Installation

Clone the repository and set up the virtual environment:

```bash
# Create virtual environment
python -m venv venv

# Activate virtual environment
# Windows:
virtualenv\Scripts\activate
# Mac/Linux:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Environment Configuration

Create a `.env` file in the root directory with the following variables:

```env
# Database
MONGO_URI=mongodb+srv://<user>:<password>@cluster.mongodb.net/?retryWrites=true&w=majority

# Security
ADMIN_PASSWORD=admin123  # Password for Admin Panel

# APIs
GEMINI_API_KEY=your_gemini_key
GREEN_API_ID=your_instance_id
GREEN_API_TOKEN=your_instance_token

# Media
CLOUDINARY_CLOUD_NAME=your_cloud_name
CLOUDINARY_API_KEY=your_key
CLOUDINARY_API_SECRET=your_secret
```

## ▶️ Running the Application

You need to run two separate processes (terminals):

### 1. Start the Backend Server (FastAPI)
This handles WhatsApp webhooks and the background scheduler.

```bash
uvicorn app.main:app --reload --port 8000
```
*   Server will run at: `http://localhost:8000`
*   Health Check: `http://localhost:8000/`
*   Webhook Endpoint: `http://localhost:8000/webhook`

### 2. Start the Admin Panel (Streamlit)
This opens the visual dashboard in your browser.

```bash
streamlit run admin_panel/app.py
```
*   Dashboard will open automatically at: `http://localhost:8501`

## 🧪 Testing

*   **Scheduler:** Go to the "Settings" page in the Admin Panel to change the run time or click "Run Now" to test the daily reminders immediately.
*   **WhatsApp:** Send a message to the connected WhatsApp number to test the AI routing and response.
