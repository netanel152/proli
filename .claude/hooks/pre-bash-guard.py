#!/usr/bin/env python3
"""PreToolUse hook for Bash — blocks dangerous shell commands."""
import json
import re
import sys


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    command = data.get("tool_input", {}).get("command", "")
    if not command:
        sys.exit(0)

    # Block rm -rf targeting /, ~, $HOME, or bare glob
    if re.search(r"rm\s+-[a-zA-Z]*r[a-zA-Z]*f|rm\s+-[a-zA-Z]*f[a-zA-Z]*r", command):
        targets = re.findall(r"rm\s+(?:-\S+\s+)+(.*)", command)
        target_str = targets[0] if targets else ""
        dangerous = re.search(
            r"(^|\s)(/\s*$|~\s*$|\$HOME\s*$|\*\s*$|/\s+|~\s+|\$HOME\s+|\*\s+)",
            " " + target_str,
        )
        if dangerous or re.search(r"rm\s+-[rf]+\s+[/~*]", command) or re.search(
            r"rm\s+-[rf]+\s+\$HOME", command
        ):
            print(
                "BLOCKED: rm -rf targeting /, ~, $HOME, or bare glob is not allowed.",
                file=sys.stderr,
            )
            sys.exit(2)

    # Block redirect into .env
    if re.search(r">>?\s*\.env\b", command):
        print("BLOCKED: Redirecting into .env is not allowed.", file=sys.stderr)
        sys.exit(2)

    # Block git push --force / -f to main or master
    if re.search(r"git\s+push\b", command) and re.search(
        r"--force\b|-f\b", command
    ) and re.search(r"\b(main|master)\b", command):
        print(
            "BLOCKED: Force-pushing to main/master is not allowed.", file=sys.stderr
        )
        sys.exit(2)

    # Block mongo/mongosh dropDatabase or drop()
    if re.search(r"\b(mongo|mongosh)\b", command) and re.search(
        r"dropDatabase\s*\(|\.drop\s*\(", command
    ):
        print(
            "BLOCKED: dropDatabase / drop() via mongo/mongosh is not allowed.",
            file=sys.stderr,
        )
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
