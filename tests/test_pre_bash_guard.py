"""Guard tests for the Bash PreToolUse hook (PRO-76 item 5).

``.claude/hooks/pre-bash-guard.py`` blocks dangerous shell commands. This
suite pins the decision logic — especially the protected-branch guard added in
PRO-76: plain ``git commit`` / ``git push`` must be blocked while on
``main``/``master`` and allowed on any feature branch.

The hook filename is hyphenated (not importable as a normal module), so it is
loaded by path via ``importlib``. Only the pure ``evaluate(command, branch)``
decision function is exercised — it takes the branch as a parameter, so no real
git repo or subprocess is involved.
"""

import importlib.util
from pathlib import Path

_HOOK_PATH = (
    Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "pre-bash-guard.py"
)


def _load_guard():
    spec = importlib.util.spec_from_file_location("pre_bash_guard", _HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = _load_guard()


# --- Protected-branch commit/push guard (the PRO-76 addition) ----------------
#
# One rule, four branch names, two verbs. Looped inside one test rather than
# split across eight: the rule is "branch is protected AND the verb mutates", so
# eight test names proved one thing eight times, and adding a protected branch
# meant hand-writing two more. The loop's assertion messages name the case.


def test_blocks_commit_and_push_on_every_protected_branch():
    for branch in ("main", "master", "dev", "production"):
        for verb in ("commit -m 'x'", "push origin HEAD"):
            code, msg = guard.evaluate(f"git {verb}", branch)
            assert code == 2, f"git {verb} was allowed on {branch}"
            assert branch in msg, f"the message for {branch} does not name it"


def test_force_push_to_a_protected_branch_is_blocked_from_anywhere():
    """Judged by the *target* of the push, not the branch you are standing on."""
    for target in ("main", "dev", "production"):
        code, msg = guard.evaluate(f"git push --force origin {target}", "feature/x")
        assert code == 2, f"force-push to {target} was allowed"
        assert "Force-pushing" in msg


def test_allows_commit_and_push_on_a_feature_branch():
    assert guard.evaluate("git commit -m 'x'", "feature/pro-76") == (0, "")
    assert guard.evaluate("git push origin feature/pro-76", "feature/pro-76") == (0, "")


def test_leading_global_options_do_not_bypass_the_guard():
    """`git -c …` / `git -C …` push the verb into a later argv position."""
    assert guard.evaluate("git -c user.email=x commit -m y", "main")[0] == 2
    assert guard.evaluate("git -C . push origin master", "master")[0] == 2


def test_allows_commit_on_empty_branch():
    """Detached HEAD / unknown branch (empty string) must not block."""
    assert guard.evaluate("git commit -m 'x'", "") == (0, "")


def test_allows_non_mutating_git_on_a_protected_branch():
    """Only commit/push are gated — status/diff/log stay allowed."""
    assert guard.evaluate("git status", "main") == (0, "")
    assert guard.evaluate("git diff HEAD", "main") == (0, "")
    assert guard.evaluate("git log -1", "master") == (0, "")


# --- The hook's other rules ---------------------------------------------------


def test_the_non_branch_rules_still_block_and_still_allow():
    """Each of these is an independent one-liner in the hook with no shared
    state, so one test per rule bought nothing a grouped assert does not — the
    assertion that fails still names the rule."""
    assert guard.evaluate("rm -rf " + "/", "feature/x")[0] == 2
    assert guard.evaluate("echo secret >> .env", "feature/x")[0] == 2
    assert guard.evaluate("mongosh --eval 'db.leads.drop()'", "feature/x")[0] == 2
    assert guard.evaluate("ls -la", "main") == (0, "")
    assert guard.evaluate("pytest -q", "feature/x") == (0, "")


# --- Which tree the guard reads (worktree-aware branch resolution) -----------
#
# ``target_dir`` decides *where* the branch is read from. Before it existed the
# hook always read the session's own directory, which is wrong in both
# directions once work runs in one worktree per issue: it blocked every commit
# made in a worktree while the main tree sat on ``dev`` (its resting state),
# and it would have waved through a commit aimed at ``dev`` whenever the main
# tree happened to be on a feature branch. See "Running several issues at once"
# in CLAUDE.md.
#
# These nine stay one-per-case on purpose: each pins a distinct parsing rule
# that was a real false positive or false negative, and the argument shapes are
# not a table — they differ from each other, not by a parameter.


def test_target_dir_defaults_to_the_session_cwd():
    assert guard.target_dir("git status", "/d/Projects/proli") == "/d/Projects/proli"


def test_target_dir_follows_dash_c():
    assert (
        guard.target_dir("git -C /d/Projects/proli-wt/pro-162 status", "/any")
        == "/d/Projects/proli-wt/pro-162"
    )


def test_target_dir_follows_dash_c_after_global_options():
    # `git -c user.email=x -C <dir> …` — the config option must not shadow -C.
    assert (
        guard.target_dir("git -c user.email=a@b -C /wt/pro-141 status", "/any")
        == "/wt/pro-141"
    )


def test_target_dir_follows_dash_c_with_quoted_path():
    assert (
        guard.target_dir('git -C "D:/Projects/proli-wt/pro 162" status', "/any")
        == "D:/Projects/proli-wt/pro 162"
    )


def test_target_dir_follows_a_leading_cd():
    assert (
        guard.target_dir("cd /d/Projects/proli-wt/pro-163 && git status", "/any")
        == "/d/Projects/proli-wt/pro-163"
    )


def test_target_dir_ignores_a_cd_that_is_not_leading():
    # Only a leading `cd X &&` redirects the guard; a cd buried later in the
    # line is not reliably the tree the git command runs in.
    assert guard.target_dir("git status && cd /elsewhere", "/session") == "/session"


def test_target_dir_without_a_cwd_is_none():
    # No -C, no leading cd, no cwd in the payload — subprocess then falls back
    # to the hook process's own directory, which is the pre-existing behaviour.
    assert guard.target_dir("git status", None) is None


def test_targeting_a_protected_tree_is_still_blocked():
    # The false-negative direction: the resolved branch is what `evaluate`
    # judges, so targeting a tree that is on `dev` stays blocked no matter
    # where the session itself is sitting.
    code, msg = guard.evaluate("git -C /d/Projects/proli push origin HEAD", "dev")
    assert code == 2
    assert "dev" in msg


def test_pushing_from_a_feature_worktree_is_allowed():
    # The false-positive direction that made parallel delivery unusable.
    assert guard.evaluate(
        "git -C /d/Projects/proli-wt/pro-162 push -u origin HEAD", "chore/parallel"
    ) == (0, "")


# --- Sync before the merge: the PR-open guard --------------------------------
#
# The merge into `dev` is a pull request, so `gh pr create` is where the branch
# and its base first have to agree. `merge_base_branch` decides *whether* a
# command is that moment and *which* base it names; `evaluate` decides on the
# measured distance. The two are split so neither needs a repo: the fetch and
# the rev-list live in `_behind_count`, which is the impure half.


def test_merge_base_branch_defaults_to_dev():
    """`gh pr create` with no --base opens against the repo's default branch."""
    assert guard.merge_base_branch("gh pr create --fill") == "dev"
    assert guard.merge_base_branch("gh pr merge 181 --squash") == "dev"


def test_merge_base_branch_reads_an_explicit_base_in_every_spelling():
    for flag in ("--base dev", "--base=dev", "-B dev", "-B=dev"):
        assert (
            guard.merge_base_branch(f"gh pr create {flag} --fill") == "dev"
        ), f"{flag} was not read"


def test_merge_base_branch_reads_a_base_that_is_not_dev():
    """The rule is about the base being ahead, whichever base that is."""
    assert guard.merge_base_branch("gh pr create --base production") == "production"
    assert (
        guard.merge_base_branch('gh pr create --base "feature/epic" --fill')
        == "feature/epic"
    )


def test_merge_base_branch_ignores_everything_that_is_not_a_merge():
    # `git merge` is the *sync* this guard asks for — gating it would refuse
    # the fix in its own message.
    for command in (
        "git merge origin/dev",
        "gh pr view 181",
        "gh pr list --state open",
        "gh pr checks",
        "git push -u origin feature/x",
        "pytest -q",
    ):
        assert guard.merge_base_branch(command) is None, f"{command} read as a merge"


def test_opening_a_pr_from_a_branch_the_base_has_moved_past_is_blocked():
    code, msg = guard.evaluate("gh pr create --fill", "feature/x", behind_base=7)
    assert code == 2
    assert "7 commits behind" in msg
    assert "git fetch origin dev && git merge origin/dev" in msg
    # With one worktree per issue, "this branch" does not say which tree the
    # guard read.
    assert "feature/x" in msg


def test_the_block_says_this_branch_when_the_branch_is_unknown():
    _, msg = guard.evaluate("gh pr create --fill", "", behind_base=3)
    assert "this branch is 3 commits behind" in msg


# A command that *writes about* the rule is not the rule being broken. Both
# cases below were found by the guard firing on the commit that added it: the
# heredoc that edited CLAUDE.md to document this rule contained the words
# `gh pr create`, and got blocked.


def test_a_heredoc_body_that_mentions_opening_a_pr_is_not_opening_one():
    # The body line *starts* with the words, which is the case the
    # command-position rule alone cannot tell from a real invocation — writing
    # a runbook or a fenced example into a doc looks exactly like this.
    command = "cat <<'EOF' > docs/runbook.md\nThen run:\ngh pr create --fill\nEOF"
    assert guard.merge_base_branch(command) is None
    assert guard.evaluate(command, "feature/x", behind_base=9) == (0, "")


def test_a_real_command_after_a_heredoc_still_counts():
    """Stripping the body must not swallow what follows the delimiter."""
    command = "cat <<'EOF' > notes.md\nsome notes\nEOF\ngh pr create --fill"
    assert guard.merge_base_branch(command) == "dev"


def test_the_words_only_count_in_command_position():
    for quoted in (
        'echo "gh pr create --fill"',
        "grep -rn 'gh pr create' .claude/",
        "git commit -m 'document gh pr create in the guard'",
    ):
        assert guard.merge_base_branch(quoted) is None, f"{quoted} read as a merge"


def test_command_position_survives_a_shell_separator():
    for command in (
        "pytest -q && gh pr create --fill",
        "git push -u origin HEAD; gh pr create --fill",
        "cd /wt/pro-162 && gh pr create --fill",
    ):
        assert guard.merge_base_branch(command) == "dev", f"{command} was missed"


def test_the_block_message_names_the_actual_base():
    code, msg = guard.evaluate(
        "gh pr create --base production", "feature/x", behind_base=2
    )
    assert code == 2
    assert "origin/production" in msg
    assert "git fetch origin production && git merge origin/production" in msg


def test_one_commit_behind_is_still_one_commit_behind():
    """Singular, because a message that says '1 commits' reads as a bug in the
    guard and gets ignored — and one commit is the common case."""
    _, msg = guard.evaluate("gh pr create --fill", "feature/x", behind_base=1)
    assert "1 commit behind" in msg
    assert "commits" not in msg.split("behind")[0]


def test_a_branch_level_with_its_base_opens_its_pr():
    assert guard.evaluate("gh pr create --fill", "feature/x", behind_base=0) == (0, "")


def test_an_unmeasurable_distance_fails_open():
    """`_behind_count` returns None for a missing remote, a dead network or a
    directory that is not a repo. Blocking the PR-open path because the network
    is down would be worse than the conflicts this prevents."""
    assert guard.evaluate("gh pr create --fill", "feature/x", behind_base=None) == (
        0,
        "",
    )
    assert guard.evaluate("gh pr create --fill", "feature/x") == (0, "")


def test_a_stale_branch_does_not_change_any_other_verdict():
    """`behind_base` gates one rule. A command that was allowed stays allowed,
    and one that was blocked keeps the message that explains why."""
    assert guard.evaluate("git status", "feature/x", behind_base=9) == (0, "")
    code, msg = guard.evaluate("git commit -m x", "dev", behind_base=9)
    assert code == 2 and "dev" in msg
