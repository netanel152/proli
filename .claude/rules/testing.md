---
paths:
  - "tests/**"
  - "docs/TESTING.md"
  - "pytest.ini"
  - ".github/workflows/tests.yml"
---

# Testing

Loaded when a test, `docs/TESTING.md`, `pytest.ini` or `tests.yml` are read.

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

No file holds a test count. CI fails a PR that passes fewer tests than `dev`, and keeps that number in the Actions cache; see "The test floor" in `docs/CI_WORKFLOWS.md`. Locally, a failure is a regression. Integration tests are skipped without `MONGO_TEST_URI`.

Canonical run is `pytest` inside the project virtualenv (PRO-50, pinned `pydantic`/`pydantic-core`/`pydantic-settings` for deterministic resolution). Unit tests need neither a real MongoDB (in-memory `mongomock`) nor a real Redis (in-memory `fakeredis`, PRO-78) — no external services required.

## Conventions

Unit tests use `mongomock_motor` (in-memory MongoDB) and mock `whatsapp` and `ai` instances via `monkeypatch`. Integration tests (marked `@pytest.mark.integration`) connect to a real `MONGO_TEST_URI` test database and clear it before each run. `conftest.py` auto-applies the mock fixtures to all non-integration tests via `autouse=True`. `asyncio_mode = strict` is set in `pytest.ini`. The autouse `_isolate_background_tasks` fixture (PRO-187) also clears `app.core.background_tasks`'s module-level registry between tests, so a test's `pending_background_tasks()` never sees another test's leftover, closed-loop tasks.

`$geoNear` is not supported by mongomock — matching service geo tests mock `users_collection.aggregate` as async generators directly.

`customer_flow.py` and `pro_flow.py` functions receive `whatsapp`/`lead_manager` as parameters (dependency injection) so `workflow_service.py` passes its shared instances.

`admin_panel/` view bodies are Streamlit and are never executed by the unit suite (only their injectable query/label/refresh seams underneath are), so a wrong-arity call inside one is invisible to every other gate — `tests/test_admin_view_call_arity.py` covers that gap with a pure-`ast` static check of every resolvable call in `admin_panel/` against its target's signature, rather than by importing or running the panel.

## Syncing before you push

No PR edits a test count, so two PRs that both add tests merge in either order. The history of how that got here is in `docs/CI_WORKFLOWS.md`.

Still sync `dev` in before pushing, because file contents conflict normally. Merge, never rebase (`.claude/hooks/pre-bash-guard.py` refuses `gh pr create`/`gh pr merge` from a branch that hasn't):

```bash
git fetch origin && git merge origin/dev
pytest -q   # inside the project venv
git push
```
