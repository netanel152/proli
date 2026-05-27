#!/usr/bin/env python3
"""PostToolUse hook for Edit/Write/MultiEdit — runs black then flake8 on .py files."""
import json
import os
import subprocess
import sys

SKIP_DIRS = ("venv", ".venv", "__pycache__", ".pytest_cache", "node_modules")


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    file_path = data.get("tool_input", {}).get("file_path", "")
    if not file_path:
        file_path = data.get("tool_input", {}).get("path", "")
    if not file_path:
        sys.exit(0)

    # Only act on .py files
    if not file_path.endswith(".py"):
        sys.exit(0)

    # Skip generated/cache directories
    normalized = file_path.replace("\\", "/")
    if any(f"/{d}/" in normalized or normalized.startswith(d + "/") for d in SKIP_DIRS):
        sys.exit(0)

    # File must still exist
    if not os.path.isfile(file_path):
        sys.exit(0)

    # Run black --quiet (auto-formats in place)
    try:
        subprocess.run(
            [sys.executable, "-m", "black", "--quiet", file_path],
            check=False,
            capture_output=True,
        )
    except FileNotFoundError:
        pass  # black not available, skip silently

    # Run flake8; print output to stderr as informational feedback
    try:
        result = subprocess.run(
            [sys.executable, "-m", "flake8", file_path],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.stdout.strip():
            print(f"flake8: {result.stdout.strip()}", file=sys.stderr)
        if result.stderr.strip():
            print(f"flake8 stderr: {result.stderr.strip()}", file=sys.stderr)
    except FileNotFoundError:
        pass  # flake8 not available, skip silently

    sys.exit(0)


if __name__ == "__main__":
    main()
