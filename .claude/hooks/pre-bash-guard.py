#!/usr/bin/env python3
"""PreToolUse hook for Bash — blocks dangerous shell commands."""
import json
import re
import subprocess
import sys

# Branches that never take a direct commit or push.
#   dev        — the integration branch (renamed from master); deploys to staging
#   production — the release branch; deploys to production
#   master/main— kept so the guard still works in clones that predate the rename,
#                and in any other repo this hook is copied into
PROTECTED_BRANCHES = ("main", "master", "dev", "production")


def target_dir(command, default_cwd=None):
    """Directory the git command actually operates on.

    The guard has to read the branch that would *receive* the commit, which is
    not always the one checked out where the session happens to be sitting:
    ``git -C <dir> commit`` and ``cd <dir> && git commit`` both target another
    tree. Once work runs in one worktree per issue (see "Running several issues
    at once" in CLAUDE.md) that stops being an edge case and becomes the normal
    shape, and reading the wrong tree is wrong in both directions — it blocked
    every commit made in a worktree while the main tree sat on ``dev`` (its
    resting state), and it would have waved through a commit aimed at ``dev``
    whenever the main tree happened to be on a feature branch.

    Pure, so it is testable without a repo.
    """
    match = re.search(r"\bgit\s+(?:-c\s+\S+\s+)*-C\s+(\"[^\"]+\"|'[^']+'|\S+)", command)
    if match:
        return match.group(1).strip("\"'")

    match = re.search(r"^\s*cd\s+(\"[^\"]+\"|'[^']+'|\S+)\s*&&", command)
    if match:
        return match.group(1).strip("\"'")

    return default_cwd


def _current_branch(cwd=None):
    """Best-effort current git branch; empty string if it can't be determined.

    ``cwd`` is the tree to inspect (see :func:`target_dir`). An unreadable or
    missing directory raises and yields ``""``, which ``evaluate`` treats as
    "unknown branch, allow" — the same fail-open this has always had.
    """
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=cwd or None,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def evaluate(command, branch):
    """Decide whether a shell command is allowed.

    Returns ``(exit_code, message)``: exit_code ``2`` blocks the command (the
    message is printed to stderr), ``0`` allows it. ``branch`` is the current
    git branch name — used only by the protected-branch commit/push guard.
    """
    # Block rm -rf targeting /, ~, $HOME, or bare glob
    if re.search(r"rm\s+-[a-zA-Z]*r[a-zA-Z]*f|rm\s+-[a-zA-Z]*f[a-zA-Z]*r", command):
        targets = re.findall(r"rm\s+(?:-\S+\s+)+(.*)", command)
        target_str = targets[0] if targets else ""
        dangerous = re.search(
            r"(^|\s)(/\s*$|~\s*$|\$HOME\s*$|\*\s*$|/\s+|~\s+|\$HOME\s+|\*\s+)",
            " " + target_str,
        )
        if (
            dangerous
            or re.search(r"rm\s+-[rf]+\s+[/~*]", command)
            or re.search(r"rm\s+-[rf]+\s+\$HOME", command)
        ):
            return (
                2,
                "BLOCKED: rm -rf targeting /, ~, $HOME, or bare glob is not allowed.",
            )

    # Block redirect into .env
    if re.search(r">>?\s*\.env\b", command):
        return 2, "BLOCKED: Redirecting into .env is not allowed."

    # Block git push --force / -f to a protected branch
    if (
        re.search(r"git\s+push\b", command)
        and re.search(r"--force\b|-f\b", command)
        and re.search(r"\b(main|master|dev|production)\b", command)
    ):
        return 2, "BLOCKED: Force-pushing to a protected branch is not allowed."

    # Block plain git commit / git push while on a protected branch. All Proli
    # work happens on a feature branch (see CLAUDE.md and the take-issue
    # guardrails); this closes the gap the force-push rule above leaves open for
    # ordinary commits and non-force pushes.
    #
    # `dev` is the integration branch (renamed from `master`) and `production`
    # is the release branch that Railway deploys — neither takes direct commits.
    # `master`/`main` stay listed so the guard keeps working in clones that have
    # not yet renamed, and for any other repo this hook is copied into.
    if branch in PROTECTED_BRANCHES and re.search(
        r"git\s+(?:-\S+\s+|-c\s+\S+\s+|-C\s+\S+\s+)*(commit|push)\b", command
    ):
        return (
            2,
            f"BLOCKED: refusing 'git commit' / 'git push' while on '{branch}'. "
            "Create a feature branch first — never commit or push to a "
            "protected branch.",
        )

    # Block mongo/mongosh dropDatabase or drop()
    if re.search(r"\b(mongo|mongosh)\b", command) and re.search(
        r"dropDatabase\s*\(|\.drop\s*\(", command
    ):
        return (
            2,
            "BLOCKED: dropDatabase / drop() via mongo/mongosh is not allowed.",
        )

    return 0, ""


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    command = data.get("tool_input", {}).get("command", "")
    if not command:
        sys.exit(0)

    branch = _current_branch(target_dir(command, data.get("cwd")))
    exit_code, message = evaluate(command, branch)
    if exit_code != 0:
        print(message, file=sys.stderr)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
