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


def test_blocks_commit_on_main():
    code, msg = guard.evaluate("git commit -m 'x'", "main")
    assert code == 2
    assert "main" in msg


def test_blocks_commit_on_master():
    code, msg = guard.evaluate("git commit -m 'x'", "master")
    assert code == 2
    assert "master" in msg


def test_blocks_push_on_master():
    code, _ = guard.evaluate("git push origin master", "master")
    assert code == 2


def test_allows_commit_on_feature_branch():
    assert guard.evaluate("git commit -m 'x'", "feature/pro-76") == (0, "")


def test_allows_push_on_feature_branch():
    assert guard.evaluate("git push origin feature/pro-76", "feature/pro-76") == (0, "")


def test_blocks_commit_with_global_option_on_main():
    """Leading global options (`git -c ...`, `git -C ...`) must not bypass the guard."""
    assert guard.evaluate("git -c user.email=x commit -m y", "main")[0] == 2
    assert guard.evaluate("git -C . push origin master", "master")[0] == 2


def test_allows_commit_on_empty_branch():
    """Detached HEAD / unknown branch (empty string) must not block."""
    assert guard.evaluate("git commit -m 'x'", "") == (0, "")


def test_allows_non_mutating_git_on_main():
    """Only commit/push are gated — status/diff/log stay allowed on main."""
    assert guard.evaluate("git status", "main") == (0, "")
    assert guard.evaluate("git diff HEAD", "main") == (0, "")
    assert guard.evaluate("git log -1", "master") == (0, "")


# --- Regression: existing rules stay intact after the refactor ---------------


def test_force_push_to_main_still_blocked():
    code, msg = guard.evaluate("git push --force origin main", "feature/x")
    assert code == 2
    assert "Force-pushing" in msg


def test_rm_rf_root_still_blocked():
    assert guard.evaluate("rm -rf /", "feature/x")[0] == 2


def test_env_redirect_still_blocked():
    assert guard.evaluate("echo secret >> .env", "feature/x")[0] == 2


def test_mongo_drop_still_blocked():
    assert guard.evaluate("mongosh --eval 'db.leads.drop()'", "feature/x")[0] == 2


def test_harmless_command_allowed():
    assert guard.evaluate("ls -la", "main") == (0, "")
    assert guard.evaluate("pytest -q", "feature/x") == (0, "")
