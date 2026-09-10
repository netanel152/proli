"""Coverage for `.github/workflows/check_pro_service_areas.yml`.

Same shape as 🛑 Stop Railway Proli Services (`tests/test_stop_workflow.py`):
the branch it is run from is the target — `dev` checks staging, `production`
checks production, anything else refuses. This test file mirrors that
harness exactly — the resolve step's shell is extracted verbatim from the
parsed YAML and executed under `bash`, so these tests run the real script.
A few structural assertions pin the ways the shape could silently regress:
the single `apply` checkbox creeping back into a free-text confirmation box,
the check step keying on the raw branch name instead of the resolved output,
a branch reaching the shell through `${{ }}`, `--apply` being passed
unconditionally, or the script's exit code no longer being the job's
verdict.
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
    / "check_pro_service_areas.yml"
)


def _load_workflow():
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _steps(doc):
    return doc["jobs"]["check-service-areas"]["steps"]


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
    script_path = tmp_path_factory.mktemp("check-resolve") / "resolve.sh"
    script_path.write_text(step["run"], encoding="utf-8")
    return script_path


def _run_resolve(resolve_script, tmp_path, ref_name):
    output_path = tmp_path / "github_output"
    output_path.write_text("", encoding="utf-8")
    env = dict(os.environ)
    if ref_name is None:
        env.pop("REF_NAME", None)
    else:
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
        pytest.param("dev", "staging", id="dev-checks-staging"),
        pytest.param("production", "production", id="production-checks-production"),
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
        pytest.param("feature/check-service-areas", id="feature-branch"),
        pytest.param(None, id="unset-ref-name"),
        pytest.param("", id="empty-ref-name"),
        pytest.param(" production ", id="whitespace-is-not-forgiven"),
        pytest.param("production-hotfix", id="prefix-match-is-not-forgiven"),
    ],
)
def test_any_other_branch_refuses(resolve_script, tmp_path, ref_name):
    proc, output = _run_resolve(resolve_script, tmp_path, ref_name)

    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "::error::" in proc.stdout
    # Nothing is written for a refusal — the check step must have no target.
    assert "target" not in output


# --- Structural: pin the shape ---


def test_workflow_dispatch_has_exactly_one_boolean_checkbox_input():
    # The one control is a checkbox, on purpose — the 2026-09-08 confirm box
    # refused four operator runs in a row over a phone keyboard's trailing
    # space. No free-text confirmation box may creep back in.
    dispatch = _load_workflow()[True]["workflow_dispatch"]
    inputs = dispatch["inputs"]
    assert list(inputs.keys()) == ["apply"]
    assert inputs["apply"]["type"] == "boolean"
    assert inputs["apply"]["default"] is False


def test_branch_reaches_the_resolve_step_through_env_not_expressions():
    step = _find_step(_load_workflow(), "target")

    assert step["env"]["REF_NAME"] == "${{ github.ref_name }}"
    assert "${{" not in step["run"]


def test_check_step_keys_railway_creds_on_the_resolved_target_not_the_branch():
    doc = _load_workflow()
    check = _find_step(doc, "check")

    for key in ("RAILWAY_TOKEN", "RAILWAY_ENV"):
        assert "steps.target.outputs.target" in check["env"][key]
        assert "github.ref_name" not in check["env"][key]
    # Both environments stay reachable — the token/env pair is selected, not fixed.
    assert "RAILWAY_TOKEN_PRODUCTION" in check["env"]["RAILWAY_TOKEN"]
    assert "RAILWAY_TOKEN_STAGING" in check["env"]["RAILWAY_TOKEN"]
    assert "'Production'" in check["env"]["RAILWAY_ENV"]
    assert "'Staging'" in check["env"]["RAILWAY_ENV"]


def test_check_step_gates_apply_flag_on_the_apply_input():
    doc = _load_workflow()
    check = _find_step(doc, "check")

    assert check["env"]["APPLY"] == "${{ inputs.apply }}"
    run = check["run"]
    # --apply is only ever passed via $FLAGS, set conditionally on $APPLY —
    # never appended to the command line unconditionally.
    assert 'if [ "${APPLY:-false}" = "true" ]; then' in run
    assert "check_pro_service_areas.py $FLAGS" in run
    assert "check_pro_service_areas.py --apply" not in run


def test_run_name_names_the_target_and_is_a_single_line():
    # The run-list line is the only place the operator sees the target before
    # the job runs. A folded scalar with more-indented continuation lines
    # keeps a newline inside the `${{ }}` expression (the PRO-183 lesson).
    run_name = _load_workflow()["run-name"]
    assert "\n" not in run_name
    assert "PRODUCTION" in run_name
    assert "staging" in run_name
    assert "github.ref_name" in run_name


def test_check_step_verdict_is_the_scripts_exit_code_via_pipestatus():
    doc = _load_workflow()
    run = _find_step(doc, "check")["run"]

    # Captured from PIPESTATUS[0] (the python process), not `$?` (which after
    # a pipe would be tee's own, always-zero, exit status).
    assert "code=${PIPESTATUS[0]}" in run
    assert "code=$?" not in run
    # The job's final action is exiting with that captured code.
    assert run.rstrip().endswith('exit "$code"')


# --- The exit-code verdict `case` block, extracted and run for real ---


def _extract_case_block(run_text):
    marker = 'case "$code" in'
    start = run_text.index(marker)
    end = run_text.index("esac", start) + len("esac")
    return run_text[start:end]


@pytest.fixture(scope="module")
def case_script(tmp_path_factory):
    run = _find_step(_load_workflow(), "check")["run"]
    block = _extract_case_block(run)
    script_path = tmp_path_factory.mktemp("check-case") / "case.sh"
    script_path.write_text(f'code="$1"\n{block}\n', encoding="utf-8")
    return script_path


@pytest.mark.parametrize(
    "code, expect_substring",
    [
        pytest.param("0", "✅ Every checked pro can be placed on the map.", id="ok"),
        pytest.param("1", "::error::Some pros have service areas", id="bad-areas"),
        pytest.param(
            "2", "::warning::The geocoder was unavailable", id="geocoder-down"
        ),
        pytest.param(
            "3",
            "::error::No pro matched the filter, so this run checked nothing",
            id="nothing-checked",
        ),
        pytest.param(
            "77", "::error::The check did not complete (exit 77)", id="unknown-code"
        ),
    ],
)
def test_exit_code_case_block_matches_the_scripts_contract(
    case_script, code, expect_substring
):
    proc = subprocess.run(
        ["bash", str(case_script), code],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert expect_substring in proc.stdout
