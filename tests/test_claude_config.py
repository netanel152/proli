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
    for script in _HOOKS.iterdir():
        if script.name == "run-hook.sh" or not script.is_file():
            continue
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


# --- Rules (.claude/rules/*.md) -----------------------------------------------
#
# September 2026: CLAUDE.md's reference bulk (service table, constants,
# configuration, admin panel, testing) moved into path-scoped rules so it loads
# only when a matching file is read. The split is only a saving if every rule
# stays scoped and every glob still points at something — a rule whose paths no
# longer match is silently never loaded, and a rule without `paths:` silently
# loads every session, which is the cost the split exists to remove.

_RULES = _CLAUDE / "rules"
_SKILLS = _CLAUDE / "skills"


def _frontmatter(path):
    """The YAML block between the leading `---` fences, or None."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return None
    return text.split("---\n", 2)[1]


def _paths_globs(frontmatter):
    """The quoted globs under a `paths:` key, in order."""
    block = re.search(r"^paths:\n((?:[ \t]+-[^\n]*\n)+)", frontmatter, re.MULTILINE)
    if not block:
        return []
    return [
        g.strip().strip("\"'")
        for g in re.findall(r"^[ \t]+-[ \t]*(.+)$", block.group(1), re.MULTILINE)
    ]


def _glob_matches_something(pattern):
    # pathlib.glob has no `**` at the *end* semantics difference we care about,
    # and treats a trailing `/**` as "the directory and everything under it".
    if "*" not in pattern:
        return (_ROOT / pattern).exists()
    return any(True for _ in _ROOT.glob(pattern))


def test_every_rule_is_path_scoped_and_its_globs_match_something():
    rules = sorted(_RULES.glob("**/*.md"))
    assert rules, ".claude/rules/ is empty — CLAUDE.md's bulk should live here"

    offenders = []
    for rule in rules:
        fm = _frontmatter(rule)
        if fm is None:
            offenders.append(f"{rule.name}: no frontmatter block")
            continue
        globs = _paths_globs(fm)
        if not globs:
            offenders.append(
                f"{rule.name}: no `paths:` — it would load every session; scope it "
                "or move its content back to CLAUDE.md deliberately"
            )
            continue
        offenders += [
            f"{rule.name}: `paths` glob {g!r} matches no file in the repo"
            for g in globs
            if not _glob_matches_something(g)
        ]
    assert not offenders, "rule frontmatter problems:\n  " + "\n  ".join(offenders)


def test_claude_md_indexes_every_rule():
    """The rules index at the bottom of CLAUDE.md names each file, so a reader
    of the always-loaded file knows what loads on demand."""
    claude_md = (_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    missing = [
        rule.name for rule in _RULES.glob("*.md") if f"`{rule.name}`" not in claude_md
    ]
    assert not missing, f"rules missing from CLAUDE.md's index: {missing}"


# --- Skills (.claude/skills/<name>/SKILL.md) ----------------------------------
#
# A skill is a procedure that *points at* code, guides and tests — the PRO-67
# drift objection is answered by checking those pointers here, not by
# forbidding skills. A cited path that no longer exists is exactly the rot
# PRO-67 feared; this test turns it into a red build.

# A backticked repo path, optionally with a `::symbol` suffix naming a test or
# a module-level name inside it — the form the skills use to point at the test
# that pins a fact. The symbol is checked too: a renamed test that leaves the
# file in place is the same rot as a deleted file.
_PATH_CITATION = re.compile(
    r"`((?:app|admin_panel|tests|docs|scripts|\.claude|\.github)/[\w./-]+"
    r"\.(?:py|md|yml|yaml|json|sh))(?:::(\w+))?`"
)


# Per-machine files the docs must name but git never carries.
_LOCAL_ONLY = {".claude/settings.local.json"}


def _cited_paths(text):
    """``(path, symbol-or-empty)`` pairs, deduplicated, local-only files dropped."""
    return sorted(
        {(p, sym) for p, sym in _PATH_CITATION.findall(text) if p not in _LOCAL_ONLY}
    )


def test_every_skill_has_a_description_and_scoped_globs_that_match():
    skills = sorted(_SKILLS.glob("*/SKILL.md"))
    assert skills, ".claude/skills/ declares no skills"

    offenders = []
    for skill in skills:
        label = f"{skill.parent.name}/SKILL.md"
        fm = _frontmatter(skill)
        if fm is None:
            offenders.append(f"{label}: no frontmatter block")
            continue
        if not re.search(r"^description:\s*\S", fm, re.MULTILINE):
            offenders.append(f"{label}: frontmatter has no description")
        name = re.search(r"^name:\s*(\S+)", fm, re.MULTILINE)
        if name and name.group(1) != skill.parent.name:
            offenders.append(
                f"{label}: name {name.group(1)!r} differs from its directory"
            )
        offenders += [
            f"{label}: `paths` glob {g!r} matches no file in the repo"
            for g in _paths_globs(fm)
            if not _glob_matches_something(g)
        ]
    assert not offenders, "skill frontmatter problems:\n  " + "\n  ".join(offenders)


def test_rules_skills_and_claude_md_cite_only_paths_that_exist():
    """Every backticked repo path in CLAUDE.md, a rule or a skill resolves."""
    files = (
        [_ROOT / "CLAUDE.md"]
        + sorted(_RULES.glob("**/*.md"))
        + sorted(_SKILLS.glob("*/SKILL.md"))
    )
    broken = []
    for path in files:
        for cited, symbol in _cited_paths(path.read_text(encoding="utf-8")):
            target = _ROOT / cited
            if not target.exists():
                broken.append(f"{path.relative_to(_ROOT)}: cites missing {cited}")
                continue
            if symbol and not re.search(
                rf"\b{re.escape(symbol)}\b", target.read_text(encoding="utf-8")
            ):
                broken.append(
                    f"{path.relative_to(_ROOT)}: {cited} has no symbol {symbol}"
                )
    assert not broken, "stale path citations:\n  " + "\n  ".join(broken)


# --- CLAUDE.md size budget ----------------------------------------------------
#
# CLAUDE.md is loaded into every session, so every character in it is paid on
# every turn of every session. It reached 54 000 characters (~15k tokens) before
# the September 2026 split; the budget keeps the split from quietly reversing.
# Reference material belongs in a path-scoped rule or in docs/ — raise this
# number only with a sentence in the commit saying what always-on rule needed
# the room.

CLAUDE_MD_BUDGET_CHARS = 22_000


def test_claude_md_stays_within_its_size_budget():
    size = len((_ROOT / "CLAUDE.md").read_text(encoding="utf-8"))
    assert size <= CLAUDE_MD_BUDGET_CHARS, (
        f"CLAUDE.md is {size} characters, over the {CLAUDE_MD_BUDGET_CHARS} budget. "
        "Move reference material into a path-scoped .claude/rules/*.md or docs/."
    )


# --- Local-only files stay local -------------------------------------------


def test_gitignore_keeps_per_developer_claude_files_out_of_git():
    ignored = (_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    for entry in ("CLAUDE.local.md", "settings.local.json", ".claude/agent-memory/"):
        assert entry in ignored, f".gitignore is missing {entry}"


# --- Git hooks --------------------------------------------------------------
#
# `core.hooksPath = .githooks` is the PR-only workflow's local backstop. Git
# runs a hook only if the file is executable, and warns once, quietly, when
# it is not ("hook was ignored because it's not set as executable") — which
# is how the pre-push guard turned out to be off on every POSIX clone while
# looking fully present in the tree. The executable bit lives in the git
# index (mode 100755), so this checks the index, not the working copy.


def test_git_hooks_carry_the_executable_bit_in_the_index():
    import subprocess

    hooks_dir = _ROOT / ".githooks"
    listed = subprocess.run(
        ["git", "ls-files", "-s", "--", str(hooks_dir)],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    assert listed, ".githooks/ has no tracked files"
    not_executable = [
        line.split()[-1] for line in listed if not line.startswith("100755 ")
    ]
    assert not not_executable, (
        "git ignores a non-executable hook: run "
        "`git update-index --chmod=+x <file>` for " + ", ".join(not_executable)
    )
