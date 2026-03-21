# Railway Multi-Service Setup

Proli requires 3 separate Railway services sharing the same repo, MongoDB, and Redis.

## Step 1: Create a Railway Project

Create a new project in Railway. You will add 3 services to it.

## Step 2: Add Shared Infrastructure

Add these plugins/add-ons to the project:
- **Redis** (Railway Redis plugin) - used for ARQ task queue, state, context cache
- **MongoDB** - use MongoDB Atlas (external) and set `MONGO_URI` as a shared variable

## Step 3: Create 3 Services

All 3 services point to the **same GitHub repo** and use the **same Dockerfile**. They differ only in the start command.

### Service 1: API (Backend)
- **Name:** `api`
- **Start Command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- **Port:** Automatically detected by Railway (`$PORT`)
- **Public Domain:** Yes (this is your webhook URL)

### Service 2: Worker
- **Name:** `worker`
- **Start Command:** `python -m app.worker`
- **Port:** None (no HTTP traffic)
- **Public Domain:** No

### Service 3: Admin Panel
- **Name:** `admin`
- **Start Command:** `streamlit run admin_panel/main.py --server.port $PORT --server.address 0.0.0.0`
- **Port:** Automatically detected
- **Public Domain:** Yes (admin dashboard URL)

## Step 4: Environment Variables

Set these as **shared variables** (project-level) so all 3 services inherit them:

```
MONGO_URI=mongodb+srv://...
REDIS_URL=redis://...  (auto-set if using Railway Redis plugin)
GREEN_API_INSTANCE_ID=...
GREEN_API_TOKEN=...
GEMINI_API_KEY=...
CLOUDINARY_CLOUD_NAME=...
CLOUDINARY_API_KEY=...
CLOUDINARY_API_SECRET=...
ADMIN_PASSWORD_HASH=...  # Generate with: python scripts/generate_admin_hash.py
ADMIN_PHONE=972501234567  # Admin WhatsApp number for SOS alerts
WEBHOOK_TOKEN=...  # Random string for webhook auth
ENVIRONMENT=production
```

## Step 5: Configure Green API Webhook

Set your Green API webhook URL to the **API service** public domain, including the webhook token:
```
https://api-production-XXXX.up.railway.app/webhook?token=YOUR_WEBHOOK_TOKEN
```

## Notes

- The `start.sh` script is kept for local convenience but is NOT used by Railway or Docker Compose.
- Each service builds from the same Dockerfile. Railway caches the build layer so only the first service triggers a full build.
- To scale: only the API and Worker services can safely have `numReplicas > 1`. The Worker requires distributed locking for scheduler jobs before scaling (see `docs/SCALING_GUIDE.md`).
