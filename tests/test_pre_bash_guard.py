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
