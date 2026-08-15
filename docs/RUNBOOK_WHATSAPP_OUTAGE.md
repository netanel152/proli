# Runbook — WhatsApp Outbound Outage

**Severity:** P1 — the bot goes dark. No customer or pro message is answered.
**Owner:** on-call operator (`ONCALL_PHONE`, falls back to `ADMIN_PHONE`).
**Related:** PRO-20 (deauth alert), PRO-71 (outbound circuit breaker), PRO-82 (fail-closed
breaker), PRO-86 (single outbound egress / provider facade), PRO-75 / PRO-18 (Sentry email
paging), `SENTRY_SETUP.md`.

Every outbound WhatsApp message passes through exactly one object — `WhatsAppFacade`
(`app/providers/whatsapp/facade.py`) — wrapping a single configured transport
(`WHATSAPP_PROVIDER`: `dryrun` by default, or `cloud`). **Green API is gone entirely**
(PRO-85 — the old instance was deleted, the tariff cancelled); this runbook is now
provider-agnostic rather than describing one vendor's console.

> **Where things stand today:** `CloudAPIProvider` (PRO-89) is code-complete against the Meta
> Graph API — `WHATSAPP_PROVIDER=cloud` sends for real once `META_ACCESS_TOKEN` /
> `META_PHONE_NUMBER_ID` (and, in prod-like environments, `META_APP_SECRET` /
> `META_VERIFY_TOKEN`) are configured; `Settings` refuses to boot with `cloud` selected and
> those missing. But **PRO-87 (Meta Business Portfolio + template approval) has not landed**
> — no template is approved, no sandbox number exists — so this deployment still runs the
> default `dryrun` and has never sent a real message via Cloud API. That means the
> account-outage scenario this runbook exists for (§1–§3) is currently **dormant** — there is
> nothing to page about. §5 (the historical Green API "number reputation" material) is kept
> only as institutional memory for whichever real provider replaces it. What stays live
> regardless of provider: §2's breaker/kill-switch mechanics, §4 (manual kill switch), and
> `WHATSAPP_DRY_RUN`.

---

## 0. Provider state and `/health`

| `/health` `checks.whatsapp.status` | Meaning | Outbound |
|---|---|---|
| `up` | provider reports `authorized` and can transmit | flowing |
| `degraded` | the configured provider **cannot transmit at all** (e.g. `dryrun`), or a transmitting provider reports `yellowCard` (see §5 — historical) | dry-run: nothing to suppress; `yellowCard`: **auto-halted** |
| `down` | not authorized / blocked / unreachable / the state probe itself failed | **auto-halted** |

`/health`'s `checks.whatsapp` object also carries `provider` (the configured transport's
name) and `transmits` (whether it can reach a real handset) — added in PRO-86 so a dry-run
deployment and a live one are distinguishable from the outside.

---

## 1. Detection

**Automatic (primary):** the worker's deauth watchdog (`monitor_service.check_whatsapp_instance_state`)
polls the provider's `get_state()` every **2 min** (`WA_STATE_CHECK_INTERVAL_MINUTES`) —
but only for a **transmitting** provider (`provider.transmits`); a non-transmitting provider
(dry-run) has no account to watch, so the watchdog skips its tick outright (PRO-86). Once a
transmitting provider has been non-authorized for **> 5 min** (`WA_STATE_ALERT_THRESHOLD_MINUTES`)
the watchdog emits `page_critical` → **Sentry → email** to the on-call operator, then
re-pages every **60 min** (`WA_STATE_REALERT_MINUTES`) until recovery.

> ⚠️ Paging depends on `SENTRY_DSN` being set on the Railway `worker` service **and** a
> Sentry alert rule `level = fatal → email` existing. See `SENTRY_SETUP.md`. If those
> aren't configured, **you will not be paged** — verify them as part of go-live (PRO-18).

**Manual check:**
```bash
curl -s https://<your-api-host>/health | jq '.checks.whatsapp'
# → { "status": "up|degraded|down", "state": "...", "provider": "dryrun|cloud", "transmits": true|false }
```
Also: worker logs show `[WA Monitor]` lines.

---

## 2. What happens automatically (do not fight it)

The breaker is **fail-closed** (PRO-82): sending requires a *positive, recent* confirmation
the account is healthy, not merely the absence of a "something is wrong" flag.

- Every successful `authorized` probe writes Redis key `wa:instance:state` = `"authorized"`
  (TTL `WA_STATE_CONFIRM_TTL_SECONDS` = 360s); any other probe result **deletes** the key.
  The facade blocks every send while this key is **absent** — "account state unconfirmed, no
  successful probe on record" — which covers both the worker-boot window and a monitor that
  has stopped running. Before PRO-82/PRO-86 an absent key read as *healthy*, which is exactly
  the gap that let a flagged number keep sending.
- The moment a transmitting provider is found non-authorized, the monitor also sets
  `wa:instance:paused` (TTL `WA_STATE_PAUSE_TTL_SECONDS` = 360s, refreshed every tick while down).
- Every outbound send short-circuits **before the provider is called** and logs
  `⛔ Outbound halted …`. Nothing is silently dropped into a filtered/blocked account.
- The one deliberate exception: a Redis **error** (not an absent key) during the check
  **fails open** — a monitoring dependency going down must never take the send path with it.
- On recovery (`authorized`), the monitor's next successful probe republishes
  `wa:instance:state`, clears `wa:instance:paused`, sends a recovery notice, and outbound
  resumes automatically.
- None of the above applies to a non-transmitting provider (`transmits = False`, e.g.
  dry-run) — there is no handset to protect, so the breaker does not gate it at all.

**Implication:** during an outage, inbound messages that arrive are still processed by the
worker, but any reply is **suppressed** (not queued). Customers who message during the outage
get no answer and will not be retroactively answered after recovery — see §3.

**SMS fallback?** **No.** SMS was removed entirely (PRO-75). The operator alert path is
Sentry → email; there is no customer-facing fallback channel. Customer communication during
downtime is manual (§3).

### If Redis kept the breaker stuck after a genuine recovery (rare)

Both breaker keys auto-expire (360s), but to force-clear:
```bash
redis-cli del wa:instance:paused
redis-cli set wa:instance:state authorized EX 360
```
Do **not** delete `wa:instance:paused:manual` unless you intended to — that's the operator
kill switch, and the monitor never touches it (§4).

---

## 3. Customer communication during downtime

- There is **no automated fallback** (no SMS). Replies are suppressed while down.
- Messages customers send during the outage are **not** answered retroactively after recovery.
- If the outage is prolonged and customer-facing, communicate out-of-band (whatever channel
  the business uses — phone calls, a status note), and after recovery consider a manual sweep
  of leads created/updated during the window (`/health/leads` shows `pending_review_count` /
  `stuck_contacted_count`).

---

## 4. Prevention (avoid a flagged / banned number)

WhatsApp flags numbers for **unsolicited / spammy** behavior, independent of which API
product sits in front of them. To keep whichever number is live healthy:

- **Never cold-initiate.** Only message users who messaged first. Test scripts are guarded
  against cold-initiating to real numbers (PRO-72) — keep that guard on.
- **Warm up a new number before it ever touches automated sends** — a brand-new number wired
  straight into an API is the classic way to earn a reputation flag in week one. Use it as a
  human phone first (see §5 step 2 for the technique, ~a week), *then* enable the provider and
  ramp automated volume up over several days. Warming is not "go slower on day one"; it happens
  before day one.
- Avoid identical bulk messages, high send rates, and messaging numbers that repeatedly report/block.
- Keep opt-in / consent flow intact (the consent gate exists for this reason).
- Watch `whatsapp.status` on `/health` and the Sentry pages — `degraded` is an early warning
  before a harder failure.

---

## 5. Historical: Green API number-reputation rehabilitation (retired, PRO-85)

This section describes the Green API `yellowCard` state — a WhatsApp-side reputation flag
on a phone number, distinct from outright deauthorization. **The number and instance this
was written for are gone** (PRO-85); it is kept only as institutional memory in case a future
provider surfaces a similar flag, not as a live procedure. None of the commands below
reference a live account.

> `yellowCard` was the insidious case: sends *looked* successful (HTTP 200) while WhatsApp
> silently filtered them (accepted, never delivered) — as opposed to `notAuthorized` /
> `blocked`, which failed outright.

**What did NOT clear it:** rotating the API credential, rebooting the session, re-scanning a
QR code, or provisioning a new API instance/session against the *same* number — all of those
operate on the API layer, and the flag lives on WhatsApp's assessment of the phone number
itself, not the session. Repeated session teardown/re-registration was itself a spam signal,
so churn on an already-flagged number was actively harmful.

**What actually lifted it — rebuilding reputation with genuine human use:**

1. **Guarantee zero automated sends** for the whole window: `WHATSAPP_DRY_RUN=true` on every
   service (still the correct flag today — see the note at the top of this runbook; it now
   forces provider *selection* to `DryRunProvider` rather than swapping an HTTP transport).
2. **Use the number as a human, on a real handset** — the normal WhatsApp mobile app, not the
   API and not a linked device driven by code. Have 5–10 real people save the number as a
   contact and message it first (inbound-first traffic from contacts who have you saved is the
   strongest positive signal); reply like a person (irregular timing, varied wording, no
   templates); no links/attachments/forwards for the first few days; keep volume low and
   conversational.
3. **Wait, and poll passively** — re-probe state at most once or twice a day; sending to "check
   if it's back" pushed the flag toward a permanent ban. Budget **days, not hours**: roughly
   5–7 days of real usage, longer if the number had no history of human conversation. Silence
   alone was *necessary* but not *sufficient* — a score recovers from positive signal, not from
   the mere absence of negative signal.
4. **Ramp back slowly** once healthy — remove the dry-run override and resume at low volume
   (single-digit automated sends the first day, growing over a week). A previously-flagged
   number re-flagged faster than a clean one.

---

## 6. Manual kill switch (halt outbound without a deploy)

To stop **all** outbound immediately (e.g. a suspected runaway send, or to freeze the system
while investigating) — independent of provider/account state:

```bash
redis-cli set wa:instance:paused:manual 1   # halt all outbound
redis-cli del wa:instance:paused:manual     # resume
```

This key is **operator-only**: the monitor never touches it, so it survives account recovery
and is not affected by the auto breaker. Remember to clear it, or outbound stays halted.

---

## 7. Escalation

- **Provider support** — once Meta onboarding (PRO-87) is complete and Cloud API is actually
  live, via Meta's Business Support channel. _(Fill in the exact support channel + account
  contact once that lands.)_
- **On-call:** `ONCALL_PHONE` (or `ADMIN_PHONE` if unset).
- If rotating numbers repeatedly, revisit §4 — a recurring ban means a behavior problem, not
  an account problem.
