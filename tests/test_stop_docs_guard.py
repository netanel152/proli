"""Decision-logic tests for the Stop hook (``.claude/hooks/stop-docs-guard.py``).

The hook refuses — once — to end a turn that changed source the docs describe
without touching any ``.md``. Only the pure ``evaluate(dirty_paths,
stop_hook_active)`` function is exercised: it takes the porcelain paths as a
list, so no git repo or subprocess is involved, and the loop-guard
(``stop_hook_active``) is a plain parameter.

Loaded by path via ``importlib`` like ``tests/test_pre_bash_guard.py`` — the
hyphenated filename is not importable as a module.
"""

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

_HOOK_PATH = (
    Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "stop-docs-guard.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("stop_docs_guard", _HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = _load()


# --- The one rule, from both sides -------------------------------------------


def test_blocks_when_documented_source_changed_and_no_markdown_did():
    code, message = guard.evaluate(["app/services/pro_flow.py"], False)
    assert code == 2
    assert "docs-syncer" in message


def test_every_documented_prefix_triggers_the_block():
    for path in (
        "app/core/constants.py",
        "admin_panel/views/home.py",
        "scripts/seed_db.py",
    ):
        code, _ = guard.evaluate([path], False)
        assert code == 2, f"{path} should trigger the docs reminder"


def test_allows_when_any_markdown_was_touched_too():
    for md in (
        "CLAUDE.md",
        ".claude/rules/services.md",
        "docs/TESTING.md",
        "README.MD",
    ):
        code, _ = guard.evaluate(["app/services/pro_flow.py", md], False)
        assert code == 0, f"{md} counts as a doc sync"


# --- What must never block ---------------------------------------------------


def test_allows_a_clean_tree():
    assert guard.evaluate([], False) == (0, "")


def test_allows_test_only_and_config_only_changes():
    """A test has no doc to sync; a .claude/ change is itself the documentation."""
    for path in (
        "tests/test_pro_flow.py",
        ".claude/settings.json",
        ".github/workflows/tests.yml",
        "requirements.txt",
    ):
        assert guard.evaluate([path], False) == (0, ""), path


def test_allows_non_python_changes_under_source_dirs():
    """A template or asset edit under app/ is not what the service docs describe."""
    assert guard.evaluate(["app/templates/email.html"], False) == (0, "")


def test_loop_guard_always_allows_the_second_stop():
    """``stop_hook_active`` means this hook already blocked once this turn."""
    assert guard.evaluate(["app/services/pro_flow.py"], True) == (0, "")


def test_windows_paths_are_normalized():
    code, _ = guard.evaluate([r"app\services\pro_flow.py"], False)
    assert code == 2


# --- The process boundary ----------------------------------------------------


def test_main_fails_open_on_unparsable_stdin(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    try:
        guard.main()
    except SystemExit as exc:
        assert exc.code == 0
    else:  # pragma: no cover - main always exits
        raise AssertionError("main() must exit")


def test_main_blocks_with_reminder_on_stderr(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"cwd": "."})))
    monkeypatch.setattr(guard, "_dirty_paths", lambda cwd=None: ["app/x.py"])
    try:
        guard.main()
    except SystemExit as exc:
        assert exc.code == 2
    assert "docs-syncer" in capsys.readouterr().err


def test_dirty_paths_fails_open_when_git_is_missing(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("no git")

    monkeypatch.setattr(subprocess, "run", boom)
    assert guard._dirty_paths() == []


def test_dirty_paths_parses_porcelain_including_renames(monkeypatch):
    class _Result:
        returncode = 0
        stdout = (
            " M app/services/pro_flow.py\n"
            "?? docs/NEW.md\n"
            'R  "old name.py" -> app/new_name.py\n'
        )

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Result())
    assert guard._dirty_paths() == [
        "app/services/pro_flow.py",
        "docs/NEW.md",
        "app/new_name.py",
    ]
