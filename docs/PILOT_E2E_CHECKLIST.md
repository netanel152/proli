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

> **Status (2026-11-21):** real sends go through `WHATSAPP_PROVIDER=cloud` (the PRO-89
> `CloudAPIProvider`; the legacy vendor is gone, PRO-85/PRO-86). PRO-87 onboarding is done.
> **Staging** sends live from Meta's Test Number, so the checklist is runnable there today as
> the rehearsal (`SANDBOX_E2E_RUNSHEET.md` Part 3). **Production** is still muted (above), so
> the run that counts as the launch gate waits for the PRO-102 cutover. Meta approved the 28
> `_v2` templates on 2026-11-18 (25 UTILITY, 3 MARKETING — the three are never armed), but
> nothing is armed until `WHATSAPP_ARMED_TEMPLATES` names a key: until then a send to a
> closed 24h window is dropped, so keep every test phone's window open (it wrote to the bot
> within the day) unless the step is about a closed window.

---

## Setup

| Role | What you need |
|---|---|
| **C — Customer phone** | A real phone whose number is **not** a registered pro. |
| **P — Pro phone** | A real phone registered + approved as a pro, whose `service_areas` cover **C's test city** (e.g. Tel Aviv). |
| **Admin** | Admin panel open (logged in) + the admin WhatsApp on `ADMIN_PHONE`. |

### Running with two phones

The whole checklist can be run with two handsets. The third role, a second pro, needs a
database record but no phone.

| Phone | Plays | Notes |
|---|---|---|
| **Phone 1 — customer** | **C** | A number that is **not** a registered pro. |
| **Phone 2 — pro** | **P**, and **Admin** if `ADMIN_PHONE` is this same number | The registered, approved pro. When it is also the admin, `ניהול` opens the admin wizard from it; until the wizard is finished its replies belong to the wizard, and after that every keyword is read as the pro's again. |

- **A second approved pro in the database, no handset:** scenario 3 (the reassignment half —
  somebody has to be offered the lead after P stays silent) and scenario 15 (the wizard
  assigns to "a different pro"; in (b) the lead is "offered to the next pro"). Give it a
  number nobody is holding. Its messages go **unobserved**: confirm what it was sent from the
  lead in the admin panel and the worker log, not from a screen, and say so in the notes.
- **Scenario 10 (self-signup) needs an unregistered number**, and Phone 1 is the only one.
  Run it **last**, from Phone 1, after a reset (`התחלה`) — once approved, that number is a pro
  and can no longer play C.
- **Operator pages** (SOS, escalations, emergencies with no pro) arrive as Sentry email, not
  on either phone. Have the inbox open.
- **Anything that needs a third handset is `N/A (sandbox)`**, not Fail. That is every step
  that needs two *customers* or a second fresh number at the same time: scenario 12 step 1's
  second number (`לא לביטול`) and its steps 2–4 where the two waiting offers come from two
  customers, scenario 13 (a) once Phone 1 already has consent on record, and scenario 16 (a)
  with two customers. Where the scenario itself names a route that needs no third phone
  ("or one customer and the panel" in 16 (a)), use that route instead of marking it N/A.

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
- [ ] Sentry watches the pipeline (PRO-17): Crons shows `worker-pipeline-watch` with recent `ok` check-ins (10-minute cadence), and the uptime monitor on the public `/health` is green (`docs/OPERATIONS_GUIDE.md`, "Monitors that do not depend on GitHub").
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
| **PRO-48** (ADMIN_PHONE is the operator's number) | ⚠️ config check | Verify `ADMIN_PHONE` is **set** in the environment and is the operator's number. Do **not** check it against the default value: production is legitimately set to a number equal to the default, because the default is the operator's own. The boot warning covers the case this row used to test — an environment where nothing set it at all. No business alert is sent to it over WhatsApp since PRO-88 (SOS, escalations and stuck-lead reports page through Sentry email), so check it from the handset: `ניהול` sent **from** that phone opens the admin wizard (scenario 8), and from any other number it does not. The only WhatsApp the bot still sends to it (or to `ONCALL_PHONE` when set) is the infrastructure courtesy notice that the account recovered after an outage, which this checklist does not trigger |
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
**Expected:** the bot **pauses for 15 min** (`PAUSED_FOR_HUMAN`); the admin is paged via **Sentry
email**, and gets **no WhatsApp** on `ADMIN_PHONE` in any configuration (PRO-88). The page names the
last four digits of the customer's number, the issue, the lead and whether the pro was notified —
never what the customer wrote.
The assigned pro gets an alert only if the lead is **BOOKED** (with the customer's phone and words,
plus the pause notice) or **NEW** (no phone and no words, offering `אשר`/`דחה`). On a CONTACTED lead,
or if the admin deactivated the pro, the pro gets nothing. Further C messages within the window are held for a human, not
auto-answered. After ~15 min the pause auto-expires.
- **PRO-133 — no indicator while paused.** Messages sent inside the 15-min window get **no blue checks and no "typing…"**: a human owns the chat and the bot answers nothing, so an indicator would promise a reply that never comes. The messages are still held and still reach the human. (Contrast with a `PENDING_ADMIN_REVIEW` chat, where the throttled ack means the indicator *is* shown — that chat is genuinely being worked on.)
- [ ] Pass  · [ ] File: ______

### 8. Unmatchable city → admin review
**Steps (C):** run intake for a city with **no covering pro** (e.g. **אילת**).
**Expected (current code):** no pro found within the max radius → lead → `PENDING_ADMIN_REVIEW`;
the **customer** receives the "pending review" message. **The admin is not paged** for a routine
unmatchable lead (PRO-77): it is a coverage gap, logged at WARNING, and no Sentry email and no
WhatsApp to `ADMIN_PHONE` is sent. The lead appears in the admin panel's pending-review view and
via the `ניהול` wizard, which is how the operator finds it.
- Verify **no Sentry email** arrives for the ordinary case.
- **Emergency variant (PRO-121):** repeat with an emergency message for the same city (e.g.
  `הצפה באילת`). This one **does** page: one Sentry email, "EMERGENCY lead … has no available
  pro … needs manual routing now". Still no WhatsApp to the admin.
- `ADMIN_PHONE` is checked by sending `ניהול` from that phone here: the wizard opens and lists
  this lead. Nothing in scenarios 7 or 8 sends a WhatsApp *to* the admin (see the PRO-48 row).
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

### 13. Life-threatening report  ·  regresses PRO-222
**Steps (C):** (a) from a fresh number with **no consent on record**, send **`יש שריפה בדירה`**.
(b) From a chat with an open lead that is still `contacted` or `new` (offered, not yet approved),
send **`יש ריח גז`**. (c) Send **`יש שריפה`** again within 10 minutes. (d) Repeat (a) from
**P's own phone** while P is in pro mode.
**Expected:**
- Each reply to C is the urgent notice (`Customer.LIFE_THREATENING`): move away and call **102**
  (fire, smoke, gas, explosion), **101** (injury or electric shock), **100** (police), and for gas
  shut the main valve only if that is safe and call from outside. It does not say "חירום", and it
  says Proli will not send a pro to this call.
- **No pro is offered or notified**, and no new lead is created. In (a) nothing about the chat is
  stored: no consent prompt, no message in the panel.
- In (b) the open lead goes to **`pending_admin_review`** with `escalation_reason: life_threatening`
  and appears in the panel's pending-review view; the offer already sent to a pro is withdrawn from
  the flow (an `אשר` from that pro is answered as no longer available).
- The operator gets **one** Sentry page per chat per 10 minutes, with the number masked and none of
  the customer's words: (c) does not page a second time.
- (d) is answered as a pro command, not as an emergency report.
- [ ] Pass  · [ ] File: ______

### 14. Message order  ·  regresses PRO-214
**Steps (C):** from a chat mid-intake, send three messages in quick succession (e.g. the city, then
the street, then the time), without waiting for a reply.
**Expected:** the bot answers them in the order they were sent, never a later message before an
earlier one, and the lead ends up with all three details. Nothing is lost or answered twice.
- [ ] Pass  · [ ] File: ______

### 15. Assignment and reassignment races  ·  regresses PRO-271, PRO-281, PRO-282, PRO-283
**Steps:** (a) With a lead in `pending_admin_review`, assign it to a pro from the admin panel's
one-click strip and, from the `ניהול` wizard, to a different pro, as close together as you can (or
assign once, then assign again from a stale screen). (b) With a lead whose pro has not answered,
and the customer's reassignment offer showing, have **P** tap `אשר` and **C** reply `1` at the same
moment. (c) In the panel, change a lead's status or pro from a page you loaded before someone else
changed it, then save.
**Expected:**
- (a) Exactly one assignment wins and exactly one pro gets the offer; the other path says the lead
  was already taken (the wizard answers `Admin.ASSIGN_LEAD_TAKEN`) and messages nobody.
- (b) Exactly one outcome: the lead is booked with P, **or** it is offered to the next pro. Never
  both: the lead ends with a single pro and the losing side changes nothing.
- (c) A save from a stale page writes nothing over the newer owner or status and says so (a row
  skipped as stale), and a cancel releases the booked slot.
- [ ] Pass  · [ ] File: ______

### 16. Slot uniqueness  ·  regresses PRO-277
**Steps:** (a) Have two customers (or one customer and the panel) book the **same pro at the same
time** as close together as you can. (b) In the panel, run the bulk slot generator for a range that
already has slots, and let the Sunday regeneration (or a second click) run over it again.
**Expected:**
- (a) One booking succeeds; the other customer is offered another time. The pro's schedule shows
  one slot taken, never two bookings at the same time, and a slot that overlaps an already-taken one
  is not offered.
- (b) No duplicate slot rows appear; a repeat run adds nothing for times that already exist.

> ⚠️ **Under review — what the code does today in (a), which is not what Expected says.** No
> one is "offered another time". When the pro approves the second lead, the lead is marked
> `booked` first and a slot is looked for afterwards (`pro_flow._approve_lead` →
> `matching_service.book_slot_for_lead`). If another free slot exists within ±2h of the
> requested time, it is taken silently: the lead's `booked_slot_id` points at a different
> hour, while its `appointment_time` and the customer's "pro found" message still state the
> time originally agreed. If no free slot is left in that window, the lead stays `booked`
> with **no slot at all**; the only visible difference is that the pro's approval reply lacks
> the "היומן עודכן בהצלחה" line. In both cases the second customer is told the pro is coming
> at the requested time and nobody — customer, pro or operator — is told about the clash. The
> slot half of Expected does hold (one slot row taken, never two). Record what you observe
> against this note; do not tick Pass on the "offered another time" wording until the review
> decides which behaviour is intended.

- [ ] Pass  · [ ] File: ______

---

## Exit criteria

- [ ] **All 16 scenarios Pass** on a single clean run (production config) — PRO-55 and PRO-56 are now in code (see the dependency table).
- [ ] Every failure encountered has a linked Linear ticket, fixed and re-verified.
- [ ] The run is signed off below.

Only when this is fully green does the pilot start.

## Sign-off

| Run # | Date | Tester | Result (X/16) | Notes / tickets filed |
|---|---|---|---|---|
|  |  |  |  |  |
|  |  |  |  |  |
