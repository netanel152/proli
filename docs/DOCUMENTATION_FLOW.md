# Proli — Logic Flow & Lead Lifecycle

## 1. Lead Lifecycle

```
CONTACTED → NEW → BOOKED → COMPLETED → CLOSED
              ↓       ↓
          REJECTED  CANCELLED
              ↓
    PENDING_ADMIN_REVIEW
```

| Status | When set | Who sets it |
|--------|----------|------------|
| `contacted` | AI extracts city + issue, opens conversation | Dispatcher (Phase 1) |
| `new` | Pro matched and approval message sent | `_finalize_deal` |
| `booked` | Pro approves via text ("אשר"/"1") | `_handle_approve` in `pro_flow.py` |
| `completed` | Pro or customer confirms work done | `customer_flow.py` |
| `rejected` | Pro rejects via text ("דחה"/"2") | `_handle_reject` in `pro_flow.py` |
| `cancelled` | Customer cancels | `pro_flow.py` |
| `closed` | Max reassignments reached | `monitor_service.py` |
| `pending_admin_review` | No replacement pro found at any radius | `monitor_service.py` / `workflow_service.py` |

---

## 2. AI Engine — Two-Phase Architecture

### Phase 1: Dispatcher

Activated when no pro is assigned to the active lead.

- Receives the last **5 conversational turns** (10 messages max — centrally trimmed in `ai_engine_service.py`)
- Extracts `city`, `issue`, `full_address`, `appointment_time` from the conversation
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
  4. Confirm appointment time and full address
  5. Close deal when all details are confirmed (`is_deal=True`)
- Token usage is incremented on the pro's `users` document after each call (`$inc total_tokens_used`) as a non-blocking background task

### Phase Skip Optimization

If the customer's active lead already has an assigned `pro_id`, Phase 1 (Dispatcher) is skipped entirely and the message goes directly to Phase 2. This eliminates a full AI call per message during ongoing conversations.

---

## 3. Deal Finalization (`_finalize_deal`)

When Phase 2 returns `is_deal=True`:

1. Create or update lead with `status=new`, `pro_id`, `full_address`, `issue_type`, `appointment_time`, `media_url`
2. Set customer state to `AWAITING_PRO_APPROVAL` (customer sees a soft-hold message on next message)
3. Send customer `Messages.Customer.AWAITING_APPROVAL`
4. Send pro a text-based approval request (e.g., "Reply 'אשר' or '1' to approve")

---

## 4. Customer State Machine (Key States)

| State | Trigger | Bot behavior |
|-------|---------|-------------|
| `IDLE` | Default | Full AI flow runs |
| `PRO_MODE` | Sender is an active pro | `pro_flow.handle_pro_text_command()` called |
| `CUSTOMER_MODE` | Pro needs a service (Zero-Touch) | Pro treated as customer, context cleared |
| `AWAITING_INTENT_CONFIRMATION` | AI detects pro needs service | Prompt pro to switch modes |
| `AWAITING_PRO_APPROVAL` | Deal sent to pro | Bot replies with STILL_WAITING, no AI |
| `PAUSED_FOR_HUMAN` | Pro/customer pauses bot | Messages logged, 15m rolling TTL resets |
| `AWAITING_ADDRESS` | Address needed | Next message saved as address, state cleared |

### SOS / Human Handoff

When the customer sends a trigger phrase (e.g., "אני צריך נציג"):

1. Customer state set to `PAUSED_FOR_HUMAN` with `ttl=900` (15 min)
2. `send_sos_alert()` sends alerts to both the assigned pro (if any) and admin
3. Customer receives `Messages.Customer.BOT_PAUSED_BY_CUSTOMER`
4. All subsequent messages reset the 15-minute TTL.
5. If 15 minutes of silence pass, the **SLA Monitor** triggers `SLA_DEFLECTION_MESSAGE`.

---

## 5. SOS Auto-Recovery ("The Healer")

Runs every 10 minutes via APScheduler.

Queries leads with status `new`, `contacted`, or `pending_admin_review` older than `WorkerConstants.SOS_TIMEOUT_MINUTES` (60 min).

For each stale lead:

```
notify customer (CUSTOMER_REASSIGNING)
    │
    ├─ reassignment_count >= MAX_REASSIGNMENTS (3)?
    │      → status = CLOSED, notify customer, clear context
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
| Daily agendas | 08:00 IL (daily) | Sends each pro a list of their booked jobs for the day |
| Stale monitor | Every 30 min | Tier 1 (4–6 h): reminder to pro. Tier 2 (6–24 h): completion check to customer. Tier 3 (>24 h): flag for admin |
| SOS Healer | Every 10 min | Reassigns stuck leads or escalates to `PENDING_ADMIN_REVIEW` |
| SOS Reporter | Every 4 h | Sends batched admin report of all still-stuck leads |
| Lead Janitor | Every 6 h | Closes `CONTACTED` leads with no assigned pro after 24 h |
| Slot Regeneration | Sunday 01:00 IL | Generates appointment slots from recurring weekly templates |

Job toggles are controlled via MongoDB `settings_collection` document `{"_id": "scheduler_config"}` with fields `sos_healer_active`, `sos_reporter_active`, `stale_monitor_active`.

---

## 7. Context Management

- Chat history stored in Redis per `chat_id` (last 20 messages, 4 h TTL)
- AI calls receive the last **5 turns** (10 messages) after trimming — centralized in `ai_engine_service.analyze_conversation`
- Context is cleared (`ContextManager.clear_context`) when:
  - Lead is completed or rejected
  - Max reassignments reached (lead closed)
  - Lead escalated to `PENDING_ADMIN_REVIEW` (healer path)
  - Customer resets with "התחלה" / "תפריט"

---

## 8. PII and Privacy

- **Log masking:** All log sinks (stdout + file) apply a regex filter that masks Israeli phone numbers: `972521234567` → `97252****567` (country code + first 2 digits + last 3 digits preserved). Applied unconditionally in all environments.
- **Consent gate:** `has_consent(chat_id)` is checked before processing customer messages. Non-consented users receive a privacy notice.
- **Data management:** `data_management_service.py` provides GDPR-compliant data export and user deletion.
