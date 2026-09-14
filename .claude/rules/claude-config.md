---
paths:
  - ".claude/**"
  - ".mcp.json"
  - ".githooks/**"
---

# Claude Code project config — inventory, contracts, how to add to it

Loaded when a file under `.claude/`, `.mcp.json` or `.githooks/` is read. Everything here
is checked into git and applies to every clone, so a typo breaks tooling for the whole team
silently and only on the next session start — which is why `tests/test_claude_config.py`
parses all of it in the normal `pytest` run.

## Layout

| path | what | pinned by |
|---|---|---|
| `CLAUDE.md` | the always-loaded rulebook; size-budgeted | `test_claude_config.py` (budget) |
| `CLAUDE.local.md` | per-developer overrides; gitignored | — |
| `.claude/rules/*.md` | path-scoped reference, `paths:` frontmatter of globs; loaded when a matching file is read; a rule **without** `paths` loads every session like `CLAUDE.md` and must justify that | `test_claude_config.py` (frontmatter, globs resolve) |
| `.claude/skills/<name>/SKILL.md` | a procedure Claude picks up by context (`description`, plus `paths` — see the note below); supporting files beside it load on demand | `test_claude_config.py` (frontmatter, referenced paths exist) |
| `.claude/commands/*.md` | operator workflows invoked as `/name` — see "Commands are skills already" below | `test_claude_config.py` (description, `disable-model-invocation` on the mutating five) |
| `.claude/agents/*.md` | subagents with their own context and tool set | `test_agent_pack_drift.py` (embedded constants) |
| `.claude/settings.json` | permissions, hooks, env, plugins; shared | `test_claude_config.py` |
| `.claude/settings.local.json` | per-machine: `disabledMcpjsonServers`, and any `enabledPlugins` entry whose marketplace is not registered in this repo (a plugin the shared file names but no clone can resolve is a warning on every session start, not a feature) — `last30days@last30days-skill` moved here in September 2026; gitignored | — |
| `.mcp.json` | project MCP servers, auto-approved via `enableAllProjectMcpServers` | `test_claude_config.py` (transport fields, `${VAR}` env only) |
| agent memory | `flow-tracer` declares `memory: project`, so Claude Code stores and reloads its notes; nothing in the repo, nothing to gitignore | — |
| `.githooks/` | `core.hooksPath` target: `pre-push` blocks direct pushes to `dev`/`main`/`master`; must carry the executable bit **in the index** (`100755`), or git skips it with a one-line hint and the backstop is silently off | `test_claude_config.py` (index mode) |

## Hooks (`settings.json` → `.claude/hooks/`)

All Python hooks run through `run-hook.sh`, which finds the project venv interpreter
(Windows or POSIX layout, then PATH) and exits 0 on a machine without Python — a hook here
is a guard or a formatter, and "no Python" must degrade to "no extra guard", never to
"every tool call fails". Every command uses `$CLAUDE_PROJECT_DIR`, never a machine path.

| event | script | contract |
|---|---|---|
| `SessionStart` (every matcher) | `session-start-context.sh` | prints branch, last commit and dirty files into context; informs, never blocks. Deliberately unscoped: `compact` and `clear` are exactly when that state would otherwise survive only as a summary |
| `SessionStart` (`startup\|resume`) | `session-start-web-setup.sh` | **cloud sessions only** (`CLAUDE_CODE_REMOTE=true`): creates `venv/`, installs `requirements.txt` (black and flake8 are pinned there), writes a placeholder `.env` if none, puts the venv on `PATH` via `CLAUDE_ENV_FILE`, sets `core.hooksPath`; synchronous, idempotent, exit 0 always. The matcher is the point: `clear` and `compact` are the same container with the venv already built, so running there only put a pip resolve in front of the first tool call after every compaction |
| `PreToolUse(Bash)` | `pre-bash-guard.py` | exit 2 blocks: `rm -rf` on dangerous targets, redirects into `.env`, force-push to protected branches, commit/push while on `dev`/`production`, mongo `dropDatabase`/`drop()`, and opening a PR from a branch its base has moved past — the merge into `dev` is a pull request, so that is the moment the branch and its base first have to agree, and the block carries the sync command. `origin/<base>` is fetched first (a session that last fetched an hour ago is exactly the one at risk), measured only for the command it gates, and every failure — no remote, no network, not a repo — reads as unknown and allows. Two narrowings, both found by the rule firing on the commit that added it: heredoc bodies are stripped and the words only count in command position, so writing *about* `gh pr create` is not opening a PR. Logic is `evaluate(command, branch, behind_base)` + `merge_base_branch()` + `strip_heredocs()`, pinned by `tests/test_pre_bash_guard.py` |
| `PreToolUse(Edit\|Write\|MultiEdit)` | `pre-edit-protect.py` | blocks edits to `.env` and anything under `.git/` |
| `PostToolUse(Edit\|Write\|MultiEdit)` | `post-edit-format.py` | runs `black --quiet` on the touched `.py`, then returns any `flake8` finding **to Claude** as `{"decision": "block", "reason": …}` on stdout — the documented PostToolUse shape. stdout and stderr on exit 0 reach only the user's transcript, so a year of findings printed to stderr were invisible to the one party that could fix them; harmless while flake8 was advisory, a committed-to CI failure since #180. Silent when the file is clean, and a black reformat rides along on a message that was going out anyway (the model's copy is then stale) rather than earning one. Logic is `is_checkable()` + `build_reason()`, pinned by `tests/test_post_edit_format.py`; silent no-op without the tools |
| `Stop` | `stop-docs-guard.py` | exit 2 (once per turn — `stop_hook_active` ends it) when **this session** wrote a `.py` under `app/`, `admin_panel/` or `scripts/` (read from the transcript's Edit/Write calls; dirty-tree fallback) and no `.md` was written by the session, is dirty in the tree, or is committed on the branch since `origin/dev`: the docs-syncer reminder made enforceable without blaming a parallel session's edits. Logic is `evaluate(written, dirty, branch, stop_hook_active)`, pinned by `tests/test_stop_docs_guard.py`; fail-open on any error |

Adding a hook: script in `.claude/hooks/`, wired in `settings.json` through `run-hook.sh`
(Python) or `sh` (shell), decision logic in a pure function with its own test file. An
unwired script fails `test_hook_scripts_are_not_orphaned`.

## Permissions

The allowlist is **read-only operations plus the PR-open path**: `gh pr create/view/list/
checks`, `gh run list/view` (not `gh api` — an arbitrary REST client can merge a PR or delete a
branch, so it prompts), read-only `git`, read-only Railway CLI and MCP
tools, the MongoDB plugin's read tools, Linear read + issue/comment save, and the
test/format/lint commands in every interpreter layout (Windows venv, POSIX venv, bare).
Bash patterns use the documented `Bash(cmd:*)` prefix form throughout (`Bash(pytest:*)` matches
both `pytest` and `pytest -q`; a trailing ` *` does not).
MCP tool names must match what the server actually exposes — Railway's are hyphenated
(`list-projects`), Linear's underscored (`get_issue`), MongoDB's carry the plugin prefix
(`mcp__plugin_mongodb_mongodb__find`) — an entry spelled otherwise matches nothing and only
looks like a grant. Sentry, Context7 and Redis read tools are allowlisted too, so `/triage`, `/user-debug` and
a docs lookup do not prompt. The denylist names what must always prompt or never run: Railway
variable writes and deletes, Redis `delete`, Sentry `update_issue`, `gh pr merge`.

## Rules and skills: the drift contract

`.claude/` used to sit outside every sync path, so an agent file rotted the first time a
TTL changed (PRO-67). The contract now:

- A rule or skill **points at** the code, the guide and the tests that pin a fact; it may
  restate the fact, but the code owns it and the same PR updates both.
- `.claude/rules/` is in the docs-syncer subagent's audit scope alongside `docs/`.
- `tests/test_agent_pack_drift.py` pins the constants embedded in `flow-tracer.md` and
  `code-reviewer.md`; `tests/test_claude_config.py` checks every path a rule or skill names
  still exists, every `paths:` glob still matches something, and that `CLAUDE.md` stays
  under its size budget.
- A rule without `paths:` costs every session what it weighs. Prefer scoping; the budget
  test does not count rules, so the discipline is the review, not the gate.

## Commands are skills already — and five of them must not self-fire

The docs treat `.claude/commands/*.md` as the older format and skills as the one going
forward, which reads like fourteen files owe a move to `.claude/skills/<name>/SKILL.md`.
They do not, and the reason is worth keeping: **Claude Code already loads this directory
through the skill surface.** Every command here is offered to the model as a skill, with its
`description` verbatim, under `/name` — checked by listing the session's skills against this
directory, not assumed. A move would rename fourteen files, and four of them interpolate
`$ARGUMENTS`, which is not in the documented skill frontmatter; it works today because these
*are* skills today. Renaming to buy a property we already have, at the cost of an
unverifiable substitution, is the trade the September permissions pass warned about from the
other side — config that only *looks* like it changed something.

What the skill format does buy is frontmatter, and it applies here as-is. Being offered to
the model is wrong for a workflow that changes the world outside this session, so the five
that do carry **`disable-model-invocation: true`** and are `/name`-only:

| command | what it changes |
|---|---|
| `take-issue` | creates a branch and a PR, moves a Linear issue |
| `triage` | opens and annotates tickets, resolves Sentry issues, republishes the audit artifact |
| `cleanup-worktrees` | removes worktrees and their branches |
| `add-pro` | writes a professional into the database |
| `full-sync-docs` | rewrites stale claims across the repo's `.md` files |

`test_operator_workflows_that_change_things_are_invocation_only` pins the set. It is written
out rather than inferred, because nothing can read "mutating" off a markdown file — adding a
command that changes something is meant to cost a line in that test. The read-only ones
(`health`, `logs`, `db-status`, `test`, `deploy-check`, `user-debug`, `finops`) and the two
local-only simulators (`simulate`, `sla-test`) stay model-invocable on purpose: reaching for
`/logs` mid-debug is the feature.

Not taken: `context: fork` on `full-sync-docs`. It is the obvious candidate — a 217-line
audit that returns a report — but forking hides the run from the operator watching it, and
the context saving is speculative against a cost that is not.

## What the frontmatter is allowed to say

`settings.json` carries `"$schema": "https://json.schemastore.org/claude-code-settings.json"`
so an editor flags an unknown key before a session does — silently and only on the next
session start is how every defect in this directory has been found.

**Rules** take `paths:` (documented) — globs, relative to the repo root, and a rule without
them loads every session. **Skills** take `description`, `allowed-tools`,
`disable-model-invocation`, `user-invocable`, `context: fork` and `arguments`; the three
here also carry `paths:`, which is *not* in the documented skill frontmatter. It is kept
because it costs nothing and may work, but nothing is allowed to depend on it: every skill's
`description` names the files that should pull it in, and that is what actually selects it.

**Subagents** take `name`, `description`, `tools`, `disallowedTools`, `model`,
`permissionMode`, `maxTurns`, `effort`, `skills`, `memory`, `hooks`, `isolation`,
`background`, `mcpServers` and `color`. `memory: project` is what `flow-tracer` uses —
Claude Code keeps and reloads the notes itself, which replaced an instruction to hand-write
a MEMORY.md under a gitignored `.claude/agent-memory/` that no clone ever once acted on.

## MCP servers (`.mcp.json`)

| server | transport | one-time setup per machine |
|---|---|---|
| `linear` | HTTP `mcp.linear.app` | OAuth browser sign-in on first use |
| `sentry` | HTTP `mcp.sentry.dev` | OAuth browser sign-in on first use |
| `context7` | HTTP `mcp.context7.com` | none (rate-limited without an API key) |
| `railway` | stdio `railway mcp` (Railway CLI ≥ ~5.x) | install Railway CLI + `railway login` |
| `redis` | stdio `uvx redis-mcp-server` | install `uv`; reads `REDIS_URL` (defaults to `redis://localhost:6379/0`) |
| `mongodb` | the `mongodb@claude-plugins-official` plugin (`enabledPlugins`) | `MDB_MCP_CONNECTION_STRING` env var, or per-call arguments |

In Claude Code on the web the HTTP servers need the connector authorized in claude.ai
settings, and the stdio ones (`railway`, `redis`) are unavailable unless the environment
installs their CLIs. Deliberately excluded: GitHub (the `gh` CLI covers it), Cloudinary
(opt in per machine with `claude mcp add --transport sse cloudinary
https://asset-management.mcp.cloudinary.com/sse`).

## Session persistence

`settings.json` sets `CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1` for every session: a session
spawned from another session inherits `CLAUDE_CODE_CHILD_SESSION=1`, which disables
transcript writing, and the 2026-08-29 batch lost hours of work to that (`docs/PARALLEL_TRACKS.md`).
