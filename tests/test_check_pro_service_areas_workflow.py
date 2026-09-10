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


# --- Behavioural: run the real "Verify the Railway credential resolves and
# works" shell, with a stubbed `railway` on PATH ---


def _find_step_by_name(doc, name):
    for step in _steps(doc):
        if step.get("name") == name:
            return step
    raise AssertionError(f"no step named {name!r} in {WORKFLOW_PATH}")


VERIFY_STEP_NAME = "Verify the Railway credential resolves and works"


@pytest.fixture(scope="module")
def verify_script(tmp_path_factory):
    step = _find_step_by_name(_load_workflow(), VERIFY_STEP_NAME)
    script_path = tmp_path_factory.mktemp("check-verify") / "verify.sh"
    script_path.write_text(step["run"], encoding="utf-8")
    return script_path


def _make_railway_stub(bin_dir, exit_code, marker_path=None):
    """A fake `railway` binary: `whoami` exits `exit_code`, optionally
    touching `marker_path` first so a test can prove it was (or wasn't)
    invoked at all."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "railway"
    marker_line = f'touch "{marker_path}"\n' if marker_path is not None else ""
    stub.write_text(
        "#!/bin/bash\n"
        f"{marker_line}"
        'if [ "$1" = "whoami" ]; then\n'
        "  echo 'stub whoami output'\n"
        f"  exit {exit_code}\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return stub


def _run_verify(verify_script, tmp_path, *, token, target, bin_dir=None):
    env = dict(os.environ)
    env["RAILWAY_TOKEN"] = token
    env["TARGET"] = target
    if bin_dir is not None:
        env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    return subprocess.run(
        ["bash", str(verify_script)],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_empty_token_fails_before_invoking_railway(verify_script, tmp_path):
    bin_dir = tmp_path / "bin"
    marker = tmp_path / "railway_was_called"
    _make_railway_stub(bin_dir, exit_code=0, marker_path=marker)

    proc = _run_verify(
        verify_script, tmp_path, token="", target="staging", bin_dir=bin_dir
    )

    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "::error::" in proc.stdout
    # The distinction that cost two rounds of guessing: repo secret vs. an
    # Environments-scoped secret, which resolves to empty in a job with no
    # `environment:`.
    assert "repository" in proc.stdout
    assert "Environments" in proc.stdout
    assert not marker.exists(), "an empty token must never reach `railway`"


def test_working_token_reports_character_count_and_succeeds(verify_script, tmp_path):
    token = "sk-test-token-1234567890"
    bin_dir = tmp_path / "bin"
    _make_railway_stub(bin_dir, exit_code=0)

    proc = _run_verify(
        verify_script, tmp_path, token=token, target="production", bin_dir=bin_dir
    )

    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert f"({len(token)} characters)" in proc.stdout


def test_rejected_token_fails_with_error(verify_script, tmp_path):
    token = "sk-wrong-token"
    bin_dir = tmp_path / "bin"
    _make_railway_stub(bin_dir, exit_code=1)

    proc = _run_verify(
        verify_script, tmp_path, token=token, target="staging", bin_dir=bin_dir
    )

    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "::error::" in proc.stdout
    assert "staging" in proc.stdout


@pytest.mark.parametrize(
    "exit_code",
    [pytest.param(0, id="railway-accepts"), pytest.param(1, id="railway-rejects")],
)
def test_token_value_never_appears_in_output(verify_script, tmp_path, exit_code):
    # The assertion that matters most: a diagnostic that leaks the credential
    # is worse than the opaque line it replaces. True whether the stub
    # accepts or rejects the token.
    sentinel = "SENTINEL-RAILWAY-TOKEN-do-not-print-9f3ac7"
    bin_dir = tmp_path / f"bin-{exit_code}"
    _make_railway_stub(bin_dir, exit_code=exit_code)

    proc = _run_verify(
        verify_script, tmp_path, token=sentinel, target="production", bin_dir=bin_dir
    )

    combined = proc.stdout + proc.stderr
    assert sentinel not in combined


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


def test_verify_step_sits_between_install_cli_and_check():
    doc = _load_workflow()
    steps = _steps(doc)
    names = [s.get("name") for s in steps]
    ids = [s.get("id") for s in steps]

    install_idx = names.index("Install Railway CLI")
    verify_idx = names.index(VERIFY_STEP_NAME)
    check_idx = ids.index("check")

    assert install_idx < verify_idx < check_idx


def test_verify_step_keys_token_on_resolved_target_not_the_branch():
    doc = _load_workflow()
    step = _find_step_by_name(doc, VERIFY_STEP_NAME)

    token_expr = step["env"]["RAILWAY_TOKEN"]
    assert "steps.target.outputs.target" in token_expr
    assert "github.ref_name" not in token_expr
    assert "RAILWAY_TOKEN_PRODUCTION" in token_expr
    assert "RAILWAY_TOKEN_STAGING" in token_expr


def test_verify_step_reaches_the_shell_through_env_not_expressions():
    doc = _load_workflow()
    step = _find_step_by_name(doc, VERIFY_STEP_NAME)

    assert "${{" not in step["run"]
    assert "RAILWAY_TOKEN" in step["env"]
    assert "TARGET" in step["env"]


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
