"""Every workflow stays hardened, and the test floor behaves.

The repo is public, so the hardening is pinned rather than remembered. The floor
steps are extracted verbatim from `tests.yml` and run under `bash -e`, the way
GitHub runs them. What the cache and the label do on GitHub itself cannot be run
here; `docs/CI_WORKFLOWS.md` says what those rest on.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_DIR = ROOT / ".github" / "workflows"
WORKFLOWS = sorted(WORKFLOW_DIR.glob("*.yml"))
# The one job that has to push, so the one checkout that keeps its credentials.
PUSHES = "promote-to-production.yml"


def _load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _all_steps(doc):
    return [step for job in doc["jobs"].values() for step in job["steps"]]


workflow_param = pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.stem)


def test_the_glob_found_the_workflows():
    assert {p.name for p in WORKFLOWS} >= {PUSHES, "tests.yml"}


@workflow_param
def test_every_workflow_declares_permissions_and_only_promote_can_write(path):
    permissions = _load(path)["permissions"]
    if path.name == PUSHES:
        assert permissions == {"contents": "write", "checks": "read"}
    else:
        assert permissions == {"contents": "read"}


@workflow_param
def test_every_job_has_a_timeout(path):
    for name, job in _load(path)["jobs"].items():
        assert job.get("timeout-minutes"), f"{path.name}: {name} has no timeout"


@workflow_param
def test_every_action_is_pinned_to_a_sha_with_a_version_comment(path):
    for line in path.read_text(encoding="utf-8").splitlines():
        if re.match(r"\s*(- )?uses:", line):
            assert re.search(
                r"uses: \S+@[0-9a-f]{40} # v\d+\.\d+\.\d+$", line
            ), f"{path.name}: {line.strip()}"


def test_each_action_is_on_one_version_across_files():
    seen = {}
    for path in WORKFLOWS:
        for match in re.finditer(
            r"uses: ([\w-]+/[\w-]+)\S*@([0-9a-f]{40})", path.read_text(encoding="utf-8")
        ):
            seen.setdefault(match.group(1), set()).add(match.group(2))
    assert seen, "no pinned actions found"
    assert {name: shas for name, shas in seen.items() if len(shas) > 1} == {}


@workflow_param
def test_nothing_is_interpolated_into_a_script_body(path):
    # A `${{ }}` in `run:` is substituted before bash parses the line.
    for step in _all_steps(_load(path)):
        assert "${{" not in step.get("run", ""), f"{path.name}: {step.get('name')}"


@workflow_param
def test_only_the_job_that_pushes_keeps_checkout_credentials(path):
    for step in _all_steps(_load(path)):
        if step.get("uses", "").startswith("actions/checkout@"):
            persist = step.get("with", {}).get("persist-credentials")
            assert persist is (None if path.name == PUSHES else False), path.name


@workflow_param
def test_no_workflow_hands_secrets_to_fork_code(path):
    assert "pull_request_target" not in _load(path)[True]


def test_dependabot_groups_action_updates_into_one_weekly_pr():
    config = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text("utf-8"))
    update = next(
        u for u in config["updates"] if u["package-ecosystem"] == "github-actions"
    )
    assert update["schedule"]["interval"] == "weekly"
    assert [g["patterns"] for g in update["groups"].values()] == [["*"]]


# --- tests.yml ---


@pytest.fixture(scope="module")
def tests_doc():
    return _load(WORKFLOW_DIR / "tests.yml")


def _step(doc, *, step_id=None, name=None):
    for step in doc["jobs"]["pytest"]["steps"]:
        if (step_id and step.get("id") == step_id) or (
            name and step.get("name") == name
        ):
            return step
    raise AssertionError(f"no step {step_id or name!r} in tests.yml")


def test_only_pull_requests_cancel_and_each_push_gets_its_own_group(tests_doc):
    # Promote reads pytest's result on the dev SHA. GitHub drops a waiting run from
    # a busy group even with cancelling off, so a branch-wide group would lose one.
    concurrency = tests_doc["concurrency"]
    assert concurrency["cancel-in-progress"] == (
        "${{ github.event_name == 'pull_request' }}"
    )
    assert "github.sha" in concurrency["group"]
    assert "github.ref" in concurrency["group"]


def test_the_required_check_keeps_its_name(tests_doc):
    assert "pytest" in tests_doc["jobs"]


def test_actionlint_is_pinned_and_its_download_verified(tests_doc):
    step = _step(tests_doc, name="Lint — actionlint")
    assert re.fullmatch(r"\d+\.\d+\.\d+", step["env"]["ACTIONLINT_VERSION"])
    assert re.fullmatch(r"[0-9a-f]{64}", step["env"]["ACTIONLINT_SHA256"])
    assert step["run"].index("sha256sum -c") < step["run"].index("tar -xzf")


def test_only_a_push_to_dev_records_the_floor(tests_doc):
    dev_only = "github.event_name == 'push' && github.ref == 'refs/heads/dev'"
    assert _step(tests_doc, name="Record the passed count for dev's floor")["if"] == (
        dev_only
    )
    assert _step(tests_doc, name="Save dev's passed count")["if"] == dev_only


def test_only_a_pull_request_reads_the_floor_and_the_keys_line_up(tests_doc):
    restore = _step(tests_doc, name="Restore dev's passed count")
    save = _step(tests_doc, name="Save dev's passed count")
    floor = _step(tests_doc, step_id="floor")

    assert restore["if"] == floor["if"] == "github.event_name == 'pull_request'"
    assert restore["uses"].startswith("actions/cache/restore@")
    assert save["uses"].startswith("actions/cache/save@")
    # The cache version is derived from the path, so these must match exactly.
    assert restore["with"]["path"] == save["with"]["path"]
    assert save["with"]["key"].startswith(restore["with"]["restore-keys"])
    # A failed restore must reach the floor step's warning, not end the job.
    assert "fail-on-cache-miss" not in restore["with"]


def test_the_floor_is_restored_before_the_suite_runs(tests_doc):
    # So a dev merge landing mid-run cannot raise this PR's floor.
    names = [s.get("name") for s in tests_doc["jobs"]["pytest"]["steps"]]
    assert names.index("Restore dev's passed count") < names.index("Run tests")


def test_the_label_reaches_the_script_through_env_and_starts_a_run(tests_doc):
    expr = _step(tests_doc, step_id="floor")["env"]["ALLOW_TEST_DROP"]
    assert expr == (
        "${{ contains(github.event.pull_request.labels.*.name, 'allow-test-drop') }}"
    )
    # A re-run replays the original event, so only a new run can see a new label.
    types = tests_doc[True]["pull_request"]["types"]
    assert {"opened", "synchronize", "reopened", "labeled", "unlabeled"} == set(types)


def _run_step(tests_doc, step_id, cwd, **env):
    script = cwd / f"{step_id}.sh"
    script.write_text(_step(tests_doc, step_id=step_id)["run"], encoding="utf-8")
    return subprocess.run(
        ["bash", "-e", str(script)],
        cwd=cwd,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        timeout=20,
    )


def _output(path):
    return path.read_text(encoding="utf-8").splitlines()


@pytest.mark.parametrize(
    "summary, expected",
    [
        ("2254 passed, 87 skipped in 120.00s", "2254"),
        ("..\n3 passed in 0.01s", "3"),
        ("note: 5 passed earlier\n7 passed, 1 skipped in 1s", "7"),
    ],
)
def test_the_count_comes_from_pytests_summary(tests_doc, tmp_path, summary, expected):
    (tmp_path / "pytest-output.txt").write_text(summary, encoding="utf-8")
    out = tmp_path / "out"
    proc = _run_step(tests_doc, "count", tmp_path, GITHUB_OUTPUT=str(out))

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _output(out) == [f"passed={expected}"]


@pytest.mark.parametrize("summary", ["", "no tests ran in 0.01s", "garbage"])
def test_an_unparseable_summary_fails_the_job(tests_doc, tmp_path, summary):
    (tmp_path / "pytest-output.txt").write_text(summary, encoding="utf-8")
    out = tmp_path / "out"
    proc = _run_step(tests_doc, "count", tmp_path, GITHUB_OUTPUT=str(out))

    assert proc.returncode == 1
    assert "::error::Could not parse" in proc.stdout
    assert not out.exists() or _output(out) == []


def _run_floor(tests_doc, tmp_path, passed, floor=None, label="false"):
    if floor is not None:
        (tmp_path / "pytest-floor").mkdir()
        (tmp_path / "pytest-floor" / "passed").write_text(floor, encoding="utf-8")
    return _run_step(
        tests_doc, "floor", tmp_path, PASSED=str(passed), ALLOW_TEST_DROP=label
    )


def test_fewer_passing_tests_than_dev_fails(tests_doc, tmp_path):
    proc = _run_floor(tests_doc, tmp_path, 99, "100\n")

    assert proc.returncode == 1
    assert "::error::Regression: 99 tests passed" in proc.stdout
    assert "allow-test-drop" in proc.stdout


@pytest.mark.parametrize("passed", [100, 101])
def test_at_or_above_the_floor_passes_with_no_warning(tests_doc, tmp_path, passed):
    proc = _run_floor(tests_doc, tmp_path, passed, "100\n")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "::warning::" not in proc.stdout


def test_a_numeric_compare_not_a_string_one(tests_doc, tmp_path):
    assert _run_floor(tests_doc, tmp_path, 1000, "999\n").returncode == 0


def test_the_label_turns_the_failure_into_a_warning(tests_doc, tmp_path):
    proc = _run_floor(tests_doc, tmp_path, 99, "100\n", label="true")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "::warning::99 tests passed, below dev's floor of 100" in proc.stdout
    assert "::error::" not in proc.stdout


@pytest.mark.parametrize(
    "floor",
    [
        pytest.param(None, id="cache-miss"),
        pytest.param("", id="empty-file"),
        pytest.param("not a number\n", id="corrupt-file"),
    ],
)
def test_no_floor_warns_that_nothing_was_checked_and_passes(tests_doc, tmp_path, floor):
    proc = _run_floor(tests_doc, tmp_path, 5, floor)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "::warning::No test floor was checked" in proc.stdout
