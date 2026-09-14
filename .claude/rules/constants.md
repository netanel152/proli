---
paths:
  - "app/core/constants.py"
  - "app/scheduler.py"
  - "app/services/**"
---

# Key constants (`app/core/constants.py`)

Loaded when `constants.py`, the scheduler, or a service is read. Every value below is
owned by `app/core/constants.py` — change the code first, then this file, in the same
PR. `tests/test_agent_pack_drift.py` pins the copies embedded in `.claude/agents/`; this
file is audited by the docs-syncer subagent.

### Key Constants (`app/core/constants.py`)

- `LeadStatus`: `contacted → new → booked → completed/rejected/closed/cancelled/pending_admin_review`
- `UserStates`: `IDLE`, `PRO_MODE`, `CUSTOMER_MODE`, `AWAITING_INTENT_CONFIRMATION`, `AWAITING_ADDRESS`, `AWAITING_CONSENT`, `AWAITING_PRO_APPROVAL`, `PAUSED_FOR_HUMAN`, `AWAITING_RESCHEDULE_TIME`, `AWAITING_LOYALTY_CONFIRMATION`, `AWAITING_NEW_OR_EXISTING`, `AWAITING_CANCEL_CONFIRMATION`, `PRO_SELECTING_JOB_TO_FINISH`, `PRO_SELECTING_JOB_TO_CANCEL`, `PRO_AWAITING_FINAL_PRICE`, `ONBOARDING_*`, `ADMIN_SELECTING_LEAD`, `ADMIN_SELECTING_ACTION`, `ADMIN_SELECTING_PRO`
- `WorkerConstants.MAX_PRO_LOAD = 3`: max concurrent leads per professional
- `WorkerConstants.MAX_CUSTOMER_COMPLETION_CHECKS = 2` / `CUSTOMER_COMPLETION_CHECK_COOLDOWN_HOURS = 6`: cap and cooldown on the "did the job finish?" nudge sent to a customer for one booked lead — the customer-side mirror of `MAX_PRO_REMINDERS`. Per *booking*, not per lead: `reassign_lead` clears `completion_check_sent_at`/`completion_check_sent_count` when the lead gets a fresh owner (PRO-45), so a reassigned lead gets a fresh nudge cycle per pro. The stale-job monitor re-runs every 30 min and a lead stays BOOKED (and therefore inside the 6–24h Tier-2 window) until somebody answers, so without these the check re-sent once per open lead on every tick. The predicate is `customer_flow.completion_check_due_filter`, applied both in the Tier-2 query and again inside `send_customer_completion_check`'s atomic `find_one_and_update` claim, so two worker replicas can't both win
- `WorkerConstants.MAX_RATING_REPROMPTS = 2`: how many times an unparseable reply to the 1-5 rating prompt is re-prompted before `waiting_for_rating` is released and the message reaches the dispatcher untouched (PRO-122)
- `WorkerConstants.RATING_PROMPT_MAX_AGE_HOURS = 48`: how long the 1-5 rating prompt stays live; `waiting_for_rating` has no other expiry, so without this window a prompt ignored months ago could still capture the next bare digit the customer typed (PRO-122)
- `WorkerConstants.NO_SHOW_REPORT_MAX_AGE_HOURS = 48`: how long the completion check stays answerable by option *3* ("איש המקצוע לא הגיע"); `completion_check_sent_at` never expires on its own, so without this window a stray "3" from that chat could be read as a no-show report for the life of the booking (PRO-45)
- `WorkerConstants.SOS_TIMEOUT_MINUTES = 60`: reassignment trigger threshold
- `WorkerConstants.SOS_REPORT_REPAGE_HOURS = 24`: how long a stuck lead stays quiet after the SOS Reporter (`monitor_service.send_periodic_admin_report`, every 4h) has paged the operator about it once — without this, a single unresolvable lead paged CRITICAL on every tick forever (PRO-162). The predicate is `monitor_service.stuck_lead_report_due_filter`, applied both in the Reporter's query and again inside its atomic `find_one_and_update` claim (stamping `admin_reported_at`), so two worker replicas can't both page the same lead; the standing already-paged backlog is logged, not re-paged
- `WorkerConstants.STALE_BOOKED_LEAD_HOURS = 24`: threshold for stale job reminders
- `WorkerConstants.GEO_RADIUS_STEPS = [10000, 20000, 30000]`: progressive geo search radii
- `WorkerConstants.PAUSE_TTL_SECONDS = 900`: 15-minute rolling TTL for PAUSED_FOR_HUMAN state
- `WorkerConstants.CANCEL_CONFIRM_TTL_SECONDS = 300`: 5-minute window for a customer to confirm a cancel keyword on a BOOKED job (`AWAITING_CANCEL_CONFIRMATION`, PRO-118); expiry leaves the job booked
- `WorkerConstants.LOYALTY_CONFIRM_TTL_SECONDS = 300`: 5-minute window for a customer to answer the "want your previous pro?" offer (`AWAITING_LOYALTY_CONFIRMATION`, PRO-119); expiry releases to normal routing instead of the old unbounded 4h default
- `WorkerConstants.NEW_OR_EXISTING_TTL_SECONDS = 300`: 5-minute window for a customer with a BOOKED lead to answer the "new request or about the existing job?" gate (`AWAITING_NEW_OR_EXISTING`, PRO-116/PRO-193); expiry releases to normal routing, and the gate is one-shot (`new_request_prompted` never re-arms) so a lapsed reply is routed as a new request rather than re-asked
- `WorkerConstants.PRO_SEARCH_RATE_LIMIT_SECONDS = 600`: 10-minute per-pro cool-down on the `מצא` proactive stuck-lead search
- `WorkerConstants.PRO_LIST_PAGE_SIZE = 10`: rows per page in the pro's job lists (`עבודות`/`פרטים` and the `סיימתי`/`ביטול` selection prompts); a longer list states how many rows it is showing out of how many, with `עוד` for the next page (PRO-147)
- `WorkerConstants.COMMISSION_RATE = 0.10`: platform take-rate applied to a recorded `final_price` → `commission_amount` (PRO-33; GMV/commission surfaced in the admin analytics Revenue tab)
- `WorkerConstants.FINAL_PRICE_TTL_SECONDS = 600`: 10-minute window for the pro to answer the post-completion "how much did you charge?" prompt (skippable, never gates COMPLETED)
- `WorkerConstants.APPROVAL_NUDGE_MINUTES = 10`: nudge a silent pro this long after a lead was offered (emergency leads: half)
- `WorkerConstants.APPROVAL_REASSIGN_OFFER_MINUTES = 25`: offer the customer a reassignment if the pro is still silent (emergency leads: half)
- `WorkerConstants.APPROVAL_SLA_CHECK_INTERVAL_MINUTES = 5`: how often the pro-approval SLA scheduler job runs
- `WorkerConstants.INBOUND_RATE_LIMIT_MAX = 20` / `INBOUND_RATE_LIMIT_WINDOW_SECONDS = 60`: per-customer inbound sliding-window limit (pros/admins exempt)
- `WorkerConstants.DAILY_AI_CALL_CAP = 40`: per-chat daily ceiling on Gemini/multimodal calls (resets at Israel-time midnight)
- `WorkerConstants.RATE_LIMIT_ABUSE_TRIP_THRESHOLD = 3`: repeated trips within a window escalate from `logger.warning` to `logger.error` (Sentry)
- `WorkerConstants.WA_STATE_CHECK_INTERVAL_MINUTES = 2`: how often the worker polls the WhatsApp provider's account state (`get_state`)
- `WorkerConstants.WA_STATE_ALERT_THRESHOLD_MINUTES = 5`: page on-call only after the instance has been non-authorized > this many minutes
- `WorkerConstants.WA_STATE_REALERT_MINUTES = 60`: re-page interval while the instance stays deauthorized
- `WorkerConstants.WA_STATE_PAUSE_TTL_SECONDS = 360`: TTL on the `wa:instance:paused` outbound-halt key; auto-releases if the monitor dies
- `WorkerConstants.WA_STATE_CONFIRM_TTL_SECONDS = 360`: TTL on `wa:instance:state`, the positive confirmation a probe found the account authorized; the outbound facade fails **closed** (blocks sending) once this key is absent (PRO-82/PRO-86)
- `WorkerConstants.PENDING_REVIEW_SHORTCIRCUIT_HOURS = 24`: how long a PENDING_ADMIN_REVIEW lead short-circuits the customer's chat before their next message proceeds to the normal dispatcher (PRO-63)
- `WorkerConstants.SCHEDULER_MONGO_AUTH_TRIP_THRESHOLD = 3`: Mongo auth failures within the rolling window before a scheduler job pages CRITICAL (PRO-112)
- `WorkerConstants.SCHEDULER_MONGO_AUTH_WINDOW_SECONDS = 1800`: 30-minute rolling window for counting Mongo auth failures across scheduler jobs
- `WorkerConstants.SCHEDULER_MONGO_AUTH_REALERT_SECONDS = 3600`: re-page interval while scheduler jobs keep hitting Mongo auth failures
- `WorkerConstants.STALE_LEAD_REMINDER_COOLDOWN_HOURS = 4`: minimum time between reminders on the same lead, so the nudger's PRO-176 boot run can't burn all `MAX_PRO_REMINDERS` across a few quick deploys
- `WorkerConstants.SCHEDULER_BOOT_RUN_DELAY_SECONDS = 60`: delay before the first boot run of a long-interval scheduler job, to let boot-time Mongo/Redis probes settle (PRO-176)
- `WorkerConstants.SCHEDULER_BOOT_RUN_STAGGER_SECONDS = 45`: spacing between each long-interval job's boot run so they don't all fire in the same second (PRO-176)
- `WorkerConstants.SCHEDULER_LONG_JOB_MISFIRE_GRACE_SECONDS = 600`: misfire grace time on the long-interval scheduler jobs, so a tick that comes due while the loop is busy runs late instead of being dropped (PRO-176)
- `WorkerConstants.BACKUP_MAX_AGE_HOURS = 48`: how stale the last recorded success must be before the freshness watchdog pages — the other half of PRO-111's failure-triggered escalation, covering paths where the backup job never runs at all (PRO-185). `run_daily_backup` records success in two places on a zero exit: `backup:last_success` (no TTL) in Redis, the primary, and a durable mirror in Mongo (`settings.backup_state`) that the watchdog and `/health` fall back to when Redis's key is absent or stale — a no-TTL, rarely-read key is exactly what `allkeys-lru` eviction reaps first, and the two writes are independent fail-open blocks so Redis can lag the mirror
- `WorkerConstants.BACKUP_WATCHDOG_INTERVAL_MINUTES = 60`: how often `run_backup_freshness_watchdog` runs (production only; also fires once shortly after boot via the PRO-176 boot-run slot)
- `WorkerConstants.BACKUP_STALE_REALERT_HOURS = 24`: re-page cadence while backups stay stale, deduped via `backup:stale_alert` (SET NX EX)
- `WorkerConstants.BACKUP_CLOCK_SKEW_TOLERANCE_SECONDS = 300`: how far `backup:last_success` may read in the future (worker clock writes it, API/worker clocks read it) before the watchdog/`/health` treat it as unusable instead of skew
- `ISRAEL_CITIES_COORDS`: static dict mapping Hebrew/English city names to `[lon, lat]` for geo queries
