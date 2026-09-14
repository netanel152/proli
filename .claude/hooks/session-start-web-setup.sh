#!/bin/sh
# SessionStart hook — make a Claude Code on the web session able to run the
# suite, black and flake8.
#
# A cloud session starts from a fresh clone: no venv, no .env, and the
# system python has a Debian-managed PyJWT that makes a bare
# `pip install -r requirements.txt` abort half-way ("Cannot uninstall PyJWT
# ... RECORD file not found"). So the test-runner and test-writer subagents,
# the PostToolUse black/flake8 hook and `/test` were all silently useless
# there. This hook builds the same layout the local machines have —
# `venv/` at the project root, which `run-hook.sh` already looks for — and
# writes a placeholder `.env` so `Settings` can construct (config loads at
# import; without it the suite cannot even collect).
#
# Local sessions exit immediately: CLAUDE_CODE_REMOTE is "true" only in the
# cloud. Idempotent — a second run finds the venv and the .env and only
# re-checks the pins. Runs synchronously on purpose: the first tool call of
# a session may well be `pytest`, and an async install would race it.
#
# Never fails the session start: every step reports and continues, so a
# network hiccup degrades to "tests unavailable, here is why" rather than a
# session that will not open.

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
    exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0

echo "web-setup: preparing venv + .env for Claude Code on the web"

# 1. Virtualenv at the project root (gitignored), the layout run-hook.sh expects.
if [ ! -x venv/bin/python ]; then
    if python3 -m venv venv 2>/dev/null; then
        echo "web-setup: created venv/"
    else
        echo "web-setup: could not create venv/ — tests and linters will not run this session" >&2
        exit 0
    fi
fi

# 2. Dependencies — the pinned set production runs on (see requirements.txt).
if venv/bin/python -m pip install -q -r requirements.txt 2>/tmp/web-setup-pip.err; then
    echo "web-setup: requirements.txt installed"
else
    echo "web-setup: pip install failed — tests may not run; last lines:" >&2
    tail -5 /tmp/web-setup-pip.err >&2
fi
if ! venv/bin/python -m pip install -q flake8 2>/dev/null; then
    echo "web-setup: flake8 not installed (black still is)" >&2
fi

# 3. Placeholder .env so Settings constructs. Same values CI uses; nothing
#    here can reach a real service (WHATSAPP_DRY_RUN=true, no Mongo URI ->
#    localhost, which mongomock never touches). Never overwrites an existing
#    file: a deliberately provided .env wins.
if [ ! -f .env ]; then
    cat > .env <<'EOF'
# Written by .claude/hooks/session-start-web-setup.sh for a Claude Code on the
# web session. Placeholder values only — enough for Settings to construct and
# the unit suite (mongomock + fakeredis) to run. Nothing here is a credential.
ENVIRONMENT=development
GEMINI_API_KEY=web-session-not-a-real-key
CLOUDINARY_CLOUD_NAME=web
CLOUDINARY_API_KEY=web
CLOUDINARY_API_SECRET=web
WHATSAPP_PROVIDER=dryrun
WHATSAPP_DRY_RUN=true
EOF
    echo "web-setup: wrote placeholder .env"
fi

# 4. Put the venv first on PATH for every Bash command this session, so
#    `pytest`, `black` and `flake8` resolve without a venv/bin/ prefix.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
    {
        echo "export PATH=\"$PWD/venv/bin:\$PATH\""
        echo "export VIRTUAL_ENV=\"$PWD/venv\""
    } >> "$CLAUDE_ENV_FILE"
fi

# 5. The PR-only workflow's local backstop, same as the one-time clone setup.
git config core.hooksPath .githooks 2>/dev/null || true

if venv/bin/python -c "import pytest, mongomock_motor, fakeredis, black" 2>/dev/null; then
    echo "web-setup: ready — pytest, mongomock, fakeredis and black import"
else
    echo "web-setup: dependency check failed — run 'venv/bin/python -m pip install -r requirements.txt' and read the error" >&2
fi
exit 0
