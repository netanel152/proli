# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## CRITICAL: text-only menus, and the single outbound egress

**Every WhatsApp menu must stay text-based** (numeric or keyword replies). Example: instead of Approve/Reject buttons, send `"Reply '1' to approve, '2' to reject."`

The original reason was a limitation of the legacy WhatsApp vendor. That vendor is gone (PRO-85 — instance deleted, tariff cancelled), and the rule now rests on a different footing: `WhatsAppProvider.send_interactive` exists on the ABC and the PRO-89 `CloudAPIProvider` transport can send it, but **nothing in any flow may call it** — no template is approved yet (PRO-87 onboarding, the Business Portfolio and template review, is still open) and adopting buttons over numeric menus is an explicit product decision not yet made (see the PRO-88 catalog). The legacy-vendor-shaped `send_interactive_buttons` helper was removed in April 2026 and stays removed.

**All outbound traffic goes through `app/providers/whatsapp/` (PRO-86).** Never build a provider directly, never call an HTTP client at a vendor endpoint — both bypass the circuit breaker and the operator kill switch, which is precisely what caused the yellowCard incident. Synchronous callers (the Streamlit admin panel) use `app.providers.whatsapp.sync.send_text_sync`.

A CI step fails the build on any reference to the old vendor's domain (`green` + `-api.com`, either spelling) anywhere in the repo — see the "Guard" step in `.github/workflows/tests.yml`. Write it split like that if you ever need to mention it in prose, or the guard will trip on your own sentence. A second "Guard — all outbound traffic through the provider facade" step fails the build on `httpx`/`requests` imports under `app/services/` (allowlisting `geocoding_service.py`) and on `CloudAPIProvider(`/`DryRunProvider(` construction anywhere in `app/` outside `app/providers/whatsapp/`.

## Commands

### Branches and how a release happens

| branch | role | deploys to |
|---|---|---|
| `dev` | integration / default branch — every feature PR merges here | **staging**, automatically |
| `production` | release branch — only ever fast-forwarded from `dev` | **production** |
| `feature/*`, `fix/*`, `chore/*` | where all work happens | nothing |

`dev` was named `master` until 2026-08-22. The rename is cosmetic in intent — `dev` says what the branch is for, and stops "master is production" being a reasonable guess — but it is load-bearing in one way: **a stale release branch is indistinguishable from a quiet one**, which is how production once sat six weeks and 132 commits behind while crash-looping (PRO-128). Staging has the same failure mode and its own detector: the `🔎 Verify staging deployed this commit` workflow fails any push to `dev` that staging does not build and serve within 12 minutes (PRO-155 — it compares the pushed SHA against authenticated `/health`'s `commit` field; needs the `STAGING_HEALTH_TOKEN` repo secret).

**Release by running the `🚢 Promote dev → production` workflow** (Actions tab → Run workflow). It is the supported path and does the whole thing: refuses a non-fast-forward, refuses a `dev` whose CI is not green, moves the ref, then **waits for production to restart and report healthy** and fails the build if it does not. Tick `dry_run` to see the commit range without changing anything.

That last step is why the workflow exists rather than a one-liner. Production's auto-deploy has silently no-opped before, and a promotion you cannot distinguish from a no-op is exactly the failure PRO-128 documents — production sat six weeks behind while looking promoted.

The equivalent by hand, if you need it — but then **verify it deployed yourself**, per "Which branch deploys where" in `docs/RAILWAY_SETUP.md`:

```bash
git fetch origin
git merge-base --is-ancestor origin/production origin/dev   # must succeed
git push origin dev:production
```

How far behind production is, at any time:

```bash
git rev-list --count origin/production..origin/dev
```

### One-time clone setup

```bash
git config core.hooksPath .githooks   # blocks direct pushes to dev (PR-only workflow)
```

**Never commit or push to `dev` directly** — all work goes through a feature branch + PR (GitHub branch protection enforces this server-side; `.githooks/pre-push` is the local backstop). `production` is written to only by the fast-forward promotion above.

### Claude Code project config (shared via git)

Everything under `.claude/` (except `settings.local.json`, which is gitignored) plus `.mcp.json` is checked in and applies to every clone.

**MCP servers (`.mcp.json`)** — auto-approved for teammates by `enableAllProjectMcpServers: true` in `.claude/settings.json`:

| server | transport | one-time setup per machine |
|---|---|---|
| `linear` | HTTP `mcp.linear.app` | OAuth browser sign-in on first use |
| `sentry` | HTTP `mcp.sentry.dev` | OAuth browser sign-in on first use |
| `context7` | HTTP `mcp.context7.com` | none (rate-limited without an API key) |
| `railway` | stdio `railway mcp` (bundled in Railway CLI ≥ ~5.x) | install Railway CLI + `railway login` |
| `redis` | stdio `uvx redis-mcp-server` | install [`uv`](https://docs.astral.sh/uv/); reads `REDIS_URL` (defaults to `redis://localhost:6379/0`) |
| `mongodb` | via the `mongodb@claude-plugins-official` plugin (enabled in `settings.json`) | set `MDB_MCP_CONNECTION_STRING` env var, or it connects per-call arguments |

**Use them only when the task actually needs the external system:**

- `linear` — issue-driven work (`/take-issue`, status moves, comments). Not for reading code.
- `sentry` — triaging a production error / paging trail. Logs-first for local issues (`/logs`).
- `context7` — current library docs (FastAPI, pydantic v2, ARQ, Streamlit, Motor) when the answer may postdate training. Not for questions the codebase itself answers.
- `railway` — deploy state, service logs, env inspection. Read-only tools are allowlisted; mutating ones prompt.
- `redis` / `mongodb` — live FSM/context/lead debugging (`/user-debug`, `/db-status`). Unit tests never need them (mongomock/fakeredis).

Deliberately excluded: **GitHub** (the `gh` CLI covers PRs/CI and is allowlisted — an MCP would duplicate it and bloat context), **Cloudinary** (rarely needed; opt in per-machine with `claude mcp add --transport sse cloudinary https://asset-management.mcp.cloudinary.com/sse`). A server you personally never use can be turned off per-machine via `"disabledMcpjsonServers": ["<name>"]` in `.claude/settings.local.json` (gitignored) — don't remove it from `.mcp.json` for everyone.

**Hooks (`.claude/settings.json` → `.claude/hooks/`)** — run through the cross-platform launcher `run-hook.sh`, which finds the project venv interpreter (Windows or POSIX layout) and degrades to a no-op on machines without Python:

- `SessionStart` → `session-start-context.sh`: injects real branch/commit/dirty-tree state into each new session (the shared-worktree trap below is why).
- `PreToolUse(Bash)` → `pre-bash-guard.py`: blocks `rm -rf` on dangerous targets, redirects into `.env`, force-pushes to protected branches, commit/push while on `dev`/`production`, and mongo `dropDatabase`/`drop()`. Decision logic is pinned by `tests/test_pre_bash_guard.py`.
- `PreToolUse(Edit|Write)` → `pre-edit-protect.py`: blocks edits to `.env` and anything under `.git/`.
- `PostToolUse(Edit|Write)` → `post-edit-format.py`: auto-runs `black` and reports `flake8` findings on touched `.py` files.

### Two shell traps on Windows, both of which fail *silently*

**`git show <rev>:<path>` is mangled by Git Bash.** MSYS path conversion rewrites the argument, and the error names a path you never typed:

```
$ git show origin/dev:.github/workflows/tests.yml
fatal: ambiguous argument 'origin\dev;.github\workflows\tests.yml': unknown revision or path not in the working tree
```

Note the `:` became `;` and the slashes flipped. If the command is inside a pipeline with `2>/dev/null`, you get **empty output and exit 0** — which reads as "that file/content isn't there" rather than "the command was broken". That misled a real check of whether a fix had landed on `dev`. Two fixes, either works:

```bash
git show "origin/dev:./.github/workflows/tests.yml"      # leading ./ — simplest
MSYS_NO_PATHCONV=1 git show origin/dev:.github/workflows/tests.yml
```

**The working tree is shared with parallel sessions.** Another session can move `HEAD` underneath you between one command and the next, so a file you are about to edit may not be from the branch you think you are on — and nothing announces it. Re-check immediately before editing or committing, and never trust branch state established earlier in a session:

```bash
git branch --show-current && git log -1 --oneline && git status --short
```

Stage by explicit path for the same reason; `git add -A` will happily commit the other session's in-flight work.

### Running several issues at once (git worktrees)

The hazard above is about the *working tree*, not the repo. To run issues in parallel, give each one its own tree — one worktree per issue, one Claude session per worktree, all under `D:\Projects\proli-wt\`. Sessions then cannot move `HEAD` under each other at all:

```bash
git fetch origin
git worktree add -b <linear-branch-name> D:/Projects/proli-wt/pro-162 origin/dev
git -C D:/Projects/proli-wt/pro-162 branch --unset-upstream
cp .env D:/Projects/proli-wt/pro-162/
cp .claude/settings.local.json D:/Projects/proli-wt/pro-162/.claude/
```

Five things that setup does **not** do for you:

- **`worktree add -b <branch> <dir> origin/dev` sets the new branch's upstream to `origin/dev`.** A bare `git push` then aims straight at the protected integration branch. Unset it, as above, and push with `git push -u origin HEAD`.
- **`.env`, `venv/` and `.claude/settings.local.json` are gitignored**, so a fresh worktree has none of them. `.env` matters most: config loads at import, so without it `Settings` refuses to construct and the suite cannot even collect.
- **No venv in the worktree, and none is needed.** There is no `pyproject.toml`/`setup.py`, so nothing is ever pip-installed and imports resolve from the current directory — the root interpreter works from any tree: `D:/Projects/proli/venv/Scripts/python.exe -m pytest -q` with the worktree as cwd. `core.hooksPath` lives in `.git/config`, which worktrees share, so the pre-push branch protection is inherited — but the `PostToolUse` black/flake8 hook looks for a *project-local* venv and **silently no-ops** in a worktree. Run `black`/`flake8` by hand there.
- **A worktree is a different project to Claude Code.** Session state is keyed by working directory, so `D:\Projects\proli-wt\pro-162` gets its own `~/.claude/projects/D--Projects-proli-wt-pro-162/` — its own auto-memory, its own permission history. Memory notes do **not** follow you into a worktree; this file does, which is why these conventions live here rather than in a memory note.
- **A session spawned *from* another session saves no transcript, so `--resume` cannot find it.** Claude Code sets `CLAUDE_CODE_CHILD_SESSION=1` in a session's environment; anything launched from there inherits the marker, and the marker disables transcript writing (the CLI says so itself: *"inherited CLAUDE_CODE_CHILD_SESSION marker … restart with CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1 to keep future transcripts"*). This is not theoretical: the 2026-08-29 batch left `~/.claude/projects/D--Projects-proli-wt-pro-123/` completely empty after hours of work, so when that session died holding 963 uncommitted lines there was nothing to resume — the work had to be reconstructed from the worktree diff. `.claude/settings.json` now sets `CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1` for every session in this project, which fixes it for anything started normally. If you launch a track some other way, unset `CLAUDE_CODE_CHILD_SESSION` and set the force flag yourself.

**Launching tracks with Windows Terminal: `wt` eats semicolons.** `;` is `wt`'s own command separator, so `wt … pwsh -NoExit -Command "$env:X='1'; claude …"` is silently truncated at the first `;` and you get a bare shell with no Claude in it — tabs open, titles look right, nothing runs. Put the whole launch in a `.ps1` and run `pwsh -NoExit -File <script>` so no semicolon ever reaches the `wt` line.

**Merges no longer have to be serialized.** `docs/TESTING.md`'s `Current status: N passed` used to be enforced as an *equality* — the guard failed below **and** above — so two open PRs that both added tests wrote conflicting counts and whichever merged second failed as a stale baseline. The guard is now a **floor**: below the line is a regression and fails the build; above it posts a `::warning` and passes, and `.github/workflows/refresh-test-baseline.yml` moves the line once the change is on `dev`. Two branches that both add tests can now merge in either order.

**That refresh job cannot actually open its PR in this repo**, and has never been observed doing so: `gh pr create` returns *"GitHub Actions is not permitted to create or approve pull requests"* because **Settings → Actions → General → Workflow permissions → Allow GitHub Actions to create and approve pull requests** is off. It pushes the `chore/refresh-test-baseline-<sha>` branch and then goes red at the last step. The workflow's own header treats that red run as the signal to bump the line by hand, which works — but it means a red 🔢 run on `dev` says *"the line drifted"*, not *"the tests broke"*, and the branch it pushed is left dangling. Turning the setting on makes the job do what the sentence above promises.

Still rebase before pushing — the *content* of `docs/TESTING.md` (and every other file) conflicts normally:

```bash
git fetch origin && git rebase origin/dev
D:/Projects/proli/venv/Scripts/python.exe -m pytest -q   # count comes from the summary line
# bumping the line yourself is welcome but optional — the refresh workflow catches it
git push --force-with-lease
```

**Disjoint file footprints are the whole selection criterion.** Two tracks editing one module spend more time resolving conflicts than the parallelism saves. Some work is never a parallel track: PRO-139 (extracting the dispatcher rewrites what every flow issue touches — it runs alone), the copy chain PRO-164/168/169 (all rewriting `messages.py`/`prompts.py`, serial by construction), and anything labelled `launch-readiness`/`ops-verification`, whose Done criterion is an operator run rather than a merged PR.

Teardown once a track's PR is merged: **`/cleanup-worktrees`** (or `bash scripts/cleanup_worktrees.sh`, `--dry-run` to preview, a name to limit it to one track). It sweeps every worktree whose PR is MERGED, removes the directory, prunes, and deletes the local branch — with `-D`, because `-d` cannot see a squash merge. It refuses to touch a worktree with uncommitted changes, unpushed commits, or an unmerged branch, and prints the reason for each one it skips.

Run it from the main checkout, never from inside a worktree and never as a step of `/take-issue`: on Windows a process's cwd is locked, so a session cannot delete the folder it is running in. That is what leaves the empty, undeletable directories behind.

### Local Development (run all three in separate terminals)

```bash
# Backend API (FastAPI)
uvicorn app.main:app --reload --port 8000

# Background Worker (ARQ + APScheduler)
python -m app.worker

# Admin Panel (Streamlit)
streamlit run admin_panel/main.py
```

### Docker (recommended)

```bash
docker-compose up --build -d
# Backend: http://localhost:8000
# Admin:   http://localhost:8501
# Worker logs: docker-compose logs -f worker
```

### Database

```bash
python scripts/seed_db.py          # Seed initial data
python scripts/create_indexes.py   # Create MongoDB indexes (runs automatically on every API boot; indexes are declared in its INDEX_SPECS table — manual run only primes a fresh DB)
python scripts/clear_history.py    # Clear chat history
```

### Testing

```bash
# Run all unit tests (uses mongomock — no real DB needed)
pytest

# Run a single test file
pytest tests/test_matching_service.py

# Run only integration tests (requires MONGO_TEST_URI in .env)
pytest -m integration

# Run with verbose output
pytest -v
```

Expected baseline lives in `docs/TESTING.md` ("Current status" line) — the single source of truth for the pass/skip count, enforced as a **floor** by the "Guard — test baseline floor" CI step: fewer passed than the line fails the build, more passed posts a warning and passes. Bumping the line in the same PR is welcome; if you don't, `refresh-test-baseline.yml` runs on `dev` after the merge and goes red until somebody moves it (it cannot open its own PR — see the note above). Integration tests are skipped without `MONGO_TEST_URI`.

Canonical run is `pytest` inside the project virtualenv (PRO-50, pinned `pydantic`/`pydantic-core`/`pydantic-settings` for deterministic resolution). Unit tests need neither a real MongoDB (in-memory `mongomock`) nor a real Redis (in-memory `fakeredis`, PRO-78) — no external services required.

### Linting / Formatting

```bash
black .
flake8 .
```

## Architecture

Proli is an AI-powered WhatsApp CRM for Israeli service professionals (plumbers, electricians, etc.). It runs as three cooperating processes:       

### Process 1: FastAPI Backend (`app/`)

Entry point for inbound WhatsApp webhooks. Its only job is to validate the incoming payload, enqueue a task to Redis via ARQ, and immediately return `200 OK`. All heavy lifting is deferred to the Worker. Routes: `POST /webhook`, `GET/POST /webhook/meta` (PRO-89 — Meta Cloud API subscription handshake and inbound; live even under `WHATSAPP_PROVIDER=dryrun`), `GET /health` (public response is only `status` + `uptime_seconds`; the per-dependency `checks` detail requires the `X-Health-Token` header, PRO-136), and `GET /health/leads` (token-only — 403 without `X-Health-Token`; error bodies are a fixed string, the real exception goes to logs/Sentry).

### Process 2: ARQ Worker (`app/worker.py` + `app/core/arq_worker.py`)

Picks up `process_message_task` jobs from Redis and calls `workflow_service.process_incoming_message`. Also hosts APScheduler for periodic jobs (SOS healer every 10 mins, stale monitor every 30 mins, stale lead nudger every 4h, daily agenda at 08:00 Israel time, pro-approval SLA check every 5 mins, WhatsApp instance deauth watchdog every 2 mins); the three long-interval jobs (SOS Reporter, stale lead nudger, lead janitor) also run once shortly after boot so a deploy cadence shorter than their interval can't starve them (PRO-176).

### Process 3: Streamlit Admin Panel (`admin_panel/`)

Protected by bcrypt cookie-based auth. Views for lead management, professional profiles, and schedule management. Auto-refresh (PRO-141) is a client-side timer (`streamlit-autorefresh`, armed in the sidebar above the view dispatch) rather than the old `time.sleep(interval); st.rerun()` at the end of the script run, which blocked the page for up to 120s per tick; it pauses (and says so) while a `st.data_editor` holds unsaved rows, per `admin_panel/core/refresh.py`.

### Service Layer (`app/services/`)

| Service | Responsibility |
|---|---|
| `workflow_service.py` | Central orchestrator — routes messages, manages FSM states, delegates to customer/pro/admin flows; handles emergency bypass and loyalty checks. The head of its dispatch runs `dispatch_guards.GUARD_CHAIN` first (PRO-179/PRO-180, slices A1–A2 of the PRO-139 decomposition) |
| `dispatch_guards.py` | Ordered guard chain (`GUARD_CHAIN`, a tuple — order is load-bearing) for the first 17 branches of `workflow_service`'s dispatch, through the holding-state cluster: admin routing wizard, global reset, help/menu, inbound rate limit, zero-touch intent confirmation, consent gate, politeness, customer status pull, SOS/human handoff, emergency hoist (PRO-121), pro-approval soft hold, paused-for-human, cancel confirmation, reschedule selection, loyalty confirmation, new-or-existing, and the BOOKED cancel/reschedule interceptor (PRO-179/PRO-180) |
| `customer_flow.py` | Customer completion checks (capped + cooled-down per lead; `"2"`/`עדיין לא` acknowledges and restarts the cooldown instead of falling through to the AI); ratings via a tolerant free-text `parse_rating` (digits, Hebrew number words, stars, "X מתוך 5") with a bounded re-prompt (`MAX_RATING_REPROMPTS`) on an unreadable reply and a 48h liveness window (`RATING_PROMPT_MAX_AGE_HOURS`) on the prompt itself (PRO-122); reviews, including an explicit skip path (`Messages.Keywords.SKIP_TOKENS`) that keeps the numeric rating but drops the comment; and rescheduling |
| `pro_flow.py` | Professional text commands (approve, reject, pause, resume, finish, cancel booked job, details, summary, **מצא** — rate-limited stuck-lead search) — implements Dynamic Dashboard and availability controls, rendered (both the dashboard's contextual subset and `Pro.HELP_MENU`'s full list) from the single canonical `Pro.CMD_*` row set (PRO-168) so the two menus cannot advertise different keywords; **עדיין עובד** silences the finish-reminder counters on all of that pro's BOOKED leads without closing any of them; on finish, captures an optional **final_price** (PRO-33) via a non-blocking `PRO_AWAITING_FINAL_PRICE` prompt and derives `commission_amount`; on reject (PRO-117), atomically claims the lead (`rejected_by`/`last_rejected_at`, `expected_status=NEW`) then hands off to `monitor_service.reassign_lead(lead, notify_old_pro=False)` for an automatic rematch — never a dead end — falling back to `_escalate_rejected_lead` (PENDING_ADMIN_REVIEW + admin page + customer message) if the rematch fails or finds no replacement |
| `admin_flow.py` | Admin routing wizard (`ניהול` keyword): list PENDING_ADMIN_REVIEW leads → self-assign or pick a pro; assignment notifies the pro via `notification_service.notify_pro_new_lead` and gives the admin tri-state feedback — ✅ on success, ⚠️ if the offer didn't reach the pro (assignment stands, contact manually), ⚠️ if the post-write lead lookup missed; the customer is only told a pro was found once the offer actually reached the pro |
| `media_handler.py` | Media type detection and download (images, audio, video) |
| `ai_engine_service.py` | Gemini with adaptive fallback (Flash Lite 3.1 → Flash 3.5 → Flash 2.5 → Flash 1.5); multimodal; 5-turn context window; non-blocking token accounting |
| `matching_service.py` | Progressive `$geoNear` aggregation (10 km → 20 km → 30 km); falls back to regex city match; load-balances by max 3 active leads per pro |
| `state_manager_service.py` | Redis-backed FSM per `chat_id` (`UserStates` enum); supports custom TTL per state |
| `context_manager_service.py` | Stores last 20 messages per `chat_id` in Redis |
| `lead_manager_service.py` | CRUD for leads in MongoDB |
| `notification_service.py` | Sends WhatsApp notifications to pros; SOS alerts; on-call paging via `send_oncall_alert` (WhatsApp when the instance is authorized, else `page_critical` → Sentry/email page); owns the shared lead-offer builder — `build_new_lead_message` (pure) and `notify_pro_new_lead` (sends offer + navigation link, fail-open) — used by `monitor_service`'s reassignment path and `admin_flow`'s assignment path so the message and its media/Hebrew-fallback policy can't drift between callers |
| `monitor_service.py` | Stale job detection, reassignment (shared `reassign_lead` helper — escalates to PENDING_ADMIN_REVIEW, with an immediate admin alert, once `MAX_REASSIGNMENTS` is exhausted; PRO-63, never closes the lead; notifies the new pro via `notification_service.notify_pro_new_lead`, and escalates to PENDING_ADMIN_REVIEW with `escalation_reason: "pro_offer_send_failed"` plus an operator page if that offer doesn't reach the pro (closed 24h window / breaker / raised send) instead of reporting a false success to the customer; excludes every pro in the lead's `rejected_by`, not just the current one; re-arms the PRO-56 approval SLA on success for non-CONTACTED leads instead of clearing state; `notify_old_pro=False` skips the `PRO_LOST_LEAD` message for a pro who explicitly rejected — shared with `pro_flow.py`'s PRO-117 reject-rematch handoff), stale lead reminders (nudger — also gated by a per-lead `STALE_LEAD_REMINDER_COOLDOWN_HOURS` cooldown so the job's boot run can't burn all `MAX_PRO_REMINDERS` across a few quick deploys, PRO-176), pro-approval SLA monitor (`check_pro_approval_sla` — nudges a silent pro, then offers the customer reassignment, gated to business hours per PRO-73), escalation to PENDING_ADMIN_REVIEW, the SOS Reporter (`send_periodic_admin_report` — pages the operator once per newly-stuck lead via `page_operator`, atomically claiming each with `admin_reported_at` so it isn't re-paged for `SOS_REPORT_REPAGE_HOURS`; the standing already-paged backlog is logged, not re-paged; `admin_reported_at` is cleared whenever the lead gets a fresh owner so a resolved-then-broken-again lead can page immediately), and WhatsApp account deauth detection (`check_whatsapp_instance_state`; skipped for non-transmitting providers) |
| `app/providers/whatsapp/` | Single outbound egress (PRO-86, not under `app/services/`) — `WhatsAppFacade` owns the PRO-71 circuit breaker (fail-closed per PRO-82 on an absent `wa:instance:state` confirmation) and the `wa:instance:paused`/`wa:instance:paused:manual` kill switch, fail-open on Redis error; provider selection via `WHATSAPP_PROVIDER` (`dryrun` default — logs, never transmits; `cloud` — the PRO-89 `CloudAPIProvider`, sends text/file/template/interactive via the Meta Graph API and enforces the 24h customer-service window (`window.py`, Redis-backed, fail-open) — a closed window with no approved fallback template pages the operator, then (PRO-159) the facade catches the resulting `ServiceWindowClosedError`/`TemplateNotRegisteredError` and returns `None` — the same "blocked, not sent" answer the circuit breaker gives, instead of crashing `process_message_task`; `template_registry.py` is the PRO-88 catalog as code, every entry `DRAFT` until PRO-87 lands approvals; `delivery.py` persists per-message delivery statuses in `wa_delivery` and retries a window-closed send as a template); text-only sends (interactive buttons defined on the ABC and sendable by Cloud API, but no flow calls them — see the note above); `app.providers.whatsapp.sync.send_text_sync` bridges the synchronous admin panel |
| `cloudinary_client_service.py` | Media upload/retrieval |
| `security_service.py` | Rate limiting via Redis — coarse fixed-window webhook DDoS shield (`check_rate_limit`), per-customer inbound sliding window (`check_sliding_window`), and daily per-chat AI/multimodal cost cap (`check_and_increment_daily_ai_cap`, Israel-time reset). Pros/admins exempt; all checks fail-open |

### Data Layer

- **MongoDB**: Primary store — `users` (pros + customers), `leads`, `slots`, `messages`, `settings`, `reviews`, `consent`, `audit_log`, `admins`, `wa_delivery` (PRO-89 — per-wamid outbound delivery statuses, kept out of `messages` so status callbacks never get replayed into the AI context)  
- **Redis**: ARQ task queue + context cache (chat history) + state machine (FSM)

### Key Constants (`app/core/constants.py`)

- `LeadStatus`: `contacted → new → booked → completed/rejected/closed/cancelled/pending_admin_review`
- `UserStates`: `IDLE`, `PRO_MODE`, `CUSTOMER_MODE`, `AWAITING_INTENT_CONFIRMATION`, `AWAITING_ADDRESS`, `AWAITING_CONSENT`, `AWAITING_PRO_APPROVAL`, `PAUSED_FOR_HUMAN`, `AWAITING_RESCHEDULE_TIME`, `AWAITING_LOYALTY_CONFIRMATION`, `AWAITING_NEW_OR_EXISTING`, `AWAITING_CANCEL_CONFIRMATION`, `PRO_SELECTING_JOB_TO_FINISH`, `PRO_SELECTING_JOB_TO_CANCEL`, `PRO_AWAITING_FINAL_PRICE`, `ONBOARDING_*`, `ADMIN_SELECTING_LEAD`, `ADMIN_SELECTING_ACTION`, `ADMIN_SELECTING_PRO`
- `WorkerConstants.MAX_PRO_LOAD = 3`: max concurrent leads per professional
- `WorkerConstants.MAX_CUSTOMER_COMPLETION_CHECKS = 2` / `CUSTOMER_COMPLETION_CHECK_COOLDOWN_HOURS = 6`: cap and cooldown on the "did the job finish?" nudge sent to a customer for one booked lead — the customer-side mirror of `MAX_PRO_REMINDERS`. The stale-job monitor re-runs every 30 min and a lead stays BOOKED (and therefore inside the 6–24h Tier-2 window) until somebody answers, so without these the check re-sent once per open lead on every tick. The predicate is `customer_flow.completion_check_due_filter`, applied both in the Tier-2 query and again inside `send_customer_completion_check`'s atomic `find_one_and_update` claim, so two worker replicas can't both win
- `WorkerConstants.MAX_RATING_REPROMPTS = 2`: how many times an unparseable reply to the 1-5 rating prompt is re-prompted before `waiting_for_rating` is released and the message reaches the dispatcher untouched (PRO-122)
- `WorkerConstants.RATING_PROMPT_MAX_AGE_HOURS = 48`: how long the 1-5 rating prompt stays live; `waiting_for_rating` has no other expiry, so without this window a prompt ignored months ago could still capture the next bare digit the customer typed (PRO-122)
- `WorkerConstants.SOS_TIMEOUT_MINUTES = 60`: reassignment trigger threshold
- `WorkerConstants.SOS_REPORT_REPAGE_HOURS = 24`: how long a stuck lead stays quiet after the SOS Reporter (`monitor_service.send_periodic_admin_report`, every 4h) has paged the operator about it once — without this, a single unresolvable lead paged CRITICAL on every tick forever (PRO-162). The predicate is `monitor_service.stuck_lead_report_due_filter`, applied both in the Reporter's query and again inside its atomic `find_one_and_update` claim (stamping `admin_reported_at`), so two worker replicas can't both page the same lead; the standing already-paged backlog is logged, not re-paged
- `WorkerConstants.STALE_BOOKED_LEAD_HOURS = 24`: threshold for stale job reminders
- `WorkerConstants.GEO_RADIUS_STEPS = [10000, 20000, 30000]`: progressive geo search radii
- `WorkerConstants.PAUSE_TTL_SECONDS = 900`: 15-minute rolling TTL for PAUSED_FOR_HUMAN state
- `WorkerConstants.CANCEL_CONFIRM_TTL_SECONDS = 300`: 5-minute window for a customer to confirm a cancel keyword on a BOOKED job (`AWAITING_CANCEL_CONFIRMATION`, PRO-118); expiry leaves the job booked
- `WorkerConstants.LOYALTY_CONFIRM_TTL_SECONDS = 300`: 5-minute window for a customer to answer the "want your previous pro?" offer (`AWAITING_LOYALTY_CONFIRMATION`, PRO-119); expiry releases to normal routing instead of the old unbounded 4h default
- `WorkerConstants.PRO_SEARCH_RATE_LIMIT_SECONDS = 600`: 10-minute per-pro cool-down on the `מצא` proactive stuck-lead search
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
- `ISRAEL_CITIES_COORDS`: static dict mapping Hebrew/English city names to `[lon, lat]` for geo queries

### Testing Conventions

Unit tests use `mongomock_motor` (in-memory MongoDB) and mock `whatsapp` and `ai` instances via `monkeypatch`. Integration tests (marked `@pytest.mark.integration`) connect to a real `MONGO_TEST_URI` test database and clear it before each run. `conftest.py` auto-applies the mock fixtures to all non-integration tests via `autouse=True`. `asyncio_mode = strict` is set in `pytest.ini`.

`$geoNear` is not supported by mongomock — matching service geo tests mock `users_collection.aggregate` as async generators directly.

`customer_flow.py` and `pro_flow.py` functions receive `whatsapp`/`lead_manager` as parameters (dependency injection) so `workflow_service.py` passes its shared instances.

### Configuration

All config is in `app/core/config.py` via `pydantic-settings`. Required env vars: `GEMINI_API_KEY`, `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET`. Optional: `ENVIRONMENT` (`development` | `staging` | `production`, defaults to `development`; any other value — including empty — fails validation at startup, PRO-34), `MONGO_URI` (defaults to localhost), `REDIS_URL`, `MONGO_TEST_URI` (for integration tests), `ADMIN_PASSWORD`, `ADMIN_PHONE` (defaults to hardcoded), `ONCALL_PHONE` (on-call number for infra alerts; defaults to `ADMIN_PHONE`), `HEALTH_TOKEN` (authenticates the detailed `/health` view and `/health/leads` via `X-Health-Token`; unset in a prod-like environment fails closed — the public `/health` liveness signal stays up either way, PRO-136), `WEBHOOK_TOKEN` (enables `?token=` webhook auth; **required**, not optional, whenever `ENVIRONMENT` is `staging`/`production` — PRO-86 removed the sender instance-id check that used to be the other half of webhook auth, so an unset token now means no authentication at all, and `Settings` refuses to boot without it in a prod-like environment), `SENTRY_DSN` (enables Sentry for all three services via the shared `app/core/sentry.py` `init_sentry()` — CRITICAL-only operator paging via `page_critical`, plus non-paging `error`-level visibility events from allowlisted framework integrations, the APScheduler job-error listener, and a throttled loguru-ERROR bridge; unset = no-op), `WHATSAPP_PROVIDER` (`dryrun` default | `cloud`; which transport the outbound facade uses — `cloud` selects the PRO-89 `CloudAPIProvider`, code-complete against the Meta Graph API though PRO-87 onboarding has not yet approved any template or gone live), `WHATSAPP_DRY_RUN` (default `false`; set `true` in local `.env` to force the `DryRunProvider` regardless of `WHATSAPP_PROVIDER`, so dev/simulation never cold-initiates a real message from the pilot number — this is also the operator's emergency mute, see `docs/RUNBOOK_WHATSAPP_OUTAGE.md`), `META_ACCESS_TOKEN`/`META_APP_SECRET`/`META_VERIFY_TOKEN` (`SecretStr | None`) and `META_PHONE_NUMBER_ID`/`META_GRAPH_API_VERSION` (default `v23.0`) — the PRO-89 Cloud API credentials; `require_cloud_provider_config` makes the token + phone-number id mandatory the moment `WHATSAPP_PROVIDER=cloud` is selected without `WHATSAPP_DRY_RUN`, and makes the app secret + verify token (which authenticate `GET`/`POST /webhook/meta`) additionally mandatory in a prod-like environment.

**`ENVIRONMENT` is cross-checked against the platform (PRO-96).** `Settings` refuses to boot when the declared value disagrees with `RAILWAY_ENVIRONMENT_NAME`, which Railway injects and an operator cannot mistype. A *legal but wrong* value used to pass silently — it happened in both directions (PRO-92: staging claiming `production`; PRO-96: production api+worker claiming `staging`, which disarmed `seed_db.py`'s destructive guard against the production database). Exempt when the Railway variable is absent (local, docker-compose, CI) or holds a preview-environment name (`pr-42`) that has no counterpart in the three-value vocabulary. `tests/conftest.py` clears both Railway variables suite-wide so `pytest` under `railway run` still works.

**Every credential-bearing setting is a `pydantic.SecretStr` (PRO-94)** — `GEMINI_API_KEY`, `MONGO_URI`, `MONGO_TEST_URI`, `ADMIN_PASSWORD`, `REDIS_URL`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET`, `WEBHOOK_TOKEN`, `GOOGLE_MAPS_API_KEY`, `SENTRY_DSN`, and (PRO-111, declared for redaction only — boto3 still reads the env directly) `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`. pydantic's default `__repr__` prints every field value, so any traceback touching `Settings` used to dump the whole secret set into a log, a Sentry event or a terminal. Read them with `.get_secret_value()` **at the point of use** — never into a module-level name, and never into an f-string or a log line. The convention is enforced by field name: anything ending in `TOKEN`, `KEY`, `SECRET`, `PASSWORD`, `DSN`, `_URI` or `_URL` must be `SecretStr` or `tests/test_settings_secret_masking.py` fails the build (this is what already covers PRO-89's `META_ACCESS_TOKEN`, `META_APP_SECRET` and `META_VERIFY_TOKEN`). `app/core/logger.py` sources its redaction list from those fields automatically, so a new credential is scrubbed from logs the moment it is typed correctly. SecretStr only protects an object that already exists: a `ValidationError` raised *during* `Settings` construction (e.g. PRO-96's environment cross-check) fires before any field is wrapped, so pydantic's default error text used to echo the raw input — env vars included — under `input_value=`. `Settings.model_config` now sets `hide_input_in_errors=True` (PRO-99) to close that construction-time gap; the flag covers `__str__`/`__repr__`/tracebacks only — `ValidationError.errors()`/`.json()` still carry the raw input dict, so no boot handler may render either.

## Linear ↔ GitHub: naming a PR that only does part of an issue

Linear links an issue to a PR through the **branch name, PR title, or PR body**, and its GitHub automation moves the issue to **Done the moment that PR merges**. There is no "partially closes" — any one of those three references is enough, and merging is the trigger.

So the issue key is a *closing* marker, not a citation. Use it only when merging the PR genuinely satisfies the whole issue.

**When a PR delivers only part of an issue** — a repo-side slice of an ops problem, one of three acceptance criteria, groundwork for a follow-up — keep the key out of **all three** of the branch name, the PR title, and the PR body. Describe the work on its own terms (`chore/partial-scope-pr-convention`, not `fix/<key>-…`), and record the connection where it does no harm: a comment on the Linear issue linking the PR. If the slice is substantial enough to deserve tracking, give it its own issue and let the PR close *that*.

This has already gone wrong once: on 2026-08-22 a PR fixing the repo-side third of an open Critical/Ops deploy issue was titled `fix(<KEY>): …`, and merging it flipped the whole issue to Done while production was still broken — the auto-close landed 19 minutes after the issue had been moved to In Progress. Note that the `/take-issue` flow's `feat($1): <summary>` convention is correct *for that flow*, because it runs one issue to completion; it is not a general rule for every PR.

A merged PR that only adds a document or a script is also not evidence that the thing was *run* — see the evidence gate in `.claude/commands/take-issue.md`.

## Session Guidelines

- Skip files over 100KB unless explicitly required.
- Suggest `/cost` when a session is running long to monitor cache ratio.
- Recommend starting a new session when switching to an unrelated task.
- After finishing a code-change task, delegate to the **docs-syncer** subagent (incremental mode) to update any `.md` files made stale by the diff.
- After completing any task/bug (PR opened or Linear issue moved), record it in the **living system-audit artifact** — but **exactly one session writes to it at a time.** It is a single shared document with no merge: a concurrent republish is refused as stale, and forcing past that refusal silently discards the other session's entry. Which mode you are in is a one-line check — `git worktree list`:
  - **One worktree — you are the only writer.** `action: "read"` the artifact first and build the republish from the version that comes back, then republish via the Artifact tool with `url: https://claude.ai/code/artifact/363c67d3-e33c-44f2-a8fd-afe6534711c7` (passing the URL is what updates in place; omitting it forks a new artifact). Add a Fix-log row (date, item, outcome + PR link) and update the affected item's status card. Keep the title ("Proli System Audit") and favicon (🩺) unchanged.
  - **More than one worktree — you are one track of a parallel batch. Do not publish.** Put your Fix-log row in the PR body instead, under a `## 🩺 Audit fix-log entry` heading, and stop there. Whoever closes the batch drains every merged PR's section into a single ordered write (`gh pr list --state merged --json number,body`), so the artifact takes one write instead of N racing ones — and no entry can be lost to a force. When in doubt, queue: a queued row costs one paste, a lost row is invisible.
