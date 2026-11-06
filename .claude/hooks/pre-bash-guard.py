#!/usr/bin/env python3
"""PreToolUse hook for Bash and PowerShell — blocks dangerous shell commands
and holds each subagent to its grant (PRO-95)."""
import json
import os
import re
import subprocess
import sys

# Branches that never take a direct commit or push.
#   dev        — the integration branch (renamed from master); deploys to staging
#   production — the release branch; deploys to production
#   master/main— kept so the guard still works in clones that predate the rename,
#                and in any other repo this hook is copied into
PROTECTED_BRANCHES = ("main", "master", "dev", "production")


def target_dir(command, default_cwd=None):
    """Directory the git command actually operates on.

    The guard has to read the branch that would *receive* the commit, which is
    not always the one checked out where the session happens to be sitting:
    ``git -C <dir> commit`` and ``cd <dir> && git commit`` both target another
    tree. Once work runs in one worktree per issue (see "Running several issues
    at once" in CLAUDE.md) that stops being an edge case and becomes the normal
    shape, and reading the wrong tree is wrong in both directions — it blocked
    every commit made in a worktree while the main tree sat on ``dev`` (its
    resting state), and it would have waved through a commit aimed at ``dev``
    whenever the main tree happened to be on a feature branch.

    Pure, so it is testable without a repo.
    """
    match = re.search(r"\bgit\s+(?:-c\s+\S+\s+)*-C\s+(\"[^\"]+\"|'[^']+'|\S+)", command)
    if match:
        return match.group(1).strip("\"'")

    match = re.search(r"^\s*cd\s+(\"[^\"]+\"|'[^']+'|\S+)\s*&&", command)
    if match:
        return match.group(1).strip("\"'")

    return default_cwd


# `gh pr create` with no --base opens against the repository's default branch,
# which for this repo is `dev` (CLAUDE.md, "Branches and how a release happens").
DEFAULT_PR_BASE = "dev"


_HEREDOC_OPENER = re.compile(r"<<-?\s*(['\"]?)(\w+)\1")

# `gh pr create` only counts in *command position*: at the start of the line or
# straight after a `;`, `&&`, `||`, `|` or `(`. Anywhere else it is a quoted
# argument — `echo "gh pr create"`, `grep "gh pr create" docs/` — which talks
# about opening a PR rather than opening one.
_PR_MERGE = re.compile(r"(?:^|[;&|(])\s*gh\s+pr\s+(?:create|merge)\b", re.MULTILINE)


def strip_heredocs(command):
    """The command with every heredoc *body* removed; openers stay.

    A command that writes *about* a rule is not that rule being broken. This
    file's own documentation is the proof: the first `python3 - <<'PY'` block
    that edited `CLAUDE.md` to describe the guard below contained the words
    `gh pr create`, and the guard blocked the edit that documented it. Pure.
    """
    kept, delimiter = [], None
    for line in command.split("\n"):
        if delimiter is not None:
            if line.strip() == delimiter:
                delimiter = None
            continue
        kept.append(line)
        match = _HEREDOC_OPENER.search(line)
        if match:
            delimiter = match.group(2)
    return "\n".join(kept)


def merge_base_branch(command):
    """The branch a command would merge into, or ``None`` if it is not one.

    In this repo the merge into ``dev`` is a pull request, so the moment to
    catch is ``gh pr create`` (and ``gh pr merge``, for a clone whose
    permissions allow it) — not ``git merge``, which is the *sync* this guard
    asks for and must never refuse. Pure, so it is testable without a repo.
    """
    command = strip_heredocs(command)
    if not _PR_MERGE.search(command):
        return None

    match = re.search(r"(?:--base|-B)[=\s]+(\"[^\"]+\"|'[^']+'|\S+)", command)
    if match:
        return match.group(1).strip("\"'")
    return DEFAULT_PR_BASE


def _behind_count(base, cwd=None):
    """How many commits ``origin/<base>`` is ahead of HEAD; ``None`` if unknown.

    ``origin/<base>`` is refreshed first, because the whole point is to compare
    against what the base branch holds *now* — a session that fetched an hour
    ago is exactly the one that opens a PR onto commits it has never seen. The
    fetch is paid once, on a command that runs once per branch.

    Every failure returns ``None`` (unknown), which ``evaluate`` treats as
    allow: a guard that blocks the PR-open path because the network is down
    would be worse than the conflicts it prevents.
    """
    try:
        subprocess.run(
            ["git", "fetch", "origin", base],
            capture_output=True,
            text=True,
            timeout=20,
            cwd=cwd or None,
        )
        result = subprocess.run(
            ["git", "rev-list", "--count", f"HEAD..origin/{base}"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=cwd or None,
        )
        if result.returncode != 0:
            return None
        return int(result.stdout.strip())
    except Exception:
        return None


def _current_branch(cwd=None):
    """Best-effort current git branch; empty string if it can't be determined.

    ``cwd`` is the tree to inspect (see :func:`target_dir`). An unreadable or
    missing directory raises and yields ``""``, which ``evaluate`` treats as
    "unknown branch, allow" — the same fail-open this has always had.
    """
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=cwd or None,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def evaluate(command, branch, behind_base=None):
    """Decide whether a shell command is allowed.

    Returns ``(exit_code, message)``: exit_code ``2`` blocks the command (the
    message is printed to stderr), ``0`` allows it. ``branch`` is the current
    git branch name — used only by the protected-branch commit/push guard.
    ``behind_base`` is how many commits the PR's base branch is ahead of HEAD,
    or ``None`` for "not measured / unknown", which allows.
    """
    # Block rm -rf targeting /, ~, $HOME, or bare glob
    if re.search(r"rm\s+-[a-zA-Z]*r[a-zA-Z]*f|rm\s+-[a-zA-Z]*f[a-zA-Z]*r", command):
        targets = re.findall(r"rm\s+(?:-\S+\s+)+(.*)", command)
        target_str = targets[0] if targets else ""
        dangerous = re.search(
            r"(^|\s)(/\s*$|~\s*$|\$HOME\s*$|\*\s*$|/\s+|~\s+|\$HOME\s+|\*\s+)",
            " " + target_str,
        )
        if (
            dangerous
            or re.search(r"rm\s+-[rf]+\s+[/~*]", command)
            or re.search(r"rm\s+-[rf]+\s+\$HOME", command)
        ):
            return (
                2,
                "BLOCKED: rm -rf targeting /, ~, $HOME, or bare glob is not allowed.",
            )

    # Block redirect into .env
    if re.search(r">>?\s*\.env\b", command):
        return 2, "BLOCKED: Redirecting into .env is not allowed."

    # Block git push --force / -f to a protected branch
    if (
        re.search(r"git\s+push\b", command)
        and re.search(r"--force\b|-f\b", command)
        and re.search(r"\b(main|master|dev|production)\b", command)
    ):
        return 2, "BLOCKED: Force-pushing to a protected branch is not allowed."

    # Block plain git commit / git push while on a protected branch. All Proli
    # work happens on a feature branch (see CLAUDE.md and the take-issue
    # guardrails); this closes the gap the force-push rule above leaves open for
    # ordinary commits and non-force pushes.
    #
    # `dev` is the integration branch (renamed from `master`) and `production`
    # is the release branch that Railway deploys — neither takes direct commits.
    # `master`/`main` stay listed so the guard keeps working in clones that have
    # not yet renamed, and for any other repo this hook is copied into.
    if branch in PROTECTED_BRANCHES and re.search(
        r"git\s+(?:-\S+\s+|-c\s+\S+\s+|-C\s+\S+\s+)*(commit|push)\b", command
    ):
        return (
            2,
            f"BLOCKED: refusing 'git commit' / 'git push' while on '{branch}'. "
            "Create a feature branch first — never commit or push to a "
            "protected branch.",
        )

    # Refuse to open a PR from a branch the base has moved past. The merge into
    # `dev` is a pull request, so `gh pr create` is the moment the branch and
    # its base first have to agree — and a branch that has not seen the base
    # since it forked is where conflicts come from, discovered on GitHub by
    # whoever reviews it rather than here, where the tree and the tests are.
    # Syncing first is the whole ask; `git merge` (never a rebase) keeps any
    # checkout of the branch valid.
    base = merge_base_branch(command)
    if base and isinstance(behind_base, int) and behind_base > 0:
        commits = "commit" if behind_base == 1 else "commits"
        # Named when known: with one worktree per issue, "this branch" is not
        # enough to tell the operator which tree the guard read.
        which = f"'{branch}'" if branch else "this branch"
        return (
            2,
            f"BLOCKED: {which} is {behind_base} {commits} behind "
            f"'origin/{base}', so the PR would be opened against a base it has "
            "never seen. Sync first, then re-run this command:\n\n"
            f"    git fetch origin {base} && git merge origin/{base}\n\n"
            "Resolve any conflict here, re-run the suite, push, then open the "
            "PR. Merge rather than rebase — a rebase invalidates every existing "
            "checkout of the branch.",
        )

    # Block mongo/mongosh dropDatabase or drop()
    if re.search(r"\b(mongo|mongosh)\b", command) and re.search(
        r"dropDatabase\s*\(|\.drop\s*\(", command
    ):
        return (
            2,
            "BLOCKED: dropDatabase / drop() via mongo/mongosh is not allowed.",
        )

    return 0, ""


# PRO-95: what each pack agent may run from a shell, deny-by-default. An empty
# tuple means the agent has no business with a shell at all: its `tools:` list
# already omits Bash, and this is the belt for when somebody adds it back.
_READ_ONLY_GIT = (
    r"git\s+(-C\s+\S+\s+)?"
    r"(diff|log|show|status|blame|rev-parse|rev-list|merge-base|ls-files"
    r"|branch\s+--show-current\s*$)(\s|$)"
)
_READ_ONLY_GH = r"gh\s+pr\s+(diff|view|list|checks)(\s|$)"
_PYTEST = (
    r"((\S*/)?venv/(Scripts/python\.exe|bin/python)\s+-m\s+pytest"
    r"|python3?\s+-m\s+pytest|pytest)(\s|$)"
)
_PIPE_FILTER = r"(tail|head|grep)(\s|$)"
_CD = r"cd(\s|$)"
AGENT_BASH_ALLOW = {
    "code-reviewer": (_READ_ONLY_GIT, _READ_ONLY_GH, _PIPE_FILTER, _CD),
    "test-runner": (_PYTEST, _PIPE_FILTER, _CD),
    "docs-syncer": (),
    "flow-tracer": (),
    "test-writer": (),
    "ux-reviewer": (),
}

# A quoted string the shell will not expand: `<`, `|` or `;` inside one is text.
_INERT_QUOTED = re.compile(r"""'[^']*'|"[^"$`]*\"""")

_VALUE = r"""("[^"]*"|'[^']*'|[^\s;&|)'"`]+)"""
_GIT_OPTION = (
    rf"(?:-c\s*{_VALUE}\s+|-C\s+{_VALUE}\s+"
    rf"|--(?:git-dir|work-tree|namespace|exec-path|config-env)[=\s]\s*{_VALUE}\s+"
    r"|-\S+\s+)"
)
# Anywhere in the text, not just in command position: `env git stash`,
# `FOO=1 git stash`, `sh -c "git stash"`, `{ git stash; }` and backticks all run
# it. That makes `echo "git stash"` a false positive, which is the cheap side of
# the trade. `git worktree add` stays out: it is the sanctioned way to isolate.
_MUTATING_GIT = re.compile(
    r"""(?<![\w.-])git(?:\.exe)?["']?\s+"""
    rf"(?:{_GIT_OPTION})*"
    r"""["']?(?:(?:stash|checkout-index|checkout|switch|reset|restore|clean|rebase"""
    r"""|merge-file|merge|pull|cherry-pick|revert|am|apply|commit|add|rm|mv"""
    r"""|read-tree|update-index|filter-branch|bisect|sparse-checkout|submodule)"""
    r"""(?![\w-])|worktree\s+(?:remove|move|prune)(?![\w-])|[$`])"""
    r"""|(?<![\w.-])git(?:\.exe)?["']?\s+.*-c\s*["']?alias\."""
)

# Everything that can point a git command somewhere other than the session's
# directory. Each match must resolve, or the command is refused.
_TARGET_PATTERNS = (
    r"(?<![\w-])(?:cd|chdir|pushd|sl|Set-Location|Push-Location)\s+(?:-\w+\s+)?"
    + _VALUE,
    r"(?<=\s)-C\s+" + _VALUE,
    r"--(?:git-dir|work-tree)[=\s]\s*" + _VALUE,
    r"GIT_(?:DIR|WORK_TREE)\s*=\s*" + _VALUE,
)
_BARE_CD = re.compile(r"(?<![\w-])cd\s*(?:[;&|)\n]|$)", re.MULTILINE)


def _agent_text(command):
    """``command`` without heredoc or PowerShell here-string bodies. Pure."""
    command = re.sub(r"@(['\"])\r?\n.*?\r?\n\1@", "", command, flags=re.DOTALL)
    return strip_heredocs(command)


def git_targets(text, cwd):
    """Every directory ``text`` may run git against, unquoted. Pure.

    ``""`` stands for a bare ``cd``, which goes somewhere this cannot name. The
    session's ``cwd`` when nothing redirects it.
    """
    found = [
        match.group(1).strip("\"'")
        for pattern in _TARGET_PATTERNS
        for match in re.finditer(pattern, text)
    ]
    if _BARE_CD.search(text):
        found.append("")
    return found or [cwd]


def agent_block(command, agent_type, in_shared_tree):
    """Why a subagent may not run ``command``, or ``""`` if it may. Pure.

    The 2026-08-09 incident (PRO-95, ``docs/AGENT_PACK.md``): docs-syncer tried
    ``railway variable delete`` on a request nobody made, and ran ``git stash``
    over a session's uncommitted work. ``agent_type`` is the hook input's field
    for that, absent outside a subagent. ``in_shared_tree`` is False only when
    the command targets the subagent's own worktree or a clone.
    """
    if not agent_type:
        return ""
    text = _agent_text(command)

    allowed = AGENT_BASH_ALLOW.get(agent_type)
    if allowed is not None:
        # No substitution, no redirect: either could hide anything behind an
        # allowed first word. `git diff --output=` is a write spelled as a flag,
        # checked before quotes go so quoting it changes nothing. An escaped
        # quote would make the quote-stripping below misread what the shell sees.
        plain = _INERT_QUOTED.sub('""', text.replace("2>&1", ""))
        segments = [s.strip() for s in re.split(r"&&|\|\||[;|&\n]", plain)]
        if (
            not re.search(r"[$`<>]", plain)
            and not re.search(r"--output|\\[\"']", text)
            and allowed
            and all(any(re.match(p, s) for p in allowed) for s in segments if s)
        ):
            return ""
        return (
            f"BLOCKED: the {agent_type} agent may not run this. Its shell grant is "
            "in AGENT_BASH_ALLOW (.claude/hooks/pre-bash-guard.py). If the task "
            "needs more, report that to the caller instead of working around it."
        )

    if in_shared_tree and _MUTATING_GIT.search(text):
        return (
            "BLOCKED: a subagent may not run a mutating git command on the session's "
            "working tree, or on a target this guard cannot resolve (PRO-95). If you "
            "need isolation, use a worktree or clone, named by a literal path."
        )
    return ""


def _native_path(path):
    """Git Bash's ``/d/x`` as ``D:/x``: native Windows Python cannot open the former."""
    match = re.match(r"^/([a-zA-Z])(/.*)?$", path) if os.name == "nt" else None
    return f"{match.group(1).upper()}:{match.group(2) or '/'}" if match else path


def _git_dir(directory):
    """Absolute, forward-slash git dir of ``directory``; ``None`` if unresolvable."""
    return _rev_parse_path(directory, "--absolute-git-dir")


def _git_common_dir(directory):
    """The git dir of the main checkout ``directory`` belongs to; ``None`` likewise."""
    return _rev_parse_path(directory, "--path-format=absolute", "--git-common-dir")


def _rev_parse_path(directory, *args):
    try:
        result = subprocess.run(
            ["git", "rev-parse", *args],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=directory or None,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return os.path.normcase(result.stdout.strip()).replace("\\", "/")


def _in_shared_tree(target, session_cwd, agent_id, project_dir):
    """Whether ``target`` is anything but this subagent's own isolation.

    Observed from real hook input (`claude -p`, 2.1.318), not assumed:
    ``CLAUDE_PROJECT_DIR`` is where the session started, even if that is a linked
    worktree, and stays put inside an `isolation: worktree` subagent. That
    subagent's ``cwd`` is ``.claude/worktrees/agent-<agent_id>``; a plain subagent's
    ``cwd`` is its parent's. So its own tree is recognised by name, which is the
    only thing telling it apart from a parent that entered a worktree mid-session.
    The name is undocumented: if it ever changes, isolated subagents get refused,
    loudly, rather than anyone's work being stashed quietly.

    An unresolvable ``target`` counts as shared. Fail-open is right for the other
    guards here; for this one it is how ``cd "$PWD" && git stash`` got through.
    """
    if not target:
        return True
    resolved = os.path.join(session_cwd or "", _native_path(os.path.expanduser(target)))
    git_dir = _git_dir(resolved)
    if git_dir is None:
        return True
    name = git_dir.rsplit("/", 1)[-1]
    if agent_id and name == os.path.normcase(f"agent-{agent_id}"):
        return False
    if name.startswith("agent-"):
        return True  # another subagent's isolation
    # A session started in a linked worktree has the main checkout as neither of
    # those, so it is named too. A clone has its own common dir and stays allowed.
    # shortcut: a sibling worktree of another session reads the same as one this
    # subagent made with `git worktree add`, unless it has an `agent-` name.
    return git_dir in (
        _git_dir(session_cwd),
        _git_dir(project_dir),
        _git_common_dir(session_cwd),
    )


def _touches_shared_tree(command, data):
    """Whether a non-pack subagent's mutating ``command`` reaches a shared tree."""
    agent_type = data.get("agent_type")
    if not agent_type or agent_type in AGENT_BASH_ALLOW:
        return False
    text = _agent_text(command)
    if not _MUTATING_GIT.search(text):  # the subprocesses are paid for only here
        return False
    session_cwd = data.get("cwd")
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    return any(
        _in_shared_tree(target, session_cwd, data.get("agent_id"), project_dir)
        for target in git_targets(text, session_cwd)
    )


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    command = data.get("tool_input", {}).get("command", "")
    if not command:
        sys.exit(0)

    refusal = agent_block(
        command, data.get("agent_type"), _touches_shared_tree(command, data)
    )
    if refusal:
        print(refusal, file=sys.stderr)
        sys.exit(2)

    cwd = target_dir(command, data.get("cwd"))
    branch = _current_branch(cwd)

    # Measured only for the command it gates. `_behind_count` fetches, and
    # paying that on every Bash call would put a network round trip in front of
    # `ls`; `gh pr create` runs about once per branch.
    base = merge_base_branch(command)
    behind_base = _behind_count(base, cwd) if base else None

    exit_code, message = evaluate(command, branch, behind_base)
    if exit_code != 0:
        print(message, file=sys.stderr)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
