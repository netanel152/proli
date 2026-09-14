---
paths:
  - "tests/**"
  - "docs/TESTING.md"
  - "pytest.ini"
  - ".github/workflows/tests.yml"
  - ".github/workflows/refresh-test-baseline.yml"
---

# Testing

Loaded when a test, `docs/TESTING.md`, `pytest.ini` or the test workflows are read.

## Commands

```bash
# Run all unit tests (uses mongomock — no real DB needed)
pytest

# Run a single test file
pytest tests/test_matching_service.py

# Run only integration tests (requires MONGO_TEST_URI in .env)
pytest -m integration

# Run with verbose output
pytest -v
```

Expected baseline lives in `docs/TESTING.md` ("Current status" line) — the single source of truth for the pass/skip count, enforced as a **floor** by the "Guard — test baseline floor" CI step: fewer passed than the line fails the build, more passed posts a warning and passes. Bumping the line in the same PR is welcome; if you don't, `refresh-test-baseline.yml` runs on `dev` after the merge and goes red until somebody moves it (it cannot open its own PR — see below). Integration tests are skipped without `MONGO_TEST_URI`.

Canonical run is `pytest` inside the project virtualenv (PRO-50, pinned `pydantic`/`pydantic-core`/`pydantic-settings` for deterministic resolution). Unit tests need neither a real MongoDB (in-memory `mongomock`) nor a real Redis (in-memory `fakeredis`, PRO-78) — no external services required.

## Conventions

Unit tests use `mongomock_motor` (in-memory MongoDB) and mock `whatsapp` and `ai` instances via `monkeypatch`. Integration tests (marked `@pytest.mark.integration`) connect to a real `MONGO_TEST_URI` test database and clear it before each run. `conftest.py` auto-applies the mock fixtures to all non-integration tests via `autouse=True`. `asyncio_mode = strict` is set in `pytest.ini`. The autouse `_isolate_background_tasks` fixture (PRO-187) also clears `app.core.background_tasks`'s module-level registry between tests, so a test's `pending_background_tasks()` never sees another test's leftover, closed-loop tasks.

`$geoNear` is not supported by mongomock — matching service geo tests mock `users_collection.aggregate` as async generators directly.

`customer_flow.py` and `pro_flow.py` functions receive `whatsapp`/`lead_manager` as parameters (dependency injection) so `workflow_service.py` passes its shared instances.

`admin_panel/` view bodies are Streamlit and are never executed by the unit suite (only their injectable query/label/refresh seams underneath are), so a wrong-arity call inside one is invisible to every other gate — `tests/test_admin_view_call_arity.py` covers that gap with a pure-`ast` static check of every resolvable call in `admin_panel/` against its target's signature, rather than by importing or running the panel.

## The baseline floor, and why two PRs that both add tests can merge in either order

**Merges do not have to be serialized.** `docs/TESTING.md`'s `Current status: N passed` used to be enforced as an *equality* — the guard failed below **and** above — so two open PRs that both added tests wrote conflicting counts and whichever merged second failed as a stale baseline. The guard is now a **floor**: below the line is a regression and fails the build; above it posts a `::warning` and passes, and `.github/workflows/refresh-test-baseline.yml` moves the line once the change is on `dev`. Two branches that both add tests can now merge in either order.

**That refresh job cannot actually open its PR in this repo**, and has never been observed doing so: `gh pr create` returns *"GitHub Actions is not permitted to create or approve pull requests"* because **Settings → Actions → General → Workflow permissions → Allow GitHub Actions to create and approve pull requests** is off. It pushes the `chore/refresh-test-baseline-<sha>` branch and then goes red at the last step. The workflow's own header treats that red run as the signal to bump the line by hand, which works — but it means a red 🔢 run on `dev` says *"the line drifted"*, not *"the tests broke"*, and the branch it pushed is left dangling. Turning the setting on makes the job do what the sentence above promises.

Still sync `dev` in before pushing — the *content* of `docs/TESTING.md` (and every other file) conflicts normally. Merge, never rebase (`.claude/hooks/pre-bash-guard.py` refuses `gh pr create`/`gh pr merge` from a branch that hasn't):

```bash
git fetch origin && git merge origin/dev
pytest -q   # inside the project venv; the count comes from the summary line
# bumping the line yourself is welcome but optional — the refresh workflow catches it
git push
```
