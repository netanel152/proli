"""Coverage for PRO-183: `.github/workflows/promote-to-production.yml`.

`skip_deploy_check` (a checkbox, ticked on 9 of the last 10 promotions) was
replaced by `skip_reason` (a string that must actually say something) plus an
unconditional "Resolve the verification decision" step (`id: gate`) that both
verification steps now gate on.

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
