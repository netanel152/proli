"""Coverage for `.github/workflows/check_pro_service_areas.yml`.

Same shape as 🛑 Stop Railway Proli Services (`tests/test_stop_workflow.py`):
the branch it is run from is the target — `dev` checks staging, `production`
checks production, anything else refuses. This test file mirrors that
harness exactly — the resolve step's and the "Verify the connection settings
resolved" step's shells are extracted verbatim from the parsed YAML and
executed under real `bash -e`, so these tests run the real script.

`railway run` and the Railway CLI are gone: a project token created on
2026-09-10 was refused under both credential names through four rotations
while a four-month-old token in the same project kept working, so the
workflow no longer depends on Railway at all. The script now runs directly,
with `MONGO_URI`/`REDIS_URL` coming straight from repo secrets selected by
the resolved target. What's pinned here:

- the branch-to-target resolution (unchanged),
- the "Verify the connection settings resolved" step: an empty `MONGO_URI`
  refuses with a clear reason rather than silently defaulting to a local
  database, and the database name it prints never carries the host or
  credentials from the URI,
- structural pins that Railway is gone from the workflow entirely,
- the single `apply` checkbox, the exit-code `case` block, and `PIPESTATUS`
  (all unchanged from before the rework).
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


# --- Behavioural: run the real "Resolve the target from the branch" shell ---


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


# --- Behavioural: run the real "Verify the connection settings resolved"
# shell. This is the step that replaced the Railway credential fingerprint:
# it fails closed on a missing MONGO_URI (instead of Settings silently
# defaulting to localhost) and discloses only the database name. ---


@pytest.fixture(scope="module")
def creds_script(tmp_path_factory):
    step = _find_step(_load_workflow(), "creds")
    script_path = tmp_path_factory.mktemp("check-creds") / "creds.sh"
    script_path.write_text(step["run"], encoding="utf-8")
    return script_path


def _run_creds(creds_script, tmp_path, *, mongo_uri, target="staging"):
    env = dict(os.environ)
    if mongo_uri is None:
        env.pop("MONGO_URI", None)
    else:
        env["MONGO_URI"] = mongo_uri
    env["TARGET"] = target
    return subprocess.run(
        ["bash", "-e", str(creds_script)],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_empty_mongo_uri_refuses_with_error_naming_the_secret_and_the_reason(
    creds_script, tmp_path
):
    proc = _run_creds(creds_script, tmp_path, mongo_uri="", target="staging")

    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "::error::" in proc.stdout
    # The distinction that matters: a repository secret vs. one scoped under
    # Settings → Environments, which resolves to empty in a job that
    # declares no `environment:`.
    assert "repository" in proc.stdout
    assert "Environments" in proc.stdout
    # And *why* it refuses rather than proceeding: Settings.MONGO_URI
    # silently defaults to localhost, so without this guard a missing
    # secret would produce a false-clean "0 pros" result against an empty
    # local database, indistinguishable from a real clean run.
    assert "silently check an empty local database" in proc.stdout


def _extract_db_name(stdout):
    line = next(line for line in stdout.splitlines() if "database:" in line)
    return line.split("database:", 1)[1].strip()


@pytest.mark.parametrize(
    "mongo_uri, expected_db",
    [
        pytest.param(
            "mongodb+srv://dbuser:dbpass123@cluster0.abcde.mongodb.net/"
            "proli_prod?retryWrites=true&w=majority",
            "proli_prod",
            id="atlas-srv-uri",
        ),
        pytest.param(
            "mongodb://user:pass@localhost:27017/proli_test",
            "proli_test",
            id="plain-mongodb-uri",
        ),
        pytest.param(
            "mongodb://user:pass@localhost:27017",
            "(none named in the URI)",
            id="no-database-in-path",
        ),
    ],
)
def test_database_name_is_parsed_and_printed(
    creds_script, tmp_path, mongo_uri, expected_db
):
    proc = _run_creds(creds_script, tmp_path, mongo_uri=mongo_uri, target="production")

    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert _extract_db_name(proc.stdout) == expected_db


def test_output_never_leaks_the_username_password_or_host(creds_script, tmp_path):
    # Only the database name may appear — never the host, never the
    # credentials, from a URI carrying distinctive values for both.
    username = "leakuser9k2"
    password = "SuperSecretPW77"
    host = "leakhost.privatecluster.net"
    db = "proli_areas_check"
    mongo_uri = f"mongodb+srv://{username}:{password}@{host}/{db}?retryWrites=true"

    proc = _run_creds(creds_script, tmp_path, mongo_uri=mongo_uri, target="staging")

    combined = proc.stdout + proc.stderr
    assert username not in combined
    assert password not in combined
    assert host not in combined
    assert db in combined


# --- Structural: Railway is gone ---


def test_no_step_named_or_containing_railway():
    doc = _load_workflow()
    for step in _steps(doc):
        name = step.get("name") or ""
        assert re.search(r"\brailway\b", name, re.IGNORECASE) is None, name


def test_no_step_declares_or_reads_a_railway_token():
    # The credential itself is gone, not just the CLI. Checked as *active
    # use* — an env key, or a `$RAILWAY_TOKEN`/`${RAILWAY_TOKEN}` shell
    # reference — rather than a bare substring search, because the "Verify
    # the connection settings resolved" step's comment legitimately quotes
    # the historical "Invalid RAILWAY_TOKEN" refusal message as the reason
    # Railway was dropped; that prose isn't a dependency.
    doc = _load_workflow()
    token_pattern = re.compile(r"\$\{?RAILWAY_(API_)?TOKEN\b")

    for step in _steps(doc):
        env = step.get("env") or {}
        for key, value in env.items():
            assert key not in ("RAILWAY_TOKEN", "RAILWAY_API_TOKEN"), step.get("name")
            assert token_pattern.search(str(value)) is None, step.get("name")
        run = step.get("run") or ""
        assert token_pattern.search(run) is None, step.get("name")


def test_check_step_keys_mongo_and_redis_on_resolved_target_not_the_branch():
    doc = _load_workflow()
    check = _find_step(doc, "check")

    for key in ("MONGO_URI", "REDIS_URL"):
        expr = check["env"][key]
        assert "steps.target.outputs.target" in expr
        assert "github.ref_name" not in expr


# --- Structural: pin the rest of the shape ---


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


def test_creds_step_reaches_the_shell_through_env_not_expressions():
    doc = _load_workflow()
    step = _find_step(doc, "creds")

    assert "${{" not in step["run"]
    assert "MONGO_URI" in step["env"]
    assert "TARGET" in step["env"]


def test_check_step_gates_apply_flag_on_the_apply_input():
    doc = _load_workflow()
    check = _find_step(doc, "check")

    assert check["env"]["APPLY"] == "${{ inputs.apply }}"
    run = check["run"]
    # --apply is only ever passed via $FLAGS, set conditionally on $APPLY —
    # never appended to the command line unconditionally.
    assert 'if [ "${APPLY:-false}" = "true" ]; then' in run
    assert 'CMD+=("$FLAGS")' in run
    assert 'if [ -n "$FLAGS" ]; then' in run
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
        ["bash", "-e", str(case_script), code],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert expect_substring in proc.stdout
