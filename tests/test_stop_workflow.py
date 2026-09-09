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
