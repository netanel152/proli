#!/usr/bin/env python3
"""PreToolUse hook for Edit/Write/MultiEdit — protects .env and .git/, and holds
each write-capable pack agent to the paths its job covers (PRO-95)."""
import json
import os
import sys


def _repo_relative(file_path, cwd):
    """``file_path`` as forward-slash, repo-relative; ``None`` if outside ``cwd``."""
    path = os.path.normpath(file_path).replace("\\", "/")
    base = os.path.normpath(cwd or "").replace("\\", "/").rstrip("/") + "/"
    if path.lower().startswith(base.lower()):
        path = path[len(base) :]
    elif path.startswith("/") or path[1:3] == ":/":
        return None
    return None if path.startswith("../") or path == ".." else path


def _docs_syncer_may_write(path):
    """Mirrors the audit scope in `.claude/agents/docs-syncer.md`."""
    if path is None or not path.endswith(".md"):
        return False
    if not path.startswith(".claude/"):
        return True
    return (
        path.startswith(".claude/rules/")
        or (path.startswith(".claude/skills/") and path.endswith("/SKILL.md"))
        or path in (".claude/agents/flow-tracer.md", ".claude/agents/code-reviewer.md")
    )


def agent_write_block(agent_type, file_path, cwd):
    """Why ``agent_type`` may not write ``file_path``, or ``""`` if it may. Pure."""
    path = _repo_relative(file_path, cwd)
    if agent_type == "docs-syncer" and not _docs_syncer_may_write(path):
        return "BLOCKED: docs-syncer writes docs only (PRO-95): " + file_path
    if agent_type == "test-writer" and not (path or "").startswith("tests/"):
        return "BLOCKED: test-writer writes under tests/ only (PRO-95): " + file_path
    # `memory: project` hands flow-tracer Write/Edit whatever `tools:` says, for
    # its notes. Those live outside the tree or in agent-memory; the code doesn't.
    if (
        agent_type == "flow-tracer"
        and path is not None
        and not path.startswith(".claude/agent-memory/")
    ):
        return "BLOCKED: flow-tracer writes only its own memory (PRO-95): " + file_path
    return ""


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
    if (
        "/.git/" in normalized
        or normalized.endswith("/.git")
        or normalized.startswith(".git/")
    ):
        print(
            "BLOCKED: Edits inside the .git directory are not allowed.",
            file=sys.stderr,
        )
        sys.exit(2)

    refusal = agent_write_block(data.get("agent_type"), file_path, data.get("cwd"))
    if refusal:
        print(refusal, file=sys.stderr)
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
