# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

It is the always-loaded rulebook, so it carries only what every session needs. Reference material
that matters for one part of the tree lives in **`.claude/rules/`** as path-scoped rules (loaded
automatically when a matching file is read — see the index at the bottom) and in `docs/`.
`tests/test_claude_config.py` fails the build if this file grows past its size budget again.

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

**Release by running the `🚢 Promote dev → production` workflow** (Actions tab → Run workflow). It is the supported path and does the whole thing: refuses a non-fast-forward, refuses a `dev` whose CI is not green, moves the ref, then **waits for production to restart and report healthy** and fails the build if it does not. Tick `dry_run` to see the commit range without changing anything. Leave `skip_reason` empty to get that verification — the normal path; typing a reason skips it instead, and a reason under 10 characters after trimming (whitespace-only included) fails the job before anything is pushed. An unverified run is visibly marked as such: the run title in the Actions list, a `::warning::` annotation, and the job summary all say so.

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

Everything under `.claude/` (except `settings.local.json`, which is gitignored) plus `.mcp.json` is checked in and applies to every clone. The inventory, the hook contracts and the conventions for adding to it live in `.claude/rules/claude-config.md` (loaded when a file under `.claude/` is read) and are pinned by `tests/test_claude_config.py`.

- **Rules** (`.claude/rules/*.md`) — path-scoped reference material, loaded when a matching file is read. Index at the bottom of this file.
- **Skills** (`.claude/skills/<name>/SKILL.md`) — procedures Claude picks up by context: `whatsapp-copy` (a string in `messages.py`), `dispatch-guard` (a new guard in `GUARD_CHAIN`), `scheduler-job` (a new APScheduler job). Each points at the code and the tests that pin it; none embeds a value the code owns.
- **Commands** (`.claude/commands/*.md`) — operator workflows invoked by name: `/take-issue`, `/triage`, `/test`, `/health`, `/logs`, `/simulate`, `/user-debug`, `/db-status`, `/deploy-check`, `/full-sync-docs`, `/cleanup-worktrees`, `/add-pro`, `/sla-test`, `/finops`. Claude Code loads them through the same surface as skills, so they are offered to the model too — the five that change something outside the session (`/take-issue`, `/triage`, `/cleanup-worktrees`, `/add-pro`, `/full-sync-docs`) carry `disable-model-invocation: true` and are yours to start, not Claude's.
- **Agents** (`.claude/agents/*.md`) — `code-reviewer`, `test-runner`, `test-writer`, `docs-syncer`, `flow-tracer`, `ux-reviewer`.
- **Hooks** (`.claude/settings.json` → `.claude/hooks/`, all through the cross-platform `run-hook.sh` launcher): `SessionStart` injects real branch/commit/dirty-tree state and, on Claude Code on the web only (matcher `startup|resume`), installs the venv so tests and linters run; `PreToolUse(Bash)` blocks `rm -rf` on dangerous targets, redirects into `.env`, force-pushes to protected branches, commit/push while on `dev`/`production`, and mongo `dropDatabase`/`drop()`; `PreToolUse(Edit|Write)` blocks edits to `.env` and `.git/`; `PostToolUse(Edit|Write)` runs `black` on touched `.py` files and returns any `flake8` finding **to Claude** as a `decision: "block"` (stderr reaches only the user, so findings used to go unseen — and since #180 every one of them is a CI failure already committed to); `Stop` refuses to end a turn in which this session wrote a `.py` under `app/`, `admin_panel/` or `scripts/` while no `.md` was written, dirtied or committed on the branch — once, then it lets go — so the docs-syncer step below cannot be forgotten silently.

**MCP servers (`.mcp.json`)** — `linear`, `sentry`, `context7` (HTTP, OAuth on first use), `railway` (stdio, needs the Railway CLI), `redis` (stdio via `uvx`), and `mongodb` through the official plugin. Use them only when the task needs the external system:

- `linear` — issue-driven work (`/take-issue`, status moves, comments). Not for reading code.
- `sentry` — triaging a production error / paging trail. Logs-first for local issues (`/logs`).
- `context7` — current library docs (FastAPI, pydantic v2, ARQ, Streamlit, Motor) when the answer may postdate training. Not for questions the codebase itself answers.
- `railway` — deploy state, service logs, env inspection. Read-only tools are allowlisted; mutating ones prompt, `set-variables` is denied.
- `redis` / `mongodb` — live FSM/context/lead debugging (`/user-debug`, `/db-status`). Unit tests never need them (mongomock/fakeredis).

Deliberately excluded: **GitHub** (the `gh` CLI covers PRs/CI and is allowlisted), **Cloudinary** (opt in per-machine). Turn a server off per-machine via `"disabledMcpjsonServers": ["<name>"]` in `.claude/settings.local.json` — don't remove it from `.mcp.json` for everyone.

### The working tree is shared with parallel sessions

Another session can move `HEAD` underneath you between one command and the next, and nothing announces it. Re-check immediately before editing or committing, stage by explicit path (never `git add -A`), and never trust branch state established earlier in a session:

```bash
git branch --show-current && git log -1 --oneline && git status --short
```

Under Git Bash on Windows, `git show <rev>:<path>` is silently mangled by MSYS path conversion — write it `git show "origin/dev:./path"` (leading `./`). Both traps, the one-worktree-per-issue setup for running several issues at once, the `wt` semicolon trap, and the `/cleanup-worktrees` teardown are in **`docs/PARALLEL_TRACKS.md`**.

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
python scripts/check_pro_service_areas.py  # Backfill/audit: geocode every approved pro's service_areas, --apply to write
#                                            (against staging/production, run the 🗺️ Check pro service areas workflow instead — branch picks the environment, report lands in the job summary)
```

### Testing

```bash
pytest                                  # all unit tests — mongomock + fakeredis, no external services
pytest tests/test_matching_service.py   # one file
pytest -m integration                   # needs MONGO_TEST_URI in .env
```

The baseline is the "Current status" line in `docs/TESTING.md`, enforced by CI as a **floor** (fewer passed fails, more passed warns). Conventions, the floor's history and the refresh workflow: `.claude/rules/testing.md`. Prefer the **test-runner** subagent (`/test`) over running the suite in the main thread.

### Linting / Formatting

```bash
black .    # enforced: CI runs `black --check --diff .` and fails the build on an unformatted file
flake8 .   # enforced too: CI runs `flake8 --count .`; config in .flake8 (E501 left to black, sys.path shims in scripts/ allowed)
```

## Architecture

Proli is an AI-powered WhatsApp CRM for Israeli service professionals (plumbers, electricians, etc.). It runs as three cooperating processes:       

### Process 1: FastAPI Backend (`app/`)

Entry point for inbound WhatsApp webhooks. Its only job is to validate the incoming payload, enqueue a task to Redis via ARQ, and immediately return `200 OK`. All heavy lifting is deferred to the Worker. Routes: `POST /webhook`, `GET/POST /webhook/meta` (PRO-89 — Meta Cloud API subscription handshake and inbound; live even under `WHATSAPP_PROVIDER=dryrun`), `GET /health` (public response is only `status` + `uptime_seconds`; the per-dependency `checks` detail requires the `X-Health-Token` header, PRO-136), and `GET /health/leads` (token-only — 403 without `X-Health-Token`; error bodies are a fixed string, the real exception goes to logs/Sentry).

### Process 2: ARQ Worker (`app/worker.py` + `app/core/arq_worker.py`)

Picks up `process_message_task` jobs from Redis and calls `workflow_service.process_incoming_message`. Also hosts APScheduler for periodic jobs (SOS healer every 10 mins, stale monitor every 30 mins, stale lead nudger every 4h, daily agenda at 08:00 Israel time, pro-approval SLA check every 5 mins, WhatsApp instance deauth watchdog every 2 mins, backup freshness watchdog every hour — production only, PRO-185); the four long-interval jobs (SOS Reporter, stale lead nudger, lead janitor, backup freshness watchdog) also run once shortly after boot so a deploy cadence shorter than their interval can't starve them (PRO-176).

### Process 3: Streamlit Admin Panel (`admin_panel/`)

Protected by bcrypt cookie-based auth; views for lead management, professional profiles and schedule management; Hebrew-first with an RTL/responsive layer. Details (auto-refresh, `TRANS`/labels, the one-click assignment strip, `responsive.py`/`rtl.py`, the preview and screenshot scripts): `.claude/rules/admin-panel.md`.

### Service Layer (`app/services/`) and the provider facade

`workflow_service.py` builds the per-turn `DispatchContext`/`GuardDeps`, runs `dispatch_guards.GUARD_CHAIN` (an ordered tuple — order is load-bearing), then hands anything unhandled to `conversation_pipeline.run_customer_pipeline`. `customer_flow.py`/`pro_flow.py`/`admin_flow.py` hold the three audiences' text commands, `matching_service.py` the progressive `$geoNear` match, `monitor_service.py` the scheduled healers and reassignment, `notification_service.py` every pro-facing notification and operator page, and `app/providers/whatsapp/` the single outbound egress. The per-service responsibility table: `.claude/rules/services.md`.

### Data Layer

- **MongoDB**: Primary store — `users` (pros + customers), `leads`, `slots`, `messages`, `settings`, `reviews`, `consent`, `audit_log`, `admins`, `wa_delivery` (PRO-89 — per-wamid outbound delivery statuses, kept out of `messages` so status callbacks never get replayed into the AI context)  
- **Redis**: ARQ task queue + context cache (chat history) + state machine (FSM)

### Key Constants (`app/core/constants.py`)

`LeadStatus`: `contacted → new → booked → completed/rejected/closed/cancelled/pending_admin_review`. `UserStates` is the Redis FSM enum. Every `WorkerConstants` value, with the incident or issue that set it: `.claude/rules/constants.md`.

### Configuration

All config is in `app/core/config.py` via `pydantic-settings`. Required env vars: `GEMINI_API_KEY`, `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET`. Every credential-bearing setting is a `pydantic.SecretStr` read with `.get_secret_value()` at the point of use — never into a module-level name, an f-string or a log line. `ENVIRONMENT` is cross-checked against Railway at boot. The full variable list and the two guards' history: `.claude/rules/config-secrets.md`.

## Path-scoped rules (`.claude/rules/`)

| rule | loads when a file matches | carries |
|---|---|---|
| `services.md` | `app/services/**`, `app/providers/**` | the per-service responsibility table |
| `constants.md` | `app/core/constants.py`, `app/scheduler.py`, `app/services/**` | every `WorkerConstants` value and why |
| `config-secrets.md` | `app/core/config.py`, `logger.py`, `sentry.py`, `phone.py`, `scripts/**` | the env-var list, SecretStr and `ENVIRONMENT` rules |
| `admin-panel.md` | `admin_panel/**`, `scripts/admin_panel_*.py`, `tests/test_admin_*.py` | the Streamlit panel's structure and guards |
| `testing.md` | `tests/**`, `docs/TESTING.md`, `pytest.ini`, the test workflows | conventions, the baseline floor, the refresh job |
| `claude-config.md` | `.claude/**`, `.mcp.json`, `.githooks/**` | the config inventory, hook contracts, how to add to it |

A fact that a rule states is owned by the code it describes: change the code first, then the rule, in the same PR. The docs-syncer subagent audits `.claude/rules/` alongside `docs/`.

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
- After finishing a code-change task, delegate to the **docs-syncer** subagent (incremental mode) to update any `.md` files made stale by the diff — `CLAUDE.md`, `.claude/rules/`, `README.md` and `docs/`. The `Stop` hook refuses (once) to end a turn in which this session wrote source under `app/`, `admin_panel/` or `scripts/` and no `.md` was touched; answer it by running the syncer or by stating why the docs are unaffected.
- After completing any task/bug (PR opened or Linear issue moved), record it in the **living system-audit artifact** — but **exactly one session writes to it at a time.** It is a single shared document with no merge: a concurrent republish is refused as stale, and forcing past that refusal silently discards the other session's entry. Which mode you are in is a one-line check — `git worktree list`:
  - **One worktree — you are the only writer.** `action: "read"` the artifact first and build the republish from the version that comes back, then republish via the Artifact tool with `url: https://claude.ai/code/artifact/363c67d3-e33c-44f2-a8fd-afe6534711c7` (passing the URL is what updates in place; omitting it forks a new artifact). Add a Fix-log row (date, item, outcome + PR link) and update the affected item's status card. Keep the title ("Proli System Audit") and favicon (🩺) unchanged.
  - **More than one worktree — you are one track of a parallel batch. Do not publish.** Put your Fix-log row in the PR body instead, under a `## 🩺 Audit fix-log entry` heading, and stop there. Whoever closes the batch drains every merged PR's section into a single ordered write (`gh pr list --state merged --json number,body`), so the artifact takes one write instead of N racing ones — and no entry can be lost to a force. When in doubt, queue: a queued row costs one paste, a lost row is invisible.
