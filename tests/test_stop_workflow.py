"""Coverage for `.github/workflows/stop_railway_services.yml`'s confirm step.

On 2026-09-08 the workflow refused three runs in a row because the operator's
phone keyboard had appended a space after the environment name — `'production '`
against `'production'` — and the check compared the raw strings byte for byte.
The confirm box exists to prove the operator meant *this* environment; trailing
whitespace and letter case say nothing about that, so the step now trims and
case-folds before comparing, and reads both inputs through `env:` rather than
interpolating free text into the shell.

Same shape as `tests/test_promote_workflow.py`: the step's shell is extracted
verbatim from the parsed YAML and executed under `bash`, so these tests run the
real script; a few structural assertions pin the ways the fix could silently
regress (the inputs moving back into `${{ }}`, the stop step keying on the raw
input instead of the normalised output).
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
def confirm_script(tmp_path_factory):
    step = _find_step(_load_workflow(), "confirm")
    script_path = tmp_path_factory.mktemp("stop-confirm") / "confirm.sh"
    script_path.write_text(step["run"], encoding="utf-8")
    return script_path


def _run_confirm(confirm_script, tmp_path, target_env, confirm):
    output_path = tmp_path / "github_output"
    output_path.write_text("", encoding="utf-8")
    env = dict(os.environ)
    env["TARGET_ENV"] = target_env
    env["CONFIRM"] = confirm
    env["GITHUB_OUTPUT"] = str(output_path)
    proc = subprocess.run(
        ["bash", str(confirm_script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return proc, _parse_github_output(output_path)


# --- Behavioural: run the real "Confirm the target" shell ---


@pytest.mark.parametrize(
    "target_env, confirm",
    [
        pytest.param("production", "production", id="exact-production"),
        pytest.param("staging", "staging", id="exact-staging"),
        # The 2026-09-08 failure: a phone keyboard's trailing space.
        pytest.param("production", "production ", id="trailing-space"),
        pytest.param("production", "  production", id="leading-space"),
        pytest.param("production", "\tproduction\n", id="tab-and-newline"),
        pytest.param("production", "Production", id="capitalised"),
        pytest.param("staging", "STAGING ", id="upper-and-trailing-space"),
    ],
)
def test_confirm_accepts_the_target_modulo_whitespace_and_case(
    confirm_script, tmp_path, target_env, confirm
):
    proc, output = _run_confirm(confirm_script, tmp_path, target_env, confirm)

    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert output.get("target") == target_env
    assert "::error::" not in proc.stdout


@pytest.mark.parametrize(
    "target_env, confirm",
    [
        # Typing the *other* environment's name is the mistake the box guards.
        pytest.param("staging", "production", id="wrong-env"),
        pytest.param("production", "staging ", id="wrong-env-trailing-space"),
        pytest.param("production", "", id="empty"),
        pytest.param("production", "   ", id="whitespace-only"),
        pytest.param("production", "prod", id="prefix"),
        pytest.param("production", "productionn", id="typo"),
        pytest.param("production", "yes", id="affirmative-word"),
        # Inner whitespace is not trimmed — it is not a trailing-space slip.
        pytest.param("production", "pro duction", id="inner-space"),
    ],
)
def test_confirm_refuses_anything_that_is_not_the_target(
    confirm_script, tmp_path, target_env, confirm
):
    proc, output = _run_confirm(confirm_script, tmp_path, target_env, confirm)

    assert proc.returncode == 1, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "Refusing to stop anything." in proc.stdout
    # Nothing is written for a refusal — the stop step must have no target.
    assert "target" not in output


def test_confirm_refuses_an_unknown_target_even_when_echoed_back(
    confirm_script, tmp_path
):
    # `target_env` is a dropdown, so this cannot happen through the UI; it
    # pins that a matching pair of strings is not enough on its own.
    proc, output = _run_confirm(confirm_script, tmp_path, "development", "development")

    assert proc.returncode == 1, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "Unknown target environment" in proc.stdout
    assert "target" not in output


def test_confirm_treats_typed_text_as_data_not_shell(confirm_script, tmp_path):
    marker = tmp_path / "injected"
    payload = f'production"; touch "{marker}"; echo "'
    proc, output = _run_confirm(confirm_script, tmp_path, "production", payload)

    assert proc.returncode == 1
    assert not marker.exists(), "confirm text was executed as shell"
    assert "target" not in output


# --- Structural: pin the shape of the fix ---


def test_inputs_reach_the_confirm_step_through_env_not_expressions():
    step = _find_step(_load_workflow(), "confirm")

    assert step["env"]["TARGET_ENV"] == "${{ inputs.target_env }}"
    assert step["env"]["CONFIRM"] == "${{ inputs.confirm }}"
    # Free text interpolated into `run:` is a shell-injection hole, and it is
    # how the raw, untrimmed string got compared in the first place.
    assert "${{ inputs.confirm }}" not in step["run"]
    assert "${{ inputs.target_env }}" not in step["run"]


def test_stop_step_keys_on_the_confirmed_target_not_the_raw_input():
    doc = _load_workflow()
    stop_steps = [s for s in _steps(doc) if "railway down" in s.get("run", "")]
    assert len(stop_steps) == 1, "expected exactly one step that runs `railway down`"
    stop = stop_steps[0]

    for key in ("RAILWAY_TOKEN", "RAILWAY_ENV"):
        assert "steps.confirm.outputs.target" in stop["env"][key]
        assert "inputs.target_env" not in stop["env"][key]
    # Both environments stay reachable — the token/env pair is selected, not fixed.
    assert "RAILWAY_TOKEN_PRODUCTION" in stop["env"]["RAILWAY_TOKEN"]
    assert "RAILWAY_TOKEN_STAGING" in stop["env"]["RAILWAY_TOKEN"]
    assert "'Production'" in stop["env"]["RAILWAY_ENV"]
    assert "'Staging'" in stop["env"]["RAILWAY_ENV"]


def test_confirm_input_has_no_default():
    # A default would let the dropdown alone confirm itself.
    inputs = _load_workflow()[True]["workflow_dispatch"]["inputs"]
    assert inputs["confirm"]["required"] is True
    assert "default" not in inputs["confirm"]
