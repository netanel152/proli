#!/usr/bin/env python3
"""PostToolUse hook for Edit/Write/MultiEdit — black formats, flake8 reports back.

**The findings have to reach the model, and stderr does not carry them.** A
PostToolUse hook's stdout and stderr on exit 0 are shown to the *user* in
transcript mode; only the documented JSON form puts text into Claude's context.
This hook printed flake8 to stderr with exit 0 for a year, which was fine while
flake8 was advisory and 242 findings deep — nobody could act on it anyway. Since
PR #180 the debt is zero and `flake8 --count .` runs in CI, so a finding in a
file this session just edited is *a build failure already committed to*, and the
one party who could fix it before the push was the only one not being told.

So a finding is now returned as `{"decision": "block", "reason": ...}` on stdout:
the documented PostToolUse shape, which hands `reason` to Claude. Nothing else
speaks. black formats in place and stays silent, because a reformat is not a
problem to fix — but when black *did* rewrite the file, the reason says so, since
the model's copy of the file is then stale and its next `old_string` would miss.

Fail-open throughout: a machine without black or flake8, an unreadable payload
or a crashed subprocess all exit 0 with no output. A guard that breaks the edit
loop when its own tooling is missing is worse than no guard.
"""

import hashlib
import json
import os
import subprocess
import sys

SKIP_DIRS = ("venv", ".venv", "__pycache__", ".pytest_cache", "node_modules")


def is_checkable(file_path):
    """True when this path is a .py file the project's linters own.

    Pure, so the skip rules are testable without a filesystem: generated and
    vendored trees are not ours to format, and a non-`.py` file has no linter
    here at all.
    """
    if not file_path or not file_path.endswith(".py"):
        return False
    normalized = file_path.replace("\\", "/")
    return not any(
        f"/{d}/" in normalized or normalized.startswith(f"{d}/") for d in SKIP_DIRS
    )


def build_reason(file_path, reformatted, findings):
    """The text handed to Claude, or ``""`` to stay silent.

    The rule is one sentence: **speak only about what would fail CI.** flake8
    findings are that; a black reformat on its own is not, so it is reported
    only as a rider on a message that was going out anyway — it tells the model
    its in-memory copy of the file is stale, which is why it rides along rather
    than being dropped.
    """
    if not findings.strip():
        return ""

    lines = [
        f"flake8 reports {len(findings.strip().splitlines())} finding(s) in "
        f"{file_path}. CI runs `flake8 --count .` and fails the build on any "
        "finding, so this must be fixed before the push:",
        "",
        findings.strip(),
    ]
    if reformatted:
        lines += [
            "",
            "(black also reformatted this file, so re-read it before your next "
            "edit — your copy is stale.)",
        ]
    return "\n".join(lines)


def _digest(path):
    """Content hash, or None if the file cannot be read."""
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return None


def _run(args):
    """Run a module, returning its stdout. Empty string on any failure."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=25,
        )
        return result.stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError, ValueError):
        sys.exit(0)

    tool_input = data.get("tool_input") or {}
    file_path = tool_input.get("file_path") or tool_input.get("path") or ""

    if not is_checkable(file_path) or not os.path.isfile(file_path):
        sys.exit(0)

    before = _digest(file_path)
    _run(["black", "--quiet", file_path])
    reformatted = before is not None and _digest(file_path) != before

    reason = build_reason(file_path, reformatted, _run(["flake8", file_path]))
    if reason:
        json.dump({"decision": "block", "reason": reason}, sys.stdout)
    sys.exit(0)


if __name__ == "__main__":  # pragma: no cover - process entry point
    main()
