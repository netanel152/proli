# WhatsApp Template Catalog (PRO-88)

**Status:** inventory complete, template designs draft. Nothing here has been submitted to Meta. The machine-readable half of this catalog now exists in code — `app/providers/whatsapp/template_registry.py` (PRO-89) — with every entry `DRAFT`; flipping an entry to `APPROVED` (with the exact name Meta approves) is the single code change that arms it for sending.

## What this document is

Every outbound WhatsApp message Proli sends, classified by whether Meta Cloud API will let us send it as free-form text or will require a **pre-approved template**. This is the input to PRO-89 (`CloudAPIProvider`), and the reason it exists separately: the number of templates we need is a product decision with a review latency attached, and discovering it *during* the Cloud API implementation would stall that ticket.

Scope boundary: this is an inventory and a design sketch. **Template submission belongs to PRO-87** (it needs the Business Portfolio and the approved display name to exist first), and the `send_template` implementation belongs to PRO-89.

> **Policy details need re-verification.** Meta's template categories, pricing model, and per-variable formatting rules have changed repeatedly. Everything below about *Proli's own sends* was read out of this codebase and is authoritative. Everything about *Meta's rules* is the working assumption and must be checked against current documentation during PRO-87 before any template is submitted. Where a rule drives a design decision here, it is flagged.

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
| **P1** | **Lead offer** (`APPROVAL_REQUEST`) | `workflow_service.py:1658` | Customer completes intake | **The product.** Interpolates customer name, address, floor/apartment, issue type, appointment time, price line, and a media-links block. Immediately followed by a second send (P2). |
| **P2** | Navigation link (`NAVIGATE_TO`) | `workflow_service.py:1660`, `notification_service.py:159` | Always paired with P1 | A separate message. Under templates that is a second approval **and** a second billable send — strong candidate to fold into P1's body. |
| **P3** | Lead offer, reassignment + admin-assignment path | `notification_service.py:157` (`notify_pro_new_lead`) | `monitor_service.reassign_lead`, `admin_flow` assignment | Shares the builder with P1 but uses the leaner `NEW_LEAD_*` templates. Same variable set. |
| **P4** | Early-lead notification (`EARLY_LEAD_*`) | `workflow_service.py:1319–1325` | Customer mid-intake | **Sends media** via `send_file_by_url` when a photo exists, text otherwise. A media-header template is a different structure from a text template — this needs two templates or a policy change. |
| **P5** | Approval nudge (`APPROVAL_NUDGE`) | `monitor_service.py:622` | Scheduler, T+10 min of pro silence | Sent precisely because the pro is *not* engaging, so assuming an open window is exactly backwards. |
| **P6** | Daily agenda | `scheduler.py:76` | Cron 08:00 Israel time | Per-pro job list. Classic UTILITY-category daily digest. |
| **P7** | Stale booked-lead reminder (`STALE_LEAD_REMINDER`) | `monitor_service.py:766` | Scheduler, every 4h, lead ≥24h old | The 24h staleness threshold guarantees the window is closed. |
| **P8** | Finish reminder (`Pro.REMINDER`) | `notification_service.send_pro_reminder` | Stale-job monitor | Capped at `MAX_PRO_REMINDERS`. |
| **P9** | Lead lost on reassignment (`PRO_LOST_LEAD`) | `monitor_service.py:196` | Reassignment | |
| **P10** | Bot paused (`PAUSE_NOTIFICATION`) | `workflow_service.py:460` | Customer triggered SOS | |
| **P11** | SOS alert (`SOS.PRO_ALERT`) | `notification_service.send_sos_alert` | Customer distress | Time-critical; a rejected template here is a safety regression. |
| **P12** | Customer cancelled (`CUSTOMER_CANCELLED`) | `workflow_service` | Customer cancels | |
| **P13** | Onboarding approved / rejected | `professionals.py:486, 497` | **Operator clicks a button in the admin panel** | Arbitrary delay after the pro's registration — hours or days. Assume closed. |

### Customer-facing

| # | Send | Call site | Trigger | Notes |
|---|---|---|---|---|
| **C1** | No pro available (`NO_PRO_AVAILABLE`) | `monitor_service.py:307` | Lead janitor, every 6h | Lead can be arbitrarily old. Assume closed. |
| **C2** | Completion check | `admin_panel/core/utils.py:191` | **Operator clicks a button** | Arbitrary timing by construction. |
| **C3** | Reassignment notices (`CUSTOMER_REASSIGNING`, `MAX_REASSIGNMENTS_REACHED`, `PENDING_REVIEW`) | `monitor_service.py:116, 157, 213` | `SOS_TIMEOUT_MINUTES=60` path → in-window; `STALE_BOOKED_LEAD_HOURS=24` path → **on the boundary** | Same code, two triggers, two different answers. Must be treated as template-required. |

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

## Design problems specific to Proli's messages

These are the parts where our current message shapes and Meta's template format are likely to collide. Each needs verification against current documentation in PRO-87.

**1. Multi-line interpolated blocks.** `build_new_lead_message` composes a header, a details block with five substitutions, a footer, and a media-links block built at runtime from a list of unknown length. Template variables are generally constrained in ways free-form text is not — notably around newlines and adjacent placeholders. The media-links block (`\n1. url\n2. url…`) is the least template-shaped thing we send.

**2. The numbered-reply menus survive, but verify how.** CLAUDE.md's text-only rule was inherited from the old vendor's limitation; PRO-88/89 have both now landed (the catalog and the `CloudAPIProvider` transport), and `send_interactive` can send real buttons/lists. Numeric-reply instructions are plain body text, so they should templatize cleanly — but the reason to keep them text-only rather than adopting interactive buttons is a *choice*, not a constraint (no template is even approved yet — that's PRO-87), and this catalog is where that choice should be made explicitly.

**3. Media.** P4 sends an image with a caption. Templates carry media in a header component with a different structure and different approval path than a text template.

**4. Typing indicators.** The facade exposes `send_chat_state_typing`, used from the dispatcher. Cloud API's presence model differs from the old vendor's. `WhatsAppProvider.send_typing` is already a non-abstract no-op default on the ABC, so `CloudAPIProvider` can simply not implement it — but confirm before assuming the UX is preserved.

**5. Hebrew.** Templates are approved per language. Every template here is `he`. `Messages.Fallbacks` supplies Hebrew defaults for missing fields, which matters because a template variable cannot be empty.

---

## Recommended next actions

1. ~~**Delete the operator-facing WhatsApp leg (O1–O3)**~~ — ✅ **done 2026-08-13.** Four templates removed, no Meta dependency.
2. **Fold P2 into P1.** One template, one send, one fee, one approval. Independent of Meta; can be done before PRO-87.
3. **Decide P4's fate.** If the early-lead notification is not load-bearing, dropping it removes the only media template.
4. **Then submit**, in priority order: P1/P3 (shared shape) → P6 → P5 → P7/P8 → the rest.

With 1 done and 2–3 taken, the catalog is roughly **9–11 professional-facing templates and 3 customer-facing**, down from 17 if every current send were templatized one-for-one.

## Open questions for PRO-87

- Current category definitions and which of P1–P13 fall under UTILITY vs MARKETING (affects both cost and opt-out obligations).
- Current per-variable formatting rules — specifically newlines inside variables, which decides whether the details block can stay one variable or must become five.
- Whether the sandbox number's 5-recipient limit applies to template sends as well as free-form (determines whether PRO-64 can exercise the template path at all).
