# Sandbox E2E Run-Sheet — PRO-101 (Cloud API go-live verification)

**What this is:** the evidence-producing run for [PRO-101], executed on the **Meta
sandbox test number** against the **staging** environment, with the 5 registered
devices from PRO-87. It is the sandbox-scale rehearsal of the full launch gate
(`docs/PILOT_E2E_CHECKLIST.md`, PRO-64), plus the Cloud-API-specific checks that
only exist since PRO-89 (service window, delivery statuses, template retry).

**Evidence gate:** PRO-101 carries `launch-readiness`. It may not move past
In Review without the artifacts this sheet produces — the filled-in tables below,
device screenshots, and worker log excerpts, attached to the Linear issue.
A ticked box with no artifact behind it does not count.

> Keywords, expected copy and the 11 product scenarios are **not** duplicated
> here — run them from `docs/PILOT_E2E_CHECKLIST.md` and record results there.
> This sheet owns: device mapping, staging config, the Cloud-API-only checks,
> and evidence collection.

---

## Device pool (fill in once, PRO-87 step A4)

The sandbox allows **5 registered recipients**. Every number here must be
OTP-confirmed in the Meta console *and* be in the reserved test range where the
checklist requires it (a registered pro's number must exist as a pro in staging).

| Slot | Role | Number (masked) | Registered in Meta | Seeded in staging DB |
|---|---|---|---|---|
| D1 | **C** — customer | ...____ | ☐ | n/a (fresh customer) |
| D2 | **P** — primary pro (covers C's city, has slots) | ...____ | ☐ | ☐ approved + active |
| D3 | **P2** — secondary pro (reassignment target) | ...____ | ☐ | ☐ approved + active |
| D4 | **Admin** — `ADMIN_PHONE` on staging | ...____ | ☐ | ☐ admin record |
| D5 | Spare — closed-window drill / pro self-signup (scenario 10) | ...____ | ☐ | ☐ unregistered |

⚠️ Verify (PRO-88 open question): whether the 5-recipient limit also applies to
**template** sends. Record the answer here: ______ — it decides how much of the
template path PRO-64 can exercise later.

## Staging configuration (before Part 1)

- [ ] `META_ACCESS_TOKEN`, `META_APP_SECRET`, `META_VERIFY_TOKEN`, `META_PHONE_NUMBER_ID` set on staging api + worker (Railway). Store `META_WABA_ID` alongside them — no code reads it (`Settings` ignores extras); it is console/template-API bookkeeping only.
- [ ] `WHATSAPP_PROVIDER=dryrun` for Part 1 (inbound only), flipped to `cloud` for Part 2+.
- [ ] `WHATSAPP_DRY_RUN` **unset/false** when Part 2 begins — it overrides everything.
- [ ] `ENVIRONMENT=staging`, `WEBHOOK_TOKEN` set (legacy route), Sentry paging live.

---

## Part 1 — Inbound only (provider still `dryrun`; zero outbound risk)

| # | Check | How | Evidence | Pass |
|---|---|---|---|---|
| 1.1 | Webhook handshake | Meta console → Configuration → Verify and save (`https://<staging-api>/webhook/meta` + verify token) | Screenshot of the green state in the console | ☐ |
| 1.2 | Signed inbound accepted | D1 texts the sandbox number | Worker log: enqueue for D1's chat_id | ☐ |
| 1.3 | Tampered inbound rejected | `curl -X POST .../webhook/meta` with a bad `X-Hub-Signature-256` | API log: "Security Alert … X-Hub-Signature-256", HTTP 403 | ☐ |
| 1.4 | Service window opens | After 1.2: `redis-cli TTL "wa:window:<D1-chat-id>"` — chat-id format is `972XXXXXXXXX@c.us`, not the bare number | Output ≈ 86400 | ☐ |
| 1.5 | Window opens on non-normalized types | D1 sends a **sticker** | Window key refreshed; nothing enqueued | ☐ |
| 1.6 | Idempotency | Meta "resend" from webhook console (or repeat POST with same wamid) | Log: "Idempotency: skipping duplicate" | ☐ |

## Part 2 — First real sends (`WHATSAPP_PROVIDER=cloud`)

| # | Check | How | Evidence | Pass |
|---|---|---|---|---|
| 2.1 | Boot refuses without creds (negative test, optional) | Temporarily unset `META_ACCESS_TOKEN` on a staging deploy | Deploy log: `require_cloud_provider_config` ValidationError | ☐ |
| 2.2 | Watchdog sees the account | Worker up ≥2 min | Log: state probe → `authorized`; `redis-cli GET wa:instance:state` = authorized | ☐ |
| 2.3 | **First real message** | D1 texts the bot; bot replies | Screenshot of the reply **on D1** + `Message sent to ...` log | ☐ |
| 2.4 | Delivery statuses persist | After 2.3, read the message on D1 | `wa_delivery` doc for the wamid progresses accepted → sent → delivered → read | ☐ |
| 2.5 | Hebrew + emoji fidelity | Compare a rendered menu on-device vs `messages.py` copy | Photo of D1 screen | ☐ |
| 2.6 | Media inbound | D1 sends a photo with caption | Lead `media_urls` holds a **Cloudinary** URL (not `meta-media://`); AI reply references the image | ☐ |

## Part 3 — Product scenarios

Run **all 11 scenarios of `docs/PILOT_E2E_CHECKLIST.md`** on the devices above
(C=D1, P=D2, second pro=D3, Admin=D4, fresh signup=D5), staging config instead of
production. Record Pass/File **in that checklist's own table** and attach the
signed-off copy to PRO-101. Sandbox deltas to expect:

- The sender shows as Meta's **"Test Number"**, not "Proli" — display name comes with the production number.
- Scenario 8 (unmatchable city) pages via Sentry email — verify it arrives, same as production would.
- Any scenario that needs a 6th phone is out of scope on sandbox; note it as `N/A (sandbox)` rather than Fail.

## Part 4 — Closed-window drill + template retry (Cloud-API-only behavior)

> **Prerequisite:** the closed-window target must exist as an **approved pro** in
> staging before 4.1 (run Part 3 scenario 10 with D5 first, then admin-approve it —
> or simply use D3 as the target). Chat-id key format everywhere below is
> `972XXXXXXXXX@c.us` (`app/core/phone.py`) — `redis-cli` on the bare number
> returns `-2`/nothing and records a false Fail.

| # | Check | How | Evidence | Pass |
|---|---|---|---|---|
| 4.1 | Window expiry blocks free-form | Target pro stays silent >24h (or `redis-cli DEL "wa:window:<chat-id>"`), then trigger a business-initiated send to them (e.g. admin-assign a lead) | `ServiceWindowClosedError` raised; message NOT delivered | ☐ |
| 4.2 | Operator page fires, deduped | Same event ×2 | First page CRITICAL (Sentry email arrives), second only ERROR in logs | ☐ |
| 4.3 | Template approved → armed | First Meta approval lands → flip the registry entry to `APPROVED` **and** map it in `freeform_fallback()` — which returns `None` for every kind until then (small PR; note the fallback must be a **parameterless** re-engagement template) | PR link: ______ | ☐ |
| 4.4a | Proactive re-route delivers | Repeat 4.1 with the fallback armed | Target device receives the template; log shows `Service window closed … re-routing text via template`; `wa_delivery` doc has `kind: "template"` | ☐ |
| 4.4b | 131047 backstop delivers | Force the pre-send window check to pass on a truly-closed window (`redis-cli SET "wa:window:<chat-id>" forced EX 600` for a recipient Meta considers closed), then send | Meta returns a `failed` status with error 131047; `wa_delivery` doc shows `retried_with_template` | ☐ |
| 4.5 | `send_template` refuses drafts | Attempt a template send with an unapproved key (staging shell) | `TemplateNotRegisteredError` + CRITICAL log; nothing transmitted | ☐ |

---

## Sign-off

| Run # | Date | Tester | Part 1 | Part 2 | Part 3 (x/11) | Part 4 | Notes / tickets |
|---|---|---|---|---|---|---|---|
|  |  |  |  |  |  |  |  |

**Exit:** all parts green on one clean run → attach this sheet + screenshots + log
excerpts to PRO-101 → move PRO-101 to Done → PRO-64 (production launch gate) is
unblocked pending the production number.

[PRO-101]: https://linear.app/proli/issue/PRO-101/qa-cloud-api-go-live-verification-sandbox-e2e-on-5-registered-devices
