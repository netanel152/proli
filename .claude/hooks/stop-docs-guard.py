#!/usr/bin/env python3
"""Stop hook — refuse (once) to end a turn that changed documented source
without touching a single ``.md`` file.

The session rule in CLAUDE.md says: after a code-change task, run the
**docs-syncer** subagent. Until this hook existed that rule lived only in the
model's memory of the rulebook, which is exactly the kind of instruction that
gets skipped on a long turn. A Stop hook is the one seam where the harness,
not the model, can enforce it.

Decision logic (pinned by ``tests/test_stop_docs_guard.py``):

* ``stop_hook_active`` is true → exit 0. Claude Code sets it when this hook
  already blocked the previous stop, so the reminder fires at most once per
  turn and can never loop.
* No dirty path under ``app/``, ``admin_panel/`` or ``scripts/`` → exit 0.
  Answering a question, editing docs, or editing tests alone needs no sync.
* At least one dirty ``.md`` anywhere (``CLAUDE.md``, ``.claude/rules/``,
  ``README.md``, ``docs/``) → exit 0. Something was synced; whether it was
  enough is the syncer's job, not this hook's.
* Otherwise → exit 2 with the reminder on stderr, which Claude Code feeds back
  to the model as the reason it may not stop yet. The model then either runs
  the syncer or says in one line why the docs are unaffected — both end the
  turn, because the second attempt arrives with ``stop_hook_active`` set.

"Dirty" is ``git status --porcelain`` relative to the worktree — staged,
unstaged and untracked alike — because a session that has already committed
has nothing left for the syncer to look at, and one that has not can still
run it before committing.

Fail-open everywhere: no git, no repo, unparsable stdin, any exception → exit
0. A hook that can block a stop must never do so by accident.
"""

import json
import subprocess
import sys

#: Directories whose changes the docs describe. Tests, workflows and the
#: `.claude/` tree are deliberately absent: a test-only change has no doc to
#: sync, and a `.claude/` change is itself the documentation.
DOCUMENTED_SOURCE_PREFIXES = ("app/", "admin_panel/", "scripts/")

REMINDER = (
    "Source under app/, admin_panel/ or scripts/ changed but no .md file did. "
    "CLAUDE.md's session rule: delegate to the docs-syncer subagent "
    "(incremental mode) to update CLAUDE.md, .claude/rules/, README.md and "
    "docs/ — or state in one line why the docs are unaffected, then stop again."
)


def _dirty_paths(cwd=None):
    """Paths from ``git status --porcelain``, or ``[]`` when git is unavailable."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    paths = []
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        # A rename shows as "old -> new"; the new path is the one that exists.
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.append(path.strip().strip('"'))
    return paths


def evaluate(dirty_paths, stop_hook_active):
    """Return ``(exit_code, message)`` — ``2`` blocks the stop with ``message``."""
    if stop_hook_active:
        return 0, ""
    normalized = [p.replace("\\", "/") for p in dirty_paths]
    touched_source = any(
        p.startswith(DOCUMENTED_SOURCE_PREFIXES) and p.endswith(".py")
        for p in normalized
    )
    if not touched_source:
        return 0, ""
    if any(p.lower().endswith(".md") for p in normalized):
        return 0, ""
    return 2, REMINDER


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError, ValueError):
        sys.exit(0)
    try:
        exit_code, message = evaluate(
            _dirty_paths(data.get("cwd") or None),
            bool(data.get("stop_hook_active")),
        )
    except Exception:  # noqa: BLE001 — a guard that can block must fail open
        sys.exit(0)
    if exit_code != 0:
        print(message, file=sys.stderr)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
