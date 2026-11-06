# WhatsApp Template Catalog (PRO-88)

**Status:** inventory complete, template bodies written, send path wired. Nothing here has been submitted to Meta. The machine-readable half of this catalog is `app/providers/whatsapp/template_registry.py` (PRO-89), with every entry `DRAFT`. The bodies live in `Messages.Templates` (`app/core/messages.py`), the one home for copy. Each `TemplateSpec` points at its body, declares its ordered `params` and its category, and carries a `source` (PRO-168) naming the free-form `Messages.*` constant it was worded from.

**Flipping an entry to `APPROVED`**, with the exact name Meta approved, arms it for every call site that passes `template_key` to the facade's `send_message`. Until then those sends behave exactly as they did before the path existed. The admin panel's sends (P13 and C2's operator button) do not pass one yet, so approving those templates changes nothing for them. See [What is wired](#what-is-wired).

## What this document is

Every outbound WhatsApp message Proli sends, classified by whether Meta Cloud API will let us send it as free-form text or will require a **pre-approved template**. This is the input to PRO-89 (`CloudAPIProvider`), and the reason it exists separately: the number of templates we need is a product decision with a review latency attached, and discovering it *during* the Cloud API implementation would stall that ticket.

Scope boundary: this is the inventory and the template design. PRO-87 (the Business Portfolio and the number) is done; **approval is PRO-150's**. `scripts/submit_whatsapp_templates.py` does the submitting: by default it only prints what it would submit, `--status --waba-id <id>` reports Meta's status and any drift from the registry, and `--apply --waba-id <id> --confirm-name "<account name>"` submits, and refuses an account whose name contains "Test" or a name that does not match. The WABA configured today, `1910054379665469`, is Meta's test account, so no real submission can happen against it.

> **Meta's rules, as read on 2026-11-06.** From `developers.facebook.com/documentation/business-messaging/whatsapp/templates/`: `template-review/` (no variable at the start or end, sequential `{{n}}`, no `#`, `$` or `%` in a variable, a sample for every variable, no duplicate wording, not too many variables for the length), `components/` and `utility-templates/` (body maximum 1024 characters), `overview/` (positional format, `example.body_text`, 100 creations an hour), `template-categorization/` (UTILITY must be non-promotional and specific to the user; mixed or unclear content is treated as MARKETING; a recategorised template stays APPROVED and only its category changes, which is why `--status` compares categories). `tests/test_template_bodies.py` holds every body to these. Three things those pages do **not** settle, and the code assumes conservatively: "no adjacent variables" is not on the review page as read and is enforced anyway; the variables-to-length ratio has no published number, so the floor in `template_registry.py` is Proli's own; and nothing says whether a parameter value may hold a newline, so `sanitize_param` flattens newlines, tabs and runs of 4+ spaces. An emoji limit is mentioned with no number, so none is checked.

## The rule that drives everything

Free-form messages are only deliverable inside the **24-hour customer service window** — the 24 hours following that recipient's most recent inbound message. Outside it, only a pre-approved template can be delivered.

**The window is per recipient, not per conversation.** This is the single most important consequence for Proli, and it is not obvious from the code:

Proli has two recipient classes — **customers** and **professionals** — and almost every message to a professional is triggered by *a customer's* action or *a scheduler*, never by the professional's own inbound message. A pro who has not texted the bot in three days has a closed window. The customer's activity does nothing to reopen it.

**The core product loop is therefore business-initiated.** A customer describes a problem, we match a pro, and we send that pro a lead offer. That offer is the product. Under Cloud API it requires an approved template, or it does not arrive.

This is the finding that justifies PRO-88 existing as its own ticket.

---

## Inventory: business-initiated sends (template required)

Ordered by how badly the product breaks if the template is missing or rejected.

### Professional-facing

| # | Send | Call site | Trigger | Notes |
|---|---|---|---|---|
| **P1** | **Lead offer** (`APPROVAL_REQUEST`) | `workflow_service.py:1031` | Customer completes intake | **The product.** Interpolates customer name, address, issue type, appointment time, price line, and a media-links block. **PRO-59** dropped the customer's phone and the floor/apartment line from this send — both now arrive only in the post-approval contact card (`Pro.CONTACT_CARD`, sent from `pro_flow._handle_approve`, not a business-initiated send). |
| **P2** | ~~Navigation link (`NAVIGATE_TO`)~~ | — | — | **Removed by PRO-59, not folded.** Neither pre-approval path (`workflow_service`'s initial offer or `notification_service.notify_pro_new_lead`) sends a navigation link any more — a map pin resolves the exact address the offer now withholds until approval. The waze line moved into the post-approval `Pro.CONTACT_CARD` instead, which needs no template of its own (it replies inside the 24h window the pro's own *אשר* just opened). One fewer template to submit, one fewer billable send. |
| **P3** | Lead offer, reassignment + admin-assignment path | `notification_service.py:213` (`notify_pro_new_lead`) | `monitor_service.reassign_lead`, `admin_flow` assignment | Shares the builder with P1 but uses the leaner `NEW_LEAD_*` templates. Since PRO-59 both templates omit the phone and floor/apartment, so the variable sets are closer than they used to be, not identical — P1 also carries `price_line` and optional emergency/loyalty headers P3 never had. |
| **P4** | Early-lead notification (`EARLY_LEAD_*`) | `conversation_pipeline.py:694–699` | Customer mid-intake | **Sends media** via `send_file_by_url` when a photo exists, text otherwise. A media-header template is a different structure from a text template — this needs two templates or a policy change. |
| **P5** | Approval nudge (`APPROVAL_NUDGE`) | `monitor_service.py:688` | Scheduler, T+10 min of pro silence | Sent precisely because the pro is *not* engaging, so assuming an open window is exactly backwards. |
| **P6** | Daily agenda | `scheduler.py:76` | Cron 08:00 Israel time | Per-pro job list. Classic UTILITY-category daily digest. |
| **P7** | Stale booked-lead reminder (`STALE_LEAD_REMINDER`) | `monitor_service.py:825` | Scheduler, every 4h, lead ≥24h old | The 24h staleness threshold guarantees the window is closed. |
| **P8** | Finish reminder (`Pro.REMINDER`) | `notification_service.send_pro_reminder` | Stale-job monitor | Capped at `MAX_PRO_REMINDERS`. |
| **P9** | Lead lost on reassignment (`PRO_LOST_LEAD`) | `monitor_service.py:248` | Reassignment | Skipped when the reassignment came from a pro's own reject (PRO-117 `notify_old_pro=False`) — that pro already got the reject acknowledgement. |
| **P10** | Bot paused (`PAUSE_NOTIFICATION`) | `workflow_service.py:460` | Customer triggered SOS | |
| **P11** | SOS alert (`SOS.PRO_ALERT` / `SOS.PRO_ALERT_PENDING`) | `notification_service.send_sos_alert` | Customer distress | Time-critical; a rejected template here is a safety regression. **PRO-59**: the phone-bearing `PRO_ALERT` sends only when the lead is BOOKED; a NEW/CONTACTED lead's pro (offered, not yet approved) gets the phone-free `PRO_ALERT_PENDING` instead — two template bodies, same trigger, so two registry entries (`sos_alert`, `sos_alert_pending`). |
| **P12** | Customer cancelled (`CUSTOMER_CANCELLED`) | `workflow_service` | Customer cancels | |
| **P13** | Onboarding approved / rejected | `professionals.py:486, 497` | **Operator clicks a button in the admin panel** | Arbitrary delay after the pro's registration — hours or days. Assume closed. Two bodies, so two registry entries (`onboarding_approved`, `onboarding_rejected`). |

### Customer-facing

| # | Send | Call site | Trigger | Notes |
|---|---|---|---|---|
| **C1** | No pro available (`NO_PRO_AVAILABLE`) | `monitor_service.py:378` | Lead janitor, every 6h | Lead can be arbitrarily old. Assume closed. |
| **C2** | Completion check | `admin_panel/core/utils.py:190` (operator) · `customer_flow.py:send_customer_completion_check` (scheduler) | **Operator clicks a button**, or stale-job monitor Tier 2 | Arbitrary timing by construction. Capped at `MAX_CUSTOMER_COMPLETION_CHECKS` per booking (PRO-45: `reassign_lead` resets the counter for a fresh owner) with a `CUSTOMER_COMPLETION_CHECK_COOLDOWN_HOURS` gap; an operator send bypasses the cap but still restarts the cooldown. |
| **C3** | Reassignment notices (`CUSTOMER_REASSIGNING`, `MAX_REASSIGNMENTS_REACHED`, `PENDING_REVIEW`) | `monitor_service.py:180, 129, 284` | `SOS_TIMEOUT_MINUTES=60` path → in-window; `STALE_BOOKED_LEAD_HOURS=24` path → **on the boundary**; PRO-117 pro-reject rematch failure → also in-window | Same code, multiple triggers (including the PRO-117 reject-rematch fallback), different window answers. Must be treated as template-required. Three bodies, so three registry entries (`reassigning`, `max_reassignments`, `pending_review`). |

### Operator-facing — ✅ **resolved, no templates needed**

| # | Send | Call site | Status |
|---|---|---|---|
| **O1** | SOS admin alert | `notification_service.send_sos_alert` | ✅ **Removed** — pages via `page_operator` |
| **O2** | Periodic admin report | `monitor_service.send_periodic_admin_report` | ✅ **Removed** — pages via `page_operator` |
| **O3** | Lead-escalation admin alert | `monitor_service._alert_admin_lead_escalated` | ✅ **Removed** — pages via `page_operator` |
| **O4** | On-call page | `notification_service.send_oncall_alert` | Already Sentry-first (PRO-75); WhatsApp is a courtesy leg, kept |

The admin never sends the bot an inbound message, so **every operator-facing send had a permanently closed window** — each would have needed its own approved template to keep working. PRO-75 had already made Sentry → email the guaranteed operator page precisely because alerting about WhatsApp over WhatsApp amplifies an outage.

O1–O3 now route through `notification_service.page_operator()` — a single `page_critical` → Sentry → email. **Four templates removed from this catalog at zero cost to the operator's signal.**

Two properties worth keeping in mind when reading those call sites:

- **Every page masks the customer phone to its last 4 digits** and carries a lead id rather than the customer record. These land in Sentry, which retains events, and the loguru PII filter runs in the *sink* — it is not guaranteed to have applied on whatever path an event takes to Sentry. Masking at the call site does not depend on that.
- **The SOS page deliberately omits the customer's message.** It is free-form text from a distressed person and can contain anything; it used to go to a chat the operator read once, and would now be retained.

One behaviour change worth knowing: an SOS with no assigned pro now produces **zero** outbound WhatsApp messages. The signal is not lost — the operator is paged — but nothing leaves the system.

---

## Safe without templates

Everything reached from the inbound dispatcher — `workflow_service` replies, `customer_flow`, `pro_flow`, `admin_flow`, and `pro_onboarding_service` — is a direct response to a message the recipient just sent. That is inside the window by construction. It is also the bulk of the 131 call sites, which is why the template count is manageable.

Two scheduler jobs are also safe, and it is worth recording *why* rather than rediscovering it:

- **SLA deflection** (`check_sla_deflection`, 5-min interval) fires on ~15 minutes of customer silence — always well inside 24h.
- **Customer reassignment offer** (`check_pro_approval_sla`, `APPROVAL_REASSIGN_OFFER_MINUTES=25`) fires ~25 minutes after the customer's intake.

Both are gated to business hours by PRO-73, which narrows them further.

---

## What is wired

A call site passes its free-form text plus `template_key` and named `params` to `WhatsAppFacade.send_message`. Open window: the text goes out as ever. Closed window and template `APPROVED`: the template goes out, with `params` in the order the spec declares. Closed window and not approved: the operator page plus `ServiceWindowClosedError`, which the facade turns into `None`, exactly as before. It stays behind the breaker and the kill switch, and `DryRunProvider` always records the text.

| Row | Registry key | Variables | Body | Call site |
|---|---|---|---|---|
| P1 | `lead_offer` | `customer_name`, `full_address`, `issue_type`, `appointment_time`, `price_estimate`, `urgency` | `LEAD_OFFER` | `workflow_service._finalize_deal` |
| P3 | `lead_offer_reassigned` | `customer_name`, `full_address`, `issue_type`, `appointment_time` | `LEAD_OFFER_REASSIGNED` | `notification_service.notify_pro_new_lead` (an emergency takes `lead_offer`) |
| P5 | `approval_nudge` | `issue_type`, `minutes` | `APPROVAL_NUDGE` | `monitor_service.check_pro_approval_sla` |
| P6 | `daily_agenda` | `pro_name`, `date`, `job_count` | `DAILY_AGENDA` | `scheduler.send_daily_reminders` |
| P7 | `stale_lead_reminder` | `pro_name`, `customer_name` | `STALE_LEAD_REMINDER` | `monitor_service.remind_stale_booked_leads` |
| P8 | `finish_reminder` | none | `FINISH_REMINDER` | `notification_service.send_pro_reminder` |
| P9 | `pro_lost_lead` | `issue_type` | `PRO_LOST_LEAD` | `monitor_service.reassign_lead` |
| P10 | `bot_paused` | `issue_type` | `BOT_PAUSED` | `dispatch_guards.guard_sos_human_handoff` |
| P11 | `sos_alert` | `phone`, `last_message` | `SOS_ALERT` | `notification_service.send_sos_alert`, lead BOOKED |
| P11 | `sos_alert_pending` | `last_message` | `SOS_ALERT_PENDING` | `notification_service.send_sos_alert`, otherwise |
| P12 | `customer_cancelled` | `customer_name`, `full_address` | `CUSTOMER_CANCELLED` | `workflow_service` cancel path |
| P13 | `onboarding_approved` | none | `ONBOARDING_APPROVED` | **Not wired:** `admin_panel/views/professionals.py` |
| P13 | `onboarding_rejected` | none | `ONBOARDING_REJECTED` | **Not wired:** `admin_panel/views/professionals.py` |
| — | `pro_found` | `pro_name`, `issue_type`, `full_address`, `appointment_time`, `price_estimate`, `pro_phone` | `PRO_FOUND` | `pro_flow._handle_approve`. The update `REASSIGNING` and `PENDING_REVIEW` promise, and the only way a closed-window customer gets the pro's phone |
| C1 | `no_pro_available` | `issue_type` | `NO_PRO_AVAILABLE` | `monitor_service.auto_reject_unassigned_leads` |
| C2 | `completion_check` | `pro_name` | `COMPLETION_CHECK` | `customer_flow.send_customer_completion_check`. **Not wired:** the operator button in `admin_panel/core/utils.py` |
| C3 | `reassigning` | `issue_type` | `REASSIGNING` | `monitor_service.reassign_lead` |
| C3 | `max_reassignments` | `issue_type` | `MAX_REASSIGNMENTS` | `monitor_service.reassign_lead` |
| C3 | `pending_review` | `issue_type` | `PENDING_REVIEW` | `monitor_service.reassign_lead` (three branches), `pro_flow._escalate_rejected_lead` |
| P4 | `early_lead` | — | none | **Not submitted, send left free-form**, see below |

That is **19 templates to submit** and one registry entry left out, against the 15 the registry used to list: P11 and P13 were each two bodies under one key, C3 was three, and `pro_found` had no row.

`tests/test_template_bodies.py` fails if a call site's `params` stop matching its spec, or if a submitted template has no call site. The two P13 entries are named there as the known exception.

## What each template had to give up

**Optional lines became values.** A variable cannot be empty, so `{price_estimate}` is the quote or `Fallbacks.PRICE_NOT_QUOTED`, and `{urgency}` is `Templates.URGENCY_EMERGENCY` or `Fallbacks.URGENCY_REGULAR`.

**Media links and the transcript arrive with the approval.** A block of unknown length cannot be a variable. When an offer goes out as a template, `notification_service.keep_withheld_details` stores it on the lead as `offer_withheld_details`, and `pro_flow._handle_approve` appends it to the approval reply. So a pro with a closed window decides without seeing the photos, and a pro who rejects never sees them.

**The loyalty header has no counterpart.** "לקוח חוזר שלך ביקש אותך" is not an urgency and the approved wording has no other place for it, so a template offer does not say it.

**The agenda is a count.** A list of unknown length cannot be a variable, so the template says how many jobs and points at *עבודות*.

**An emergency P3 takes `lead_offer`,** because `lead_offer_reassigned` has no urgency line and the free-form version opens with the emergency header.

**`pro_found` drops two optional lines.** `PRO_FOUND`'s profession and rating are not in the template. Its price stays, as `price_estimate` with the same fallback the offer uses.

**Runtime values are cut.** `sanitize_param` limits `last_message` to 700 characters and every other variable to 120, so that the whole message stays inside the 1024 Meta states for a body. Those two figures are ours.

**Text-only stays.** The owner decided on 2026-11-06 that the pilot runs on text menus and buttons come after it (PRO-103). Replies are plain keywords in the body (`*אשר*`), so nothing changes in the FSM and no template declares a button.

## Settled, and still to do

The owner settled these on 2026-11-06: the loyalty header is lost on template offers, a closed-window pro decides without media, an emergency reassignment goes through `lead_offer`, and `early_lead` is not submitted (it would need a media template and is not load-bearing), so its free-form send stays.

Still to do: **the panel sends.** They wait on the open panel-send PR, because `send_text_sync` is changing there. Until they move, P13 and the operator's C2 are lost on a closed window even after approval.

## Open questions

- Whether the sandbox number's 5-recipient limit applies to template sends as well as free-form (determines whether PRO-64 can exercise the template path at all).
- Whether Meta accepts each body as UTILITY. Only review can answer that, and `--status` reports the result.
- Whether a parameter value may contain a newline, `#`, `$` or `%`. Meta's pages list those characters as a problem with a template's variables without saying whether a runtime value counts. `sanitize_param` flattens the whitespace and leaves the characters in place, so a customer's message containing `%` still goes out as written.
- **Sends with no row here.** The claim above that everything from the inbound dispatcher is in-window holds for the *sender*, not the other party. A customer's or pro's message can trigger a send to someone whose window is closed: `Pro.CUSTOMER_RESCHEDULED_SUCCESS`, `Pro.CUSTOMER_EXISTING_JOB_QUERY`, `Pro.CUSTOMER_REPORTED_COMPLETION`, `Pro.CUSTOMER_REPORTED_NO_SHOW`, `Customer.PRO_CANCELLED_BOOKING`, `Customer.RATE_SERVICE`, `Customer.AWAITING_APPROVAL_TRANSPARENT`. None has a template. On a closed window they page and drop, as they always have. `AWAITING_APPROVAL_TRANSPARENT` is deliberately left that way: on the reassignment and admin paths it is only a progress note, and `pro_found`, `reassigning` or `pending_review` carries the outcome.
