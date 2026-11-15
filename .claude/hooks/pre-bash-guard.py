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
# It may not cross a line: a quote opened on a comment line closes nothing, and
# that is how `git log # '\ntouch p\n# '` ran a second command past the allowlist.
_INERT_QUOTED = re.compile(r"""'[^'\r\n]*'|"[^"$`\\\r\n]*\"""")
_ANY_QUOTED = re.compile(r"""'[^']*'|"(?:[^"\\]|\\.)*\"""", re.DOTALL)

_VALUE = r"""("[^"]*"|'[^']*'|[^\s;&|)'"`]+)"""
# The alternatives must not overlap: when two can match the same option, the
# backtracking doubles per option, and 26 of them outran the hook's 10s timeout.
_VALUED_FLAG = r"--(?:git-dir|work-tree|namespace|exec-path|config-env)"
_GIT_OPTION = (
    rf"(?:-[cC]\s*{_VALUE}\s+"
    rf"|{_VALUED_FLAG}[=\s]\s*{_VALUE}\s+"
    rf"|(?!-[cC]|{_VALUED_FLAG}[=\s])-\S+\s+)"
)
_GIT_HEAD = rf"""(?<![\w.-])git(?:\.exe)?["']?\s+(?:{_GIT_OPTION})*["']?"""
_WORKTREE_WIDE = r"""worktree["']?\s+(?:-\S+\s+)*["']?(?:remove|move|prune)(?![\w-])"""
# Anywhere in the text, not just in command position: `env git stash`,
# `FOO=1 git stash`, `sh -c "git stash"`, `{ git stash; }` and backticks all run
# it. That makes `echo "git stash"` a false positive, which is the cheap side of
# the trade. `git worktree add` stays out: it is the sanctioned way to isolate.
_MUTATING_GIT = re.compile(
    _GIT_HEAD + r"(?:(?:checkout-index|checkout|switch|reset|restore|clean|rebase"
    r"|merge-file|merge|pull|cherry-pick|revert|am|apply|commit|add|rm|mv"
    r"|read-tree|update-index|filter-branch|bisect|sparse-checkout|submodule)"
    r"(?![\w-])"
    r"|stash(?![\w-])(?!\s+(?:list|show)(?![\w-]))"
    rf"|{_WORKTREE_WIDE}"
    r"|[$`])"
    r"""|(?<![\w.-])git(?:\.exe)?["']?\s+.*-c\s*["']?alias\.""",
    re.IGNORECASE,
)
# The stash stack and the worktree list belong to the repo, not to a tree, so a
# subagent's own worktree is no protection: these reach other sessions' work.
_REPO_WIDE_GIT = re.compile(
    _GIT_HEAD + rf"(?:{_WORKTREE_WIDE}"
    r"""|stash["']?\s+(?:-\S+\s+)*["']?(?:clear|drop|pop|branch)(?![\w-]))""",
    re.IGNORECASE,
)
# A plain search names git commands without running them. `rg --pre` runs one.
_PLAIN_SEARCH = re.compile(r"\s*(?:grep|rg)\s(?!.*(?:--pre|[$`]))", re.DOTALL)

_CD_VERB = r"(?:cd|chdir|pushd|sl|Set-Location|Push-Location)"
# `cd..` is a real PowerShell function. It has no named target, so it reads as
# unresolvable and refuses.
_CD_SEGMENT = re.compile(
    rf"\s*(?:cd\.\.[\\/]*|{_CD_VERB}(?:\s+(?:-\w+\s+)?{_VALUE})?)\s*$", re.IGNORECASE
)
_CD_MENTION = re.compile(rf"(?<![\w-]){_CD_VERB}\s+(?:-\w+\s+)?{_VALUE}", re.IGNORECASE)
_BARE_CD = re.compile(
    r"(?<![\w-])cd(?:\.\.[\\/]*)?\s*(?:[;&|)\n]|$)", re.MULTILINE | re.IGNORECASE
)
_GIT_C = re.compile(rf"\s-C\s+{_VALUE}")
_GIT_DIR_FLAG = re.compile(rf"--git-dir[=\s]\s*{_VALUE}")
_WORK_TREE_FLAG = re.compile(rf"--work-tree[=\s]\s*{_VALUE}")
_GIT_ENV = re.compile(rf"GIT_(?:DIR|WORK_TREE)\s*=\s*{_VALUE}")
# Where a `cd` may not carry to the next command, or may not happen at all.
_CONTROL_FLOW = re.compile(
    r"[()`|{}]|(?<![&>])&(?!&)"
    r"|\b(?:if|then|else|elif|do|while|until|for|case|popd|pushd|Pop-Location"
    r"|Push-Location)\b",
    re.IGNORECASE,
)
_HEREDOC_START = re.compile(r"""<<(-?)\s*(?:'(\w+)'|"(\w+)"|\\?(\w+))""")


def _quoted_end(text, start):
    """The index just past the quoted string opening at ``start``."""
    quote = text[start]
    i = start + 1
    while i < len(text):
        if quote == '"' and text[i] == "\\":
            i += 2
        elif text[i] == quote:
            return i + 1
        else:
            i += 1
    return len(text)


def _agent_text(command):
    """``command`` with continuations joined; comments and heredoc bodies gone. Pure.

    A scanner, because a line-based match hid real commands: a `<<X` in a comment
    (`# <<X`), a quote (`echo '<<X'`), a here-string (`<<<x`) or arithmetic
    (`$((1<<2))`) opens no heredoc, and the lines after it still run. Comments go
    because ``_segments`` knows nothing of them: the apostrophe in `grep x f # it's`
    would merge the next line into that segment. Main-session guards keep
    ``strip_heredocs``.
    """
    text = re.sub(r"\\\r?\n", "", command)
    out, pending, i = [], [], 0
    while i < len(text):
        char = text[i]
        end = i + 1
        if char in "'\"":
            end = _quoted_end(text, i)
        elif char == "\\":
            end = i + 2
        elif char == "#" and (i == 0 or text[i - 1] in " \t\n;&|("):
            newline = text.find("\n", i)
            i = len(text) if newline < 0 else newline
            continue
        elif text.startswith(("$((", "(("), i):
            close = text.find("))", i)
            end = len(text) if close < 0 else close + 2
        elif text.startswith("<<", i):
            match = _HEREDOC_START.match(text, i)
            if match:
                pending.append((match.group(1), next(filter(None, match.groups()[1:]))))
                end = match.end()
            else:
                end = i + 2
        elif char == "\n" and pending:
            out.append("\n")
            i = _past_heredoc_bodies(text, i + 1, pending)
            pending = []
            continue
        out.append(text[i:end])
        i = end
    return "".join(out)


def _past_heredoc_bodies(text, start, pending):
    """The index after the bodies of ``pending`` heredocs that begin at ``start``."""
    i = start
    for dash, delimiter in pending:
        while i < len(text):
            newline = text.find("\n", i)
            end = len(text) if newline < 0 else newline
            line = text[i:end].rstrip("\r")
            i = end + 1
            if (line.lstrip("\t") if dash else line) == delimiter:
                break
    return min(i, len(text))


def _segments(text):
    """``text`` split into simple commands at unquoted separators. Pure."""
    parts, start, i = [], 0, 0
    while i < len(text):
        char = text[i]
        if char in "'\"":
            i = _quoted_end(text, i)
            continue
        if char == "\\":
            i += 2
            continue
        if char in ";&|()`\n":
            parts.append(text[start:i])
            start = i + 1
        i += 1
    parts.append(text[start:])
    return [part for part in parts if part.strip()]


def _mutating_segments(text):
    return [
        segment
        for segment in _segments(text)
        if _MUTATING_GIT.search(segment) and not _PLAIN_SEARCH.match(segment)
    ]


def _unquote(value):
    return value.strip("\"'")


def _resolve(base, value):
    """``value`` from ``base``. ``""`` means somewhere this cannot name."""
    if not value or base == "":
        return ""
    path = _native_path(os.path.expanduser(_unquote(value)))
    return os.path.join(base, path) if base else path


def git_targets(text, cwd):
    """Every directory the mutating git commands in ``text`` may act on. Pure.

    ``cwd`` stays in unless a command is explicitly redirected: a mention of a path
    is not a redirect (`git -C ../clone status; git stash`, `echo cd ../clone; ...`).
    Only `cd X` in command position moves later commands, and only in a plain
    sequence; a subshell, pipe, branch or `pushd` keeps ``cwd`` and adds X. `--git-dir`
    alone still acts on the current work tree, so it adds rather than replaces.
    """
    masked = _ANY_QUOTED.sub('""', text)
    uncertain = bool(_CONTROL_FLOW.search(masked))
    current, targets = cwd, []
    if uncertain:
        targets.append(cwd)
        targets += [_resolve(cwd, m.group(1)) for m in _CD_MENTION.finditer(text)]
        if _BARE_CD.search(masked):
            targets.append("")
    environment = [m.group(1) for m in _GIT_ENV.finditer(text)]

    for segment in _segments(text):
        change = _CD_SEGMENT.match(segment)
        if change and not uncertain:
            current = _resolve(current, change.group(1))
            continue
        match = _MUTATING_GIT.search(segment)
        if not match or _PLAIN_SEARCH.match(segment):
            continue
        head, prefix = segment[: match.end()], segment[: match.start()]
        directory = _GIT_C.search(head)
        git_dir = _GIT_DIR_FLAG.search(head)
        work_tree = _WORK_TREE_FLAG.search(head)
        named = [m.group(1) for m in (directory, git_dir, work_tree) if m]
        targets += [_resolve(current, value) for value in named]
        if not directory and not (git_dir and work_tree):
            targets.append(current)
        # Wrapped in a quoted `sh -c "cd X; git stash"`: nothing here can follow it.
        if re.search(r"[\"']", prefix):
            targets += [
                _resolve(cwd, m.group(1)) for m in _CD_MENTION.finditer(segment)
            ]
            targets += [_resolve(cwd, m.group(1)) for m in _GIT_C.finditer(segment)]

    if environment:
        targets.append(current)
        targets += [_resolve(cwd, value) for value in environment]
    return list(dict.fromkeys(targets)) or [cwd]


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

    allowed = AGENT_BASH_ALLOW.get(agent_type)
    if allowed is not None:
        # Judged on the raw command, shared by code-reviewer and test-runner: only
        # whole quoted strings on one line are blanked, then anything that could
        # end, hide or redirect a command is refused. A real heredoc needs `<`, and
        # a line continuation or an escaped quote needs a backslash. `--output` is a
        # write spelled as a flag, searched with quotes gone so `--out''put` counts,
        # and brace expansion would spell it too (`--outp{u,u}t`); `HEAD@{1}` is fine.
        raw = command.replace("2>&1", "")
        plain = _INERT_QUOTED.sub("Q", raw)
        segments = [s.strip() for s in re.split(r"&&|\|\||[;|&\n]", plain)]
        if (
            not re.search(r"""[$`<>#\\\r'"]|\{[^}]*(?:,|\.\.)""", plain)
            and "--output" not in re.sub(r"""["'\\]""", "", raw)
            and allowed
            and all(any(re.match(p, s) for p in allowed) for s in segments if s)
        ):
            return ""
        return (
            f"BLOCKED: the {agent_type} agent may not run this. Its shell grant is "
            "in AGENT_BASH_ALLOW (.claude/hooks/pre-bash-guard.py). If the task "
            "needs more, report that to the caller instead of working around it."
        )

    mutating = _mutating_segments(_agent_text(command))
    if any(_REPO_WIDE_GIT.search(segment) for segment in mutating):
        return (
            "BLOCKED: a subagent may not remove, move or prune a worktree, or clear, "
            "drop, pop or branch a stash, from any tree: the worktree list and the "
            "stash stack are shared across the repo (PRO-95). If this is a commit "
            "message that only mentions the command, use `git commit -F <file>`."
        )
    if in_shared_tree and mutating:
        return (
            "BLOCKED: a subagent may not run a mutating git command on the session's "
            "working tree, or on a target this guard cannot resolve (PRO-95). If you "
            "need isolation, use a worktree or clone, named by a literal path. If this "
            "is a commit message that only mentions the command, use "
            "`git commit -F <file>`."
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
    if not _mutating_segments(text):  # the subprocesses are paid for only here
        return False
    session_cwd = data.get("cwd")
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    return any(
        _in_shared_tree(target, session_cwd, data.get("agent_id"), project_dir)
        for target in git_targets(text, session_cwd)
    )


def _leaves_the_repo(command, data):
    """Whether a pack agent's `cd` or `git -C` reaches a different repository.

    Another repo's config can set `diff.external`, so a plain `git diff` there runs
    its helper. A target must share the session's git common dir; anything that
    cannot be resolved counts as leaving. Quoted separators only split a value into
    junk that then fails to resolve, which fails closed.
    """
    session_cwd = data.get("cwd")
    current, targets = session_cwd, []
    for segment in re.split(r"&&|\|\||[;|&\n]", command):
        change = re.match(rf"\s*cd(?:\s+{_VALUE})?\s*$", segment)
        if change:
            current = _resolve(current, change.group(1))
            targets.append(current)
        elif re.match(r"\s*git\s", segment):
            targets += [_resolve(current, m.group(1)) for m in _GIT_C.finditer(segment)]
    if not targets:
        return False
    home = _git_common_dir(session_cwd)
    return not home or any(
        not target or _git_common_dir(target) != home for target in targets
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
    if AGENT_BASH_ALLOW.get(data.get("agent_type")) and _leaves_the_repo(command, data):
        print(
            f"BLOCKED: the {data['agent_type']} agent may not `cd` or `git -C` out of "
            "this repository: another repo's config can run a helper (PRO-95). Report "
            "that to the caller instead of working around it.",
            file=sys.stderr,
        )
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
