"""Coverage for `.github/workflows/backfill_pro_trades.yml` (PRO-234).

The same harness as its sibling `tests/test_check_pro_service_areas_workflow.py`:
the branch the workflow is run from is the target — `dev` writes staging,
`production` writes production, anything else refuses — and the shells of the
resolve, creds and backfill steps are extracted verbatim from the parsed YAML
and executed under real `bash -e`, so these tests run the real script.

What this pins, beyond the sibling's shape:

- the exit-code `case` block covers **exactly** the codes
  `scripts/backfill_pro_trades.py` can return, read off the script's own
  `EXIT_*` constants rather than copied into this file. The workflow is how an
  operator learns what a run meant, and a code the `case` block does not name
  is reported as "did not complete" — a green backfill read as a crash, or
  worse, the other way round,
- `--apply` reaches the script only through `$FLAGS`, so an unticked box
  cannot write. This workflow's apply path mutates every pro document, so a
  dry run that quietly wrote would be the worst failure it has.

Every `subprocess` call decodes as UTF-8 explicitly: these shells echo `✅` and
`🧰`, and under a cp1252 console the default locale decoding mangles them, which
fails the assertion for a reason that has nothing to do with the workflow.
"""

import ast
import os
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "backfill_pro_trades.yml"
SCRIPT_PATH = REPO_ROOT / "scripts" / "backfill_pro_trades.py"


def _load_workflow():
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _steps(doc):
    return doc["jobs"]["backfill-pro-trades"]["steps"]


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


def _bash(script_path, *args, cwd=None, env=None):
    return subprocess.run(
        ["bash", "-e", str(script_path), *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )


# --- Behavioural: the real "Resolve the target from the branch" shell ---


@pytest.fixture(scope="module")
def resolve_script(tmp_path_factory):
    step = _find_step(_load_workflow(), "target")
    script_path = tmp_path_factory.mktemp("backfill-resolve") / "resolve.sh"
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
    proc = _bash(resolve_script, env=env)
    return proc, _parse_github_output(output_path)


@pytest.mark.parametrize(
    "ref_name, target",
    [
        pytest.param("dev", "staging", id="dev-writes-staging"),
        pytest.param("production", "production", id="production-writes-production"),
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
        pytest.param("feat/pro-trades-explicit-list", id="feature-branch"),
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
    # Nothing is written for a refusal — the backfill step must have no target.
    assert "target" not in output


# --- Behavioural: the real "Verify the connection settings resolved" shell ---


@pytest.fixture(scope="module")
def creds_script(tmp_path_factory):
    step = _find_step(_load_workflow(), "creds")
    script_path = tmp_path_factory.mktemp("backfill-creds") / "creds.sh"
    script_path.write_text(step["run"], encoding="utf-8")
    return script_path


def _run_creds(creds_script, tmp_path, *, mongo_uri, target="staging"):
    env = dict(os.environ)
    if mongo_uri is None:
        env.pop("MONGO_URI", None)
    else:
        env["MONGO_URI"] = mongo_uri
    env["TARGET"] = target
    return _bash(creds_script, cwd=tmp_path, env=env)


def test_empty_mongo_uri_refuses_with_error_naming_the_secret_and_the_reason(
    creds_script, tmp_path
):
    proc = _run_creds(creds_script, tmp_path, mongo_uri="", target="staging")

    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "::error::" in proc.stdout
    # A repository secret vs. one scoped under Settings → Environments, which
    # resolves to empty in a job that declares no `environment:`.
    assert "repository" in proc.stdout
    assert "Environments" in proc.stdout
    # And why it refuses rather than proceeding: `Settings.MONGO_URI` defaults
    # to localhost, so a missing secret would report "no pro on record" against
    # an empty local database — indistinguishable from a backfill with nothing
    # left to do.
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
    # Only the database name may appear — never the host, never the credentials.
    username = "leakuser9k2"
    password = "SuperSecretPW77"
    host = "leakhost.privatecluster.net"
    db = "proli_trades_backfill"
    mongo_uri = f"mongodb+srv://{username}:{password}@{host}/{db}?retryWrites=true"

    proc = _run_creds(creds_script, tmp_path, mongo_uri=mongo_uri, target="staging")

    combined = proc.stdout + proc.stderr
    assert username not in combined
    assert password not in combined
    assert host not in combined
    assert db in combined


# --- Structural: the shape that keeps a dry run dry ---


def test_workflow_dispatch_has_exactly_one_boolean_checkbox_input():
    # One checkbox, defaulting to off: the read-only report is what an operator
    # gets by pressing Run without reading anything.
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
    step = _find_step(_load_workflow(), "creds")

    assert "${{" not in step["run"]
    assert "MONGO_URI" in step["env"]
    assert "TARGET" in step["env"]


def test_backfill_step_keys_mongo_and_redis_on_resolved_target_not_the_branch():
    backfill = _find_step(_load_workflow(), "backfill")

    for key in ("MONGO_URI", "REDIS_URL"):
        expr = backfill["env"][key]
        assert "steps.target.outputs.target" in expr
        assert "github.ref_name" not in expr


def test_backfill_step_gates_apply_flag_on_the_apply_input():
    backfill = _find_step(_load_workflow(), "backfill")

    assert backfill["env"]["APPLY"] == "${{ inputs.apply }}"
    run = backfill["run"]
    # `--apply` is only ever passed via $FLAGS, set conditionally on $APPLY —
    # never appended to the command line unconditionally. This workflow's apply
    # path rewrites every pro's `categories`, so an unticked box that wrote
    # anyway would be its worst failure.
    assert 'if [ "${APPLY:-false}" = "true" ]; then' in run
    assert 'CMD+=("$FLAGS")' in run
    assert 'if [ -n "$FLAGS" ]; then' in run
    assert "backfill_pro_trades.py --apply" not in run


def test_run_name_names_the_target_and_is_a_single_line():
    # The run-list line is the only place the operator sees the target before
    # the job runs. A folded scalar with more-indented continuation lines keeps
    # a newline inside the `${{ }}` expression (the PRO-183 lesson).
    run_name = _load_workflow()["run-name"]
    assert "\n" not in run_name
    assert "PRODUCTION" in run_name
    assert "staging" in run_name
    assert "github.ref_name" in run_name


def test_backfill_step_verdict_is_the_scripts_exit_code_via_pipestatus():
    run = _find_step(_load_workflow(), "backfill")["run"]

    # Captured from PIPESTATUS[0] (the python process), not `$?` (which after a
    # pipe would be tee's own, always-zero, exit status).
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


def _script_exit_codes():
    """Every `EXIT_* = <int>` the backfill script defines, as strings."""
    tree = ast.parse(SCRIPT_PATH.read_text(encoding="utf-8"))
    codes = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id.startswith("EXIT_"):
                codes[target.id] = str(node.value.value)
    return codes


@pytest.fixture(scope="module")
def case_script(tmp_path_factory):
    run = _find_step(_load_workflow(), "backfill")["run"]
    block = _extract_case_block(run)
    script_path = tmp_path_factory.mktemp("backfill-case") / "case.sh"
    script_path.write_text(f'code="$1"\n{block}\n', encoding="utf-8")
    return script_path


def test_the_case_block_names_every_code_the_script_can_return():
    # The pin that makes this file worth having: a new `EXIT_*` in the script
    # with no arm here would be reported to the operator as "did not complete".
    block = _extract_case_block(_find_step(_load_workflow(), "backfill")["run"])
    arms = {line.strip().rstrip(")") for line in block.splitlines() if ")" in line}

    for name, code in _script_exit_codes().items():
        assert code in arms, f"{name} ({code}) has no arm in the case block"
    # And the catch-all, so an unexpected code is never silently green.
    assert "*" in arms


@pytest.mark.parametrize(
    "code, expect_substring",
    [
        pytest.param(
            "0",
            "✅ Every handyman / general pro has an explicit trade list.",
            id="ok",
        ),
        pytest.param(
            "1", "::error::Some pros still carry no trade list", id="needs-backfill"
        ),
        pytest.param(
            "3",
            "::error::No pro matched the filter, so this run checked nothing",
            id="nothing-checked",
        ),
        pytest.param(
            "77", "::error::The backfill did not complete (exit 77)", id="unknown-code"
        ),
    ],
)
def test_exit_code_case_block_matches_the_scripts_contract(
    case_script, code, expect_substring
):
    proc = _bash(case_script, code)

    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert expect_substring in proc.stdout
