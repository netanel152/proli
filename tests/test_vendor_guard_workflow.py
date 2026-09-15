"""Coverage for the "Guard — no legacy WhatsApp vendor references" CI step.

The step is a ratchet: PRO-85 removed the legacy WhatsApp vendor and PRO-86
made `app/providers/whatsapp/` the single egress, so a reintroduction of that
vendor's URL in code is how the pre-PRO-86 bypass class comes back. It has
found nothing since it landed, which is what a working ratchet looks like —
and which is exactly why it needs a test: nothing else would notice if the
grep stopped matching.

`*.md` was excluded on 2026-09-14. Prose that *names* the retired vendor is
history rather than a bypass (the account is deleted), while scanning prose
made writing about this rule trip it — CLAUDE.md carried a warning telling
people to spell the domain in two pieces. Both directions are pinned below,
because the exclusion is only safe while code is still scanned.

Same shape as `tests/test_stop_workflow.py` and `tests/test_promote_workflow.py`:
the step's shell is extracted verbatim from the parsed YAML and run under real
`bash -e` against a throwaway tree, so these exercise the actual script rather
than a paraphrase of it.

**This file never spells the retired domain.** It is assembled from parts at
runtime, the same trick CLAUDE.md's prose used to need — otherwise the test
would be caught by the guard it tests, on the very commit that narrowed it.
"""

import subprocess
from pathlib import Path

import pytest
import yaml

WORKFLOW_PATH = (
    Path(__file__).resolve().parent.parent / ".github" / "workflows" / "tests.yml"
)

# Assembled, never written out: see the module docstring.
RETIRED_HOST = "green" + "-api.com"
RETIRED_HOST_NODASH = "green" + "api.com"


def _find_step(step_id):
    doc = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    for step in doc["jobs"]["pytest"]["steps"]:
        if step.get("id") == step_id:
            return step
    raise AssertionError(f"no step with id={step_id!r} in {WORKFLOW_PATH}")


@pytest.fixture(scope="module")
def guard_script(tmp_path_factory):
    """The real `run:` body, written out so bash can execute it."""
    script = tmp_path_factory.mktemp("vendor-guard") / "guard.sh"
    script.write_text(_find_step("vendor-guard")["run"], encoding="utf-8")
    return script


def _run(guard_script, tree):
    """Run the guard with `tree` as the working directory.

    `bash -e` matches how GitHub invokes every `run:` block — the trap #162
    cost a day of red runs by testing under a plain `bash`.
    """
    return subprocess.run(
        ["bash", "-e", str(guard_script)],
        cwd=tree,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "app" / "clean.py").write_text("x = 1\n", encoding="utf-8")
    return tmp_path


# --- The ratchet still bites where a bypass can live -------------------------


def test_a_clean_tree_passes(guard_script, tree):
    result = _run(guard_script, tree)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


@pytest.mark.parametrize(
    "path",
    [
        "app/whatsapp_client.py",
        "scripts/send.py",
        "tests/fixtures.py",
        "app/config.yml",
        "requirements.txt",
    ],
)
def test_the_retired_host_in_code_fails_the_build(guard_script, tree, path):
    """Code, scripts, fixtures and config are all still scanned."""
    target = tree / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f'URL = "https://api.{RETIRED_HOST}/send"\n', encoding="utf-8")

    result = _run(guard_script, tree)
    assert result.returncode == 1, f"{path} was allowed:\n{result.stdout}"
    assert "::error::" in result.stdout


def test_both_spellings_are_caught(guard_script, tree):
    """The vendor was reachable under two hostnames; the pattern names both."""
    for host in (RETIRED_HOST, RETIRED_HOST_NODASH):
        (tree / "app" / "bypass.py").write_text(
            f'URL = "https://api.{host}/x"\n', encoding="utf-8"
        )
        result = _run(guard_script, tree)
        assert result.returncode == 1, f"{host} was allowed:\n{result.stdout}"


# --- …and stops biting prose -------------------------------------------------


@pytest.mark.parametrize(
    "path", ["README.md", "docs/HISTORY.md", "CLAUDE.md", "docs/nested/deep/notes.md"]
)
def test_prose_may_name_the_retired_vendor(guard_script, tree, path):
    """The 2026-09-14 narrowing. A doc cannot be an egress bypass — the account
    is deleted — and scanning prose made documenting this rule trip it."""
    target = tree / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        f"We left {RETIRED_HOST} in PRO-85; see app/providers/whatsapp/.\n",
        encoding="utf-8",
    )

    result = _run(guard_script, tree)
    assert result.returncode == 0, f"{path} tripped the guard:\n{result.stdout}"


def test_a_markdown_mention_does_not_mask_a_code_one(guard_script, tree):
    """The exclusion is per-file, not a global off switch: prose naming the
    vendor must not buy silence for a `.py` doing the same."""
    (tree / "docs" / "HISTORY.md").write_text(
        f"Historically we used {RETIRED_HOST}.\n", encoding="utf-8"
    )
    (tree / "app" / "bypass.py").write_text(
        f'URL = "https://api.{RETIRED_HOST}/x"\n', encoding="utf-8"
    )

    result = _run(guard_script, tree)
    assert result.returncode == 1, result.stdout


# --- Shape --------------------------------------------------------------------


def test_the_guard_excludes_markdown_and_still_scans_everything_else():
    """Read off the real `run:` body, so widening the exclusion to `docs/` or
    dropping the scan entirely has to change this line too."""
    run = _find_step("vendor-guard")["run"]
    assert "--exclude='*.md'" in run
    assert "--exclude-dir=.github" in run, "the detector must not scan itself"
    for never_excluded in ("app", "scripts", "tests", "admin_panel"):
        assert f"--exclude-dir={never_excluded}" not in run
