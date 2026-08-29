"""Validation tests for the shared Claude Code project config.

`.claude/` and `.mcp.json` are checked into git and apply to every clone, so a
typo in them breaks tooling for the whole team — silently, and only on the next
session start. Nothing else in CI parses these files.

What is pinned here is structural, not stylistic: the JSON parses, every hook
command points at a script that exists, every MCP server declares the fields its
transport needs, and every slash command carries the frontmatter that makes it
discoverable. Adding a hook, a server or a command is expected; pointing one at
a path that isn't there is not.

Each rule is **one** test that collects every offender, rather than one test per
hook / server / command file. Both shapes catch the same breakage, but the
parametrized shape grew the suite by a test every time somebody added a slash
command, and reported thirteen passes for a single rule — while a failure named
only the first file it hit.
"""

import json
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_CLAUDE = _ROOT / ".claude"
_SETTINGS = _CLAUDE / "settings.json"
_MCP = _ROOT / ".mcp.json"
_HOOKS = _CLAUDE / "hooks"
_COMMANDS = _CLAUDE / "commands"


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


# There is deliberately no standalone "the JSON parses" test: every test below
# calls `_load()`, so a malformed file fails them all with the json error.


# --- MCP servers --------------------------------------------------------------


def test_mcp_servers_declare_the_fields_their_transport_needs():
    """stdio needs a command; http/sse need a url. A missing one fails silently."""
    servers = _load(_MCP)["mcpServers"]
    assert servers, ".mcp.json declares no servers"

    for name, cfg in servers.items():
        # `type` is optional and defaults to stdio, which is what `command` implies.
        transport = cfg.get("type", "stdio")
        assert transport in (
            "stdio",
            "http",
            "sse",
        ), f"{name}: unknown type {transport!r}"
        if transport == "stdio":
            assert cfg.get("command"), f"{name}: stdio server has no command"
            assert isinstance(cfg.get("args", []), list), f"{name}: args must be a list"
        else:
            assert cfg.get("url", "").startswith(
                "https://"
            ), f"{name}: needs an https url"


def test_mcp_env_values_are_placeholders_not_literal_secrets():
    """Env values must be ${VAR} expansions — .mcp.json is committed."""
    for name, cfg in _load(_MCP)["mcpServers"].items():
        for key, value in (cfg.get("env") or {}).items():
            assert (
                "${" in value
            ), f"{name}.{key} looks like a literal value, not ${{VAR}}"


# --- Hooks --------------------------------------------------------------------


def _hook_commands():
    """Every `command` string across every hook event in settings.json."""
    for matchers in (_load(_SETTINGS).get("hooks") or {}).values():
        for matcher in matchers:
            for hook in matcher.get("hooks", []):
                if hook.get("type") == "command":
                    yield hook["command"]


def test_every_hook_command_points_at_a_file_that_exists():
    """A hook whose script is missing is a per-tool-call error on every session."""
    commands = list(_hook_commands())
    assert commands, "settings.json declares no command hooks"

    broken = []
    for command in commands:
        referenced = re.findall(r"\.claude/hooks/([A-Za-z0-9_.-]+)", command)
        if not referenced:
            broken.append(f"{command}: references no .claude/hooks script")
            continue
        broken += [
            f"{command}: missing hook script {name}"
            for name in referenced
            if not (_HOOKS / name).is_file()
        ]
        # run-hook.sh takes the real hook as its argument — check that too.
        broken += [
            f"{command}: run-hook.sh dispatches to a missing {name}"
            for name in re.findall(r'run-hook\.sh"?\s+([A-Za-z0-9_.-]+\.py)', command)
            if not (_HOOKS / name).is_file()
        ]
    assert not broken, "broken hook commands:\n  " + "\n  ".join(broken)


def test_hook_commands_use_the_project_dir_variable():
    """Absolute machine paths in a committed settings file break other clones."""
    for command in _hook_commands():
        assert (
            "CLAUDE_PROJECT_DIR" in command
        ), f"hook command is not portable: {command}"


def test_hook_scripts_are_not_orphaned():
    """Every script in .claude/hooks/ is wired to something (or is the launcher)."""
    wired = " ".join(_hook_commands())
    for script in _HOOKS.glob("*.py"):
        assert script.name in wired, f"{script.name} is never invoked by settings.json"


# --- Slash commands -----------------------------------------------------------


def test_every_command_has_a_description():
    """Without frontmatter a command shows up unlabelled in the picker."""
    offenders = []
    for command_file in sorted(_COMMANDS.glob("*.md")):
        text = command_file.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            offenders.append(f"{command_file.name}: no frontmatter block")
            continue
        frontmatter = text.split("---\n", 2)[1]
        if not re.search(r"^description:\s*\S", frontmatter, re.MULTILINE):
            offenders.append(f"{command_file.name}: frontmatter has no description")
    assert not offenders, "slash commands missing a description:\n  " + "\n  ".join(
        offenders
    )


# --- Permissions --------------------------------------------------------------


def test_permission_allowlist_has_no_duplicates():
    allow = (_load(_SETTINGS).get("permissions") or {}).get("allow", [])
    duplicates = {entry for entry in allow if allow.count(entry) > 1}
    assert not duplicates, f"duplicate allowlist entries: {sorted(duplicates)}"
