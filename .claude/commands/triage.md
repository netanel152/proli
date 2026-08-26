---
description: Ops triage cockpit — sweep Sentry, cluster errors by root cause, cross-reference Linear, open/annotate tickets, resolve stale issues, report project status, and update the living system-audit artifact.
argument-hint: "[report] — pass 'report' for read-only mode (no tickets opened, nothing resolved)"
allowed-tools: Bash(git:*), Bash(gh:*), Read, Grep, Glob
---

You are running the Proli ops-triage loop. Mode: **$1** (empty = full triage with actions; `report` = read-only, findings only — take NO mutating action in Sentry, Linear, or the artifact).

Stack context lives in CLAUDE.md — read it first if not already in context. Sentry org is `proli-kb`, project `python`. Linear team is `Proli`.

## Current repo state (injected)

- Branch: !`git branch --show-current`
- Last commit: !`git log -1 --oneline`
- Working tree: !`git status --short`

## 0. Continuity — what did the last triage already do?

Read the Fix log of the living audit artifact (Artifact tool, `action: "read"`, `url: https://claude.ai/code/artifact/363c67d3-e33c-44f2-a8fd-afe6534711c7`) and find the most recent "Sentry triage" row. That gives you the last-run date and what was already ticketed/resolved. Never re-comment evidence a previous triage already posted on a Linear ticket (check the ticket's comments first), and treat Sentry issues resolved by a previous run that are back as **regressions** — that's a stronger signal than a new issue, say so explicitly.

## 1. Project status snapshot

Gather, in parallel where possible:

- **Deploy lag:** `git fetch origin` then `git rev-list --count origin/production..origin/dev` — how many commits production is behind. >0 for more than a few days deserves a callout (PRO-128 is the cautionary tale).
- **CI on dev:** `gh run list --branch dev --limit 3` — is the latest run green? A red `🔎 Verify staging deployed this commit` run means staging is stale (PRO-155).
- **Runtime health (Railway MCP, read-only tools only):** `list_deployments` for the latest deploy status per service in both environments, and `get_logs` filtered to error lines for anything Sentry can't see (boot loops, OOM kills, and crashes *before* Sentry initializes never produce a Sentry event). `http_error_rate` on the api service catches webhook 5xx spikes. Never call mutating Railway tools from this skill.
- **Linear pulse:** via Linear MCP, list issues in `started` state and any `Urgent`/`High` priority issues in `Triage`/`Backlog` updated in the last 7 days.

Do **not** use the `redis`/`mongodb` MCP servers here: they connect to whatever `REDIS_URL`/`MDB_MCP_CONNECTION_STRING` is set locally (usually the dev instance), so "findings" from them would describe the wrong environment. Live prod/staging data questions go through `/user-debug`, `/db-status`, or the token-gated `/health` — not this skill.

## 2. Sentry sweep

Via the Sentry MCP (`mcp__sentry__*` — load with ToolSearch if deferred):

1. `search_issues` on project `python`, `is:unresolved`, sort `recommended`, period `14d`, limit 25.
2. For every issue that is **new, regressed, or last-seen within 48h**, fetch details with `get_sentry_resource` — you need the **environment tag** (production vs staging), `handled` flag, culprit frame, and occurrence trend. Batch these calls.
3. If anything production-tagged appears, run `search_events` (dataset `errors`, `environment:production`, last 3d, sorted `-timestamp`) to establish whether it is *ongoing* or a *finished burst*.

## 3. Cluster by root cause, not by title

Sentry fragments one root cause into many issues (different culprits/messages). Group them. Known recurring clusters to recognize:

- **Window-closed family** (`ServiceWindowClosedError` / "no fallback template" / "24h service window closed"): external blocker — Meta template approval (PRO-87/PRO-88/PRO-150). Repo-side siblings: PRO-125 (pro-facing silent drop), PRO-159 (unhandled crash on customer-facing path).
- **Mongo auth / AutoReconnect bursts**: usually credential rotation or Atlas blips. Check whether the burst *ended* — a 20-minute burst that stopped days ago is stale, not active.
- **Stuck-lead healer pages** (`lead(s) stuck for more than 60 minutes`): a symptom, not a cause — find *why* the lead is stuck (window-closed offer? no matching pro? unknown city?) before treating it as its own bug.

## 4. Cross-reference Linear before opening anything

For each cluster, search Linear (`list_issues` with `query`) for an existing ticket. Then classify:

| Finding | Action (full mode) |
|---|---|
| Covered by an existing open ticket | `save_comment` on that ticket with the new Sentry evidence (links + last-seen). Never open a duplicate. |
| New repo-side bug with clear evidence | Open a ticket via `save_issue` (team `Proli`): Sentry links, stack frames, environment, "why it matters", suggested direction, `relatedTo` the sibling tickets, label `Bug` (+ `launch-readiness` if it gates the pilot). Priority: Urgent only if production-user-facing *now*, else High. |
| Stale — burst ended, cause known/fixed | Resolve in Sentry via `update_issue` with a `reason` naming the cause and the covering PRO-xxx, so a recurrence re-alerts as a regression. |
| Known noise blocked on an external event | Leave unresolved (or `ignored`/`untilEscalating` if it's drowning the feed) and note the blocking ticket. Do **not** resolve — it will genuinely recur. |
| Ambiguous — can't tell if it's a bug | First try `analyze_issue_with_seer` on the Sentry issue and, if the culprit file is known, read the actual code — a stack frame plus 30 lines of source usually settles it. Only if it's still ambiguous, report it as an open question. Do not open a speculative ticket. |

Verification bar for a new ticket: **read the culprit code before filing.** A ticket that says "line 77 does X on an empty frame, here's the failing path and the one-line fix direction" gets fixed; a pasted stack trace gets ignored.

Hard rules: **never** paste secrets/tokens into tickets; keep phone numbers masked as Sentry gives them; one ticket per root cause, not per Sentry issue; the issue key in a PR title/branch/body auto-closes on merge (see CLAUDE.md) — tickets you open here are for *tracking*, so no PR references unless a PR exists.

## 5. Update the living system-audit artifact

(Skip in `report` mode.) Republish the "Proli System Audit" artifact via the Artifact tool with `url: https://claude.ai/code/artifact/363c67d3-e33c-44f2-a8fd-afe6534711c7` — the `url` parameter updates it in place; omitting it forks a new artifact. Re-read it first (`action: "read"`) in case another session republished. Add a Fix-log row (date, "Sentry triage", outcome: tickets opened/resolved counts with links) — into the top table of the split fix log, keeping only the five newest rows there and moving the overflow into the collapsed `details.loghist` history (bump its count/date-range summary). Update any item status cards the triage changed, **refresh the "Needs attention" section** (`id="attention"`) so it always shows the current short list and nothing stale, and keep the top-nav environment pills (`production · …` / `staging · …`) truthful. Keep the title ("Proli System Audit") and 🩺 favicon unchanged.

## 6. Final report

End with a compact, plain-language block:

- **Production:** healthy / degraded — with the one-line reason and deploy lag.
- **Staging:** same.
- **Clusters found:** one line each — root cause, env, trend, covering ticket.
- **Actions taken:** tickets opened (keys + links), comments added, Sentry issues resolved/ignored.
- **Needs a human:** anything ambiguous, external blockers, or decisions (e.g. template approval progress) that no ticket can fix.
- **Config drift:** anything observed at runtime that contradicts the recorded environment state — flag it, never silently rewrite the record. **Prove drift from the stack trace, not from tags:** the Sentry `provider` tag reports `settings.WHATSAPP_PROVIDER`, which `WHATSAPP_DRY_RUN=true` silently overrides, so `provider: cloud` alone proves nothing. A frame inside `app/providers/whatsapp/cloud_api.py` does prove it — `DryRunProvider` can never execute that file.

## Cadence

This skill is designed to be run manually (`/triage`) or on a schedule with `/loop 1d /triage report`. Note that scheduled *cloud* runs may lack the OAuth'd Sentry/Linear MCP sessions — prefer a local loop or a manual weekly run. Full mode (with ticket-opening) should stay manual: a human reads the report before the backlog grows.
