"""Coverage for `.github/workflows/prepare_staging_e2e.yml`.

Staging-only, so unlike its siblings (`dev` → staging, `production` → production)
this workflow has **no** production arm: `dev` resolves to staging, `production`
is refused by name, anything else is refused. The shells of the resolve, creds and
prepare steps are extracted verbatim from the parsed YAML and run under real
`bash -e`, the way `tests/test_backfill_pro_trades_workflow.py` does.

What this pins on top of the sibling harness:

- the file never names a production secret, so a typo cannot point the job there,
- both STAGING secrets are required (this run clears Redis state as well as Mongo),
- the handsets come from the STAGING_TEST_* repository secrets (this repo is
  public, so its inputs panel is world readable); the phone inputs are optional
  overrides, and a run with neither set fails with a message naming both,
- the four free-form inputs reach the script through `env:` and a quoted
  `--flag=value`, never through `${{ }}` inside the shell (a city is user text),
- `--apply` only ever arrives through `$FLAGS`, so an unticked box cannot write,
- the `trade` choices are exactly the trades the script accepts,
- the exit-code `case` names every `EXIT_*` the script can return.

Every `subprocess` call decodes as UTF-8 explicitly: these shells echo `✅`.
"""

import ast
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from app.core.pro_trades import LISTABLE_TRADES

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "prepare_staging_e2e.yml"
SCRIPT_PATH = REPO_ROOT / "scripts" / "prepare_staging_e2e.py"


# An input override wins; otherwise the repository secret.
PHONE_EXPR = {
    "CUSTOMER_PHONE": "${{ inputs.customer_phone || secrets.STAGING_TEST_CUSTOMER_PHONE }}",
    "PRO_PHONE": "${{ inputs.pro_phone || secrets.STAGING_TEST_PRO_PHONE }}",
}


def _load_workflow():
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _find_step(doc, step_id):
    for step in doc["jobs"]["prepare-staging-e2e"]["steps"]:
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


def _step_script(tmp_path_factory, step_id, name):
    step = _find_step(_load_workflow(), step_id)
    path = tmp_path_factory.mktemp(name) / f"{name}.sh"
    path.write_text(step["run"], encoding="utf-8")
    return path


# --- Behavioural: the real "Resolve the target from the branch" shell ---


@pytest.fixture(scope="module")
def resolve_script(tmp_path_factory):
    return _step_script(tmp_path_factory, "target", "resolve")


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


def test_dev_is_staging(resolve_script, tmp_path):
    proc, output = _run_resolve(resolve_script, tmp_path, "dev")

    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert output.get("target") == "staging"
    assert "::error::" not in proc.stdout


def test_production_is_refused_by_name(resolve_script, tmp_path):
    proc, output = _run_resolve(resolve_script, tmp_path, "production")

    assert proc.returncode != 0
    assert "staging-only" in proc.stdout
    assert "target" not in output


@pytest.mark.parametrize(
    "ref_name",
    [
        pytest.param("feat/e2e-data", id="feature-branch"),
        pytest.param(None, id="unset-ref-name"),
        pytest.param("", id="empty-ref-name"),
        pytest.param(" dev ", id="whitespace-is-not-forgiven"),
        pytest.param("dev-hotfix", id="prefix-match-is-not-forgiven"),
        pytest.param("master", id="the-old-default-branch-name"),
    ],
)
def test_any_other_branch_refuses(resolve_script, tmp_path, ref_name):
    proc, output = _run_resolve(resolve_script, tmp_path, ref_name)

    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "::error::" in proc.stdout
    assert "target" not in output


# --- Behavioural: the real "Verify the connection settings resolved" shell ---


@pytest.fixture(scope="module")
def creds_script(tmp_path_factory):
    return _step_script(tmp_path_factory, "creds", "creds")


def _run_creds(creds_script, tmp_path, *, mongo_uri, redis_url):
    env = dict(os.environ)
    for key, value in (("MONGO_URI", mongo_uri), ("REDIS_URL", redis_url)):
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    env["TARGET"] = "staging"
    return _bash(creds_script, cwd=tmp_path, env=env)


@pytest.mark.parametrize(
    "mongo_uri, redis_url",
    [
        pytest.param("", "redis://r.example:6379/0", id="no-mongo"),
        pytest.param("mongodb://u:p@h:27017/proli_staging_db", "", id="no-redis"),
        pytest.param("", "", id="neither"),
        pytest.param(None, None, id="both-unset"),
    ],
)
def test_a_missing_secret_refuses_and_names_the_repository_secret_rule(
    creds_script, tmp_path, mongo_uri, redis_url
):
    proc = _run_creds(creds_script, tmp_path, mongo_uri=mongo_uri, redis_url=redis_url)

    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "::error::" in proc.stdout
    assert "STAGING_MONGO_URI" in proc.stdout and "STAGING_REDIS_URL" in proc.stdout
    assert "repository" in proc.stdout and "Environments" in proc.stdout


def test_output_names_the_database_and_never_the_credentials_or_host(
    creds_script, tmp_path
):
    username, password = "leakuser9k2", "SuperSecretPW77"
    host, redis_host = "leakhost.privatecluster.net", "leakredis.private.net"
    proc = _run_creds(
        creds_script,
        tmp_path,
        mongo_uri=f"mongodb+srv://{username}:{password}@{host}/proli_staging_db?retryWrites=true",
        redis_url=f"redis://:{password}@{redis_host}:6379/0",
    )

    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0, combined
    assert "database:    proli_staging_db" in combined
    for secret in (username, password, host, redis_host):
        assert secret not in combined


# --- Structural: staging only, and a dry run that stays dry ---


def test_the_file_never_names_a_production_secret():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "PRODUCTION_" not in text
    assert "RAILWAY_TOKEN_PRODUCTION" not in text
    secrets_used = set(re.findall(r"secrets\.([A-Z0-9_]+)", text))
    assert secrets_used == {
        "STAGING_MONGO_URI",
        "STAGING_REDIS_URL",
        "GOOGLE_MAPS_API_KEY",
        "STAGING_TEST_CUSTOMER_PHONE",
        "STAGING_TEST_PRO_PHONE",
    }


def test_the_mongo_and_redis_secrets_are_the_staging_ones_in_every_step():
    doc = _load_workflow()
    for step_id in ("creds", "prepare"):
        env = _find_step(doc, step_id)["env"]
        assert env["MONGO_URI"] == "${{ secrets.STAGING_MONGO_URI }}"
        assert env["REDIS_URL"] == "${{ secrets.STAGING_REDIS_URL }}"


def test_the_inputs_are_exactly_these_and_apply_defaults_to_off():
    inputs = _load_workflow()[True]["workflow_dispatch"]["inputs"]
    assert list(inputs) == ["customer_phone", "pro_phone", "city", "trade", "apply"]
    assert inputs["apply"]["type"] == "boolean"
    assert inputs["apply"]["default"] is False
    for name in ("city", "trade"):
        assert inputs[name]["required"] is True
    # The handsets are optional overrides: the secrets are the normal path.
    for name in ("customer_phone", "pro_phone"):
        assert inputs[name]["required"] is False
        assert inputs[name].get("default", "") == ""
        description = inputs[name]["description"]
        assert "public" in description.lower()
        assert "WARNING" in description
        assert "secret" in description


def test_the_trade_choices_are_exactly_the_trades_the_script_accepts():
    trade = _load_workflow()[True]["workflow_dispatch"]["inputs"]["trade"]
    assert trade["type"] == "choice"
    assert tuple(trade["options"]) == LISTABLE_TRADES
    assert trade["default"] in LISTABLE_TRADES


def test_branch_and_secrets_reach_the_shell_through_env_not_expressions():
    doc = _load_workflow()
    assert _find_step(doc, "target")["env"]["REF_NAME"] == "${{ github.ref_name }}"
    for step_id in ("mask", "phones", "target", "creds", "prepare"):
        assert "${{" not in _find_step(doc, step_id)["run"]


def test_every_free_form_input_goes_through_env_and_a_quoted_flag():
    step = _find_step(_load_workflow(), "prepare")
    env, run = step["env"], step["run"]

    for name, var in (
        ("customer_phone", "CUSTOMER_PHONE"),
        ("pro_phone", "PRO_PHONE"),
        ("city", "CITY"),
        ("trade", "TRADE"),
    ):
        expected = PHONE_EXPR.get(var, f"${{{{ inputs.{name} }}}}")
        assert env[var] == expected
        # `--flag="$VAR"` (one token), so a value that starts with `--` is data.
        assert f'--{name.replace("_", "-")}="${var}"' in run


def test_apply_reaches_the_script_only_through_the_flags_variable():
    step = _find_step(_load_workflow(), "prepare")
    run = step["run"]

    assert step["env"]["APPLY"] == "${{ inputs.apply }}"
    assert 'if [ "${APPLY:-false}" = "true" ]; then' in run
    assert 'CMD+=("$FLAGS")' in run
    assert 'if [ -n "$FLAGS" ]; then' in run
    assert "prepare_staging_e2e.py --apply" not in run
    assert re.search(r"--apply(?!\s*\()", run.replace('FLAGS="--apply"', "")) is None


def test_the_script_runs_with_a_non_production_environment_and_no_real_keys():
    env = _find_step(_load_workflow(), "prepare")["env"]
    assert env["ENVIRONMENT"] == "development"
    for key in (
        "GEMINI_API_KEY",
        "CLOUDINARY_CLOUD_NAME",
        "CLOUDINARY_API_KEY",
        "CLOUDINARY_API_SECRET",
    ):
        assert env[key] == "unused-by-this-script"


def test_run_name_is_a_single_line_that_says_staging_only():
    run_name = _load_workflow()["run-name"]
    assert "\n" not in run_name
    assert "github.ref_name" in run_name
    assert "staging only" in run_name
    assert "PRODUCTION" not in run_name


def test_the_verdict_is_the_scripts_exit_code_via_pipestatus():
    run = _find_step(_load_workflow(), "prepare")["run"]

    assert "code=${PIPESTATUS[0]}" in run
    assert "code=$?" not in run
    assert run.rstrip().endswith('exit "$code"')


# --- The exit-code verdict `case` block, extracted and run for real ---


def _extract_case_block(run_text):
    marker = 'case "$code" in'
    start = run_text.index(marker)
    end = run_text.index("esac", start) + len("esac")
    return run_text[start:end]


def _script_exit_codes():
    tree = ast.parse(SCRIPT_PATH.read_text(encoding="utf-8"))
    codes = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.startswith("EXIT_"):
                    codes[target.id] = str(node.value.value)
    return codes


@pytest.fixture(scope="module")
def case_script(tmp_path_factory):
    block = _extract_case_block(_find_step(_load_workflow(), "prepare")["run"])
    path = tmp_path_factory.mktemp("case") / "case.sh"
    path.write_text(f'code="$1"\n{block}\n', encoding="utf-8")
    return path


def test_the_case_block_names_every_code_the_script_can_return():
    block = _extract_case_block(_find_step(_load_workflow(), "prepare")["run"])
    arms = {line.strip().rstrip(")") for line in block.splitlines() if ")" in line}

    codes = _script_exit_codes()
    assert set(codes) == {
        "EXIT_OK",
        "EXIT_CITY_UNRESOLVED",
        "EXIT_REFUSED",
        "EXIT_INCOMPLETE",
    }
    for name, code in codes.items():
        assert code in arms, f"{name} ({code}) has no arm in the case block"
    assert "*" in arms


@pytest.mark.parametrize(
    "code, expect_substring",
    [
        pytest.param("0", "✅ Staging is ready", id="ok"),
        pytest.param("2", "::error::The city could not be placed", id="city"),
        pytest.param("3", "::error::The script refused", id="refused"),
        pytest.param("4", "::error::The apply stopped part-way", id="incomplete"),
        pytest.param(
            "77", "::error::The preparation did not complete (exit 77)", id="unknown"
        ),
    ],
)
def test_exit_code_case_block_matches_the_scripts_contract(
    case_script, code, expect_substring
):
    proc = _bash(case_script, code)

    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert expect_substring in proc.stdout


# --- Review findings: masking, and an early refusal of a non-staging database ---


def test_the_handset_numbers_are_masked_by_the_first_step():
    steps = _load_workflow()["jobs"]["prepare-staging-e2e"]["steps"]
    first = steps[0]

    assert first["id"] == "mask"
    assert first["env"]["CUSTOMER_PHONE"] == PHONE_EXPR["CUSTOMER_PHONE"]
    assert first["env"]["PRO_PHONE"] == PHONE_EXPR["PRO_PHONE"]
    assert '"::add-mask::$CUSTOMER_PHONE"' in first["run"]
    assert '"::add-mask::$PRO_PHONE"' in first["run"]


def test_the_mask_step_ends_green_even_with_an_empty_input(tmp_path_factory, tmp_path):
    """A trailing `[ -n … ] && echo` would exit 1 on an empty value and fail the job."""
    script = _step_script(tmp_path_factory, "mask", "mask")
    env = {
        k: v for k, v in os.environ.items() if k not in ("CUSTOMER_PHONE", "PRO_PHONE")
    }
    env["CUSTOMER_PHONE"] = ""
    env["PRO_PHONE"] = "0507654321"

    proc = _bash(script, cwd=tmp_path, env=env)

    assert proc.returncode == 0, proc.stderr
    assert "::add-mask::0507654321" in proc.stdout
    assert proc.stdout.count("::add-mask::") == 1


@pytest.mark.parametrize(
    "db", ["proli_db", "proli_prod", "proli_production_db", "proli_staging_db2"]
)
def test_the_creds_step_refuses_a_database_that_is_not_the_staging_one(
    creds_script, tmp_path, db
):
    proc = _run_creds(
        creds_script,
        tmp_path,
        mongo_uri=f"mongodb://u:p@h.example:27017/{db}",
        redis_url="redis://r.example:6379/0",
    )

    assert proc.returncode != 0, proc.stdout
    assert "::error::" in proc.stdout
    assert "proli_staging_db" in proc.stdout
    assert "u:p" not in proc.stdout + proc.stderr


def test_a_uri_with_no_database_is_refused_too(creds_script, tmp_path):
    proc = _run_creds(
        creds_script,
        tmp_path,
        mongo_uri="mongodb://u:p@h.example:27017",
        redis_url="redis://r.example:6379/0",
    )
    assert proc.returncode != 0


# --- The handsets come from secrets, with the inputs as optional overrides ---


def test_the_masking_and_the_require_step_run_before_anything_else():
    steps = _load_workflow()["jobs"]["prepare-staging-e2e"]["steps"]
    assert [s.get("id") for s in steps[:2]] == ["mask", "phones"]


def test_the_phones_are_resolved_the_same_way_in_every_step_that_reads_them():
    doc = _load_workflow()
    for step_id in ("mask", "phones", "prepare"):
        env = _find_step(doc, step_id)["env"]
        assert env["CUSTOMER_PHONE"] == PHONE_EXPR["CUSTOMER_PHONE"]
        assert env["PRO_PHONE"] == PHONE_EXPR["PRO_PHONE"]


@pytest.fixture(scope="module")
def phones_script(tmp_path_factory):
    return _step_script(tmp_path_factory, "phones", "phones")


def _run_phones(phones_script, tmp_path, *, customer, pro):
    env = {
        k: v for k, v in os.environ.items() if k not in ("CUSTOMER_PHONE", "PRO_PHONE")
    }
    for key, value in (("CUSTOMER_PHONE", customer), ("PRO_PHONE", pro)):
        if value is not None:
            env[key] = value
    return _bash(phones_script, cwd=tmp_path, env=env)


def test_both_phones_set_passes(phones_script, tmp_path):
    proc = _run_phones(phones_script, tmp_path, customer="0501234567", pro="0507654321")

    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "::error::" not in proc.stdout


@pytest.mark.parametrize(
    "customer, pro, named, not_named",
    [
        pytest.param("", "0507654321", "CUSTOMER", "PRO_PHONE", id="no-customer"),
        pytest.param("0501234567", "", "PRO", "CUSTOMER_PHONE", id="no-pro"),
        pytest.param("   ", "0507654321", "CUSTOMER", "PRO_PHONE", id="blank-customer"),
        pytest.param("0501234567", None, "PRO", "CUSTOMER_PHONE", id="unset-pro"),
    ],
)
def test_a_missing_handset_refuses_and_names_the_secret_and_the_input(
    phones_script, tmp_path, customer, pro, named, not_named
):
    proc = _run_phones(phones_script, tmp_path, customer=customer, pro=pro)

    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "::error::" in proc.stdout
    assert f"STAGING_TEST_{named}_PHONE" in proc.stdout
    assert f"{named.lower()}_phone" in proc.stdout
    assert f"STAGING_TEST_{not_named}" not in proc.stdout


def test_both_handsets_missing_reports_both(phones_script, tmp_path):
    proc = _run_phones(phones_script, tmp_path, customer="", pro="")

    assert proc.returncode != 0
    assert "STAGING_TEST_CUSTOMER_PHONE" in proc.stdout
    assert "STAGING_TEST_PRO_PHONE" in proc.stdout
    assert proc.stdout.count("::error::") == 2


def test_a_refusal_never_echoes_the_other_number(phones_script, tmp_path):
    proc = _run_phones(phones_script, tmp_path, customer="", pro="0507654321")

    assert proc.returncode != 0
    assert "0507654321" not in proc.stdout + proc.stderr
