# Proli — Logic Flow & Lead Lifecycle

## 1. Lead Lifecycle

```
CONTACTED → NEW → BOOKED → COMPLETED → CLOSED
              ↓       ↓
          REJECTED  CANCELLED
            ↓  ↑
            │  └── reassigned to a new pro → NEW
    PENDING_ADMIN_REVIEW
```

| Status | When set | Who sets it |
|--------|----------|------------|
| `contacted` | AI extracts city + issue, opens conversation | Dispatcher (Phase 1) |
| `new` | Pro matched and approval message sent | `_finalize_deal` |
| `booked` | Pro approves via text ("אשר"/"1") | `_handle_approve` in `pro_flow.py` |
| `completed` | Pro or customer confirms work done | `customer_flow.py` |
| `rejected` | Pro rejects via text ("דחה"/"2") — a way-station, not terminal: `_handle_reject` claims it then hands off to `monitor_service.reassign_lead`, which reopens the lead as `new` under the next pro or escalates it (PRO-117) | `_handle_reject` in `pro_flow.py` |
| `cancelled` | Customer cancels — a cancel keyword on a BOOKED job first asks for explicit confirmation (`AWAITING_CANCEL_CONFIRMATION`, PRO-118) rather than cancelling on the first keyword hit | `workflow_service.py` |
| `closed` | Admin closes a lead, or the Janitor closes a never-assigned lead after 24 h | `admin_flow.py` / `monitor_service.py` |
| `pending_admin_review` | No replacement pro found at any radius, `MAX_REASSIGNMENTS` exhausted, or a rejected lead's rematch itself fails | `monitor_service.py` / `workflow_service.py` / `pro_flow.py` |

Each transition is appended to the lead's `status_history` array (`{status, at, by}`) via `set_lead_status()`, the single writer for lead status in `lead_manager_service.py`.

---

## 2. AI Engine — Two-Phase Architecture

### Phase 1: Dispatcher

Activated when no pro is assigned to the active lead.

- Receives the last **5 conversational turns** (10 messages max — centrally trimmed in `ai_engine_service.py`)
- Extracts `city`, `issue`, `street`, `street_number`, `floor`, `apartment`, `appointment_time`, `appointment_datetime` (ISO 8601, resolved from relative expressions; left null for open-ended times like "בהקדם") from the conversation
- If `city` + `issue` are found → calls `matching_service.determine_best_pro()`
- If `city` or `issue` missing → sends a clarifying reply, waits for next message
- If pro found → calls Phase 2 immediately
- If no pro found after all radius steps → escalates lead to `PENDING_ADMIN_REVIEW`, sends `Messages.Customer.PENDING_REVIEW`

### Phase 2: Pro Persona

Activated when a pro is assigned (either from Phase 1 or an existing `pro_id` on the lead).

- Builds a system prompt from the pro's: `business_name`, `price_list`, `system_prompt`, `social_proof`
- Follows a structured conversation flow:
  1. Introduce pro and service
  2. Ask for photo/video of the issue (skipped if media already received)
  3. Provide estimate based on price list
  4. Confirm appointment time and full 5-part address (street, number, city, floor, apt)
  5. Close deal when all details are confirmed (`is_deal=True`)
- Token usage is incremented on the pro's `users` document after each call (`$inc total_tokens_used`) as a non-blocking background task

### Phase Skip Optimization

If the customer's active lead already has an assigned `pro_id`, Phase 1 (Dispatcher) is skipped entirely and the message goes directly to Phase 2. This eliminates a full AI call per message during ongoing conversations.

---

## 3. Deal Finalization (`_finalize_deal`)

When Phase 2 returns `is_deal=True` (or the lead is flagged `is_emergency` and a pro has already been matched — PRO-121 finalizes on the spot, without waiting for a `[DEAL]` marker):

1. Create or update lead with `status=new`, `pro_id`, `full_address` (composed), `street`, `street_number`, `city`, `floor`, `apartment`, `issue_type`, `appointment_time`, `appointment_datetime` (parsed to a BSON UTC date via `parse_iso_to_utc`), `media_url`
2. Set customer state to `AWAITING_PRO_APPROVAL` (customer sees a soft-hold message on next message)
3. Send customer `Messages.Customer.AWAITING_APPROVAL`
4. Send pro a text-based approval request (e.g., "Reply 'אשר' or '1' to approve")

If the customer is themselves a registered pro, they are **not** returned to `PRO_MODE` here — they stay on the customer side until their own lead closes, so follow-ups like "מתי הוא מגיע?" aren't answered with the pro dashboard. The ways back are a pro business keyword (Safety Bypass) or the IDLE auto-detect once the lead is done.

---

## 4. Customer State Machine (Key States)

| State | Trigger | Bot behavior |
|-------|---------|-------------|
| `IDLE` | Default | Full AI flow runs |
| `PRO_MODE` | Sender is an active pro | `pro_flow.handle_pro_text_command()` called |
| `CUSTOMER_MODE` | Pro types `לקוח`, or confirms the intent prompt | Pro treated as customer, context cleared; sticky while their own lead is open |
| `AWAITING_INTENT_CONFIRMATION` | AI detects pro needs service | Prompt pro to switch modes; `1`/`כן` accepts, `2`/`לא` declines, anything else re-prompts once |
| `AWAITING_PRO_APPROVAL` | Deal sent to pro | Bot replies with STILL_WAITING, no AI — unless the sender is a pro using an unambiguous business keyword, or an emergency keyword arrives (PRO-121: `EMERGENCY_WHILE_WAITING`, state kept, once per lead) |
| `PAUSED_FOR_HUMAN` | Pro/customer pauses bot | Messages logged, 15m rolling TTL resets. An emergency keyword here flags the lead and pages the operator once, without un-pausing or replying (PRO-121) |
| `AWAITING_ADDRESS` | Address missing parts | AI re-extracts parts from next message, state cleared if complete. An emergency keyword with a known city releases the gate early instead (PRO-121) |

### SOS / Human Handoff

When the customer sends a trigger phrase (e.g., "אני צריך נציג"):

1. Customer state set to `PAUSED_FOR_HUMAN` with `ttl=900` (15 min)
2. `send_sos_alert()` sends alerts to both the assigned pro (if any) and admin
3. Customer receives `Messages.Customer.BOT_PAUSED_BY_CUSTOMER`
4. All subsequent messages reset the 15-minute TTL.
5. If 15 minutes of silence pass, the **SLA Monitor** triggers `SLA_DEFLECTION_MESSAGE` (PRO-73: business hours + `sla_monitor_active` toggle, default OFF).

### Edge-Case Bailouts

| Scenario | Trigger | Outcome |
|----------|---------|---------|
| **Address cancellation** | Customer sends "לא משנה" / "ביטול" while in `AWAITING_ADDRESS` | State + Redis context cleared; customer returned to normal flow |
| **Silent media (image-only)** | Customer sends an image with no text | Treated as service intent; media collected and dispatcher proceeds normally |
| **Empty radius (30 km)** | No pro found after all three geo radius steps | State cleared, lead escalated to `PENDING_ADMIN_REVIEW`, customer notified |
| **Fat finger guard** | Pro replies to a lead they already replied to within 5 minutes | Second reply silently ignored to prevent double-action |

### Customer Name Capture

The Dispatcher AI extracts and stores `customer_name` from the conversation. The name is included in the approval notification sent to the pro so they know who they're speaking with before accepting the lead.

---

## 5. SOS Auto-Recovery ("The Healer")

Runs every 10 minutes via APScheduler. PRO-73: gated to business hours (08:00–21:00 IL) and the `sos_healer_active` toggle (default OFF).

Queries leads with status `new` or `contacted` older than `WorkerConstants.SOS_TIMEOUT_MINUTES` (60 min). `pending_admin_review` is excluded — it is a terminal state for the Healer, already handed to a human.

For each stale lead:

```
reassignment_count >= MAX_REASSIGNMENTS (3)?
    │      → status = PENDING_ADMIN_REVIEW, alert admin, notify customer
    │        (a human takes over; the lead is not closed — PRO-63)
    │
notify customer (CUSTOMER_REASSIGNING)
    │
    ├─ determine_best_pro() returns a pro?
    │      → update lead (new pro_id, reset created_at, increment reassignment_count)
    │        notify new pro
    │        notify old pro (lost lead)
    │        clear customer state
    │
    └─ no pro found?
           → status = PENDING_ADMIN_REVIEW
             notify customer (PENDING_REVIEW)
             clear context
```

---

## 6. Scheduling Jobs

All jobs run inside the Worker process via APScheduler.

| Job | Schedule | What it does |
|-----|----------|-------------|
| Daily agendas | 08:00 IL (daily) | Sends each pro a list of their booked jobs for the day, keyed on `appointment_datetime`; leads without a resolved `appointment_datetime` (e.g. ASAP) are not included |
| Stale monitor | Every 30 min | Tier 1 (4–6 h): reminder to pro (capped at `MAX_PRO_REMINDERS`). Tier 2 (6–24 h): completion check to customer (capped at `MAX_CUSTOMER_COMPLETION_CHECKS`, min `CUSTOMER_COMPLETION_CHECK_COOLDOWN_HOURS` apart — the lead stays BOOKED inside the window, so without the cap every tick re-sent). Tier 3 (>24 h): flag for admin |
| SOS Healer | Every 10 min | Reassigns stuck leads or escalates to `PENDING_ADMIN_REVIEW`. PRO-73: gated to business hours (08:00–21:00 IL) + `sos_healer_active` toggle (default OFF) |
| SLA Monitor | Every 5 min | Wakes up silent `PAUSED_FOR_HUMAN` chats after 15m; offers phone call. PRO-73: gated to business hours (08:00–21:00 IL) + `sla_monitor_active` toggle (default OFF) |
| Pro-Approval SLA | Every 5 min | Nudges a silent pro at T+10m, then offers the customer a reassignment at T+25m (half thresholds for emergency leads); the reassignment offer is gated to business hours (PRO-73), the pro nudge is not |
| SOS Reporter | Every 4 h | Pages operator once per newly-stuck lead (muted for `SOS_REPORT_REPAGE_HOURS`/24h after paging); the standing already-paged backlog is logged, not re-paged |
| Stale Lead Nudger | Every 4 h | Reminds pros to close booked leads older than 24 h |
| Lead Janitor | Every 6 h | Closes `CONTACTED` leads with no assigned pro after 24 h. PRO-73: gated to business hours (08:00–21:00 IL) + `lead_janitor_active` toggle (default OFF) |
| Slot Regeneration | Sunday 01:00 IL | Generates appointment slots from recurring weekly templates |
| Daily Backup | 02:00 IL (daily), production only (PRO-127) | Creates gzipped `mongodump`; uploads to S3 if configured |
| WhatsApp Deauth Watchdog | Every 2 min | Polls the WhatsApp provider's account state (skipped for a non-transmitting provider); pages on-call if non-authorized > 5 min |

Job toggles are controlled via MongoDB `settings_collection` document `{"_id": "scheduler_config"}` with fields `sos_healer_active`, `sos_reporter_active`, `stale_monitor_active`, `lead_janitor_active`, `sla_monitor_active`. The last two, plus `sos_healer_active`, gate cold customer-facing re-engagement jobs and default OFF (pilot safety, PRO-73) until enabled post warm-up.

---

## 7. Context Management

- Chat history stored in Redis per `chat_id` (last 20 messages, 4 h TTL)
- AI calls receive the last **5 turns** (10 messages) after trimming — centralized in `ai_engine_service.analyze_conversation`
- Context is cleared (`ContextManager.clear_context`) when:
  - Lead is completed
  - A pro's reject fails to find a replacement and the lead is escalated to `PENDING_ADMIN_REVIEW` (PRO-117); a *successful* reject → rematch deliberately keeps context — the conversation continues with the new pro
  - Max reassignments reached (lead escalated to `PENDING_ADMIN_REVIEW`)
  - Lead escalated to `PENDING_ADMIN_REVIEW` (healer path)
  - Customer resets with "התחלה" / "reset"

---

## 8. PII and Privacy

- **Log masking:** All log sinks (stdout + file) apply a regex filter that masks Israeli phone numbers: `972521234567` → `97252****567` (country code + first 2 digits + last 3 digits preserved). Applied unconditionally in all environments.
- **Consent gate:** `has_consent(chat_id)` is checked before processing customer messages. Non-consented users receive a privacy notice.
- **Data management:** `data_management_service.py` provides GDPR-compliant data export and user deletion.
