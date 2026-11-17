"""PRO-111 — app/db/backup.py (scripts/backup.py wraps it) strict --upload-s3 exit codes.

Before PRO-111, `--upload-s3` failing to land the archive in S3 (missing
bucket, boto3 error, etc.) still exited 0 — the scheduler then logged "backup
completed successfully" while nothing durable existed. These tests pin the
strict behavior: a failed S3 upload under --upload-s3 is `sys.exit(1)`, and
`cleanup_old_backups()` still runs so local disk doesn't fill up on repeated
failures.

`main()` isn't async — these are plain sync tests despite `asyncio_mode =
strict` elsewhere in the suite; nothing here awaits.
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import app.db.backup as backup_module


def _archive_path_from_cmd(cmd: list[str]) -> Path:
    """run_mongodump builds `cmd` as
    ["mongodump", f"--uri={uri}", f"--archive={archive_path}", "--gzip"] —
    pull the real, timestamp-generated path back out so a fake subprocess.run
    can write to (and tests can assert on) the exact file the code will
    later stat/unlink."""
    archive_arg = next(a for a in cmd if a.startswith("--archive="))
    return Path(archive_arg.split("=", 1)[1])


@pytest.mark.parametrize("flag", ["--upload-s3"])
def test_main_upload_s3_failure_exits_1_and_still_cleans_up(monkeypatch, flag):
    fake_path = Path("/tmp/proli_fake_backup.gz")

    monkeypatch.setattr(
        backup_module, "run_mongodump", MagicMock(return_value=fake_path)
    )
    monkeypatch.setattr(backup_module, "upload_to_s3", MagicMock(return_value=False))
    mock_cleanup = MagicMock()
    monkeypatch.setattr(backup_module, "cleanup_old_backups", mock_cleanup)
    monkeypatch.setattr(backup_module.sys, "argv", ["backup.py", flag])

    with pytest.raises(SystemExit) as exc_info:
        backup_module.main()

    assert exc_info.value.code == 1
    mock_cleanup.assert_called_once()


def test_main_upload_s3_success_exits_normally_and_cleans_up(monkeypatch):
    fake_path = Path("/tmp/proli_fake_backup.gz")

    monkeypatch.setattr(
        backup_module, "run_mongodump", MagicMock(return_value=fake_path)
    )
    monkeypatch.setattr(backup_module, "upload_to_s3", MagicMock(return_value=True))
    mock_cleanup = MagicMock()
    monkeypatch.setattr(backup_module, "cleanup_old_backups", mock_cleanup)
    monkeypatch.setattr(backup_module.sys, "argv", ["backup.py", "--upload-s3"])

    # main() falls through to the end of the function on success — no
    # SystemExit at all (only the `if not archive_path` / failed-upload
    # branches call sys.exit).
    backup_module.main()

    mock_cleanup.assert_called_once()


def test_main_no_upload_flag_never_calls_upload_to_s3_and_exits_normally(
    monkeypatch,
):
    fake_path = Path("/tmp/proli_fake_backup.gz")

    monkeypatch.setattr(
        backup_module, "run_mongodump", MagicMock(return_value=fake_path)
    )
    mock_upload = MagicMock(return_value=True)
    monkeypatch.setattr(backup_module, "upload_to_s3", mock_upload)
    monkeypatch.setattr(backup_module, "cleanup_old_backups", MagicMock())
    monkeypatch.setattr(backup_module.sys, "argv", ["backup.py"])

    backup_module.main()

    mock_upload.assert_not_called()


def test_upload_to_s3_returns_false_when_bucket_unset(monkeypatch):
    mock_settings = MagicMock()
    mock_settings.BACKUP_S3_BUCKET = None
    monkeypatch.setattr(backup_module, "settings", mock_settings)

    result = backup_module.upload_to_s3(Path("/tmp/proli_fake_backup.gz"))

    assert result is False


def test_upload_to_s3_returns_true_on_successful_upload(monkeypatch):
    mock_settings = MagicMock()
    mock_settings.BACKUP_S3_BUCKET = "proli-backups-bucket"
    monkeypatch.setattr(backup_module, "settings", mock_settings)

    mock_boto3 = MagicMock()
    mock_s3_client = MagicMock()
    mock_boto3.client.return_value = mock_s3_client
    monkeypatch.setitem(__import__("sys").modules, "boto3", mock_boto3)

    result = backup_module.upload_to_s3(Path("/tmp/proli_fake_backup.gz"))

    assert result is True
    mock_s3_client.upload_file.assert_called_once()


# --- run_mongodump: partial-archive cleanup + minimum-size guard (PRO-111) -

# All three tests point BACKUP_DIR at a pytest tmp_path so the "partial
# archive left on disk" assertions are against a real, isolated filesystem
# location rather than the repo's actual backups/ dir.


def test_run_mongodump_nonzero_returncode_returns_none_and_unlinks_partial_file(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(backup_module, "BACKUP_DIR", tmp_path)
    captured = {}

    def fake_run(cmd, **kwargs):
        path = _archive_path_from_cmd(cmd)
        captured["path"] = path
        # mongodump can write a partial archive before it fails.
        path.write_bytes(b"partial-garbage")
        result = MagicMock()
        result.returncode = 1
        result.stderr = "mongodump: connection refused"
        result.stdout = ""
        return result

    monkeypatch.setattr(backup_module.subprocess, "run", fake_run)

    result = backup_module.run_mongodump()

    assert result is None
    assert not captured["path"].exists()


def test_run_mongodump_tiny_archive_treated_as_failure_and_removed(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(backup_module, "BACKUP_DIR", tmp_path)
    captured = {}

    def fake_run(cmd, **kwargs):
        path = _archive_path_from_cmd(cmd)
        captured["path"] = path
        # Well under the 1024-byte floor — a truncated/empty dump.
        path.write_bytes(b"x" * 10)
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        result.stdout = ""
        return result

    monkeypatch.setattr(backup_module.subprocess, "run", fake_run)

    result = backup_module.run_mongodump()

    assert result is None
    assert not captured["path"].exists()


def test_run_mongodump_timeout_returns_none_and_unlinks_partial_file(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(backup_module, "BACKUP_DIR", tmp_path)
    captured = {}

    def fake_run(cmd, **kwargs):
        path = _archive_path_from_cmd(cmd)
        captured["path"] = path
        path.write_bytes(b"partial-before-timeout")
        raise subprocess.TimeoutExpired(cmd="mongodump", timeout=300)

    monkeypatch.setattr(backup_module.subprocess, "run", fake_run)

    result = backup_module.run_mongodump()

    assert result is None
    assert not captured["path"].exists()


# --- Restructure phase 3: production runs app.db.backup, not scripts/ -------

import os  # noqa: E402
import sys  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]


def test_backup_dir_is_still_the_repository_root_backups_folder():
    """Moving the module from scripts/ to app/db/ must not move where the
    archives land (``.dockerignore`` and the retention sweep expect it)."""
    assert backup_module.BACKUP_DIR == _ROOT / "backups"


@pytest.mark.asyncio
async def test_nightly_job_runs_the_module_from_the_repository_root(
    monkeypatch, fake_redis
):
    import app.scheduler as scheduler_module

    run = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(subprocess, "run", run)

    await scheduler_module.run_daily_backup()

    cmd = run.call_args.args[0]
    assert cmd == [sys.executable, "-m", "app.db.backup", "--upload-s3"]
    assert Path(run.call_args.kwargs["cwd"]) == _ROOT
    assert not any("scripts" in str(part) for part in cmd)


@pytest.mark.parametrize(
    "argv",
    [["-m", "app.db.backup", "--help"], ["scripts/backup.py", "--help"]],
    ids=["module", "cli-wrapper"],
)
def test_both_entry_points_start(argv):
    """The scheduler's `-m` form and the operator's script both resolve and
    parse their flags (``--help`` exits before any dump)."""
    env = {**os.environ, "PYTHONPATH": ""}
    result = subprocess.run(
        [sys.executable, *argv],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr[-800:]
    assert "--upload-s3" in result.stdout


@pytest.mark.parametrize("name", ["backup.py", "create_indexes.py"])
def test_cli_wrappers_hold_no_second_copy_of_the_logic(name):
    """A wrapper that re-declared INDEX_SPECS or the dump would be a second
    source of truth, and a test patching it would check nothing."""
    import ast

    tree = ast.parse((_ROOT / "scripts" / name).read_text(encoding="utf-8"))
    defined = {
        n.name
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    } | {
        t.id
        for n in tree.body
        if isinstance(n, ast.Assign)
        for t in n.targets
        if isinstance(t, ast.Name)
    }
    assert defined == set()
