---
description: Pull a Linear issue, implement it on a feature branch, review + test + sync docs via subagents, open a PR, and move the issue to In Review. One issue per run.
argument-hint: <ISSUE-ID> (e.g. PRO-123)
allowed-tools: Bash(git:*), Bash(gh:*), Bash(venv/Scripts/pytest:*), Bash(python -m pytest:*), Read, Grep, Glob, Edit, Write
model: opus
---

Stack: FastAPI + ARQ worker + Streamlit admin, MongoDB + Redis. Conventions live in CLAUDE.md — read it first if it isn't already in context.

## Current repo state (injected)

- Branch: !`git branch --show-current`
- Working tree: !`git status --short`
- Last commit: !`git log -1 --oneline`

## Guardrails (non-negotiable)

- **One issue per run.** Do only $1. Do not pick up other work.
- **Never touch `dev`.** All work happens on a feature branch. Never commit or push to `dev` (the default branch, renamed from `master`) or to `production`.
- **Stop if the working tree is dirty.** If `git status` above shows uncommitted changes, stop and tell me to stash or commit first — do not build on a dirty tree.
- **Stuck > 15 min of effort or blocked on a real ambiguity → stop and ask.** Don't guess at unclear requirements; post what you need on the issue and pause.
- **You are the implementer.** Write the production code yourself. Delegate review, tests, and docs to the subagents below — never delegate the implementation.
- **Run all subagents in the foreground (blocking).** Never background a subagent and poll its output file with shell sleep loops.

## The loop

**1. Fetch the issue.** Use the Linear MCP to get issue **$1**: title, description, acceptance criteria, priority, labels, linked issues, and Linear's suggested branch name. If the issue can't be found, stop and say so.

**Model selection (per-issue).** Read the issue's labels: a `model:sonnet` label → run the **implementation** pass on Sonnet; a `model:opus` label → Opus; neither → the current default. Heuristic for how issues should be labelled: **sonnet** for typo / config / string / dedup / known-root-cause bugfixes (mechanical, well-scoped); **opus** for unclear FSM or concurrency bugs, refactors, epics, or architectural change. This label governs the implementation pass **only** — the `code-reviewer` and `flow-tracer` subagents always keep whatever model their own frontmatter declares, regardless of the label.

**2. Move to In Progress.** Update the issue status to "In Progress" via Linear MCP, and assign it to me (`me`) if unassigned.

**3. Create the branch.** Fetch first, then branch off the **remote** tip so you never build on a stale local main: `git fetch origin` then `git checkout -b <branch-name> origin/dev`. If Linear gives no branch name, use `feature/$1-<short-slug>`. **De-dupe the issue id:** Linear's suggested `gitBranchName` doubles the identifier when the issue title itself starts with it — e.g. a title "PRO-75: Delete SMS…" yields `…/pro-75-pro-75-delete-sms…`. Collapse the repeated `pro-N-pro-N` to a single `pro-N` before creating the branch (`…/pro-75-delete-sms…`). **Worktree guard:** if the target branch already exists in another worktree (`git worktree list` shows it, or `git checkout` reports it is checked out elsewhere), stop and tell me — do not force or delete it. **But if the branch for $1 is already checked out in *this* worktree**, that is the parallel-batch setup, not a collision: skip this step entirely and go to step 4. See "Running several issues at once" in CLAUDE.md.

**4. Plan.** Restate the requirements as a short checklist of changes (files + what changes in each). Map each acceptance criterion to a change. Show me the plan and the FSM/lifecycle invariants it must preserve (context clearing, TTLs, DI pattern, text-only menus (no buttons)). Wait for nothing if the plan is obvious; pause for my confirmation only if there's a genuine design fork. Once the plan is settled, **post it as a comment on the issue** (via Linear MCP) before writing any code, so the plan is persisted even if the run is interrupted.

**5. Implement.** Write the code, file by file. Respect every Proli convention: async safety, dependency injection through parameters in pro_flow/customer_flow, state writes through state_manager_service with the right WorkerConstants TTL, context cleared on flow exit, no hardcoded secrets, PII masked in logs.

**6. Tests (write).** Delegate to the **test-writer** subagent to add coverage for the new/changed branches. It runs **only its own new/changed test files** — the full suite does not run yet; that happens once, in step 8, after review has finalized the branch.

**7. Review.** Delegate to the **code-reviewer** subagent. If it returns BLOCKERS, fix them and re-review. WARNINGS: fix if quick, otherwise note them in the PR description. Review-driven test changes go back through **test-writer** (continue the same agent), which again runs only the affected test file. If the change touches `admin_panel/`, also delegate to the **ux-reviewer** subagent.

**8. Full suite (once, on the final branch state).** Delegate to the **test-runner** subagent to run the full suite — after review fixes are in, so one run covers the code that will actually be committed (running it before review means re-running it after every review-driven test change). Re-run **only** to confirm a fix after a failure — a confirmation re-run is not a violation of the once-per-loop intent. The baseline lives in `docs/TESTING.md` ("Current status" line): fewer passed = regression (fix the production code yourself and re-run until green); more passed = update the baseline in `docs/TESTING.md` (step 9). CI enforces the line as a **floor** (the "Guard — test baseline floor" step fails on a regression and warns on growth), so a forgotten bump will not block the merge — `refresh-test-baseline.yml` opens a PR for it afterwards — but move it in step 9 anyway so the floor keeps tracking reality.

**9. Sync docs.** Delegate to the **docs-syncer** subagent (incremental mode) to update any docs invalidated by this change — including the `docs/TESTING.md` baseline line, using the exact count from step 8.

**10. Commit & PR.** Stage the changes, write a commit message referencing $1 (e.g. `feat($1): <summary>`), and open a PR with `gh pr create`. **The `$1` in that title is a closing marker:** Linear links via branch name, PR title *or* PR body, and moves the issue to Done on merge. That is correct here, because this flow runs one issue to completion. If you ever finish only part of $1, keep the key out of all three and link the PR from a Linear comment instead — see the Linear ↔ GitHub section in `CLAUDE.md`. The PR body must include: a one-paragraph summary, the acceptance criteria checked off, the code-reviewer's verdict, and the test count as "<old from docs/TESTING.md> → <new>". If the change touched `app/core/constants.py` (new state, TTL, or lifecycle value) or the `workflow_service.py` dispatch order, no manual agent-file review is needed: the embedded agent-pack facts (UserStates, LeadStatus lifecycle, TTLs, and dispatch order) are guarded automatically by `tests/test_agent_pack_drift.py` — a failing run there points at the exact stale fact to fix.

**11. Close the loop in Linear.** Post a comment on $1 summarizing what was implemented and link the PR. Move the issue to "In Review" (not Done — a human merges the PR). 

**12. Record it in the living system-audit artifact — respecting the single-writer rule.** Run `git worktree list` first; it decides which of these you do.

- **One worktree — you are the only writer.** `action: "read"` the artifact and build your update from the version that comes back, then republish it via the Artifact tool with `url: https://claude.ai/code/artifact/363c67d3-e33c-44f2-a8fd-afe6534711c7` (the `url` parameter is what updates it in place — omitting it forks a new artifact). Add a Fix-log row for $1 (date, one-line outcome, PR link) and, if $1 appears among the audit's "Filed in Linear" cards, mark that card done. Keep the title and 🩺 favicon unchanged.
- **More than one worktree — you are one track of a parallel batch. Do not publish.** The artifact has other writers right now; a concurrent republish is refused as stale, and forcing past it discards their entry. Put your Fix-log row in the PR body under a `## 🩺 Audit fix-log entry` heading and stop there — the session closing the batch drains every merged PR's section into one write. Say in your final status block that the row is queued, not published, so nobody assumes the dashboard is current.

**Evidence gate:** If the issue carries `launch-readiness` or `ops-verification`, it may NOT be moved past "In Review" — by anyone, including Linear↔GitHub automation on PR merge — without evidence attached to the issue: an execution log, screenshot, or checklist with boxes actually ticked. A merged PR that only *adds a document or script* is not evidence that it was *executed*. If the deliverable is "run X", the Done criterion is the run, not the artifact.

## Final output

End with a compact status block:

```
Issue:    $1 — <title>
Branch:   <branch>
PR:       <url>
Tests:    <old> → <new> (no regressions)
Review:   <blockers fixed N / warnings M>
Linear:   In Review
Audit:    published | queued in PR body (parallel batch)
```

If you stopped early for any reason, say exactly which step and why, and what you need from me to continue.
