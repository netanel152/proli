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

## Scenarios

For each: run the steps on the real devices, compare to **Expected**, tick Pass or File.
"File" = open a Linear ticket with the observed behavior and link it here.

### 1. Happy path — full customer journey  ·  regresses PRO-55, PRO-44
**Steps (C):** send a first message → accept consent (`כן`) → describe the issue **with a photo**
(e.g. "נזילה מתחת לכיור" + image) → provide full address when asked (street, number, city,
floor, apartment) → provide a time → receive the AI estimate → **(P)** approve the lead →
**(C)** confirm the booking → after the job, complete + rate `5`.
**Expected:**
- Bot asks for exactly the missing address parts; only proceeds when all five are present.
- Pro's approval request **shows the AI-quoted price** (PRO-55).
- **No `[DEAL:]` / marker text ever appears in a customer-facing message** (PRO-44).
- Booking confirmed to C; slot marked taken; completion + rating recorded.
- [ ] Pass  · [ ] File: ______

### 2. Emergency lane  ·  emergency keywords
**Steps (C):** from a fresh chat, send **"יש לי הצפה!!"**.
**Expected:** the message is treated as emergency (contains `הצפה`) — fast-tracked; the pro's
notification carries the emergency header; the address gate is relaxed for emergencies (city is
enough to dispatch). Copy reads as urgent, not the normal slow intake.
- [ ] Pass  · [ ] File: ______

### 3. Pro silent — SLA nudge + reassignment offer  ·  regresses PRO-56
**Steps:** create a fresh lead (C) that routes to P; **do not approve on P**. Wait.
**Expected:** at **~10 min** P receives an approval **nudge**; at **~25 min** C receives a
**reassignment offer**. Timings per PRO-56.
- [ ] Pass  · [ ] File: ______

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

### 8. Unmatchable city → admin review  ·  regresses PRO-48
**Steps (C):** run intake for a city with **no covering pro** (e.g. **אילת**).
**Expected:** no pro found within the max radius → lead → `PENDING_ADMIN_REVIEW`; an admin alert
is delivered **to the correct phone** (`ADMIN_PHONE`/`ONCALL_PHONE`, not a hard-coded default —
PRO-48). The lead appears in the admin panel's pending-review view.
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

- [ ] **All 11 scenarios Pass** on a single clean run (production config).
- [ ] Every failure encountered has a linked Linear ticket, fixed and re-verified.
- [ ] The run is signed off below.

Only when this is fully green does the pilot start.

## Sign-off

| Run # | Date | Tester | Result (X/11) | Notes / tickets filed |
|---|---|---|---|---|
|  |  |  |  |  |
|  |  |  |  |  |
