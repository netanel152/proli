#!/bin/sh
# SessionStart hook — inject the repo's real git state into the new session.
#
# The working tree is shared between parallel Claude sessions (see the
# "shell traps" section of CLAUDE.md): another session can move HEAD at any
# time, so a session must never assume it starts on `dev` with a clean tree.
# Printing branch + last commit + dirty files here puts the actual state into
# context from message one. stdout of a SessionStart hook is added as context;
# always exit 0 — this hook informs, it never blocks.

cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

branch="$(git branch --show-current 2>/dev/null)"
echo "git: on branch '${branch:-<detached>}', last commit: $(git log -1 --oneline 2>/dev/null)"

dirty="$(git status --short 2>/dev/null)"
if [ -n "$dirty" ]; then
    echo "git: working tree is DIRTY (may belong to a parallel session — re-check before editing or committing):"
    echo "$dirty" | head -20
else
    echo "git: working tree clean"
fi
exit 0
