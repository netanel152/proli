# Pilot E2E Checklist — Real Phones, Production Config

**This is the launch gate.** The pilot does not start until every box below passes.
The automated suite (`docs/TESTING.md`) proves the units work; this proves the
**product** works end-to-end on real WhatsApp. The first real customer must never be
the first real test.

> Run against **production** configuration after all Wave-1 launch fixes have merged.
> Every failure → open a Linear ticket **before** the pilot, fix, then **re-run the
> whole checklist** until it is 100% green. Record the run in the sign-off table.

---

## Setup

| Role | What you need |
|---|---|
| **C — Customer phone** | A real phone whose number is **not** a registered pro. |
| **P — Pro phone** | A real phone registered + approved as a pro, whose `service_areas` cover **C's test city** (e.g. Tel Aviv). |
| **Admin** | Admin panel open (logged in) + the admin WhatsApp on `ADMIN_PHONE`. |

Keywords used below (from `app/core/messages.py`, exact): emergency `הצפה`/`דחוף` · SOS
`נציג`/`אנושי`/`מנהל` · status `סטטוס`/`?` · cancel `בטל` · reschedule `מועד אחר` ·
register `הרשמה` · reset `התחלה` · rating `1`–`5` · admin wizard `ניהול`. Pro approval is
**text, not buttons** (Green API) — the pro replies per the prompt (e.g. `1`/`אשר`).

### Pre-flight (must all be green before starting)

- [ ] `GET /health` → `mongodb`/`redis`/`worker` all `up`; **`whatsapp.status: "up"`, `whatsapp.state: "authorized"`**.
- [ ] Circuit breaker not engaged: `redis-cli exists wa:instance:paused wa:instance:paused:manual` → `0`.
- [ ] Sentry paging live (PRO-18): `SENTRY_DSN` set, `fatal→email` rule exists, a test critical emails you.
- [ ] Worker running (APScheduler jobs active) — check logs for `worker:heartbeat` and `[WA Monitor]`.
- [ ] At least one **approved, active** pro covers C's city with free upcoming slots.
- [ ] If testing SLA timings, confirm no quiet-hours/scheduler gating (PRO-73) will suppress the nudges during the run.

---

## Dependency status (verified against code on this branch)

Some scenarios reference features from tickets that are **not yet in the code**. They are
kept here because this checklist is the launch gate and those tickets must land first — but a
box that depends on an unshipped ticket **will fail today**, and that is expected until the
ticket merges. Re-verify this table before each run.

| Referenced | In code now? | Effect on checklist |
|---|---|---|
| **PRO-44** ([DEAL:] strip) | ✅ yes | Scenario 1 no-leak check is a valid regression |
| **PRO-32 / PRO-43** (cancel frees slot) | ✅ yes | Scenario 4 is a valid regression |
| **PRO-60** (`is_verified` not auto-true) | ✅ holds — defaults false in admin, absent in onboarding | Scenario 10 valid |
| **PRO-55** (quoted price to pro) | ❌ **not built** — `APPROVAL_REQUEST` has no price; `quoted_price` unused | Scenario 1's price sub-check **fails until PRO-55 ships** |
| **PRO-56** (10-min nudge / 25-min offer) | ❌ **not built** — no approval-nudge job exists | Scenario 3 **fails until PRO-56 ships** |
| **PRO-48** (ADMIN_PHONE not hard-coded) | ⚠️ config check | Verify the admin phone is set via env, not the default |

---

## Scenarios

For each: run the steps on the real devices, compare to **Expected**, tick Pass or File.
"File" = open a Linear ticket with the observed behavior and link it here.

### 1. Happy path — full customer journey  ·  regresses PRO-44 (PRO-55 pending)
**Steps (C):** send a first message → accept consent (`כן`) → describe the issue **with a photo**
(e.g. "נזילה מתחת לכיור" + image) → provide full address when asked (street, number, city,
floor, apartment) → provide a time → receive the AI estimate → **(P)** approve with `1`/`אשר` →
**(C)** confirm the booking → after the job, complete + rate `5`.
**Expected:**
- Bot asks for exactly the missing address parts; only proceeds when all five (street, number, city, floor, apartment) are present.
- **No `[DEAL:]` / marker text ever appears in a customer-facing message** (PRO-44 — in code).
- Pro's approval request shows customer name, phone, full address, floor/apartment, issue, and time. It does **not** currently show a price — the AI estimate goes to the *customer*, not the pro. ⚠️ **PRO-55 (price to pro) is not built**; tick the price sub-box only once it ships.
- Booking confirmed to C; slot marked taken; completion + rating recorded.
- [ ] Pass (core journey)  ·  [ ] Pass (price-to-pro — needs PRO-55)  ·  [ ] File: ______

### 2. Emergency lane  ·  emergency keywords
**Steps (C):** from a fresh chat, send **"יש לי הצפה!!"**.
**Expected:** the message is treated as emergency (contains `הצפה`) — fast-tracked; the pro's
notification carries the emergency header; the address gate is relaxed for emergencies (city is
enough to dispatch). Copy reads as urgent, not the normal slow intake.
- [ ] Pass  · [ ] File: ______

### 3. Pro silent — SLA nudge + reassignment offer  ·  ⚠️ requires PRO-56 (not built)
**Steps:** create a fresh lead (C) that routes to P; **do not approve on P**. Wait.
**Expected (target, PRO-56):** at ~10 min P receives an approval nudge; at ~25 min C receives a reassignment offer.
**Current code (until PRO-56 ships):** there is **no 10-min approval nudge and no 25-min customer offer**. What exists instead: the **SOS Healer** reassigns a stale `new`/`contacted` lead after **60 min** (`SOS_TIMEOUT_MINUTES`), and a customer parked in `AWAITING_PRO_APPROVAL` holds on a bounded TTL. So this scenario **fails today by design** — it's a launch-gate reminder that PRO-56 must land before pilot.
- [ ] Pass (needs PRO-56)  ·  [ ] File: ______

### 4. Customer cancels a BOOKED lead  ·  regresses PRO-32 / PRO-43
**Steps (C):** with a **BOOKED** lead, send **`בטל`**.
**Expected:** lead → `cancelled`; **the reserved slot is released** (becomes bookable again);
**the assigned pro is notified** of the cancellation. Verify the freed slot in the admin panel.
- [ ] Pass  · [ ] File: ______

### 5. Reschedule flow
**Steps (C):** with a BOOKED lead, send **`מועד אחר`** → pick a new offered slot.
**Expected:** the **old slot is freed**, the **new slot is booked**, and the lead reflects the
new time. No double-booking.
- [ ] Pass  · [ ] File: ______

### 6. Status query in three lifecycle stages
**Steps (C):** send **`סטטוס`** (and once **`?`**) at three points — (a) after the issue is
described but before pro approval, (b) after booking, (c) after completion.
**Expected:** each returns an accurate, deterministic status reply matching the real lead state
at that moment (no AI hallucination, no stale state).
- [ ] Pass  · [ ] File: ______

### 7. SOS / human handoff
**Steps (C):** mid-flow, send **`נציג`** (or `אנושי` / `מנהל`).
**Expected:** the bot **pauses for 15 min** (`PAUSED_FOR_HUMAN`); the admin (and assigned pro, if
any) receives an SOS alert; further C messages within the window are held for a human, not
auto-answered. After ~15 min the pause auto-expires.
- [ ] Pass  · [ ] File: ______

### 8. Unmatchable city → admin review
**Steps (C):** run intake for a city with **no covering pro** (e.g. **אילת**).
**Expected (current code):** no pro found within the max radius → lead → `PENDING_ADMIN_REVIEW`;
the **customer** receives the "pending review" message; the admin is paged via
**`logger.critical` → Sentry email** (not a WhatsApp to `ADMIN_PHONE`). The lead appears in the
admin panel's pending-review view and via the `ניהול` wizard.
- Verify the **Sentry email** arrives (reuses the PRO-18 / PRO-75 paging path).
- ⚠️ **Note:** this `logger.critical` fires on *every* unmatchable lead — expect one email each. If that's too noisy for a routine no-pro case, downgrade the log level (separate ticket).
- `ADMIN_PHONE`/`ONCALL_PHONE` not-hard-coded (PRO-48) is verified via the **SOS/handoff** path (scenario 7), which *does* WhatsApp the admin.
- [ ] Pass  · [ ] File: ______

### 9. Voice-note intake
**Steps (C):** describe the issue as a **voice note** instead of text.
**Expected:** the audio is transcribed and the transcription **drives the dispatcher** (city/issue
extracted from speech); the flow proceeds as if typed.
- [ ] Pass  · [ ] File: ______

### 10. Pro self-signup  ·  regresses PRO-60
**Steps:** from a **new, unregistered** phone, send **`הרשמה`** → complete onboarding. In the admin
panel, find the pending pro → approve.
**Expected:** the new pro appears in **pending approvals**; on approval the pro is **notified**;
**`is_verified` is NOT auto-set to true** on creation (PRO-60) — it stays false until explicitly
verified.
- [ ] Pass  · [ ] File: ______

### 11. Admin panel — edit / unassign / kanban  ·  regresses PRO-60
**Steps (Admin):** edit a lead's fields; **unassign** a lead from its pro; view the kanban board.
**Expected:** edits persist; **unassign clears `pro_id`** (it does not linger — PRO-60); the kanban
reflects real current lead states (no stale columns). Mutations show success feedback.
- [ ] Pass  · [ ] File: ______

---

## Exit criteria

- [ ] **All 11 scenarios Pass** on a single clean run (production config) — note this requires **PRO-55 and PRO-56 to have shipped first** (scenario 1's price sub-check and scenario 3 depend on them; see the dependency table). The checklist cannot reach 100% until then.
- [ ] Every failure encountered has a linked Linear ticket, fixed and re-verified.
- [ ] The run is signed off below.

Only when this is fully green does the pilot start.

## Sign-off

| Run # | Date | Tester | Result (X/11) | Notes / tickets filed |
|---|---|---|---|---|
|  |  |  |  |  |
|  |  |  |  |  |
