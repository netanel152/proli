# Proli - Manual WhatsApp Test Plan

> ⚠️ **PRO-86 update:** the legacy WhatsApp vendor is gone entirely (PRO-85 — instance deleted, tariff
> cancelled) and outbound now goes through the provider facade (`app/providers/whatsapp/`).
> `CloudAPIProvider` (PRO-89) is code-complete against the Meta Graph API, but there is
> still **no live transmitting provider** in this deployment — `WHATSAPP_PROVIDER` defaults
> to `dryrun` (never transmits), and selecting `cloud` for real requires Meta credentials
> that don't exist yet because PRO-87 (Business Portfolio, template approval, a sandbox
> number) hasn't landed. The real-send steps in this plan are **not currently executable**;
> the legacy-vendor references below describe the pre-PRO-86 setup and are kept as a template
> for whichever real provider goes live.

## Setup Requirements

| Component | Status Check |
|-----------|-------------|
| FastAPI (uvicorn) | `curl http://localhost:8000/health` |
| ARQ Worker | Worker terminal shows "APScheduler Started" |
| ngrok | Running, URL set in the provider's webhook config |
| WhatsApp provider | Account authorized, phone connected |
| MongoDB | Health check shows "up" |
| Redis | Health check shows "up" |

### Test Phones

| Phone | Role | Description |
|-------|------|-------------|
| 972524828796 | Pro | נתנאל - אינסטלציה (plumber, Tel Aviv) |
| 972523651414 | Customer | Adi - test customer |

> ⚠️ **These are REAL WhatsApp numbers, not virtual test doubles.** Every step
> in this manual plan sends a **genuine outbound WhatsApp message** to the
> numbers above via a real running backend + worker. (The automated scripts
> that used to hit these same numbers, `tests/simulate_test.py` and
> `tests/smoke_test_railway.py`, were deleted in PRO-83 — equivalent coverage
> now runs offline with zero real sends via `pytest tests/e2e`; see
> `docs/TESTING.md` §6.)

### QA number & instance policy (PRO-72)

Sending real test bursts from a cold/production WhatsApp number is the #1
WhatsApp yellowCard trigger. To keep manual testing safe:

- **Use a dedicated QA SIM/instance** for all test runs — never the production
  instance that serves live customers.
- Keep the recipient numbers (`972523651414`, `972524828796`) on SIMs you
  control and have warmed; they receive real messages on every run.

> **No automatic guard here:** the `--instance-id` production check described
> in earlier versions of this doc belonged to the deleted scripts above. The
> guard itself (`tests/qa_safety.py`) was vestigial — it read an instance-id
> env var that died with the old vendor (PRO-85/86) — and was deleted, along
> with its 18 tests, in the 2026-08 test-suite cleanup. PRO-29, which was
> going to supply a separate staging instance to gate future live-fire
> automation on, is cancelled; that automation is blocked on PRO-87 (Meta
> onboarding) instead.
> This manual plan has no equivalent check — confirm by hand that you're
> pointed at a QA/non-production number before starting.

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
| 1 | 972524828796 | "דחה" | "העבודה נדחתה. הפנייה הועברה לאיש מקצוע אחר." | Lead reassigned to the next pro |

**DB check:** `status_history` shows `rejected` (actor=pro) then `new` (actor=system); lead status = "new" under the new pro (or "pending_admin_review" if no replacement was found — customer is messaged either way)

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

### TC-7: Pro Dynamic Dashboard (Help Menu)

| Step | From Phone | Send Message | Expected Bot Response | Verify |
|------|-----------|-------------|----------------------|--------|
| 1 | 972524828796 | "תפריט" | Pro Dashboard: rating, availability, active job count, and command list | State stays PRO_MODE |
| 2 | 972524828796 | "מה קורה?" | Same Dashboard (fallback for unknown text) | |

---

### TC-8: Global Reset Command

Reset keywords are `"reset"` and `"התחלה"`. The `"תפריט"` keyword sends the help/info message for customers — it does **not** reset state.

| Step | From Phone | Send Message | Expected Bot Response | Verify |
|------|-----------|-------------|----------------------|--------|
| 1 | 972523651414 | "reset" | *(nothing — the reset is deliberately silent)* | State cleared to idle |
| 2 | 972524828796 | "reset" | *(nothing — the reset is deliberately silent)* | State cleared (will re-detect as pro on next message) |

---

### TC-9: SOS / Human Handoff

**Pre-condition:** Customer has active lead

SOS keywords: `"נציג"`, `"לנציג"`, `"הנציג"`, `"נציגה"`, `"אנושי"`, `"מנהל"`, `"למנהל"`, `"מנהלת"`, `"למנהלת"`, `"admin"`, `"sos"` — matched as whole words, so "מנהל עבודה" (foreman) does not trigger SOS (PRO-118). Note: `"עזרה"` is a help keyword — it sends an info message, not an SOS alert.

| Step | From Phone | Send Message | Expected Bot Response | Verify |
|------|-----------|-------------|----------------------|--------|
| 1 | 972523651414 | "נציג" | "העברתי את הפרטים..." (SOS message) | State = SOS, admin notified |
| 2 | 972523651414 | "אנושי" | Same SOS response | Works with multiple keywords |

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
| 2 | Provider sends duplicate webhook (same idMessage) | Webhook returns "duplicate" | No second task created |

---

### TC-13: Media Message (Image/Audio)

| Step | From Phone | Send Message | Expected Bot Response | Verify |
|------|-----------|-------------|----------------------|--------|
| 1 | 972523651414 | Send photo of a leak + "נזילה בתל אביב" | AI analyzes image + text, responds | Worker log: media_url present |
| 2 | 972523651414 | Send voice message describing issue | AI transcribes + responds | Worker log: transcription in response |

---

## Routing Coverage Matrix — TC-19..TC-28 (PRO-84)

These ten cases exercise **every branch** of `matching_service.determine_best_pro`.
They require the staging coverage-matrix seed:

```bash
python scripts/seed_coverage_matrix.py
```

Staging only — the script refuses to run against any other environment or database, or
if the configured WhatsApp provider can transmit (PRO-86), and it refuses to run at all
while any *untagged* professional is present (`seed_db.py`'s pro sits on the Tel Aviv
coordinate at 4.9 with slots and would silently win TC-20). Purge those first, or use a
database dedicated to the matrix.

All 27 seeded professionals use the reserved `972000000100`–`972000000199` block.
`972` followed by `0` is not a valid Israeli MSISDN — the digit after the country
code is the national number with the trunk `0` stripped — so no number in the block
can reach a subscriber. Remove them with `--purge` (deletes only
`seed_batch: "coverage_v1"`).

**Send each message from the customer test phone and read the pro's name off the
`🎉 נמצא לך איש מקצוע` reply, or off the lead's `pro_id` in Mongo.**

| TC | Scenario | Send as location | Expected professional | What it proves |
|----|----------|------------------|----------------------|----------------|
| **TC-19** | S01 · rating sort | `תל אביב` | `[S01] אינסטלציה תל אביב 01` (4.8) | Rating sort inside a dense cluster — **and** that the +10 availability bonus outweighs rating, since `[FILL] חשמל חולון 01` is rated 5.0, is 7.8 km away, and must still lose because it has no slots |
| **TC-20** | S02 · load balancing | `תל אביב`, after `--scenario load-balance` | `[S01] מנעולן תל אביב 04` (4.5) | The three best are at `MAX_PRO_LOAD` and are skipped |
| **TC-21** | S03 · everyone overloaded | `מודיעין`, after `--scenario overload-shfela` | *none* → `PENDING_ADMIN_REVIEW` | Routing stops at the first radius that returns documents; it does **not** widen again once the load filter empties the list |
| **TC-22** | S04 · expand to 20 km | `מודיעין` | `[S04] אינסטלציה לוד 01` (4.8) | Nothing within 10 km → `GEO_RADIUS_STEPS[1]` answers |
| **TC-23** | S05 · expand to 30 km | `נתניה` | `[S01] אינסטלציה תל אביב 01` | Steps 1 and 2 empty → `GEO_RADIUS_STEPS[2]` catches the TA cluster |
| **TC-24** | S06 · coverage gap | `חדרה` | *none* → `PENDING_ADMIN_REVIEW` | Nobody inside 30 km. Check the log says **warning**, not critical (PRO-77) |
| **TC-25** | S07 · geocoding | `ראש העין` | `[S07] אינסטלציה פתח תקווה 01` | The city is absent from `ISRAEL_CITIES_COORDS`, so Google + the Redis cache must answer. Reproduces the 2026-04-18 post-mortem. **Needs a working `GOOGLE_MAPS_API_KEY` (PRO-19)** |
| **TC-26** | S08 · text fallback | `מתחם בדיקות פרולי` | `[S08] אינסטלציה ניידת 01` | An unresolvable locality drops routing into the `service_areas` regex |
| **TC-27** | S09 · reverse match | `רחוב הבדים 4, מרחב דן בדיקות` | `[S09] צבע אזורי 01` | The full string matches no regex, so only step 3's `area in location` can find anyone |
| **TC-28** | S10 · ineligible filter | `תל אביב` | **never** a `[S10]` pro | All four are rated 5.0 and sit in TA — a broken `base_filter` puts them first |

### Notes

* **Run TC-19 before TC-20/TC-21.** The two `--scenario` flags inject artificial load
  and deliberately change who wins; the default seed is the clean state every other
  case expects. Re-run the script without flags to reset (`--purge` then re-seed).
* **Petah Tikva is a boundary trap.** It sits ~10.01 km from Tel Aviv, exactly on
  `GEO_RADIUS_STEPS[0]`, so whether it is inside the first radius depends on
  floating-point detail. No case above depends on the answer — PT is only ever
  reached from Rosh HaAyin (6.6 km, comfortably inside).
* **The Sharon is empty on purpose.** No professional in Herzliya, Ra'anana, Kfar
  Saba, Hod HaSharon, Netanya or Hadera; Modiin is empty too. That single decision
  is what makes TC-22, TC-23 and TC-24 possible. Adding a pro there silently
  destroys three cases.
* **Only S01 and S04 professionals have calendar slots.** `determine_best_pro` adds
  a +10 availability bonus, which outweighs the entire 3.0–5.0 rating range — so
  availability, not rating, is the dominant signal. `[FILL] חשמל חולון 01` is
  deliberately the highest-rated pro in the Tel Aviv ring (5.0) with no slots, so
  TC-19 only produces its documented answer if that bonus is working.
* **Routing does not filter on profession.** `determine_best_pro` takes
  `issue_type` but never uses it in `base_filter` — the seven types are seeded for
  realistic prompts and admin-panel variety, not because any case above tests
  profession matching.

All ten are also asserted automatically in `tests/test_seed_coverage_matrix.py`,
which runs the real routing engine against the same matrix — so a routing
regression fails CI rather than waiting to be found by hand here.

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
11. **TC-14** → Emergency Lead Flow (Bypass)
12. **TC-15** → Pro Availability & Vacation Mode
13. **TC-16** → Multi-Job Finish Flow
14. **TC-17** → Admin: Create Pro respects Verified checkbox
15. **TC-18** → Admin: "Unassigned" clears pro_id

After each test, verify in worker logs and database that the expected state changes occurred.

---

### TC-14: Emergency Lead Flow (Bypass)

**Pre-condition:** Customer has consent, state is idle.

| Step | From Phone | Send Message | Expected Bot Response | Verify |
|------|-----------|-------------|----------------------|--------|
| 1 | 972523651414 | "הצילו! יש לי פיצוץ מים בבית בתל אביב" | 1. EMERGENCY_ACK: "🚨 זיהיתי מצב חירום..." <br> 2. Pro persona reply with [DEAL] | Lead `is_emergency` = True |
| 2 | - | - | Pro (972524828796) receives: "🚨 *קריאת חירום דחופה!*" | Address gate bypassed even without street/number |
| 3 | 972524828796 | "אשר" | Pro gets: "✅ העבודה אושרה!" | Lead status = BOOKED |
| 4 | - | - | Customer gets pro details | |

---

### TC-15: Pro Availability & Vacation Mode

**Pre-condition:** Pro is in PRO_MODE.

| Step | From Phone | Send Message | Expected Bot Response | Verify |
|------|-----------|-------------|----------------------|--------|
| 1 | 972524828796 | "הפסקה" | "☕ הסטטוס שלך שונה ל'בהפסקה'..." | User `is_active` = False |
| 2 | 972524828796 | "תפריט" | Dashboard shows: "סטטוס: בהפסקה 🔴" | |
| 3 | 972524828796 | "זמין" | "🚀 הסטטוס שלך שונה ל'זמין'..." | User `is_active` = True |
| 4 | 972524828796 | "תפריט" | Dashboard shows: "סטטוס: זמין 🟢" | |

---

### TC-16: Multi-Job Finish Flow

**Pre-condition:** Pro has 2+ leads in BOOKED status.

| Step | From Phone | Send Message | Expected Bot Response | Verify |
|------|-----------|-------------|----------------------|--------|
| 1 | 972524828796 | "סיימתי" | "איזו עבודה סיימת?" + Numbered list of active jobs | State = PRO_SELECTING_JOB_TO_FINISH |
| 2 | 972524828796 | "1" | "✅ עודכן שהעבודה הסתיימה" | Selected lead status = COMPLETED |
| 3 | 972524828796 | "תפריט" | Dashboard active job count decreased | State back to PRO_MODE |

---

## Admin Panel (Streamlit) Test Cases

These cover admin-panel data-integrity paths that are thin UI wrappers over MongoDB
writes (no WhatsApp involvement). Run in the Streamlit admin panel
(`streamlit run admin_panel/main.py`).

### TC-17: Create Pro Respects the "Verified" Checkbox (PRO-60 Bug 1)

**Pre-condition:** Logged into the admin panel, on the Professionals view, "Add Pro" form open.

| Step | Action | Expected | Verify |
|------|--------|----------|--------|
| 1 | Fill a new pro, leave **Verified** checkbox **unchecked**, submit | "Pro created" success | `db.users.findOne({business_name: "<name>"})` → `is_verified: false` |
| 2 | Add another pro, **check** the Verified checkbox, submit | "Pro created" success | New pro doc → `is_verified: true` |
| 3 | — | — | `audit_log` has a `create_pro` entry for each |

**Regression guarded:** before the fix, every created pro was forced `is_verified: true`
regardless of the checkbox.

### TC-18: "Unassigned" Actually Clears pro_id (PRO-60 Bug 2)

**Pre-condition:** A lead currently assigned to a pro (has a non-null `pro_id`). Open its
Edit Lead form in the admin panel.

| Step | Action | Expected | Verify |
|------|--------|----------|--------|
| 1 | In the Professional selectbox choose **"לא משויך"** (the unassigned option), Save Changes | "Lead updated" success | `db.leads.findOne({_id})` → `pro_id: null` AND `professional: "לא משויך"` |
| 2 | Re-open the same lead, choose a real pro, Save | "Lead updated" | Lead doc → `pro_id` = that pro's `_id` |
| 3 | — | — | `audit_log` has an `edit_lead` entry for each save |

**Regression guarded:** before the fix, choosing the unassigned option changed only the
displayed `professional` name; `pro_id` stayed pointed at the old pro (ghost assignment)
so matching/healer/pro-flow still treated the lead as owned. The option now uses the
localized `T["unknown_pro"]` (`"לא משויך"`) sentinel, consistent with every other view.

