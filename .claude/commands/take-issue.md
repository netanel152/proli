---
description: Pull a Linear issue, implement it on a feature branch, review + test + sync docs via subagents, open a PR, and move the issue to In Review. One issue per run.
argument-hint: <ISSUE-ID> (e.g. PRO-123)
allowed-tools: Bash(git:*), Bash(gh:*), Bash(venv/Scripts/pytest:*), Bash(python -m pytest:*), Read, Grep, Glob, Edit, Write
model: opus
---

You are running the Proli Linear-driven development loop for issue **$1**.

Stack: FastAPI + ARQ worker + Streamlit admin, MongoDB + Redis. Conventions live in CLAUDE.md — read it first if it isn't already in context.

## Current repo state (injected)

- Branch: !`git branch --show-current`
- Working tree: !`git status --short`
- Last commit: !`git log -1 --oneline`

## Guardrails (non-negotiable)

- **One issue per run.** Do only $1. Do not pick up other work.
- **Never touch main.** All work happens on a feature branch. Never commit or push to main/master.
- **Stop if the working tree is dirty.** If `git status` above shows uncommitted changes, stop and tell me to stash or commit first — do not build on a dirty tree.
- **Stuck > 15 min of effort or blocked on a real ambiguity → stop and ask.** Don't guess at unclear requirements; post what you need on the issue and pause.
- **You are the implementer.** Write the production code yourself. Delegate review, tests, and docs to the subagents below — never delegate the implementation.
- **Run all subagents in the foreground (blocking).** Never background a subagent and poll its output file with shell sleep loops.

## The loop

**1. Fetch the issue.** Use the Linear MCP to get issue **$1**: title, description, acceptance criteria, priority, labels, linked issues, and Linear's suggested branch name. If the issue can't be found, stop and say so.

**Model selection (per-issue).** Read the issue's labels: a `model:sonnet` label → run the **implementation** pass on Sonnet; a `model:opus` label → Opus; neither → the current default. Heuristic for how issues should be labelled: **sonnet** for typo / config / string / dedup / known-root-cause bugfixes (mechanical, well-scoped); **opus** for unclear FSM or concurrency bugs, refactors, epics, or architectural change. This label governs the implementation pass **only** — the `code-reviewer` and `flow-tracer` subagents always keep whatever model their own frontmatter declares, regardless of the label.

**2. Move to In Progress.** Update the issue status to "In Progress" via Linear MCP, and assign it to me (`me`) if unassigned.

**3. Create the branch.** Fetch first, then branch off the **remote** tip so you never build on a stale local main: `git fetch origin` then `git checkout -b <linear-suggested-branch-name> origin/main` (use `origin/master` if that is the repo's default branch). If Linear gives no branch name, use `feature/$1-<short-slug>`. **Worktree guard:** if the target branch already exists in another worktree (`git worktree list` shows it, or `git checkout` reports it is checked out elsewhere), stop and tell me — do not force or delete it.

**4. Plan.** Restate the requirements as a short checklist of changes (files + what changes in each). Map each acceptance criterion to a change. Show me the plan and the FSM/lifecycle invariants it must preserve (context clearing, TTLs, DI pattern, Green API no-buttons). Wait for nothing if the plan is obvious; pause for my confirmation only if there's a genuine design fork. Once the plan is settled, **post it as a comment on the issue** (via Linear MCP) before writing any code, so the plan is persisted even if the run is interrupted.

**5. Implement.** Write the code, file by file. Respect every Proli convention: async safety, dependency injection through parameters in pro_flow/customer_flow, state writes through state_manager_service with the right WorkerConstants TTL, context cleared on flow exit, no hardcoded secrets, PII masked in logs.

**6. Tests.** Delegate to the **test-writer** subagent to add coverage for the new/changed branches (it runs only its own new/changed test files). Then delegate to the **test-runner** subagent to run the full suite. Run it **once** after a green test-writer pass; re-run **only** to confirm a fix after a failure — a confirmation re-run is not a violation of the once-per-loop intent. The baseline lives in `docs/TESTING.md` ("Current status" line): fewer passed = regression; more passed = note "update baseline in docs/TESTING.md". If test-runner reports failures, fix the production code yourself and re-run until green.

**7. Review.** Delegate to the **code-reviewer** subagent. If it returns BLOCKERS, fix them and re-review. WARNINGS: fix if quick, otherwise note them in the PR description. If the change touches `admin_panel/`, also delegate to the **ux-reviewer** subagent.

**8. Sync docs.** Delegate to the **docs-syncer** subagent (incremental mode) to update any docs invalidated by this change.

**9. Commit & PR.** Stage the changes, write a commit message referencing $1 (e.g. `feat($1): <summary>`), and open a PR with `gh pr create`. The PR body must include: a one-paragraph summary, the acceptance criteria checked off, the code-reviewer's verdict, and the test count as "<old from docs/TESTING.md> → <new>". If the change touched `app/core/constants.py` (new state, TTL, or lifecycle value) or the `workflow_service.py` dispatch order, no manual agent-file review is needed: the embedded agent-pack facts (UserStates, LeadStatus lifecycle, TTLs, and dispatch order) are guarded automatically by `tests/test_agent_pack_drift.py` — a failing run there points at the exact stale fact to fix.

**10. Close the loop in Linear.** Post a comment on $1 summarizing what was implemented and link the PR. Move the issue to "In Review" (not Done — a human merges the PR). 

## Final output

End with a compact status block:

```
Issue:    $1 — <title>
Branch:   <branch>
PR:       <url>
Tests:    <old> → <new> (no regressions)
Review:   <blockers fixed N / warnings M>
Linear:   In Review
```

If you stopped early for any reason, say exactly which step and why, and what you need from me to continue.
