# Runbook — WhatsApp (Green API) Instance Banned / Disconnected

**Severity:** P1 — the bot goes dark. No customer or pro message is answered.
**Owner:** on-call operator (`ONCALL_PHONE`, falls back to `ADMIN_PHONE`).
**Related:** PRO-20 (deauth alert), PRO-71 (outbound circuit breaker), PRO-75 / PRO-18 (Sentry email paging), `SENTRY_SETUP.md`.

The WhatsApp instance is a **single point of failure**: every inbound and outbound
message flows through one Green API instance (`GREEN_API_INSTANCE_ID`). If WhatsApp
flags or unlinks the number, the platform cannot send. This runbook covers detection,
the automatic safeguards already in place, manual recovery, and prevention.

---

## 0. The instance states (Green API `getStateInstance`)

| `stateInstance` | Meaning | `/health` `whatsapp.status` | Outbound |
|---|---|---|---|
| `authorized` | Healthy, linked | `up` | flowing |
| `yellowCard` | **Number flagged** — Green API returns HTTP 200 but WhatsApp silently filters messages (accepted, never delivered) | `degraded` | **auto-halted** |
| `notAuthorized` | Session dropped / phone unlinked | `down` | **auto-halted** |
| `blocked` | Number blocked/banned | `down` | **auto-halted** |
| `starting` | Instance booting | `down` | **auto-halted** |
| (unreachable) | Green API not responding | `down` | **auto-halted** |

`yellowCard` is the insidious one: sends *look* successful. The rest fail outright.

---

## 1. Detection

**Automatic (primary):** the worker's deauth watchdog (`monitor_service.check_whatsapp_instance_state`)
polls `getStateInstance` every **2 min** (`WA_STATE_CHECK_INTERVAL_MINUTES`). After the
instance has been non-authorized for **> 5 min** (`WA_STATE_ALERT_THRESHOLD_MINUTES`) it
emits `logger.critical` → **Sentry → email** to the on-call operator, then re-pages every
**60 min** (`WA_STATE_REALERT_MINUTES`) until recovery. The page text branches:
- `yellowCard` → "messages are being silently filtered by WhatsApp"
- `notAuthorized` / `blocked` / unreachable → "no messages are being processed"

> ⚠️ Paging depends on `SENTRY_DSN` being set on the Railway `worker` service **and** a
> Sentry alert rule `level = fatal → email` existing. See `SENTRY_SETUP.md`. If those
> aren't configured, **you will not be paged** — verify them as part of go-live (PRO-18).

**Manual checks:**
```bash
# Live instance state + health
curl -s https://<your-api-host>/health | jq '.checks.whatsapp'
# → { "status": "up|degraded|down", "state": "authorized|yellowCard|..." }

# Or hit Green API directly
curl -s "https://api.green-api.com/waInstance$GREEN_API_INSTANCE_ID/getStateInstance/$GREEN_API_TOKEN"
```
Also: worker logs show `[WA Monitor]` lines; the Green API console (https://console.green-api.com)
shows the instance state and QR/authorization status.

---

## 2. What happens automatically (do not fight it)

The moment the instance is non-authorized, the **circuit breaker (PRO-71)** engages:

- The monitor sets Redis key `wa:instance:paused` (TTL `WA_STATE_PAUSE_TTL_SECONDS` = 360s,
  refreshed every tick while down).
- Every outbound send (`send_message`, `send_file_by_url`, typing indicators) short-circuits
  **before any HTTP call** and logs `⛔ Outbound halted …`. Nothing is silently dropped into
  a filtering instance.
- On recovery (`authorized`), the monitor clears `wa:instance:paused`, sends a recovery notice,
  and outbound resumes automatically.

**Implication:** during the outage, inbound messages that arrive are still processed by the
worker, but any reply is **suppressed** (not queued). Customers who message during the outage
get no answer and will not be retroactively answered after recovery — see §4.

**SMS fallback?** **No.** SMS was removed entirely (PRO-75). The operator alert path is
Sentry → email; there is no customer-facing fallback channel. Customer communication during
downtime is manual (§4).

---

## 3. Recovery

> ### ⚠️ `yellowCard` specifically — what does NOT work
> `yellowCard` is a **WhatsApp-side** restriction on the *phone number's reputation*, not a
> Green API session problem. It is **not cleared** by any of the following (all operate on the
> wrong layer — WhatsApp never sees the change):
> - **Rotating the Green API token** — that's just the API credential.
> - **Rebooting the instance** — restarts Green API's session; the flag persists.
> - **Re-scanning the QR** — re-links the device (fixes `notAuthorized`, irrelevant to a flag).
>
> Creating a **new Green API instance** does not help either, as long as it is linked to the
> same number — an instance is just an API session pointed at the number, so the flag follows
> it. Repeated session teardown/re-registration is itself a spam signal, so extra churn on an
> already-flagged number is actively harmful, not neutral.
>
> **Silence alone does not lift a `yellowCard`.** Going quiet is *necessary* — it stops the
> score dropping further — but it is not *sufficient*. The flag is a trust score on the phone
> number, and a score recovers from **positive signal**, not from the absence of negative
> signal. A number that has only ever emitted automated outbound and is now completely silent
> generates no positive signal and can sit `yellowCard` indefinitely.
>
> What actually lifts it is **rebuilding reputation with genuine human use** — see §3d. Budget
> **days, not hours**: roughly 5–7 days of real usage, and longer if the number has no history
> of human conversation.
>
> **Do not send test messages to "check if it's back"** — every automated send while flagged
> pushes yellow → red (`blocked`, a permanent ban). Poll `getStateInstance` / `/health`
> instead; the watchdog already does this every 2 min. If it flips to `blocked`, or ~2 weeks of
> genuine rehabilitation (§3d) produce no change, treat the number as burned and rotate to a
> fresh, warmed number (§3b).

### 3a. Re-link / re-authorize the existing instance (most common)
1. Open the Green API console → your instance → **QR / Authorization**.
2. On the phone running the WhatsApp account: **WhatsApp → Settings → Linked Devices →
   Link a Device**, scan the QR shown in the console.
3. Wait up to ~2 min; the watchdog's next poll should report `authorized`.
4. Confirm: `curl …/health | jq '.checks.whatsapp'` → `status: "up"`, and worker logs show
   `✅ [WA Monitor] Green API instance recovered`.
5. The breaker auto-releases; outbound resumes. No deploy needed.

### 3b. Rotate to a fresh instance (if the number is banned, not just unlinked)
1. Provision a new Green API instance (new `GREEN_API_INSTANCE_ID` + `GREEN_API_TOKEN`),
   linked to a **different, warmed** number (see §5).
2. Set both on the Railway `api` **and** `worker` services (both build the client):
   ```bash
   railway variables --set "GREEN_API_INSTANCE_ID=<new>" --service worker --environment Production
   railway variables --set "GREEN_API_TOKEN=<new>"       --service worker --environment Production
   railway variables --set "GREEN_API_INSTANCE_ID=<new>" --service api    --environment Production
   railway variables --set "GREEN_API_TOKEN=<new>"       --service api    --environment Production
   ```
   Setting a variable redeploys the service.
3. Re-point the Green API webhook to `https://<your-api-host>/webhook` (and re-apply
   `WEBHOOK_TOKEN` if used).
4. Confirm state `authorized` and a test message round-trips.

### 3c. If Redis kept the breaker stuck after a genuine recovery (rare)
The breaker key auto-expires after 360s, but to force-clear:
```bash
redis-cli del wa:instance:paused
```
Do **not** delete `wa:instance:paused:manual` unless you intended to (that's the manual switch, §6).

### 3d. Rehabilitate a `yellowCard`ed number (the actual fix)

Reputation is rebuilt by making the number look like a **person's phone**, not a bot endpoint.
The API stays completely out of it for the whole window.

**Step 1 — guarantee zero automated sends.**
The PRO-71 breaker already halts outbound while the state is non-`authorized`, but it is set by
the worker's *scheduled* monitor job and the key carries a 360s TTL. Between worker boot and the
first monitor tick the key is absent and outbound is **fail-open** — a webhook landing in that
window sends a real message from a flagged number. Close the gap explicitly:

```bash
railway variables --set "WHATSAPP_DRY_RUN=true" --service worker --environment Production
railway variables --set "WHATSAPP_DRY_RUN=true" --service api    --environment Production
```

The dry-run guard runs **before** the breaker check in every send method, so it has no timing
window at all. Nothing is lost by enabling it: a `yellowCard`ed instance silently filters
outbound anyway. **Remove it before the real E2E run** — otherwise the bot is mute and you will
debug a working system for an hour.

**Step 2 — use the number as a human, on a real handset.**
Put the SIM in a phone and use the **normal WhatsApp mobile app** (not Green API, not a linked
device driven by code):
- Have 5–10 real people **save the number as a contact** and message it first. Inbound-first
  traffic from contacts who have you saved is the strongest positive signal.
- Reply like a person: irregular timing, varied wording, no templates, no bulk-identical text.
- No links, no attachments, no forwards for the first few days.
- Keep volume low and conversational — a handful of real threads beats any volume of outbound.

**Step 3 — wait, and poll passively.**
Re-probe `getStateInstance` **at most once or twice a day**. Do not send to test. The state flips
to `authorized` on its own when the score recovers.

**Step 4 — ramp back slowly.**
After it reports `authorized`, remove `WHATSAPP_DRY_RUN` and resume at low volume (single-digit
automated sends the first day, growing over a week). A number with `yellowCard` history re-flags
faster than a clean one, so treat the first week back as fragile.

> If the pilot cannot wait out this window, run §3b (fresh warmed number) **in parallel** rather
> than instead — warming a replacement takes about the same week, so starting it costs nothing
> and removes the single point of failure either way.

---

## 4. Customer communication during downtime

- There is **no automated fallback** (no SMS). Replies are suppressed while down.
- Messages customers send during the outage are **not** answered retroactively after recovery.
- If the outage is prolonged and customer-facing, communicate out-of-band (whatever channel
  the business uses — phone calls, a status note), and after recovery consider a manual sweep
  of leads created/updated during the window (`/health/leads` shows `pending_review_count` /
  `stuck_contacted_count`).

---

## 5. Prevention (avoid triggering a ban / yellowCard)

WhatsApp flags numbers for **unsolicited / spammy** behavior. To keep the number healthy:

- **Never cold-initiate.** Only message users who messaged first. Test scripts are guarded
  against cold-initiating to real numbers (PRO-72) — keep that guard on.
- **Warm up new numbers before they ever touch Green API** — a brand-new number wired straight
  into the API is the classic way to earn a `yellowCard` in week one. Use it as a human phone
  first (same technique as §3d step 2, ~a week), *then* link the instance and ramp automated
  volume up over several days. Warming is not "go slower on day one"; it happens before day one.
- Avoid identical bulk messages, high send rates, and messaging numbers that repeatedly report/block.
- Keep opt-in / consent flow intact (the consent gate exists for this reason).
- Watch `whatsapp.status` on `/health` and the Sentry pages — `degraded` (yellowCard) is an
  early warning before a hard ban.

---

## 6. Manual kill switch (halt outbound without a deploy)

To stop **all** outbound immediately (e.g. a suspected runaway send, or to freeze the system
while investigating) — independent of instance state:

```bash
redis-cli set wa:instance:paused:manual 1   # halt all outbound
redis-cli del wa:instance:paused:manual     # resume
```

This key is **operator-only**: the monitor never touches it, so it survives instance recovery
and is not affected by the auto breaker. Remember to clear it, or outbound stays halted.

---

## 7. Escalation

- **Green API support** — via the Green API console / your support plan. _(Fill in the exact
  support channel + account contact for your plan.)_
- **On-call:** `ONCALL_PHONE` (or `ADMIN_PHONE` if unset).
- If rotating numbers repeatedly, revisit §5 — a recurring ban means a behavior problem, not
  an instance problem.
