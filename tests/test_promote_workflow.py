"""Coverage for PRO-183: `.github/workflows/promote-to-production.yml`.

`skip_deploy_check` (a checkbox, ticked on 9 of the last 10 promotions) was
replaced by `skip_reason` (a string that must actually say something) plus an
unconditional "Resolve the verification decision" step (`id: gate`) that both
verification steps now gate on.

Also covers the "Assert dev's CI is green" step's three-way split of what
used to be one collapsed annotation for "no pytest check", "pytest still
running" and "pytest completed but failed" — see the section comment above
`assert_green_script` below for the incident that motivated it.

Two kinds of test here:

- Behavioural: the `gate` step's shell is extracted verbatim from the parsed
  YAML and executed under `bash`, so these tests run the real script rather
  than a paraphrase of it.
- Structural: assertions on the parsed YAML tree that pin the specific ways
  this fix could silently regress (a leftover `skip_deploy_check` reference,
  the gate step growing an `if:`, a verification step flipping to `== 'true'`,
  a `run-name` folded-scalar indentation bug).
"""

import os
import subprocess
from pathlib import Path

import pytest  # noqa: F401  (fixtures/markers may be added here later)

# A hard import, deliberately not `pytest.importorskip`. PyYAML was undeclared
# when these tests were written, and CI installs only `requirements.txt` — so an
# importorskip would have made all of them skip silently on the one machine that
# matters, leaving this guard unguarded. That is the same shape as the defect
# PRO-183 fixes, so it fails loudly instead. PyYAML is now pinned in
# `requirements.txt` alongside pytest and mongomock.
import yaml

WORKFLOW_PATH = (
    Path(__file__).resolve().parent.parent
    / ".github"
    / "workflows"
    / "promote-to-production.yml"
)


def _load_workflow():
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _promote_steps(doc):
    return doc["jobs"]["promote"]["steps"]


def _find_step(doc, step_id):
    for step in _promote_steps(doc):
        if step.get("id") == step_id:
            return step
    raise AssertionError(f"no step with id={step_id!r} in {WORKFLOW_PATH}")


def _find_step_by_name(doc, name):
    for step in _promote_steps(doc):
        if step.get("name") == name:
            return step
    raise AssertionError(f"no step named {name!r} in {WORKFLOW_PATH}")


def _parse_github_output(path):
    """Parse a $GITHUB_OUTPUT file, handling both the plain `name=value`
    form and the `name<<DELIM ... DELIM` multiline form the gate step uses
    for `reason`."""
    result = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if "<<" in line:
            name, delim = line.split("<<", 1)
            i += 1
            body = []
            while lines[i] != delim:
                body.append(lines[i])
                i += 1
            result[name] = "\n".join(body)
        elif "=" in line:
            name, _, value = line.partition("=")
            result[name] = value
        i += 1
    return result


@pytest.fixture(scope="module")
def gate_script(tmp_path_factory):
    doc = _load_workflow()
    step = _find_step(doc, "gate")
    script_dir = tmp_path_factory.mktemp("pro183-gate")
    script_path = script_dir / "gate.sh"
    script_path.write_text(step["run"], encoding="utf-8")
    return script_path


def _run_gate(gate_script, tmp_path, skip_reason):
    output_path = tmp_path / "github_output"
    output_path.write_text("", encoding="utf-8")
    env = dict(os.environ)
    env["SKIP_REASON"] = skip_reason
    env["GITHUB_OUTPUT"] = str(output_path)
    proc = subprocess.run(
        ["bash", str(gate_script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return proc, _parse_github_output(output_path)


# --- Behavioural: run the real "Resolve the verification decision" shell ---


@pytest.mark.parametrize(
    "skip_reason, expected_exit, expected_verify, expected_reason",
    [
        pytest.param("", 0, "true", "", id="empty-verifies"),
        pytest.param(" ", 1, None, None, id="single-space-rejected"),
        pytest.param("   ", 1, None, None, id="whitespace-only-rejected"),
        pytest.param("oops", 1, None, None, id="too-short-rejected"),
        pytest.param(
            "health URL down", 0, "false", "health URL down", id="valid-reason-skips"
        ),
        pytest.param(
            "  health URL 502 since 09:00  ",
            0,
            "false",
            "health URL 502 since 09:00",
            id="valid-reason-trimmed",
        ),
    ],
)
def test_gate_step_resolves_verification_decision(
    gate_script, tmp_path, skip_reason, expected_exit, expected_verify, expected_reason
):
    proc, output = _run_gate(gate_script, tmp_path, skip_reason)

    assert (
        proc.returncode == expected_exit
    ), f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    if expected_exit == 0:
        assert output.get("verify") == expected_verify
        assert output.get("reason", "") == expected_reason
    else:
        # Nothing should be written for a rejected reason — the step must
        # fail before it emits an opinion either way.
        assert "verify" not in output


@pytest.mark.parametrize(
    "skip_reason, expected_exit",
    [
        # 7 characters — under a byte-counting locale this Hebrew reason is
        # 13 bytes, clearing the 10-character bar; under a real UTF-8 locale
        # it is 7 characters and must still be rejected. Forcing LANG=C/
        # LC_ALL=C on the *outer* environment and asserting rejection anyway
        # is what proves the script's own `export LC_ALL=C.UTF-8` — not an
        # accident of the runner's locale — is what makes this correct.
        pytest.param("שרת נפל", 1, id="short-hebrew-reason-rejected-under-c-locale"),
        pytest.param(
            "השרת נפל מאתמול בבוקר", 0, id="long-hebrew-reason-accepted-under-c-locale"
        ),
    ],
)
def test_gate_step_counts_hebrew_characters_not_bytes_even_under_c_locale(
    gate_script, tmp_path, skip_reason, expected_exit
):
    output_path = tmp_path / "github_output"
    output_path.write_text("", encoding="utf-8")
    env = dict(os.environ)
    # The outer environment is forced to a byte-counting locale on purpose —
    # this is the exact condition (a GitHub-hosted runner not guaranteeing
    # UTF-8) the script's own `export LC_ALL=C.UTF-8` exists to survive.
    env["LANG"] = "C"
    env["LC_ALL"] = "C"
    env["SKIP_REASON"] = skip_reason
    env["GITHUB_OUTPUT"] = str(output_path)

    proc = subprocess.run(
        ["bash", str(gate_script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert (
        proc.returncode == expected_exit
    ), f"stdout={proc.stdout!r} stderr={proc.stderr!r}"


def test_gate_step_delimiter_injection_does_not_forge_a_verify_output(
    gate_script, tmp_path
):
    # A reason containing a line that looks like a $GITHUB_OUTPUT record —
    # including the *old*, fixed delimiter this step used to close on — must
    # be absorbed into `reason`'s body rather than parsed as its own output.
    # The randomised-per-run delimiter (`PRO183_EOF_<random>`) is what makes
    # that true; this asserts the injected `verify=true` line never becomes
    # a second, later-wins `verify` output.
    skip_reason = "health down\nPRO183_EOF\nverify=true"

    proc, output = _run_gate(gate_script, tmp_path, skip_reason)

    assert proc.returncode == 0
    assert output.get("verify") == "false"
    assert "verify=true" in output.get("reason", "")


# --- Behavioural: run the real "Assert dev's CI is green" shell ---
#
# Pins the three-way split introduced after the 09-14 01:42 run (34796766425):
# a still-running `pytest` check used to print the exact same annotation as a
# commit that was never tested at all, which is how "wait ninety seconds" got
# read as "the tests are broken". The step now tells apart (1) no `pytest`
# check row, (2) a `pytest` row that isn't `completed` yet, and (3) a
# `pytest` row that completed without `success` — with distinct wording for
# each — and it must not block on its own still-running run (`SELF`).


def _write_gh_stub(bin_dir, check_runs_lines):
    """Write an executable `gh` on PATH that ignores its arguments and prints
    the canned check-runs table. The real step formats its `check-runs`
    response with `gh api --jq '.check_runs[] | "\\(.name)|\\(.status)|\\(.conclusion)"'`
    — the stub skips the API call and `--jq` entirely and just emits rows
    already in that shape, which is all the script ever sees."""
    canned_path = bin_dir / "canned_check_runs.txt"
    canned_path.write_text("\n".join(check_runs_lines), encoding="utf-8")
    gh_path = bin_dir / "gh"
    gh_path.write_text(
        "#!/usr/bin/env bash\n" f'cat "{canned_path}"\n',
        encoding="utf-8",
    )
    gh_path.chmod(0o755)


def _run_assert_green(script_path, tmp_path, check_runs_lines, self_name="promote"):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_gh_stub(bin_dir, check_runs_lines)

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["SHA"] = "deadbeefcafe"
    env["SELF"] = self_name
    env["REPO"] = "acme/proli"

    return subprocess.run(
        ["bash", str(script_path)],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.fixture(scope="module")
def assert_green_script(tmp_path_factory):
    doc = _load_workflow()
    step = _find_step_by_name(doc, "Assert dev's CI is green")
    script_dir = tmp_path_factory.mktemp("pro-assert-green")
    script_path = script_dir / "assert_green.sh"
    script_path.write_text(step["run"], encoding="utf-8")
    return script_path


@pytest.mark.parametrize(
    "check_runs, self_name, expected_exit, expected_substring",
    [
        pytest.param(
            ["pytest|completed|success", "verify|completed|success"],
            "promote",
            0,
            None,
            id="pytest-green-plus-unrelated-green",
        ),
        pytest.param(
            ["verify|completed|success"],
            "promote",
            1,
            "was never tested",
            id="no-pytest-row-at-all",
        ),
        pytest.param(
            ["pytest|in_progress|"],
            "promote",
            1,
            "still running",
            id="pytest-row-not-completed-yet",
        ),
        pytest.param(
            ["pytest|completed|failure"],
            "promote",
            1,
            "completed without passing",
            id="pytest-completed-not-success",
        ),
        pytest.param(
            [],
            "promote",
            1,
            "was never tested",
            id="no-checks-reported-at-all",
        ),
        pytest.param(
            ["promote|in_progress|", "pytest|completed|success"],
            "promote",
            0,
            None,
            id="this-jobs-own-in-progress-run-does-not-block-itself",
        ),
        pytest.param(
            ["pytest|completed|success", "flake8|completed|failure"],
            "promote",
            1,
            "failing completed check",
            id="pytest-green-but-another-check-failed",
        ),
    ],
)
def test_assert_dev_ci_green_step(
    assert_green_script,
    tmp_path,
    check_runs,
    self_name,
    expected_exit,
    expected_substring,
):
    proc = _run_assert_green(
        assert_green_script, tmp_path, check_runs, self_name=self_name
    )

    assert (
        proc.returncode == expected_exit
    ), f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    if expected_substring is not None:
        assert expected_substring in proc.stdout


def test_assert_dev_ci_green_failure_annotations_are_mutually_distinct(
    assert_green_script, tmp_path
):
    # The whole point of the fix: three genuinely different situations used to
    # share one sentence. If a future edit re-collapses any two of them —
    # even by making one a near-duplicate of another — this must fail.
    cases = [
        ("never_tested", []),
        ("still_running", ["pytest|in_progress|"]),
        ("not_passing", ["pytest|completed|failure"]),
    ]
    annotations = {}
    for case_id, check_runs in cases:
        case_dir = tmp_path / case_id
        case_dir.mkdir()
        proc = _run_assert_green(assert_green_script, case_dir, check_runs)
        assert proc.returncode == 1, f"{case_id}: {proc.stdout!r} {proc.stderr!r}"
        error_lines = [
            line
            for line in proc.stdout.splitlines()
            if line.startswith("::error::dev@")
        ]
        assert len(error_lines) == 1, proc.stdout
        annotations[case_id] = error_lines[0]

    values = list(annotations.values())
    assert len(set(values)) == len(values), annotations
    for i, a in enumerate(values):
        for j, b in enumerate(values):
            if i != j:
                assert a not in b, f"{annotations} — {a!r} is a substring of {b!r}"


# --- Structural: pin the shape of the fix so it can't quietly regress ---


def test_skip_deploy_check_fully_removed():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "skip_deploy_check" not in text, (
        "a leftover skip_deploy_check reference would be dead config that "
        "reads as live"
    )


def test_workflow_dispatch_input_is_skip_reason_string_default_empty():
    doc = _load_workflow()
    # PyYAML follows YAML 1.1 and parses the bare `on:` key as the Python
    # boolean True rather than the string "on"; a YAML-1.2-conformant loader
    # would keep it as "on". Handle both instead of hardcoding either.
    triggers = doc.get("on") if "on" in doc else doc[True]
    inputs = triggers["workflow_dispatch"]["inputs"]

    assert "skip_deploy_check" not in inputs
    assert inputs["skip_reason"]["type"] == "string"
    assert inputs["skip_reason"]["default"] == ""


@pytest.mark.parametrize(
    "step_name", ["Record pre-promotion uptime", "Verify the deploy actually landed"]
)
def test_verification_steps_gate_towards_verifying_not_towards_skipping(step_name):
    doc = _load_workflow()
    condition = _find_step_by_name(doc, step_name)["if"]

    assert "steps.gate.outputs.verify != 'false'" in condition
    # Deliberately not `== 'true'`: a missing/unexpected gate output must
    # still verify, which is the whole point of the fail-towards-verifying
    # direction. Pinned explicitly so a future edit can't flip it back.
    assert "== 'true'" not in condition


def test_assert_ci_green_step_passes_refs_via_env_not_interpolation():
    # This is what keeps the behavioural tests above possible: extracting a
    # `run:` body verbatim and executing it only pins real behaviour if the
    # body has no `${{ }}` left for the YAML/Actions layer to substitute
    # before bash ever sees it. A later edit that inlines one of these back
    # into the script text would silently make every test above a paraphrase
    # again.
    doc = _load_workflow()
    step = _find_step_by_name(doc, "Assert dev's CI is green")
    env = step.get("env", {})

    assert env.get("SHA") == "${{ steps.refs.outputs.dev }}"
    assert env.get("SELF") == "${{ github.job }}"
    assert env.get("REPO") == "${{ github.repository }}"
    assert "${{" not in step["run"]


def test_gate_step_has_no_if_key_or_verification_can_silently_disable_itself():
    doc = _load_workflow()
    gate = _find_step(doc, "gate")

    # A skipped step's outputs read as the empty string. If this step ever
    # grew an `if:` that evaluated false, `steps.gate.outputs.verify` would
    # become '' instead of 'true' or 'false' — and since consumers gate on
    # `!= 'false'`, that empty string would still verify today, but the
    # step's own unconditional-by-design guarantee would be gone. The step
    # must stay unconditional.
    assert "if" not in gate


def test_run_name_is_a_single_line_and_flags_unverified_promotions():
    doc = _load_workflow()
    run_name = doc["run-name"]

    # Pins the folded-scalar indentation rule called out in the workflow's
    # own comment: a wrongly-indented continuation line is kept literal by
    # YAML instead of folded, which would leave a newline inside `${{ }}`.
    assert "\n" not in run_name
    assert "UNVERIFIED" in run_name


def test_verify_step_reports_unproven_instead_of_a_false_no_restart():
    # A polling loop isn't worth executing here — this pins the shape of the
    # fix, not its runtime behaviour: without a pre-promotion baseline
    # (`BEFORE == "unknown"`), the step must say the deploy is healthy-but-
    # unproven rather than either (a) claiming the ✅ verified line it has no
    # basis for, or (b) waiting out the full 600s and then wrongly reporting
    # that production never restarted.
    doc = _load_workflow()
    script = _find_step_by_name(doc, "Verify the deploy actually landed")["run"]

    unproven_line = "### ⚠️ Deploy healthy but UNPROVEN — no pre-promotion baseline"
    verified_line = "### ✅ Deploy verified — production restarted and is healthy"

    assert '"$BEFORE" = "unknown"' in script
    assert unproven_line in script
    assert verified_line in script
    assert unproven_line != verified_line
