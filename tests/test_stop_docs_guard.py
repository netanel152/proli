"""Decision-logic tests for the Stop hook (``.claude/hooks/stop-docs-guard.py``).

The hook refuses — once — to end a turn in which *this session* changed source
the docs describe without any ``.md`` being touched. Only the pure
``evaluate(written_paths, dirty_paths, branch_paths, stop_hook_active)``
function and the small parsers around it are exercised: they take lists, so no
git repo is involved, and the loop-guard (``stop_hook_active``) is a plain
parameter. The transcript reader is fed a temp file shaped like a Claude Code
transcript.

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

SRC = "app/services/pro_flow.py"


def _eval(written=None, dirty=(), branch=(), active=False):
    return guard.evaluate(written, list(dirty), list(branch), active)


# --- The one rule, from both sides -------------------------------------------


def test_blocks_when_session_wrote_documented_source_and_nothing_touched_a_doc():
    code, message = _eval(written=[SRC])
    assert code == 2
    assert "docs-syncer" in message


def test_every_documented_prefix_triggers_the_block():
    for path in (
        "app/core/constants.py",
        "admin_panel/views/home.py",
        "scripts/seed_db.py",
    ):
        code, _ = _eval(written=[path])
        assert code == 2, f"{path} should trigger the docs reminder"


def test_a_doc_written_by_the_session_releases_it():
    for md in (
        "CLAUDE.md",
        ".claude/rules/services.md",
        "docs/TESTING.md",
        "README.MD",
    ):
        code, _ = _eval(written=[SRC, md])
        assert code == 0, f"{md} counts as a doc sync"


def test_a_doc_dirty_in_the_tree_releases_it():
    """A docs-syncer subagent's edits are in its own transcript, not ours —
    but they do dirty the tree."""
    assert _eval(written=[SRC], dirty=["docs/ARCHITECTURE.md"]) == (0, "")


def test_a_doc_committed_on_the_branch_releases_it():
    """/take-issue commits before it stops; the tree is clean by then."""
    assert _eval(written=[SRC], branch=[SRC, "CLAUDE.md"]) == (0, "")


# --- What must never block ---------------------------------------------------


def test_allows_a_session_that_wrote_nothing():
    assert _eval(written=[]) == (0, "")


def test_someone_elses_dirty_source_does_not_block_a_session_that_wrote_nothing():
    """The tree is shared with parallel sessions (CLAUDE.md): a .py another
    session left uncommitted must not be blamed on this one."""
    assert _eval(written=[], dirty=[SRC]) == (0, "")


def test_committed_source_on_the_branch_alone_does_not_block():
    """branch_paths only ever release; a branch that already carries source is
    not evidence *this turn* changed it."""
    assert _eval(written=[], branch=[SRC]) == (0, "")


def test_allows_test_only_and_config_only_changes():
    """A test has no doc to sync; a .claude/ change is itself the documentation."""
    for path in (
        "tests/test_pro_flow.py",
        ".claude/settings.json",
        ".github/workflows/tests.yml",
        "requirements.txt",
    ):
        assert _eval(written=[path]) == (0, ""), path


def test_allows_non_python_changes_under_source_dirs():
    """A template or asset edit under app/ is not what the service docs describe."""
    assert _eval(written=["app/templates/email.html"]) == (0, "")


def test_loop_guard_always_allows_the_second_stop():
    """``stop_hook_active`` means this hook already blocked once this turn."""
    assert _eval(written=[SRC], active=True) == (0, "")


def test_windows_paths_are_normalized():
    code, _ = _eval(written=[r"app\services\pro_flow.py"])
    assert code == 2
    assert _eval(written=[SRC], dirty=[r"docs\NOTES.md"]) == (0, "")


# --- The fallback when the transcript is unreadable --------------------------


def test_unreadable_transcript_falls_back_to_the_dirty_tree():
    assert _eval(written=None, dirty=[SRC])[0] == 2
    assert _eval(written=None, dirty=[SRC, "docs/X.md"]) == (0, "")
    assert _eval(written=None, dirty=[]) == (0, "")


# --- Parsers -----------------------------------------------------------------


def test_transcript_reader_collects_write_tool_paths_relative_to_cwd(tmp_path):
    cwd = tmp_path / "repo"
    transcript = tmp_path / "t.jsonl"

    def entry(name, **tool_input):
        return json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "hi"},
                        {"type": "tool_use", "name": name, "input": tool_input},
                    ]
                },
            }
        )

    transcript.write_text(
        "\n".join(
            [
                "not json at all",
                json.dumps({"type": "user", "message": {"content": "plain string"}}),
                entry("Read", file_path=str(cwd / "app/x.py")),  # reads don't count
                entry("Edit", file_path=str(cwd / "app/services/pro_flow.py")),
                entry("Write", file_path=str(cwd / "docs/NEW.md")),
                entry("NotebookEdit", notebook_path=str(cwd / "scripts/nb.ipynb")),
                entry("Bash", command="echo"),
            ]
        ),
        encoding="utf-8",
    )
    assert guard._session_written_paths(str(transcript), str(cwd)) == [
        "app/services/pro_flow.py",
        "docs/NEW.md",
        "scripts/nb.ipynb",
    ]


def test_transcript_reader_returns_none_when_unreadable(tmp_path):
    assert guard._session_written_paths(None) is None
    assert guard._session_written_paths(str(tmp_path / "missing.jsonl")) is None


def test_dirty_paths_parses_nul_porcelain_including_renames(monkeypatch):
    class _Result:
        returncode = 0
        stdout = (
            " M app/services/pro_flow.py\0"
            "?? docs/NEW.md\0"
            "R  app/new_name.py\0app/old -> name.py\0"
            "?? app/with space.py\0"
        )

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Result())
    assert guard._dirty_paths() == [
        "app/services/pro_flow.py",
        "docs/NEW.md",
        "app/new_name.py",
        "app/old -> name.py",
        "app/with space.py",
    ]


def test_git_helpers_fail_open_when_git_is_missing(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("no git")

    monkeypatch.setattr(subprocess, "run", boom)
    assert guard._dirty_paths() == []
    assert guard._branch_paths() == []


def test_branch_paths_is_empty_when_origin_dev_is_unknown(monkeypatch):
    class _Result:
        returncode = 128
        stdout = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Result())
    assert guard._branch_paths() == []


# --- The process boundary ----------------------------------------------------


def _run_main_expecting_exit(code):
    try:
        guard.main()
    except SystemExit as exc:
        assert exc.code == code
    else:  # pragma: no cover - main always exits
        raise AssertionError("main() must exit")


def test_main_fails_open_on_unparsable_stdin(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    _run_main_expecting_exit(0)


def test_main_fails_open_on_non_object_stdin(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("[1, 2]"))
    _run_main_expecting_exit(0)


def test_main_blocks_with_reminder_on_stderr(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"cwd": "."})))
    monkeypatch.setattr(guard, "_session_written_paths", lambda *a, **k: [SRC])
    monkeypatch.setattr(guard, "_dirty_paths", lambda *a, **k: [])
    monkeypatch.setattr(guard, "_branch_paths", lambda *a, **k: [])
    _run_main_expecting_exit(2)
    assert "docs-syncer" in capsys.readouterr().err


def test_main_fails_open_when_a_helper_raises(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"cwd": "."})))

    def boom(*a, **k):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(guard, "_session_written_paths", boom)
    _run_main_expecting_exit(0)
