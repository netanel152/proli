#!/bin/sh
# Cross-platform launcher for the Python hooks in this directory.
#
# .claude/settings.json is shared through git, so its hook commands must work
# on every teammate's machine. Hardcoding venv/Scripts/python.exe (Windows
# venv layout) broke macOS/Linux clones and any clone whose venv lives
# elsewhere — this script resolves the interpreter at run time instead:
# project venv first (Windows then POSIX layout), then whatever python the
# PATH offers. Claude Code runs hook commands through Git Bash on Windows and
# /bin/sh elsewhere, so POSIX sh is the one dialect available everywhere.
#
# Usage (from settings.json):
#   "$CLAUDE_PROJECT_DIR"/.claude/hooks/run-hook.sh <hook-script.py>
#
# A missing interpreter exits 0 on purpose: hooks here are guards and
# formatters, and a machine without Python should degrade to "no extra
# guard", not "every tool call fails".

hook_dir="$(cd "$(dirname "$0")" && pwd)"
project_root="$(cd "$hook_dir/../.." && pwd)"

script="$1"
shift 2>/dev/null || true

if [ -z "$script" ] || [ ! -f "$hook_dir/$script" ]; then
    echo "run-hook.sh: unknown hook script '$script'" >&2
    exit 0
fi

for candidate in \
    "$project_root/venv/Scripts/python.exe" \
    "$project_root/venv/bin/python" \
    "$project_root/.venv/Scripts/python.exe" \
    "$project_root/.venv/bin/python"; do
    if [ -x "$candidate" ]; then
        exec "$candidate" "$hook_dir/$script" "$@"
    fi
done

if command -v python3 >/dev/null 2>&1; then
    exec python3 "$hook_dir/$script" "$@"
fi
if command -v python >/dev/null 2>&1; then
    exec python "$hook_dir/$script" "$@"
fi

exit 0
