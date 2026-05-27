#!/usr/bin/env python3
"""PreToolUse hook for Edit/Write/MultiEdit — protects .env and .git/."""
import json
import os
import sys


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    file_path = data.get("tool_input", {}).get("file_path", "")
    if not file_path:
        # MultiEdit uses a different key
        file_path = data.get("tool_input", {}).get("path", "")
    if not file_path:
        sys.exit(0)

    basename = os.path.basename(file_path)
    normalized = file_path.replace("\\", "/")

    # Block edits to .env exactly (allow .env.example, .env.test, etc.)
    if basename == ".env":
        print(
            "BLOCKED: Direct edits to .env are not allowed. Use .env.example instead.",
            file=sys.stderr,
        )
        sys.exit(2)

    # Block edits to anything under .git/
    if "/.git/" in normalized or normalized.endswith("/.git") or normalized.startswith(".git/"):
        print(
            "BLOCKED: Edits inside the .git directory are not allowed.",
            file=sys.stderr,
        )
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
