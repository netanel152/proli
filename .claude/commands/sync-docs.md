Review uncommitted code changes and update any `.md` docs that are factually outdated by the diff.

### Steps

1. Run `git status` and `git diff` to see what changed. Skip untracked doc files (they'll be committed separately).
2. For each changed source file, decide which docs it could affect:
   - `app/services/*.py` → service layer table in `CLAUDE.md`; message-processing flow and FSM table in `docs/ARCHITECTURE.md`.
   - `app/core/constants.py` → `UserStates` / `WorkerConstants` lists in `CLAUDE.md`; FSM table in `docs/ARCHITECTURE.md`.
   - `app/core/redis_client.py` or new Redis keys → Redis keys table in `docs/ARCHITECTURE.md`.
   - `app/scheduler.py` or new APScheduler jobs → scheduler jobs table in `docs/ARCHITECTURE.md`.
   - New user-facing features (commands, keywords, flows) → Core Features lists in `README.md` (both English and Hebrew sections).
   - Changes in `tests/` that alter the expected pass/skip count → test count in both `CLAUDE.md` and `README.md`.
   - Operator-visible workflow changes → `docs/OPERATIONS_GUIDE.md`.
   - Testing convention changes → `docs/TESTING.md`.
3. Apply the edits using `Edit`. Only change what the diff actually invalidates — do not rewrite still-accurate prose, do not reflow unchanged tables, do not "polish."
4. If nothing in the docs is affected, say so explicitly. Do not force edits.

### Output

A short per-file summary of what was updated and why. Example:

- `CLAUDE.md` — added `admin_flow.py` to service layer table (new file `app/services/admin_flow.py`)
- `README.md` — bumped expected test count 162 → 208 (new tests in `tests/test_pro_flow.py`)
- `docs/ARCHITECTURE.md` — no changes needed

### Rules

- Never create a new `.md` file unless the user asks.
- Do not update `.md` files under `venv/`, `.pytest_cache/`, or any dependency folder.
- Do not commit the changes — leave them staged for the user to review.
