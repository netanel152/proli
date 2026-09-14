"""Decision-logic tests for the PostToolUse formatter hook.

The hook (``.claude/hooks/post-edit-format.py``) runs black and then flake8 on a
touched ``.py`` and — since PR #181 — returns any finding to *Claude* through the
documented ``{"decision": "block", "reason": ...}`` shape rather than to stderr,
where only the user could see it. Two pure functions carry that decision and are
exercised here; the subprocess plumbing around them is not, because a test that
shells out to black is a test of black.

Loaded by path via ``importlib`` like ``tests/test_pre_bash_guard.py`` — the
hyphenated filename is not importable as a module.
"""

import importlib.util
import io
import json
import sys
from pathlib import Path

_HOOK_PATH = (
    Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "post-edit-format.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("post_edit_format", _HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hook = _load()

FINDING = "app/services/pro_flow.py:12:1: F401 'os' imported but unused"


# --- Which files the linters own ---------------------------------------------


def test_only_python_files_are_checkable():
    assert hook.is_checkable("app/services/pro_flow.py")
    for path in ("README.md", "app/templates/x.html", "Makefile", ""):
        assert not hook.is_checkable(path), path


def test_generated_and_vendored_trees_are_skipped():
    for path in (
        "venv/lib/python3.11/site-packages/x.py",
        "app/__pycache__/pro_flow.py",
        ".venv/bin/thing.py",
        "node_modules/pkg/setup.py",
        "app/.pytest_cache/x.py",
    ):
        assert not hook.is_checkable(path), path


def test_windows_separators_are_normalized():
    assert not hook.is_checkable(r"venv\lib\x.py")
    assert hook.is_checkable(r"app\services\pro_flow.py")


def test_a_directory_named_like_a_skip_dir_mid_path_is_still_skipped():
    assert not hook.is_checkable("tools/venv/x.py")


def test_a_file_merely_prefixed_with_a_skip_name_is_checkable():
    """`venvtools/` is not `venv/` — the guard matches path segments."""
    assert hook.is_checkable("venvtools/x.py")


# --- What the hook says, and when it stays quiet ------------------------------


def test_a_clean_file_produces_no_message():
    """The hook speaks only about what would fail CI."""
    assert hook.build_reason("app/x.py", reformatted=False, findings="") == ""
    assert hook.build_reason("app/x.py", reformatted=False, findings="   \n") == ""


def test_a_reformat_alone_is_not_worth_a_message():
    """black rewriting a file is the hook working, not a problem to report."""
    assert hook.build_reason("app/x.py", reformatted=True, findings="") == ""


def test_a_finding_is_reported_with_the_ci_consequence_and_the_finding_itself():
    reason = hook.build_reason("app/x.py", reformatted=False, findings=FINDING + "\n")
    assert FINDING in reason
    assert "flake8 --count ." in reason, "the message must say why it matters"
    assert "1 finding(s)" in reason
    assert "stale" not in reason, "nothing was reformatted"


def test_the_finding_count_is_the_number_of_lines():
    findings = "\n".join([FINDING, FINDING.replace("12", "13")]) + "\n"
    assert "2 finding(s)" in hook.build_reason("app/x.py", False, findings)


def test_a_reformat_rides_along_on_a_message_that_was_going_out_anyway():
    """Claude's copy of the file is stale after black rewrites it, and its next
    `old_string` would miss — worth saying, but not on its own."""
    reason = hook.build_reason("app/x.py", reformatted=True, findings=FINDING)
    assert "stale" in reason
    assert FINDING in reason


# --- The process boundary -----------------------------------------------------


def _main_with(payload, monkeypatch, capsys, findings="", reformatted=False):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(hook.os.path, "isfile", lambda _p: True)
    monkeypatch.setattr(
        hook, "_run", lambda args: findings if args[0] == "flake8" else ""
    )
    digests = iter(["before", "after" if reformatted else "before"])
    monkeypatch.setattr(hook, "_digest", lambda _p: next(digests))
    try:
        hook.main()
    except SystemExit as exc:
        assert exc.code == 0, "this hook never fails the edit loop"
    return capsys.readouterr().out


def test_main_emits_the_documented_block_shape_for_a_finding(monkeypatch, capsys):
    out = _main_with(
        {"tool_input": {"file_path": "app/x.py"}}, monkeypatch, capsys, findings=FINDING
    )
    payload = json.loads(out)
    assert payload["decision"] == "block"
    assert FINDING in payload["reason"]


def test_main_prints_nothing_when_the_file_is_clean(monkeypatch, capsys):
    assert (
        _main_with({"tool_input": {"file_path": "app/x.py"}}, monkeypatch, capsys) == ""
    )


def test_main_reports_a_reformat_alongside_a_finding(monkeypatch, capsys):
    out = _main_with(
        {"tool_input": {"file_path": "app/x.py"}},
        monkeypatch,
        capsys,
        findings=FINDING,
        reformatted=True,
    )
    assert "stale" in json.loads(out)["reason"]


def test_main_ignores_a_payload_with_no_python_file(monkeypatch, capsys):
    for tool_input in ({}, {"file_path": "README.md"}, {"path": "notes.txt"}):
        monkeypatch.setattr(
            sys, "stdin", io.StringIO(json.dumps({"tool_input": tool_input}))
        )
        try:
            hook.main()
        except SystemExit as exc:
            assert exc.code == 0
        assert capsys.readouterr().out == ""


def test_main_fails_open_on_unparsable_stdin(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    try:
        hook.main()
    except SystemExit as exc:
        assert exc.code == 0
    assert capsys.readouterr().out == ""


def test_run_returns_empty_when_the_tool_is_missing(monkeypatch):
    """No black or flake8 on the machine degrades to 'no extra guard'."""

    def boom(*_args, **_kwargs):
        raise OSError("no such executable")

    monkeypatch.setattr(hook.subprocess, "run", boom)
    assert hook._run(["flake8", "x.py"]) == ""


def test_digest_returns_none_for_an_unreadable_file(tmp_path):
    assert hook._digest(str(tmp_path / "missing.py")) is None
