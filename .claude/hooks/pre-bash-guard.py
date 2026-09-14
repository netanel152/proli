#!/usr/bin/env python3
"""PreToolUse hook for Bash — blocks dangerous shell commands."""
import json
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


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    command = data.get("tool_input", {}).get("command", "")
    if not command:
        sys.exit(0)

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
