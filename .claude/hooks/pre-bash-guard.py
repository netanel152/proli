#!/usr/bin/env python3
"""PreToolUse hook for Bash — blocks dangerous shell commands."""
import json
import re
import subprocess
import sys


def _current_branch():
    """Best-effort current git branch; empty string if it can't be determined."""
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=5,
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

    # Block git push --force / -f to main or master
    if (
        re.search(r"git\s+push\b", command)
        and re.search(r"--force\b|-f\b", command)
        and re.search(r"\b(main|master)\b", command)
    ):
        return 2, "BLOCKED: Force-pushing to main/master is not allowed."

    # Block plain git commit / git push while on main or master. All Proli work
    # happens on a feature branch (see CLAUDE.md and the take-issue guardrails);
    # this closes the gap the force-push rule above leaves open for ordinary
    # commits and non-force pushes.
    if branch in ("main", "master") and re.search(
        r"git\s+(?:-\S+\s+|-c\s+\S+\s+|-C\s+\S+\s+)*(commit|push)\b", command
    ):
        return (
            2,
            f"BLOCKED: refusing 'git commit' / 'git push' while on '{branch}'. "
            "Create a feature branch first — never commit or push to main/master.",
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

    exit_code, message = evaluate(command, _current_branch())
    if exit_code != 0:
        print(message, file=sys.stderr)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
