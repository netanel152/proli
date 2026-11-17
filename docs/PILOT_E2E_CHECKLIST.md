# Pilot E2E Checklist — Real Phones, Production Config

**This is the launch gate.** The pilot does not start until every box below passes.
The automated suite (`docs/TESTING.md`) proves the units work; this proves the
**product** works end-to-end on real WhatsApp. The first real customer must never be
the first real test.

> **Prerequisite:** production runs `WHATSAPP_DRY_RUN=true` and is muted until the
> PRO-102 cutover (see `docs/RAILWAY_SETUP.md`). Nothing transmits with the mute on,
> so a "Pass" on any scenario below would mean nothing — this checklist cannot start
> until that flips. Same rule `SANDBOX_E2E_RUNSHEET.md` states for its Part 2.
>
> Run against **production** configuration after all Wave-1 launch fixes have merged.
> Every failure → open a Linear ticket **before** the pilot, fix, then **re-run the
> whole checklist** until it is 100% green. Record the run in the sign-off table.

> ⚠️ **PRO-86 update:** the legacy WhatsApp vendor is gone (PRO-85). Real sends now require
> `WHATSAPP_PROVIDER=cloud`, which selects the PRO-89 `CloudAPIProvider` —
> code-complete against the Meta Graph API, but with no live account behind it yet:
> PRO-87 (Business Portfolio, template approval, a sandbox number) has not landed, so
> this checklist is **not currently runnable end-to-end** (no configuration produces
> `whatsapp.status: "up"` yet). Re-enable once PRO-87 onboarding completes.

---

## Setup

| Role | What you need |
|---|---|
| **C — Customer phone** | A real phone whose number is **not** a registered pro. |
| **P — Pro phone** | A real phone registered + approved as a pro, whose `service_areas` cover **C's test city** (e.g. Tel Aviv). |
| **Admin** | Admin panel open (logged in) + the admin WhatsApp on `ADMIN_PHONE`. |

Keywords used below (from `app/core/messages.py`, exact): emergency `הצפה`/`דחוף` · SOS
`נציג`/`אנושי`/`מנהל` · status `סטטוס`/`?` · cancel `בטל` · reschedule `מועד אחר` ·
register `הרשמה` · reset `התחלה` · rating `1`–`5` · admin wizard `ניהול`. Every prompt
states its replies as text, and typing them always works. Three prompts also show **reply
buttons** on an open window (PRO-104/PRO-105, see CLAUDE.md): the pro's lead offer and nudge
(`אשר`/`דחה`, each tap naming its own lead), the customer's consent request (`כן`/`לא`) and the
completion check (`1`/`2`/`3`). Typed `אשר` with more than one offer waiting opens a numbered
list, and a bare `1` only opens a list whose own `1` then approves. Scenario 12 checks the
buttons.

### Pre-flight (must all be green before starting)

- [ ] `GET /health` with `X-Health-Token: $HEALTH_TOKEN` (PRO-136; the `checks` detail is omitted without it) → `mongodb`/`redis`/`worker` all `up`; **`whatsapp.status: "up"`, `whatsapp.state: "authorized"`**.
- [ ] Circuit breaker not engaged: `redis-cli exists wa:instance:paused wa:instance:paused:manual` → `0`.
- [ ] The panel can send: `admin` has the variables in "What `admin` needs in order to send" (`docs/RAILWAY_SETUP.md`), and after loading a page its logs show `WhatsApp egress using provider 'cloud'` (an INFO line, so at `LOG_LEVEL=WARNING` use a real panel send as the evidence). `/health` reports `api`'s provider, not `admin`'s.
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
| **PRO-55** (quoted price to pro) | ✅ built — `quoted_price` persisted, shown in `APPROVAL_REQUEST` **and** `PRO_FOUND` | Scenario 1's price check is valid |
| **PRO-56** (10-min nudge / 25-min offer) | ✅ built — `check_pro_approval_sla` job runs every 5 min | Scenario 3 is a valid regression |
| **PRO-48** (ADMIN_PHONE reaches the operator) | ⚠️ config check | Verify `ADMIN_PHONE` is **set** in the environment and is the operator's number. Do **not** check it against the default value: production is legitimately set to a number equal to the default, because the default is the operator's own. The boot warning covers the case this row used to test — an environment where nothing set it at all |
| **PRO-133** (read receipt + typing indicator) | ⚠️ depends on PR #231 | Until it merges, the indicator checks in scenarios 1 and 7 fail by design — on Cloud API nothing was ever transmitted, because `CloudAPIProvider` never overrode the base no-op. This is the only handset evidence PRO-133 can get: the unit suite and the offline harness prove the call is *made*, not that WhatsApp renders it |
| **Policy links in the consent gate and pro signup** | ⚠️ config check | The consent message carries a privacy link only once `PUBLIC_BASE_ORIGIN` is set, and a terms link (consent + signup welcome) only once `TERMS_ENABLED=true` **and** `app/static/terms.html` is the owner-approved text. `/terms` answers 404 until then. Both variables on **api and worker**. Open every link the message sends |

---

## Scenarios

For each: run the steps on the real devices, compare to **Expected**, tick Pass or File.
"File" = open a Linear ticket with the observed behavior and link it here.

### 1. Happy path — full customer journey  ·  regresses PRO-44, PRO-55
**Steps (C):** send a first message **with a photo** (e.g. "נזילה מתחת לכיור" + image) → accept
consent (`כן`) → the dispatcher answers that message without it being repeated, and the photo
reaches the lead as a Cloudinary URL → provide full address when asked (street, number, city,
floor, apartment) → provide a time → receive the AI estimate → **(P)** approve with `אשר` →
**(C)** confirm the booking → after the job, complete + rate `5`.
**Expected:**
- **PRO-133 — within a second or two of each customer message, before any reply: the two blue checks appear and the chat header shows "typing…".** Watch the *first* message of the run especially: it arrives pre-consent, so the indicator must show there too. It should stay up for the 3–8s the AI takes and clear when the reply lands. A reply that arrives on one of the instant paths (consent, politeness) may legitimately beat the indicator onto the screen, leaving "typing…" up briefly afterwards — that is known and accepted, not a failure.
- Bot asks for exactly the missing address parts; only proceeds when all five (street, number, city, floor, apartment) are present.
- **No `[DEAL:]` / marker text ever appears in a customer-facing message** (PRO-44 — in code).
- Pro's approval request shows customer name, street + city, issue, time, **and the AI-quoted price** (`💰 הערכת מחיר שניתנה ללקוח`) when the AI gave an estimate (PRO-55). The **same** figure is shown to the customer on approval (`PRO_FOUND`) — single source of truth. A deal with no estimate shows no price line (not a broken/empty line).
- **PRO-59 — the approval request must NOT contain the customer's phone, their floor/apartment, or a navigation link.** All three arrive in one message the moment the pro sends *אשר*, together with the approval confirmation. Check both halves: a pro who can reach the customer before approving can reject in-bot and take the job off-platform, and a pro who approves and gets no contact details cannot do the job at all.
- A customer who types *נציג* **while still waiting for approval** must page the pro *without* their phone number — the pro is told to approve first. Only a BOOKED lead's SOS carries the number.
- Booking confirmed to C; slot marked taken; completion + rating recorded.
- [ ] Pass  ·  [ ] File: ______

### 2. Emergency lane  ·  emergency keywords
**Steps (C):** from a fresh chat, send **"יש לי הצפה!!"**.
**Expected:** the message is treated as emergency (contains `הצפה`) — fast-tracked; the pro's
notification carries the emergency header; the address gate is relaxed for emergencies (city is
enough to dispatch). Copy reads as urgent, not the normal slow intake.
- [ ] Pass  · [ ] File: ______

### 3. Pro silent — SLA nudge + reassignment offer  ·  regresses PRO-56
**Steps:** create a fresh lead (C) that routes to P; **do not approve on P**. Wait.
**Expected:** at ~10 min (`APPROVAL_NUDGE_MINUTES`) P receives an approval nudge; at ~25 min (`APPROVAL_REASSIGN_OFFER_MINUTES`), during business hours (08:00–21:00 IL), C receives a reassignment offer (half the thresholds for emergency leads). The **SOS Healer** still reassigns a stale `new`/`contacted` lead after 60 min (`SOS_TIMEOUT_MINUTES`) as a backstop if this scenario is somehow missed — note both the reassignment offer and the SOS Healer are business-hours-gated, and the SOS Healer's `sos_healer_active` toggle defaults OFF (PRO-73), so confirm it's enabled before running this test.
- [ ] Pass  ·  [ ] File: ______

### 4. Customer cancels a BOOKED lead  ·  regresses PRO-32 / PRO-43, PRO-118
**Steps (C):** with a **BOOKED** lead, send **`בטל`**, then reply **`1`** to the confirmation prompt.
**Expected:** the first message only asks for confirmation (`CANCEL_CONFIRM_PROMPT`) — the lead
stays `booked` and the slot stays taken until confirmed. After `1`: lead → `cancelled`; **the
reserved slot is released** (becomes bookable again); **the assigned pro is notified** of the
cancellation. Verify the freed slot in the admin panel.
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
**Expected:** the bot **pauses for 15 min** (`PAUSED_FOR_HUMAN`); the admin is paged via Sentry.
The assigned pro gets an alert only if the lead is **BOOKED** (with the customer's phone and words,
plus the pause notice) or **NEW** (no phone and no words, offering `אשר`/`דחה`). On a CONTACTED lead,
or if the admin deactivated the pro, the pro gets nothing. Further C messages within the window are held for a human, not
auto-answered. After ~15 min the pause auto-expires.
- **PRO-133 — no indicator while paused.** Messages sent inside the 15-min window get **no blue checks and no "typing…"**: a human owns the chat and the bot answers nothing, so an indicator would promise a reply that never comes. The messages are still held and still reach the human. (Contrast with a `PENDING_ADMIN_REVIEW` chat, where the throttled ack means the indicator *is* shown — that chat is genuinely being worked on.)
- [ ] Pass  · [ ] File: ______

### 8. Unmatchable city → admin review
**Steps (C):** run intake for a city with **no covering pro** (e.g. **אילת**).
**Expected (current code):** no pro found within the max radius → lead → `PENDING_ADMIN_REVIEW`;
the **customer** receives the "pending review" message; the admin is paged via
**`page_critical` → Sentry email** (not a WhatsApp to `ADMIN_PHONE`). The lead appears in the
admin panel's pending-review view and via the `ניהול` wizard.
- Verify the **Sentry email** arrives (reuses the PRO-18 / PRO-75 paging path).
- ⚠️ **Note:** this `page_critical` fires on *every* unmatchable lead — expect one email each. If that's too noisy for a routine no-pro case, downgrade the log level (separate ticket).
- `ADMIN_PHONE`/`ONCALL_PHONE` reaching the operator (PRO-48) is verified via the **SOS/handoff** path (scenario 7), which *does* WhatsApp the admin. The check is that the message arrives — not that the value differs from the default, which it legitimately does not.
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

### 12. Reply buttons  ·  regresses PRO-104, PRO-105
Each step on a phone whose 24h window is open (it wrote to the bot within the day).
**Steps:**
1. **(C)** From a number with no consent on record, write anything. → The consent request shows
   two buttons, `כן להמשיך` and `לא לביטול`. Tap `כן להמשיך` → the same reply typing `כן` gives
   (the ack, and the opening message answered). On another fresh number, tap `לא לביטול` → the
   decline message, and nothing about that chat in the panel.
2. **(P)** Have two offers waiting for P at once (two customers, or a second lead routed to P).
   On the **older** offer's message tap `אשר` → **that** lead is booked and its customer's
   details arrive; no numbered list opens, and the other offer is still waiting.
3. **(P)** Tap `אשר` (or `דחה`) on an offer that has since moved on (reassigned to another pro
   by the SLA, or booked already) → "the list changed" / "no pending approvals" reply, and
   nothing is booked or rejected — not even the other waiting offer.
4. **(P)** With two offers waiting, **type** `אשר` → the numbered list, exactly as before.
5. **(C)** On a booked job, trigger the completion check (admin panel). → Three buttons,
   `כן הסתיים`, `עדיין לא`, `לא הגיע`. Tap `כן הסתיים` → the job completes and the rating
   prompt arrives, as typing `1` would.
- [ ] Pass  · [ ] File: ______

---

## Exit criteria

- [ ] **All 12 scenarios Pass** on a single clean run (production config) — PRO-55 and PRO-56 are now in code (see the dependency table).
- [ ] Every failure encountered has a linked Linear ticket, fixed and re-verified.
- [ ] The run is signed off below.

Only when this is fully green does the pilot start.

## Sign-off

| Run # | Date | Tester | Result (X/11) | Notes / tickets filed |
|---|---|---|---|---|
|  |  |  |  |  |
|  |  |  |  |  |
