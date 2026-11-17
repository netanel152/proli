# WhatsApp Template Catalog (PRO-88)

**Status:** inventory complete, template bodies written, send path wired. Nothing here has been submitted to Meta. The machine-readable half of this catalog is `app/providers/whatsapp/template_registry.py` (PRO-89), with every entry `DRAFT` except `delivery_probe`, Meta's own `hello_world` (see below). The bodies live in `Messages.Templates` (`app/core/messages.py`), the one home for copy. Each `TemplateSpec` points at its body, declares its ordered `params` and its category, and carries a `source` (PRO-168) naming the free-form `Messages.*` constant it was worded from.

**Arming a template is two halves (PRO-232).** `template_registry.resolve(key)` returns a spec only if the entry is `APPROVED` in code (flipped with the exact name Meta approved) **and** its key is named in the environment's `WHATSAPP_ARMED_TEMPLATES` (comma, space or semicolon separated; see `docs/RAILWAY_SETUP.md`, "Arming a template"). Only then is it armed for every call site that passes `template_key` to the facade's `send_message`. Unknown keys, `DRAFT` keys and wildcards arm nothing and are logged once (ERROR in production, WARNING elsewhere); an empty value arms nothing and never refuses boot. Until armed, those sends deliver exactly what they did before the path existed (every send that names a template is now guarded so a failure cannot abort a written transition, see [Settled, and still to do](#settled-and-still-to-do)). The admin panel's sends (P13 and C2's operator button) pass one too, through `send_text_sync`'s `template_key` and `params`. See [What is wired](#what-is-wired).

## What this document is

Every outbound WhatsApp message Proli sends, classified by whether Meta Cloud API will let us send it as free-form text or will require a **pre-approved template**. This is the input to PRO-89 (`CloudAPIProvider`), and the reason it exists separately: the number of templates we need is a product decision with a review latency attached, and discovering it *during* the Cloud API implementation would stall that ticket.

Scope boundary: this is the inventory and the template design. PRO-87 (the Business Portfolio and the number) is done; **approval is PRO-150's**. `scripts/submit_whatsapp_templates.py` does the submitting: by default it only prints what it would submit, `--status --waba-id <id>` reports, per template, Meta's status, the category Meta holds and the registry's status (exit 3 if Meta holds any template outside UTILITY, even when the registry agrees; exit 1 on any other drift; 0 when they agree; "flip the registry" is printed only for a template approved as UTILITY), and `--apply --waba-id <id> --confirm-name "<account name>"` submits, and refuses an account whose name contains "Test" or a name that does not match. The WABA configured today, `1910054379665469`, is Meta's test account, so no real submission can happen against it.

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
| **P1** | **Lead offer** (`APPROVAL_REQUEST`) | `workflow_service.py:1031` | Customer completes intake | **The product.** Interpolates customer name, address, issue type, appointment time, price line, and a media-links block. **PRO-59** dropped the customer's phone and the floor/apartment line from this send — both now arrive only in the post-approval contact card (`Pro.CONTACT_CARD`, sent from `pro_flow._approve_lead`, not a business-initiated send). |
| **P2** | ~~Navigation link (`NAVIGATE_TO`)~~ | — | — | **Removed by PRO-59, not folded.** Neither pre-approval path (`workflow_service`'s initial offer or `notification_service.notify_pro_new_lead`) sends a navigation link any more — a map pin resolves the exact address the offer now withholds until approval. The waze line moved into the post-approval `Pro.CONTACT_CARD` instead, which needs no template of its own (it replies inside the 24h window the pro's own *אשר* just opened). One fewer template to submit, one fewer billable send. |
| **P3** | Lead offer, reassignment + admin-assignment path | `notification_service.py:213` (`notify_pro_new_lead`) | `monitor_service.reassign_lead`, `admin_flow` assignment | Shares the builder with P1 but uses the leaner `NEW_LEAD_*` templates. Since PRO-59 both templates omit the phone and floor/apartment, so the variable sets are closer than they used to be, not identical — P1 also carries `price_line` and optional emergency/loyalty headers P3 never had. |
| **P4** | Early-lead notification (`EARLY_LEAD_*`) | `conversation_pipeline.py:694–699` | Customer mid-intake | **Sends media** via `send_file_by_url` when a photo exists, text otherwise. A media-header template is a different structure from a text template — this needs two templates or a policy change. |
| **P5** | Approval nudge (`APPROVAL_NUDGE`) | `monitor_service.py:688` | Scheduler, T+10 min of pro silence | Sent precisely because the pro is *not* engaging, so assuming an open window is exactly backwards. |
| **P6** | Daily agenda | `scheduler.py:76` | Cron 08:00 Israel time | Per-pro job list. Classic UTILITY-category daily digest. |
| **P7** | Stale booked-lead reminder (`STALE_LEAD_REMINDER`) | `monitor_service.py:825` | Scheduler, every 4h, lead ≥24h old | The 24h staleness threshold guarantees the window is closed. |
| **P8** | Finish reminder (`Pro.REMINDER`) | `notification_service.send_pro_reminder` | Stale-job monitor | Capped at `MAX_PRO_REMINDERS`. |
| **P9** | Lead lost on reassignment (`PRO_LOST_LEAD`) | `monitor_service.py:248` | Reassignment | Skipped when the reassignment came from a pro's own reject (PRO-117 `notify_old_pro=False`) — that pro already got the reject acknowledgement. |
| **P10** | Bot paused (`PAUSE_NOTIFICATION`) | `dispatch_guards.guard_sos_human_handoff` | Customer triggered SOS | Only on a BOOKED lead, and never to a pro the admin deactivated (PRO-212). |
| **P11** | SOS alert (`SOS.PRO_ALERT` / `SOS.PRO_ALERT_PENDING`) | `notification_service.send_sos_alert` | Customer distress | Time-critical; a rejected template here is a safety regression. **PRO-59**: the phone-bearing `PRO_ALERT` sends only when the lead is BOOKED; a NEW lead's pro (offered, not yet approved) gets the phone-free `PRO_ALERT_PENDING` instead, which since PRO-212 has no `{last_message}` variable either, because a customer can type their number there — two template bodies, same trigger, so two registry entries (`sos_alert`, `sos_alert_pending`). A CONTACTED or absent lead's pro gets no alert (PRO-212). |
| **P12** | Customer cancelled (`CUSTOMER_CANCELLED`) | `workflow_service` | Customer cancels | |
| **P13** | Onboarding approved / rejected | `professionals.py:486, 497` | **Operator clicks a button in the admin panel** | Arbitrary delay after the pro's registration — hours or days. Assume closed. Two bodies, so two registry entries (`onboarding_approved`, `onboarding_rejected`). |
| **P14** | Lead left a pro outside its trade (`LEAD_OUTSIDE_TRADE`, `LEAD_OUTSIDE_TRADE_OFFER`) | `conversation_pipeline.py` wrong-trade check | The customer's next message on a lead whose pro doesn't fit its trade (PRO-215) | Not `PRO_LOST_LEAD`: nobody failed to answer. Worded to stay true whether the lead goes to another pro or to manual review. It fires on the *customer's* message, so this pro's window is usually closed: one template (`pro_lead_outside_trade`) for both notices. |

### Customer-facing

| # | Send | Call site | Trigger | Notes |
|---|---|---|---|---|
| **C1** | No pro available (`NO_PRO_AVAILABLE`) | `monitor_service.py:378` | Lead janitor, every 6h | Lead can be arbitrarily old. Assume closed. |
| **C2** | Completion check | `admin_panel/core/utils.py:190` (operator) · `customer_flow.py:send_customer_completion_check` (scheduler) | **Operator clicks a button**, or stale-job monitor Tier 2 | Arbitrary timing by construction. Capped at `MAX_CUSTOMER_COMPLETION_CHECKS` per booking (PRO-45: `reassign_lead` resets the counter for a fresh owner) with a `CUSTOMER_COMPLETION_CHECK_COOLDOWN_HOURS` gap; an operator send bypasses the cap but still restarts the cooldown. |
| **C3** | Reassignment notices (`CUSTOMER_REASSIGNING`, `MAX_REASSIGNMENTS_REACHED`, `PENDING_REVIEW`) | `monitor_service.py:180, 129, 284` | `SOS_TIMEOUT_MINUTES=60` path → in-window; `STALE_BOOKED_LEAD_HOURS=24` path → **on the boundary**; PRO-117 pro-reject rematch failure → also in-window | Same code, multiple triggers (including the PRO-117 reject-rematch fallback), different window answers. Must be treated as template-required. Three bodies, so three registry entries (`reassigning`, `max_reassignments`, `pending_review`). |

### Sent to the other party of a conversation

One party's message, or a scheduler, triggers a send to the *other* party. The sender is inside their window by construction; the recipient may not have written in days. These had no row until the 2026-11-14 audit (an AST pass over every send in `app/` and `admin_panel/`).

| Send | Call site | To | Trigger |
|---|---|---|---|
| Pro cancelled the booking (`Customer.PRO_CANCELLED_BOOKING`) | `pro_flow._execute_cancel` | customer | The pro's *ביטול* |
| Customer rescheduled (`Pro.CUSTOMER_RESCHEDULED_SUCCESS`) | `customer_flow.handle_reschedule_selection` | pro | The customer picks a new slot |
| Customer asks about the existing job (`Pro.CUSTOMER_EXISTING_JOB_QUERY`) | `dispatch_guards.guard_new_or_existing` | pro | The customer answers *2* to "new request or the existing job?". The customer is told the pro will get back to them, so a lost send here is a broken promise. The lead is re-read as BOOKED before the send, so the phone it carries is already that pro's to have (PRO-59). |
| Rating prompt (`Customer.RATE_SERVICE`) | `pro_flow._execute_finish` | customer | The pro's *סיימתי* |
| Customer reported a no-show (`Pro.CUSTOMER_REPORTED_NO_SHOW`) | `customer_flow._handle_no_show_report` | pro | The customer answers *3* to the completion check |
| Reassignment offer (`Customer.REASSIGN_OFFER`) | `monitor_service.check_pro_approval_sla` | customer | Scheduler, the pro silent for `APPROVAL_REASSIGN_OFFER_MINUTES`. In-window after the customer's own intake, but `reassign_lead` re-arms the same timer (and the `AWAITING_PRO_APPROVAL` state it needs), when the customer may not have written for a day. An admin assignment does not arm it. |
| Awaiting approval (`Customer.AWAITING_APPROVAL_TRANSPARENT`) | `monitor_service.reassign_lead`, `admin_flow.assign_lead_to_pro` | customer | A reassignment or an admin assignment found a pro. The same text from `_finalize_deal` answers the customer's own message and names no template. |
| Customer reported completion (`Pro.CUSTOMER_REPORTED_COMPLETION`) | `customer_flow.handle_customer_completion_text` | pro | The customer answers *1* to the completion check |
| Bot paused by the pro (`Customer.BOT_PAUSED_BY_PRO`) | `pro_flow._pause_lead` | customer | The pro's *השהה* on a BOOKED lead |

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

Everything reached from the inbound dispatcher — `workflow_service` replies, `customer_flow`, `pro_flow`, `admin_flow`, and `pro_onboarding_service` — that goes back to **the person who just wrote** is a direct response to their message. That is inside the window by construction. What the same flows send to the *other* party is not, and is listed above. It is also the bulk of the 131 call sites, which is why the template count is manageable.

One scheduler job is left free-form on purpose, and it is worth recording *why* rather than rediscovering it:

- **SLA deflection** (`check_sla_deflection`, 5-min interval, `Customer.SLA_DEFLECTION_MESSAGE`) fires after `PAUSE_TTL_SECONDS` of silence in a paused chat. The owner left it free-form on 2026-11-14 because it follows a pause the customer started minutes earlier, so their window is open. The one case that reasoning does not cover: the query matches any `is_paused` lead, and `pro_flow._pause_lead` (the pro's *השהה*) sets that flag too. A customer paused by the pro who has not written in 24h and does not answer the `bot_paused_by_pro` template loses this message. The pause still ends; they are just not told the bot is back.

The customer reassignment offer used to be listed here as safe. It is, ~25 minutes after the customer's own intake, but not after a reassignment, so it now names `reassign_offer`.

---

## What is wired

A call site passes its free-form text plus `template_key` and named `params` to `WhatsAppFacade.send_message`. Open window: the text goes out as ever. Closed window and template armed (`APPROVED` and named in `WHATSAPP_ARMED_TEMPLATES`): the template goes out, with `params` in the order the spec declares. Closed window and not armed: the operator page plus `ServiceWindowClosedError`, which the facade turns into `None`, exactly as before. A send that passes `untemplated="<reason>"` instead (and no `template_key`) is dropped quietly to a closed window: WARNING, `None`, no page. The early-lead notice, the SLA deflection and the short completion line while degraded use it. It stays behind the breaker and the kill switch, and `DryRunProvider` always records the text.

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
| P11 | `sos_alert_pending` | none | `SOS_ALERT_PENDING` | `notification_service.send_sos_alert`, lead NEW (a CONTACTED or absent lead sends the pro nothing) |
| P12 | `customer_cancelled` | `customer_name`, `full_address` | `CUSTOMER_CANCELLED` | `workflow_service` cancel path |
| P13 | `onboarding_approved` | none | `ONBOARDING_APPROVED` | `admin_panel/views/professionals.py::_notify_pro_approved` |
| P13 | `onboarding_rejected` | none | `ONBOARDING_REJECTED` | `admin_panel/views/professionals.py::_notify_pro_rejected` |
| P14 | `pro_lead_outside_trade` | `issue_type` | `PRO_LEAD_OUTSIDE_TRADE` | `conversation_pipeline.py` wrong-trade check, for both `LEAD_OUTSIDE_TRADE` and `LEAD_OUTSIDE_TRADE_OFFER` |
| — | `pro_found` | `pro_name`, `issue_type`, `full_address`, `appointment_time`, `price_estimate`, `pro_phone` | `PRO_FOUND` | `pro_flow._approve_lead`. The update `REASSIGNING` and `PENDING_REVIEW` promise, and the only way a closed-window customer gets the pro's phone |
| C1 | `no_pro_available` | `issue_type` | `NO_PRO_AVAILABLE` | `monitor_service.auto_reject_unassigned_leads` |
| C2 | `completion_check` | `pro_name` | `COMPLETION_CHECK` | `customer_flow.send_customer_completion_check`, and the operator button, `admin_panel/core/utils.py::send_completion_check_sync` |
| C3 | `reassigning` | `issue_type` | `REASSIGNING` | `monitor_service.reassign_lead` |
| C3 | `max_reassignments` | `issue_type` | `MAX_REASSIGNMENTS` | `monitor_service.reassign_lead` |
| C3 | `pending_review` | `issue_type` | `PENDING_REVIEW` | `monitor_service.reassign_lead` (three branches), `pro_flow._escalate_rejected_lead` |
| — | `pro_cancelled_booking` | none | `PRO_CANCELLED_BOOKING` | `pro_flow._execute_cancel` |
| — | `customer_rescheduled` | `customer_name`, `full_address`, `old_time`, `new_time` | `CUSTOMER_RESCHEDULED` | `customer_flow.handle_reschedule_selection` |
| — | `customer_existing_job_query` | `customer_name`, `issue_type`, `phone` | `CUSTOMER_EXISTING_JOB_QUERY` | `dispatch_guards.guard_new_or_existing` |
| — | `rate_service` | `pro_name` | `RATE_SERVICE` | `pro_flow._execute_finish`, the full prompt only. **Not submitted** (2026-11-17), see below |
| — | `customer_reported_no_show` | none | `CUSTOMER_REPORTED_NO_SHOW` | `customer_flow._handle_no_show_report` |
| — | `reassign_offer` | none | `REASSIGN_OFFER` | `monitor_service.check_pro_approval_sla` |
| — | `awaiting_approval` | `pro_name` | `AWAITING_APPROVAL` | `monitor_service.reassign_lead`, `admin_flow.assign_lead_to_pro` |
| — | `customer_reported_completion` | `customer_name` | `CUSTOMER_REPORTED_COMPLETION` | `customer_flow.handle_customer_completion_text` |
| — | `bot_paused_by_pro` | none | `BOT_PAUSED_BY_PRO` | `pro_flow._pause_lead` |
| P4 | `early_lead` | — | none | **Not submitted, send left free-form**, see below |

That is **28 templates to submit** and three registry entries left out (`early_lead`; `rate_service` since 2026-11-17; and `delivery_probe`: Meta's test-account `hello_world`, en_US, `APPROVED`, `submit=False`, staging only, used by `scripts/probe_template_delivery.py --to <number>` as staging's proof of a closed-window delivery; exit 0 template, 3 text because the window was open, 4 not sent), against the 15 the registry used to list: P11 and P13 were each two bodies under one key, C3 was three, `pro_found` had no row, P14 came with PRO-215, and the nine notices to the other party of a conversation had no row at all.

`tests/test_template_bodies.py` fails if a call site's `params` stop matching its spec, if a submitted template has no call site, if a call site names a template that is not submitted (one exception, `rate_service`, listed with its reason in `NAMED_BUT_NOT_SUBMITTED`: it has a body, and while it is a draft its send behaves as one naming no template), or if one of the admin panel's sends names none. It scans `admin_panel/` as well as `app/`.

## What each template had to give up

**Optional lines became values.** A variable cannot be empty, so `{price_estimate}` is the quote or `Fallbacks.PRICE_NOT_QUOTED`, and `{urgency}` is `Templates.URGENCY_EMERGENCY` or `Fallbacks.URGENCY_REGULAR`.

**Media links and the transcript arrive with the approval.** A block of unknown length cannot be a variable. When an offer goes out as a template, `notification_service.keep_withheld_details` stores it on the lead as `offer_withheld_details`, and `pro_flow._approve_lead` appends it to the approval reply and unsets it (a change of owner, in `monitor_service.reassign_lead`, `admin_flow.assign_lead_to_pro` or the מצא claim, unsets it too). So a pro with a closed window decides without seeing the photos, and a pro who rejects never sees them.

**The loyalty header has no counterpart.** "לקוח חוזר שלך ביקש אותך" is not an urgency and the approved wording has no other place for it, so a template offer does not say it.

**The agenda is a count.** A list of unknown length cannot be a variable, so the template says how many jobs and points at *עבודות*.

**An emergency P3 takes `lead_offer`,** because `lead_offer_reassigned` has no urgency line and the free-form version opens with the emergency header.

**The wrong-trade notice is one body.** The free-form send has two wordings: one for a pro who was offered the lead ("אין צורך לאשר או לדחות אותה") and one for a pro who had only heard of it. `pro_lead_outside_trade` says "אין צורך לעשות כלום", which is true of both, and like the free-form notice it does not say where the lead went.

**`pro_found` drops two optional lines.** `PRO_FOUND`'s profession and rating are not in the template. Its price stays, as `price_estimate` with the same fallback the offer uses.

**Runtime values are cut.** `sanitize_param` limits `last_message` to 700 characters and every other variable to 120, so that the whole message stays inside the 1024 Meta states for a body. Those two figures are ours.

**Quick-reply buttons (PRO-230).** The owner decided on 2026-11-14 that a template may carry quick-reply buttons for the pilot, reversing the 2026-11-06 "text menus only" decision for templates alone. Replies stay plain keywords in the body ("לאישור כתוב אשר", or a numbered line "1 - …"), and a template's buttons are exactly the replies its body advertises (`template_registry.advertised_replies` reads both shapes, `unread_write_prompts` flags any "כתוב …" it cannot read; `button_rule_violations`: same list, plus Meta's limits of at most 10 buttons, a label of at most 25 characters, letters, digits and single spaces), so a tap and the typed reply reach the same handler and nothing changes in the FSM. A pro template's payload is always a keyword, never a bare digit (PRO-212). A tap that arrives without its payload (WhatsApp Manager's test send) is read back by its label (`payload_for_label`). Labels live in `Messages.Templates.Buttons`.

| Template | Buttons (label) |
|---|---|
| `lead_offer`, `lead_offer_reassigned`, `approval_nudge`, `sos_alert_pending` | אשר, דחה |
| `stale_lead_reminder` | סיימתי |
| `finish_reminder` | סיימתי, עדיין עובד |
| `daily_agenda` | עבודות |
| `no_pro_available`, `pro_cancelled_booking` | נציג |
| `completion_check` | three buttons, payloads `1`/`2`/`3` |
| `reassign_offer` | two buttons, payloads `1`/`2` |

`scripts/submit_whatsapp_templates.py` submits a `BUTTONS` component of `QUICK_REPLY`s after the BODY and prints each button and what a tap sends in the dry run; a rule violation refuses the run, and `--status` reports buttons that differ from the registry and says not to flip such a template, because every send would fail. Every template stays `DRAFT` and nothing was submitted. `send_interactive` is still called by no flow.

**The same buttons on an open window, and the lead they name (PRO-104/PRO-105).** `template_registry.buttons_for(key)` hands a template's quick replies to the free-form send of the same message, so an open window gets them as interactive reply buttons (at most 3, title at most 20 characters, body at most 1024, else the plain text) on the pro lead offers (`lead_offer`, `lead_offer_reassigned`, `approval_nudge`, `sos_alert_pending`) and the completion check (`completion_check`); the consent request has no template and sends its two buttons (`כן`/`לא`) on an open window only. A pro offer's buttons also name their lead: the reply id on an open window, and each quick-reply payload sent with the template on a closed one, is `"<keyword>#<lead_id>"` (`with_reply_ref`), e.g. `אשר#6a1b2c3d4e5f6a7b8c9d0e1f`. The registry still stores the bare keyword, so the parity rule is unchanged; `normalize_meta_message` splits the suffix off into `reply_ref`, and **אשר**/**דחה** then act on that lead only, or answer that it is no longer available. A payload without a valid 24-hex suffix is read whole, and a label-only tap (`payload_for_label`) carries no lead.

## Settled, and still to do

**The owner's voice, 2026-11-17.** Every template body was rewritten to read like a person, not an AI, before PRO-150's submission: no emoji, no em dash, no `*bold*`, no slashed gender forms, and פרולי speaks as "אנחנו". A reply is asked for in the sentence ("לאישור כתוב אשר, לדחייה כתוב דחה.") and a numbered menu is `1 - text` lines. Variables, their order, button labels and keywords did not change, and the free-form open-window texts kept their own wording, so three pairs that used to be word for word now only promise the same thing (`ONBOARDING_REJECTED`, `PRO_FOUND`'s closing line, `MAX_REASSIGNMENTS`). Every body still clears Meta's review rules and the 20-fixed-characters-per-variable floor without lengthening; the tightest is `pro_found`, at about 21.7. `tests/test_template_bodies.py` pins the voice for every body. The same day `rate_service` was left out of submission: a rating request reads as a survey, which Meta is likely to hold as MARKETING. It keeps its body and its send site still names it, so submitting it later is the one `submit` flag.

**The `_v2` names, 2026-11-17.** The first submission to the production account went out from code older than the rewrite: 29 templates in the old wording, `proli_rate_service` among them, and Meta placed four of them (`proli_lead_offer`, `proli_daily_agenda`, `proli_pro_lead_outside_trade`, `proli_reassign_offer`) in MARKETING while they were still pending. On the owner's decision all 29 were deleted, and the current wording is submitted under `proli_<key>_v2`, because Meta does not let a just-deleted name be reused straight away. Registry keys did not change, so `WHATSAPP_ARMED_TEMPLATES` and every call site are unaffected. `tests/test_template_bodies.py` keeps the 29 deleted names in `DELETED_ON_META` so none is reused. `--apply` skips a name Meta already holds without comparing its body, so after a submission run `--status`, which does compare bodies.

The owner settled these on 2026-11-06: the loyalty header is lost on template offers, a closed-window pro decides without media, an emergency reassignment goes through `lead_offer`, and `early_lead` is not submitted (it would need a media template and is not load-bearing), so its free-form send stays. On 2026-11-14 the owner approved a body for `pro_lead_outside_trade` (P14, PRO-215): one template for the offered and the not-yet-offered case, because "אין צורך לפעול" is true of both, and its free-form wording for an open window stays as it was.

On 2026-11-14 the owner approved bodies for the nine notices to the other party of a conversation, and named what stays free-form on purpose:

- **`early_lead` (P4).** The 2026-11-06 decision stands: it would need a media template and is not load-bearing.
- **SLA deflection.** It follows a pause the customer started minutes earlier (see [Safe without templates](#safe-without-templates) for the one case that does not cover).
- **The short rating line** (`pro_flow._execute_finish`, when `non_essential_sends_paused()`). It is sent instead of the rating prompt while the number's quality is degraded, to say only that the job closed. A template there would be one more business-initiated conversation at exactly the moment the number can least afford it.

Five of the nine sends used to be the last, unguarded statement after a write: the pro's cancel, the customer's reschedule, the pro's finish (rating prompt), the customer's reported completion, and the pro's pause. A refused or failed send there would have aborted the handler after the state change was already written, so each is now wrapped the way the no-show send already was: the failure is logged at ERROR and the handler carries on. The other four were already inside a `try`. PRO-232 guarded the four older sends that name a template, and `lead_offer`, the same way: `pro_found` (`pro_flow._approve_lead`), `bot_paused` (`dispatch_guards.guard_sos_human_handoff`), `pro_lost_lead` (`monitor_service.reassign_lead`), `customer_cancelled` (`workflow_service._execute_customer_cancel`) and `lead_offer` (`workflow_service._finalize_deal`).

A template Meta accepted and then failed pages the operator under its own once-per-recipient-per-day budget (`window.TEMPLATE_LOST`, re-exported by `delivery`), separate from the "window closed, nothing sent" page, and the page carries Meta's numeric error code. A template Meta refuses outright when the window is closed and the template is armed (any 4xx except 401, 403 and 429) is paged through the same claim by `CloudAPIProvider.send_business_initiated` and becomes `TemplateRefusedError`, which the facade turns into `None`; 401, 403, 429, a 5xx and a free-form text rejection still propagate.

The quality watchdog also checks every armed template (`monitor_service.check_armed_templates`, reading `GET /{META_WABA_ID}/message_templates`): `template:unwatched`, `template:unreadable`, `template:<key>:status:<S>` and `template:<key>:category:<C>`, once per condition per day, paged in production and logged at WARNING elsewhere. It never disarms. See `docs/RUNBOOK_WHATSAPP_OUTAGE.md` §8.

## Open questions

- Whether the sandbox number's 5-recipient limit applies to template sends as well as free-form (determines whether PRO-64 can exercise the template path at all).
- Whether Meta accepts each body as UTILITY. Only review can answer that, and `--status` reports the result.
- Whether a parameter value may contain a newline, `#`, `$` or `%`. Meta's pages list those characters as a problem with a template's variables without saying whether a runtime value counts. `sanitize_param` flattens the whitespace and leaves the characters in place, so a customer's message containing `%` still goes out as written.
- **Whether the reassignment offer's *1*/*2* can always be answered.** Since PRO-238 `customer_flow.handle_reassign_offer_reply` reads those digits by the lead's `reassign_offered_at`/`reassign_offered_pro_id`, not by the FSM state, for `REASSIGN_OFFER_REPLY_MAX_AGE_HOURS` (24h), from both `guard_pro_approval_soft_hold` and the pipeline. A customer who answers the `reassign_offer` template after `AWAITING_PRO_APPROVAL` (`PRO_APPROVAL_TTL_SECONDS`, an hour) has lapsed is still understood; past 24h the digit is nobody's and reaches the ordinary pipeline. The free-form offer behaves the same.
