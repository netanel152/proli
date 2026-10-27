"""Coverage for `.github/workflows/stop_railway_services.yml`.

The workflow takes no inputs: the branch it is run from is the target — `dev`
stops staging, `production` stops production, anything else refuses. This
replaced a dropdown-plus-confirm-box that refused four operator runs in a row
on 2026-09-08 (a phone keyboard's trailing space, then the other environment's
name typed into the box).

Same shape as `tests/test_promote_workflow.py`: the resolve step's shell is
extracted verbatim from the parsed YAML and executed under `bash`, so these
tests run the real script; a few structural assertions pin the ways the shape
could silently regress (inputs creeping back, the stop step keying on the raw
branch name instead of the resolved output, a branch reaching the shell
through `${{ }}`).
"""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

WORKFLOW_PATH = (
    Path(__file__).resolve().parent.parent
    / ".github"
    / "workflows"
    / "stop_railway_services.yml"
)


def _load_workflow():
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _steps(doc):
    return doc["jobs"]["stop-railway"]["steps"]


def _find_step(doc, step_id):
    for step in _steps(doc):
        if step.get("id") == step_id:
            return step
    raise AssertionError(f"no step with id={step_id!r} in {WORKFLOW_PATH}")


def _parse_github_output(path):
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            name, _, value = line.partition("=")
            result[name] = value
    return result


@pytest.fixture(scope="module")
def resolve_script(tmp_path_factory):
    step = _find_step(_load_workflow(), "target")
    script_path = tmp_path_factory.mktemp("stop-resolve") / "resolve.sh"
    script_path.write_text(step["run"], encoding="utf-8")
    return script_path


def _run_resolve(resolve_script, tmp_path, ref_name):
    output_path = tmp_path / "github_output"
    output_path.write_text("", encoding="utf-8")
    env = dict(os.environ)
    env["REF_NAME"] = ref_name
    env["GITHUB_OUTPUT"] = str(output_path)
    proc = subprocess.run(
        ["bash", str(resolve_script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return proc, _parse_github_output(output_path)


# --- Behavioural: run the real "Resolve the target from the branch" shell ---


@pytest.mark.parametrize(
    "ref_name, target",
    [
        pytest.param("dev", "staging", id="dev-stops-staging"),
        pytest.param("production", "production", id="production-stops-production"),
    ],
)
def test_the_branch_is_the_target(resolve_script, tmp_path, ref_name, target):
    proc, output = _run_resolve(resolve_script, tmp_path, ref_name)

    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert output.get("target") == target
    assert "::error::" not in proc.stdout


@pytest.mark.parametrize(
    "ref_name",
    [
        pytest.param("main", id="old-default-branch-name"),
        pytest.param("master", id="pre-rename-name"),
        pytest.param("staging", id="environment-name-is-not-a-branch"),
        pytest.param("feature/stop-services-by-branch", id="feature-branch"),
        pytest.param("Production", id="case-is-not-forgiven"),
        pytest.param("production ", id="whitespace-is-not-forgiven"),
        pytest.param("", id="empty"),
    ],
)
def test_any_other_branch_refuses(resolve_script, tmp_path, ref_name):
    proc, output = _run_resolve(resolve_script, tmp_path, ref_name)

    assert proc.returncode == 1, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "Refusing to stop anything." in proc.stdout
    # Nothing is written for a refusal — the stop step must have no target.
    assert "target" not in output


def test_branch_name_is_data_not_shell(resolve_script, tmp_path):
    marker = tmp_path / "injected"
    payload = f'dev"; touch "{marker}"; echo "'
    proc, output = _run_resolve(resolve_script, tmp_path, payload)

    assert proc.returncode == 1
    assert not marker.exists(), "branch name was executed as shell"
    assert "target" not in output


# --- Structural: pin the shape ---


def test_workflow_takes_no_inputs():
    # The whole point: nothing to choose, nothing to type. An input creeping
    # back is the 2026-09-08 failure class returning.
    dispatch = _load_workflow()[True]["workflow_dispatch"]
    assert not dispatch or "inputs" not in dispatch


def test_branch_reaches_the_resolve_step_through_env_not_expressions():
    step = _find_step(_load_workflow(), "target")

    assert step["env"]["REF_NAME"] == "${{ github.ref_name }}"
    assert "${{" not in step["run"]


def test_stop_step_keys_on_the_resolved_target_not_the_branch():
    doc = _load_workflow()
    stop_steps = [s for s in _steps(doc) if "railway down" in s.get("run", "")]
    assert len(stop_steps) == 1, "expected exactly one step that runs `railway down`"
    stop = stop_steps[0]

    for key in ("RAILWAY_TOKEN", "RAILWAY_ENV"):
        assert "steps.target.outputs.target" in stop["env"][key]
        assert "github.ref_name" not in stop["env"][key]
    # Both environments stay reachable — the token/env pair is selected, not fixed.
    assert "RAILWAY_TOKEN_PRODUCTION" in stop["env"]["RAILWAY_TOKEN"]
    assert "RAILWAY_TOKEN_STAGING" in stop["env"]["RAILWAY_TOKEN"]
    assert "'Production'" in stop["env"]["RAILWAY_ENV"]
    assert "'Staging'" in stop["env"]["RAILWAY_ENV"]


def test_run_name_names_the_target_and_is_a_single_line():
    # The run-list line is the only place the operator sees the target before
    # the job runs. A folded scalar with more-indented continuation lines keeps
    # a newline inside the `${{ }}` expression (the PRO-183 lesson).
    run_name = _load_workflow()["run-name"]
    assert "\n" not in run_name
    assert "PRODUCTION" in run_name
    assert "staging" in run_name
    assert "github.ref_name" in run_name


# --- Behavioural: run the real "Stop Services" shell against a fake `railway` --
#
# The step used to end every `railway down` with `|| echo "…: no active
# deployment"`. A *rejected token* took that path too, so the job printed "All
# services stopped" and went green having stopped nothing — measured on
# staging, 2026-09-22 and 2026-10-06 (the step ran in 2 s, against 13 s when
# production genuinely had something to remove). Only "already stopped" may
# count as success; everything else must fail the job.

_FAKE_RAILWAY = """#!/usr/bin/env bash
# Behaviour is chosen per service by FAKE_<SERVICE>: ok | nodeploy | badtoken.
svc=""
while [ $# -gt 0 ]; do
  if [ "$1" = "--service" ]; then svc="$2"; fi
  shift
done
mode_var="FAKE_$(printf '%s' "$svc" | tr '[:lower:]' '[:upper:]')"
case "${!mode_var:-ok}" in
  ok)       echo "Deployment removed"; exit 0 ;;
  nodeploy) echo "No deployments found"; exit 1 ;;
  badtoken) echo "Invalid RAILWAY_TOKEN. Please check that it is valid and has access to the resource you're trying to use."; exit 1 ;;
esac
exit 99
"""


@pytest.fixture(scope="module")
def stop_script(tmp_path_factory):
    step = _find_step(_load_workflow(), "stop")
    base = tmp_path_factory.mktemp("stop-run")
    script_path = base / "stop.sh"
    script_path.write_text(step["run"], encoding="utf-8")
    bin_dir = base / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "railway"
    fake.write_text(_FAKE_RAILWAY, encoding="utf-8")
    fake.chmod(0o755)
    return script_path, bin_dir


def _run_stop(stop_script, tmp_path, **modes):
    script_path, bin_dir = stop_script
    summary = tmp_path / "summary.md"
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["RAILWAY_ENV"] = "Staging"
    env["GITHUB_STEP_SUMMARY"] = str(summary)
    for svc, mode in modes.items():
        env[f"FAKE_{svc.upper()}"] = mode
    # `bash -e` because that is how GitHub runs every `run:` block: errexit is
    # already on before the script's first line (the PR #162 lesson).
    proc = subprocess.run(
        ["bash", "-e", str(script_path)],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    text = summary.read_text(encoding="utf-8") if summary.exists() else ""
    return proc, text


def test_all_services_stopped_succeeds(stop_script, tmp_path):
    proc, summary = _run_stop(stop_script, tmp_path)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "All services stopped" in proc.stdout
    assert summary == "", "a clean run must not write a failure summary"


def test_an_already_stopped_environment_is_success(stop_script, tmp_path):
    # Idempotence is the reason the old `|| echo` existed (cc51936): a second
    # click on an environment that is already down must stay green.
    proc, summary = _run_stop(
        stop_script, tmp_path, api="nodeploy", worker="nodeploy", admin="nodeploy"
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "already stopped" in proc.stdout
    assert "NOT stopped" not in proc.stdout
    assert summary == ""


def test_a_rejected_token_fails_the_job_instead_of_reporting_success(
    stop_script, tmp_path
):
    proc, summary = _run_stop(
        stop_script, tmp_path, api="badtoken", worker="badtoken", admin="badtoken"
    )

    assert proc.returncode != 0
    assert "All services stopped" not in proc.stdout
    assert "no active deployment" not in proc.stdout
    # Railway's own words stay in the log, so the operator sees *why*.
    assert "Invalid RAILWAY_TOKEN" in proc.stdout
    for svc in ("api", "worker", "admin"):
        assert f"{svc} was NOT stopped" in proc.stdout
    assert "still running" in summary


def test_one_failure_among_successes_still_fails_and_names_only_that_service(
    stop_script, tmp_path
):
    # A partial stop is the dangerous case: two green lines can hide the one
    # service that kept sending. It must also keep going after the first
    # failure rather than leaving the remaining services untouched.
    proc, summary = _run_stop(
        stop_script, tmp_path, api="ok", worker="badtoken", admin="nodeploy"
    )

    assert proc.returncode != 0
    assert "worker was NOT stopped" in proc.stdout
    assert "api was NOT stopped" not in proc.stdout
    assert "admin was NOT stopped" not in proc.stdout
    assert "api: stopped" in proc.stdout
    assert "admin: no active deployment (already stopped)" in proc.stdout
    assert "NOT stopped in Staging: worker" in summary


def test_a_failure_on_the_first_service_does_not_skip_the_rest(stop_script, tmp_path):
    proc, _ = _run_stop(stop_script, tmp_path, api="badtoken", worker="ok", admin="ok")

    assert proc.returncode != 0
    assert "worker: stopped" in proc.stdout
    assert "admin: stopped" in proc.stdout
