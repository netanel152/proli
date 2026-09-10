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
import re
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
        ["bash", "-e", str(resolve_script)],
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


# --- Behavioural: run the real "Fingerprint the Railway credential" shell.
# It never invokes the `railway` binary, so no stub is needed. ---


def _find_step_by_name(doc, name):
    for step in _steps(doc):
        if step.get("name") == name:
            return step
    raise AssertionError(f"no step named {name!r} in {WORKFLOW_PATH}")


VERIFY_STEP_NAME = "Fingerprint the Railway credential"

# What a Railway project token looks like — used both as a "well-formed"
# fixture and as the source for the trailing-4-characters comparison.
UUID_TOKEN = "550e8400-e29b-41d4-a716-446655440000"


@pytest.fixture(scope="module")
def verify_script(tmp_path_factory):
    step = _find_step_by_name(_load_workflow(), VERIFY_STEP_NAME)
    script_path = tmp_path_factory.mktemp("check-verify") / "verify.sh"
    script_path.write_text(step["run"], encoding="utf-8")
    return script_path


def _run_verify(verify_script, tmp_path, *, token, target):
    env = dict(os.environ)
    env["RAILWAY_TOKEN"] = token
    env["TARGET"] = target
    return subprocess.run(
        ["bash", "-e", str(verify_script)],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_empty_token_fails_with_error(verify_script, tmp_path):
    proc = _run_verify(verify_script, tmp_path, token="", target="staging")

    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "::error::" in proc.stdout
    # The distinction that cost two rounds of guessing: repo secret vs. an
    # Environments-scoped secret, which resolves to empty in a job with no
    # `environment:`.
    assert "repository" in proc.stdout
    assert "Environments" in proc.stdout


def test_well_formed_uuid_reports_full_fingerprint(verify_script, tmp_path):
    proc = _run_verify(verify_script, tmp_path, token=UUID_TOKEN, target="production")

    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "length:      36 characters" in proc.stdout
    assert f"last 4:      ...{UUID_TOKEN[-4:]}" in proc.stdout
    assert "whitespace:  none" in proc.stdout
    assert "a well-formed UUID" in proc.stdout


def test_trailing_newline_reports_whitespace_and_the_real_last_four(
    verify_script, tmp_path
):
    # A trailing newline used to push a line break into the middle of the
    # comparison the "last 4" line exists to serve — pin that the printed
    # characters come from the trimmed value, not the raw one.
    token = UUID_TOKEN + "\n"

    proc = _run_verify(verify_script, tmp_path, token=token, target="staging")

    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "whitespace:  YES" in proc.stdout
    assert "1 whitespace character(s)" in proc.stdout
    assert f"last 4:      ...{UUID_TOKEN[-4:]}" in proc.stdout


def test_non_uuid_value_reports_shape_mismatch(verify_script, tmp_path):
    proc = _run_verify(
        verify_script, tmp_path, token="not-a-railway-token", target="production"
    )

    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "NOT a UUID" in proc.stdout


def test_disclosure_is_bounded_to_exactly_the_last_four_characters(
    verify_script, tmp_path
):
    # The property changed on purpose: the last 4 characters are now printed
    # deliberately (Railway's own tokens page shows the same "****-3bc1"
    # slice). What must stay pinned is that the disclosure never grows past
    # that — no 5-character suffix, no 8-character prefix, and never the
    # full value.
    sentinel = "PROJTOK-9f3ac7d2-DO-NOT-LEAK-b1e2"

    proc = _run_verify(verify_script, tmp_path, token=sentinel, target="production")

    combined = proc.stdout + proc.stderr
    assert sentinel not in combined
    assert sentinel[-4:] in combined  # "b1e2" — the deliberate disclosure
    assert sentinel[-5:] not in combined  # "-b1e2"
    assert sentinel[:8] not in combined  # "PROJTOK-"


def test_verify_step_never_invokes_railway():
    # The whole point of narrowing this step: `railway whoami` is
    # account-scoped, and a valid *project* token (what these secrets hold)
    # has no user identity behind it, so it can fail the probe while the
    # credential is fine. If a probe creeps back in here, read the comment
    # above the step in the workflow before re-adding it.
    doc = _load_workflow()
    step = _find_step_by_name(doc, VERIFY_STEP_NAME)

    assert re.search(r"\brailway\b", step["run"]) is None


def test_check_step_does_not_pass_an_environment_flag_to_railway():
    # A Railway project token is bound to one environment already, so the
    # environment is pinned by the credential rather than by a flag that has
    # to agree with it. Passing --environment as well made the CLI resolve an
    # environment name at project level, which a newly-created
    # environment-scoped token is not permitted to do: every staging run on
    # 2026-09-10 failed on "Invalid RAILWAY_TOKEN" holding a token the
    # fingerprint step had already proved correct. Re-adding the flag brings
    # that back, so read the comment above the command first.
    doc = _load_workflow()
    step = _find_step(doc, "check")
    run = step["run"]

    # The command lives in the CMD array now, built once and reused for the
    # RAILWAY_API_TOKEN retry.
    cmd_line = next(
        line for line in run.splitlines() if line.strip().startswith("CMD=(railway run")
    )
    assert "--environment" not in cmd_line
    # The resolved target still selects the token and names the environment
    # in the log line; only the flag is gone.
    assert "$RAILWAY_ENV" in run


# --- Behavioural: run the real "check" step's shell with a stubbed `railway`
# binary on PATH. This step is what fell back to RAILWAY_API_TOKEN on
# 2026-09-10 — a stub is needed here (unlike the fingerprint step above)
# because the fallback logic only exists inside this step's script, driven by
# the real command's exit code and output. ---

# Records one line per invocation (so a test can assert "called once" /
# "called twice" without caring about ordering) and one line per invocation
# recording exactly which credential name carried a value, so the retry can
# be proven to pass the *same* token under the new name rather than a new one.
RAILWAY_STUB = """#!/usr/bin/env bash
set -u
: "${STUB_COUNTER_FILE:?}"
: "${STUB_RECEIVED_TOKEN_FILE:?}"

printf 'x\\n' >> "$STUB_COUNTER_FILE"

if [ -n "${RAILWAY_API_TOKEN:-}" ]; then
  printf 'API:%s\\n' "$RAILWAY_API_TOKEN" >> "$STUB_RECEIVED_TOKEN_FILE"
else
  printf 'TOKEN:%s\\n' "${RAILWAY_TOKEN:-}" >> "$STUB_RECEIVED_TOKEN_FILE"
fi

REFUSAL="Error: Invalid RAILWAY_TOKEN, or your token does not have access to the resource you are trying to use."

case "${STUB_MODE:-succeed}" in
  succeed)
    echo "Checking pro service areas..."
    echo "5 pros checked, all placed on the map."
    exit 0
    ;;
  real_finding)
    echo "Checking pro service areas..."
    echo "No pro matched the filter."
    exit 3
    ;;
  refuse_until_api)
    if [ -n "${RAILWAY_API_TOKEN:-}" ]; then
      echo "Checking pro service areas..."
      echo "5 pros checked, all placed on the map."
      exit 0
    fi
    echo "$REFUSAL" >&2
    exit 1
    ;;
  always_refuse)
    echo "$REFUSAL" >&2
    exit 1
    ;;
  *)
    echo "unknown STUB_MODE: ${STUB_MODE:-}" >&2
    exit 99
    ;;
esac
"""


@pytest.fixture(scope="module")
def check_script(tmp_path_factory):
    step = _find_step(_load_workflow(), "check")
    script_path = tmp_path_factory.mktemp("check-run") / "check.sh"
    script_path.write_text(step["run"], encoding="utf-8")
    return script_path


def _run_check(check_script, tmp_path, *, stub_mode, token=UUID_TOKEN, apply_="false"):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub_path = bin_dir / "railway"
    stub_path.write_text(RAILWAY_STUB, encoding="utf-8")
    stub_path.chmod(0o755)

    counter_file = tmp_path / "invocation_count"
    counter_file.write_text("", encoding="utf-8")
    received_file = tmp_path / "received_tokens"
    received_file.write_text("", encoding="utf-8")
    summary_file = tmp_path / "step_summary.md"
    summary_file.write_text("", encoding="utf-8")

    workdir = tmp_path / "work"
    workdir.mkdir()

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["RAILWAY_TOKEN"] = token
    env.pop("RAILWAY_API_TOKEN", None)
    env["RAILWAY_ENV"] = "Staging"
    env["APPLY"] = apply_
    env["GITHUB_STEP_SUMMARY"] = str(summary_file)
    env["STUB_MODE"] = stub_mode
    env["STUB_COUNTER_FILE"] = str(counter_file)
    env["STUB_RECEIVED_TOKEN_FILE"] = str(received_file)

    proc = subprocess.run(
        ["bash", "-e", str(check_script)],
        env=env,
        cwd=workdir,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return (
        proc,
        counter_file.read_text(encoding="utf-8"),
        received_file.read_text(encoding="utf-8"),
    )


def test_check_step_first_credential_name_succeeds_without_retry(
    check_script, tmp_path
):
    proc, counter, received = _run_check(check_script, tmp_path, stub_mode="succeed")

    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert len(counter.strip().splitlines()) == 1
    assert "::warning::" not in proc.stdout
    assert received.strip() == f"TOKEN:{UUID_TOKEN}"


def test_check_step_real_finding_is_not_mistaken_for_an_auth_failure(
    check_script, tmp_path
):
    # This is the case an exit-code-based judgement would have got wrong:
    # the script's own exit 3 ("no pro matched the filter") looks like a
    # failure but is not a credential problem, so no retry may happen.
    proc, counter, received = _run_check(
        check_script, tmp_path, stub_mode="real_finding"
    )

    assert proc.returncode == 3, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert len(counter.strip().splitlines()) == 1
    assert "::warning::" not in proc.stdout
    assert (
        "::error::No pro matched the filter, so this run checked nothing" in proc.stdout
    )


def test_check_step_retries_under_railway_api_token_and_succeeds(
    check_script, tmp_path
):
    proc, counter, received = _run_check(
        check_script, tmp_path, stub_mode="refuse_until_api"
    )

    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert len(counter.strip().splitlines()) == 2
    assert "::warning::" in proc.stdout
    assert "RAILWAY_API_TOKEN" in proc.stdout
    assert "::notice::" in proc.stdout
    # The notice also flags the sibling workflow that needs the same fix.
    assert "Stop Railway Proli Services" in proc.stdout


def test_check_step_both_names_refused_reports_auth_failure_before_case_block(
    check_script, tmp_path
):
    proc, counter, received = _run_check(
        check_script, tmp_path, stub_mode="always_refuse"
    )

    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert len(counter.strip().splitlines()) == 2
    assert "::error::Railway refused the credential under both names" in proc.stdout
    # The exit-code `case` block's geocoder-findings language must never
    # appear for an auth failure — that would misreport pros that were never
    # checked as having been checked.
    assert "No pro matched the filter" not in proc.stdout
    assert "Every checked pro can be placed on the map" not in proc.stdout
    assert "service areas the geocoder does not know" not in proc.stdout


def test_check_step_retry_passes_the_identical_token_value(check_script, tmp_path):
    token = "PROJTOK-9f3ac7d2-abcd-b1e2"

    proc, counter, received = _run_check(
        check_script, tmp_path, stub_mode="refuse_until_api", token=token
    )

    lines = received.strip().splitlines()
    assert lines[0] == f"TOKEN:{token}"
    assert lines[1] == f"API:{token}"


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
    # $FLAGS is appended to the CMD array only when non-empty, so --apply
    # can never reach the command line unconditionally.
    assert 'CMD+=("$FLAGS")' in run
    assert 'if [ -n "$FLAGS" ]; then' in run
    assert "check_pro_service_areas.py --apply" not in run


def test_check_step_builds_the_command_once_and_reuses_it_for_the_retry():
    # The original call and the RAILWAY_API_TOKEN retry must run the exact
    # same command — built once into CMD, not two call sites that could drift
    # apart (e.g. one gaining --apply and the other not).
    doc = _load_workflow()
    run = _find_step(doc, "check")["run"]

    assert run.count("CMD=(") == 1
    assert run.count('"${CMD[@]}"') == 2


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
        ["bash", "-e", str(case_script), code],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert expect_substring in proc.stdout
