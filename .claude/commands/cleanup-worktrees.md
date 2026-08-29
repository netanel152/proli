---
description: Tear down the parallel-track worktrees under D:/Projects/proli-wt whose PR has merged (optionally one named track, e.g. /cleanup-worktrees pro-123)
---

Run the teardown sweep for finished tracks. `$ARGUMENTS` may name specific
worktrees (`pro-123 pro-143`) or be empty to sweep all of them.

```bash
bash scripts/cleanup_worktrees.sh --dry-run $ARGUMENTS
```

Show the user that plan, then run it for real:

```bash
bash scripts/cleanup_worktrees.sh $ARGUMENTS
```

The script only removes a worktree whose PR is **MERGED** (falling back to "the
branch is already an ancestor of `origin/dev`" when `gh` is unavailable — note
that fallback is false for squash merges, so `gh` is the one that matters), with
a clean tree and nothing unpushed. It deletes the merged local branch too and
runs `git worktree prune`. Anything it refuses is printed with the reason.

Read the output rather than assuming success, and relay it:

- **`SKIP … uncommitted changes`** — real work is sitting there. Do not `--force`
  past this. Find out whose it is first; the usual cause is a session that died
  before committing.
- **`SKIP … is not merged`** — the PR is still open or was closed unmerged.
- **`SKIP … you are inside it`** — run the command from `D:/Projects/proli`.
- **`PARTIAL … folder still locked`** — git's entry is gone but Windows will not
  delete a directory another process has as its cwd. Tell the user which shell
  or editor to close; the folder is empty and harmless until they do.
- **`LEFTOVER`** — a directory git no longer tracks that is not empty. Inspect it
  before suggesting anything; do not delete it.

`--force` exists (skips the merged/clean/pushed checks) but is not yours to
choose: only pass it if the user asks for it in this conversation, and say what
will be discarded before you do.

Do not run this from inside a worktree, and never as part of `/take-issue` — a
session cannot delete the folder it is running in, which is what leaves the
locked empty directories behind. Teardown belongs to the session that closes the
batch, from the main checkout.
