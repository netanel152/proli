# Proli - Manual WhatsApp Test Plan

## Setup Requirements

| Component | Status Check |
|-----------|-------------|
| FastAPI (uvicorn) | `curl http://localhost:8000/health` |
| ARQ Worker | Worker terminal shows "APScheduler Started" |
| ngrok | Running, URL set in Green API dashboard |
| Green API | Instance authorized, phone connected |
| MongoDB | Health check shows "up" |
| Redis | Health check shows "up" |

### Test Phones

| Phone | Role | Description |
|-------|------|-------------|
| 972524828796 | Pro | נתנאל - אינסטלציה (plumber, Tel Aviv) |
| 972523651414 | Customer | Adi - test customer |

---

## Test Cases

### TC-1: Customer Consent Flow (First Contact)

**Pre-condition:** Clear customer state and consent:
```bash
redis-cli DEL "state:972523651414@c.us"
# In MongoDB: db.consent.deleteOne({chat_id: "972523651414@c.us"})
```

| Step | From Phone | Send Message | Expected Bot Response | Verify |
|------|-----------|-------------|----------------------|--------|
| 1 | 972523651414 | "שלום" | Consent request message (privacy policy) | State = AWAITING_CONSENT |
| 2 | 972523651414 | "בלה בלה" | Consent request repeated | State still AWAITING_CONSENT |
| 3 | 972523651414 | "כן" | "תודה! ספר/י לי במה אפשר לעזור?" | State = IDLE, consent saved |

**Redis check:** `redis-cli GET "state:972523651414@c.us"` → empty/idle

---

### TC-2: Customer Consent Decline

**Pre-condition:** Clear customer state and consent

| Step | From Phone | Send Message | Expected Bot Response | Verify |
|------|-----------|-------------|----------------------|--------|
| 1 | 972523651414 | "שלום" | Consent request | State = AWAITING_CONSENT |
| 2 | 972523651414 | "לא" | Decline message | State cleared |
| 3 | 972523651414 | "שלום שוב" | Consent request again (re-ask) | State = AWAITING_CONSENT |

---

### TC-3: Customer Full Lead Flow (Happy Path)

**Pre-condition:** Customer has consent, state is idle
```bash
redis-cli DEL "state:972523651414@c.us"
# Ensure consent exists in DB
```

| Step | From Phone | Send Message | Expected Bot Response | Verify |
|------|-----------|-------------|----------------------|--------|
| 1 | 972523651414 | "היי יש לי נזילה בתל אביב" | AI response about the plumbing issue (acting as pro persona) | Worker log: Dispatcher extracts city=תל אביב, issue=נזילה |
| 2 | - | - | Pro (972524828796) receives: "📢 הצעת עבודה חדשה" with address + issue | Check worker log: "Message sent to 972524828796@c.us" |
| 3 | 972524828796 | "אשר" | Pro gets: "✅ העבודה אושרה!" | Lead status = BOOKED |
| 4 | - | - | Customer (972523651414) gets: "🎉 נמצא איש מקצוע!" with pro details | Check worker log |

**DB check:** `db.leads.findOne({chat_id: "972523651414@c.us"})` → status = "booked"

---

### TC-4: Pro Rejects Lead

**Pre-condition:** TC-3 Step 2 complete (lead exists with status NEW)

| Step | From Phone | Send Message | Expected Bot Response | Verify |
|------|-----------|-------------|----------------------|--------|
| 1 | 972524828796 | "דחה" | "העבודה נדחתה" | Lead status = REJECTED |

**DB check:** Lead status = "rejected"

---

### TC-5: Pro Finishes Job

**Pre-condition:** Lead exists with status BOOKED (TC-3 completed through step 4)

| Step | From Phone | Send Message | Expected Bot Response | Verify |
|------|-----------|-------------|----------------------|--------|
| 1 | 972524828796 | "סיימתי" | "✅ עודכן שהעבודה הסתיימה" | Lead status = COMPLETED |
| 2 | - | - | Customer gets rating request (1-5) | waiting_for_rating = true |

---

### TC-6: Customer Rating + Review

**Pre-condition:** TC-5 completed (lead COMPLETED, waiting_for_rating=true)

| Step | From Phone | Send Message | Expected Bot Response | Verify |
|------|-----------|-------------|----------------------|--------|
| 1 | 972523651414 | "5" | "תודה על הדירוג! תרצה לכתוב ביקורת?" | Pro rating updated |
| 2 | 972523651414 | "שירות מעולה, מקצועי ומהיר!" | "תודה רבה! הביקורת נשמרה" | Review saved in reviews_collection |

**DB check:**
- `db.reviews.findOne({customer_chat_id: "972523651414@c.us"})` → exists
- Pro's social_proof.rating updated

---

### TC-7: Pro Help Menu

| Step | From Phone | Send Message | Expected Bot Response | Verify |
|------|-----------|-------------|----------------------|--------|
| 1 | 972524828796 | "בלה בלה" | Pro help menu with available commands | State stays PRO_MODE |

---

### TC-8: Global Reset Command

| Step | From Phone | Send Message | Expected Bot Response | Verify |
|------|-----------|-------------|----------------------|--------|
| 1 | 972523651414 | "תפריט" | "🔄 השיחה אופסה בהצלחה" | State cleared to idle |
| 2 | 972524828796 | "reset" | "🔄 השיחה אופסה בהצלחה" | State cleared (will re-detect as pro on next message) |

---

### TC-9: SOS / Human Handoff

**Pre-condition:** Customer has active lead

| Step | From Phone | Send Message | Expected Bot Response | Verify |
|------|-----------|-------------|----------------------|--------|
| 1 | 972523651414 | "נציג" | "העברתי את הפרטים..." (SOS message) | State = SOS, admin notified |
| 2 | 972523651414 | "עזרה" | Same SOS response | Works with multiple keywords |

---

### TC-10: Pro Onboarding (Self-Registration)

**Pre-condition:** Use a phone number NOT registered as pro, with consent given.
Can simulate by temporarily clearing pro record.

| Step | From Phone | Send Message | Expected Bot Response | Verify |
|------|-----------|-------------|----------------------|--------|
| 1 | (test phone) | "הרשמה" | Welcome + ask for business name | State = ONBOARDING_NAME |
| 2 | (test phone) | "בדיקה שרברב" | Ask for profession type (1-7 list) | State = ONBOARDING_TYPE |
| 3 | (test phone) | "1" | Ask for service areas | State = ONBOARDING_AREAS |
| 4 | (test phone) | "תל אביב, רמת גן" | Ask for prices | State = ONBOARDING_PRICES |
| 5 | (test phone) | "ביקור 200, תיקון 400" | Show summary, ask to confirm | State = ONBOARDING_CONFIRM |
| 6 | (test phone) | "אשר" | "🎉 הפרופיל נשלח לאישור" | Pro created with pending_approval=true |

---

### TC-11: Pro Onboarding Cancel

| Step | From Phone | Send Message | Expected Bot Response | Verify |
|------|-----------|-------------|----------------------|--------|
| 1 | (test phone) | "הרשמה" | Welcome message | State = ONBOARDING_NAME |
| 2 | (test phone) | "ביטול" | "❌ ההרשמה בוטלה" | State cleared |

---

### TC-12: Duplicate Message (Idempotency)

| Step | Action | Expected | Verify |
|------|--------|----------|--------|
| 1 | Send same WhatsApp message | Processed once | Worker shows 1 task |
| 2 | Green API sends duplicate webhook (same idMessage) | Webhook returns "duplicate" | No second task created |

---

### TC-13: Media Message (Image/Audio)

| Step | From Phone | Send Message | Expected Bot Response | Verify |
|------|-----------|-------------|----------------------|--------|
| 1 | 972523651414 | Send photo of a leak + "נזילה בתל אביב" | AI analyzes image + text, responds | Worker log: media_url present |
| 2 | 972523651414 | Send voice message describing issue | AI transcribes + responds | Worker log: transcription in response |

---

## Quick DB Verification Commands

```javascript
// MongoDB shell (mongosh)
use proli_db

// Check lead status
db.leads.find({chat_id: "972523651414@c.us"}).sort({created_at: -1}).limit(1).pretty()

// Check user/pro record
db.users.findOne({phone_number: "972524828796"})

// Check consent
db.consent.findOne({chat_id: "972523651414@c.us"})

// Check reviews
db.reviews.find({customer_chat_id: "972523651414@c.us"}).pretty()

// Count active leads per pro
db.leads.aggregate([
  {$match: {status: {$in: ["new", "contacted", "booked"]}}},
  {$group: {_id: "$pro_id", count: {$sum: 1}}}
])
```

## Quick Redis Verification Commands

```bash
# Check user state
redis-cli GET "state:972523651414@c.us"
redis-cli GET "state:972524828796@c.us"

# Clear state for fresh test
redis-cli DEL "state:972523651414@c.us"

# Check all states
redis-cli KEYS "state:*"
```

## Reset Everything for Fresh Test Run

```bash
# 1. Clear Redis states
redis-cli FLUSHDB

# 2. Clear MongoDB test data
mongosh proli_db --eval "
  db.leads.deleteMany({});
  db.consent.deleteMany({});
  db.reviews.deleteMany({});
  db.messages.deleteMany({});
"

# 3. Re-seed consent for test phones
python -c "
import asyncio
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorClient
async def seed():
    client = AsyncIOMotorClient('mongodb://localhost:27017/proli_db')
    db = client['proli_db']
    await db.consent.insert_many([
        {'chat_id': '972524828796@c.us', 'accepted': True, 'timestamp': datetime.now(timezone.utc)},
        {'chat_id': '972523651414@c.us', 'accepted': True, 'timestamp': datetime.now(timezone.utc)},
    ])
    client.close()
asyncio.run(seed())
"

# 4. Set pro state
redis-cli SET "state:972524828796@c.us" "pro_mode"
```

## Test Execution Order (Recommended)

Run these in order for a full system validation:

1. **TC-1** → Consent flow works
2. **TC-3** → Full happy path (lead creation + pro matching + approval)
3. **TC-5** → Pro finishes job
4. **TC-6** → Rating + review
5. **TC-8** → Reset works
6. **TC-4** → Pro rejection works
7. **TC-7** → Pro help menu
8. **TC-9** → SOS works
9. **TC-12** → Idempotency
10. **TC-13** → Media handling

After each test, verify in worker logs and database that the expected state changes occurred.
