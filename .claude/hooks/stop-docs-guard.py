#!/usr/bin/env python3
"""Stop hook — refuse (once) to end a turn that changed documented source
without any ``.md`` being touched.

The session rule in CLAUDE.md says: after a code-change task, run the
**docs-syncer** subagent. Until this hook existed that rule lived only in the
model's memory of the rulebook, which is exactly the kind of instruction that
gets skipped on a long turn. A Stop hook is the one seam where the harness,
not the model, can enforce it.

Two questions decide it, each answered from more than one source so the hook
is neither blind to committed work nor fooled by someone else's:

* **Did this session change documented source?** — a ``.py`` under ``app/``,
  ``admin_panel/`` or ``scripts/`` among the files *this session* wrote
  (``Edit``/``Write``/``MultiEdit``/``NotebookEdit`` calls read from the
  transcript Claude Code hands the hook as ``transcript_path``). The working
  tree is shared with parallel sessions and a previous turn may have left
  things uncommitted, so a bare ``git status`` would blame this session for
  another's edits; the transcript is what this session actually did. When the
  transcript cannot be read the hook falls back to the dirty tree.
* **Was any ``.md`` touched?** — by this session (transcript), *or* dirty in
  the tree (a docs-syncer subagent's edits live in its own transcript, not
  this one, but they do dirty the tree), *or* committed on this branch since
  ``origin/dev`` (``/take-issue`` commits before it stops). Any of the three
  counts: whether the sync was *enough* is the syncer's job, not this hook's.

Decision logic (pinned by ``tests/test_stop_docs_guard.py``):

* ``stop_hook_active`` is true → exit 0. Claude Code sets it when this hook
  already blocked the previous stop, so the reminder fires at most once per
  turn and can never loop.
* No documented source written → exit 0. Answering a question, editing docs,
  editing tests or `.claude/` alone needs no sync.
* Some ``.md`` touched by any of the three sources → exit 0.
* Otherwise → exit 2 with the reminder on stderr, which Claude Code feeds back
  to the model as the reason it may not stop yet. The model then either runs
  the syncer or says in one line why the docs are unaffected — both end the
  turn, because the second attempt arrives with ``stop_hook_active`` set.

Fail-open everywhere: no git, no repo, unreadable transcript, unparsable
stdin, any exception → the affected source is treated as empty and the hook
exits 0. A hook that can block a stop must never do so by accident.
"""

import json
import os
import subprocess
import sys

#: Directories whose changes the docs describe. Tests, workflows and the
#: `.claude/` tree are deliberately absent: a test-only change has no doc to
#: sync, and a `.claude/` change is itself the documentation.
DOCUMENTED_SOURCE_PREFIXES = ("app/", "admin_panel/", "scripts/")

#: Tool calls that write a file; their `file_path` is what this session changed.
_WRITE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")

REMINDER = (
    "Source under app/, admin_panel/ or scripts/ changed but no .md file did. "
    "CLAUDE.md's session rule: delegate to the docs-syncer subagent "
    "(incremental mode) to update CLAUDE.md, .claude/rules/, README.md and "
    "docs/ — or state in one line why the docs are unaffected, then stop again."
)


def _git(args, cwd=None):
    """stdout of a git command, or ``None`` when git is unavailable or fails."""
    try:
        result = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _dirty_paths(cwd=None):
    """Paths from ``git status --porcelain -z`` — staged, unstaged, untracked.

    ``-z`` so a path containing `` -> `` or a space is never mis-split; in that
    mode a rename is two consecutive NUL fields (new, then old) and both are
    returned, which is right for "was this path touched".
    """
    out = _git(["status", "--porcelain", "-z", "--untracked-files=all"], cwd)
    if out is None:
        return []
    paths = []
    fields = out.split("\0")
    i = 0
    while i < len(fields):
        field = fields[i]
        i += 1
        if len(field) < 4:
            continue
        paths.append(field[3:])
        if field[0] in "RC":  # rename/copy: the next field is the source path
            if i < len(fields) and fields[i]:
                paths.append(fields[i])
            i += 1
    return paths


def _branch_paths(cwd=None):
    """Paths committed on this branch since it left ``origin/dev`` (or ``[]``)."""
    out = _git(["diff", "--name-only", "origin/dev...HEAD"], cwd)
    if out is None:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def _session_written_paths(transcript_path, cwd=None):
    """Files this session's write tools touched, relative to ``cwd``.

    ``None`` (not ``[]``) when the transcript cannot be read, so the caller can
    fall back to the dirty tree rather than concluding nothing was written.
    """
    if not transcript_path:
        return None
    written = set()
    try:
        with open(transcript_path, encoding="utf-8") as fh:
            for raw in fh:
                try:
                    entry = json.loads(raw)
                except ValueError:
                    continue
                message = entry.get("message") if isinstance(entry, dict) else None
                content = (message or {}).get("content") if message else None
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    if block.get("name") not in _WRITE_TOOLS:
                        continue
                    tool_input = block.get("input") or {}
                    path = tool_input.get("file_path") or tool_input.get(
                        "notebook_path"
                    )
                    if path:
                        written.add(_relative(path, cwd))
    except OSError:
        return None
    return sorted(written)


def _relative(path, cwd):
    """A path as the repo-relative, forward-slash form the prefixes expect."""
    path = str(path).replace("\\", "/")
    if cwd:
        base = str(cwd).replace("\\", "/").rstrip("/") + "/"
        if path.startswith(base):
            path = path[len(base) :]
    return path


def evaluate(written_paths, dirty_paths, branch_paths, stop_hook_active):
    """Return ``(exit_code, message)`` — ``2`` blocks the stop with ``message``.

    ``written_paths`` is what this session wrote (``None`` → fall back to
    ``dirty_paths`` as the best available proxy); the other two lists only
    ever *release* the block, never cause it.
    """
    if stop_hook_active:
        return 0, ""
    source = dirty_paths if written_paths is None else written_paths
    normalized = [p.replace("\\", "/") for p in source]
    touched_source = any(
        p.startswith(DOCUMENTED_SOURCE_PREFIXES) and p.endswith(".py")
        for p in normalized
    )
    if not touched_source:
        return 0, ""
    everything = normalized + [
        p.replace("\\", "/") for p in (dirty_paths or []) + (branch_paths or [])
    ]
    if any(p.lower().endswith(".md") for p in everything):
        return 0, ""
    return 2, REMINDER


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError, ValueError):
        sys.exit(0)
    if not isinstance(data, dict):
        sys.exit(0)
    try:
        cwd = data.get("cwd") or os.getcwd()
        exit_code, message = evaluate(
            _session_written_paths(data.get("transcript_path"), cwd),
            _dirty_paths(cwd),
            _branch_paths(cwd),
            bool(data.get("stop_hook_active")),
        )
    except Exception:  # noqa: BLE001 — a guard that can block must fail open
        sys.exit(0)
    if exit_code != 0:
        print(message, file=sys.stderr)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
