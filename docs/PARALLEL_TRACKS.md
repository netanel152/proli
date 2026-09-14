# Parallel tracks — git worktrees, Windows shell traps, teardown

Moved out of `CLAUDE.md` in September 2026 so the always-loaded rulebook carries only what
every session needs; the operator-side detail lives here. `CLAUDE.md` keeps the two lines
that bind every session (re-check branch state before editing; `git show` needs a `./`
prefix under Git Bash) and points here for the rest.

## Two shell traps on Windows, both of which fail *silently*

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

## Running several issues at once (git worktrees)

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

**Disjoint file footprints are the whole selection criterion.** Two tracks editing one module spend more time resolving conflicts than the parallelism saves. Some work is never a parallel track: PRO-139 (extracting the dispatcher rewrites what every flow issue touches — it runs alone), the copy chain PRO-164/168/169 (all rewriting `messages.py`/`prompts.py`, serial by construction), and anything labelled `launch-readiness`/`ops-verification`, whose Done criterion is an operator run rather than a merged PR.

Teardown once a track's PR is merged: **`/cleanup-worktrees`** (or `bash scripts/cleanup_worktrees.sh`, `--dry-run` to preview, a name to limit it to one track). It sweeps every worktree whose PR is MERGED, removes the directory, prunes, and deletes the local branch — with `-D`, because `-d` cannot see a squash merge. It refuses to touch a worktree with uncommitted changes, unpushed commits, or an unmerged branch, and prints the reason for each one it skips.

Run it from the main checkout, never from inside a worktree and never as a step of `/take-issue`: on Windows a process's cwd is locked, so a session cannot delete the folder it is running in. That is what leaves the empty, undeletable directories behind.
