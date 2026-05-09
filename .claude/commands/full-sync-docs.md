# /full-sync-docs

Perform a **code-first, full-codebase documentation audit**. Unlike `/sync-docs` (which only patches what the latest diff broke), this skill reads the *live* source code, extracts ground truth, then cross-checks every section of every `.md` file in the project. It finds stale content that accumulated over many commits — not just content broken by the latest one.

---

## Phase 1 — Extract Ground Truth from Live Code

Read each source file below and record the exact current values. Do **not** use git diff for this phase — read the actual files.

### 1.1 `app/core/constants.py`
- Extract every member of `UserStates` (full list, in order).
- Extract every member of `LeadStatus` (full list, in order).
- Extract every attribute of `WorkerConstants` with its value.

### 1.2 `app/services/*.py` — Service Responsibilities
For each file in `app/services/`, read the top-level `async def` functions and any `Messages.Keywords.*` branches in the main dispatch function. Record:
- What commands / keywords each service handles.
- What state transitions it makes.

Pay special attention to: `pro_flow.py`, `workflow_service.py`, `customer_flow.py`, `admin_flow.py`, `monitor_service.py`.

### 1.3 `app/scheduler.py` — Scheduler Jobs
Read `app/scheduler.py` (the actual APScheduler setup — not `app/worker.py`). List every `scheduler.add_job(...)` call with its trigger (cron/interval) and job function name. This is the authoritative job count.

### 1.4 Routes — `app/api/routes/webhook.py` and `app/api/routes/health.py`
List every `@router.get` / `@router.post` decorator with method, path, and one-line description.

### 1.5 `app/core/messages.py` — Keyword Lists
Extract every `Keywords.*` list (HELP_COMMANDS, MENU_COMMANDS, APPROVE_COMMANDS, FINISH_COMMANDS, CANCEL_BOOKED_COMMANDS, DETAILS_COMMANDS, SUMMARY_COMMANDS, SOS_COMMANDS, RESET_COMMANDS, etc.). These are the ground truth for what commands exist.

### 1.6 Test Count
Count test functions across all `tests/test_*.py` source files:
```
grep -rc "^async def test_\|^def test_" tests/
```
Sum only `.py` source files (exclude `__pycache__`). This is the authoritative total for "N passed" claims in docs.

### 1.7 `app/core/database.py` — MongoDB Collections
List every `db.<name>` collection assignment. This is the authoritative collection list.

### 1.8 `scripts/` directory
List all `.py` files in `scripts/`. This is the authoritative script list.

---

## Phase 2 — Audit Every Doc File

Read each file below in full. Check every listed claim against Phase 1 ground truth. Mark each claim as **✓ correct**, **✗ stale**, or **? needs update**.

### `CLAUDE.md`
| Section | What to check |
|---------|---------------|
| Service layer table | Every service listed; description matches actual responsibilities |
| `UserStates` list | Exactly matches Phase 1.1 — no missing, no extra, correct names |
| `WorkerConstants` list | Values match Phase 1.1 |
| `LeadStatus` flow | Matches Phase 1.1 enum order |
| Process 1 routes | Lists all routes from Phase 1.4 |
| Process 2 APScheduler summary | Scheduler job list is plausible (need not be exhaustive, but must not be wrong) |
| Test count (`N passed, M skipped`) | Matches Phase 1.6 |

### `README.md`
| Section | What to check |
|---------|---------------|
| Core Features list (English) | Reflects current commands and flows |
| Feature list (Hebrew section) | Mirrors English section |
| Test count | Matches Phase 1.6 |
| Command examples | Reflect actual keyword lists from Phase 1.5 |

### `GEMINI.md`
| Section | What to check |
|---------|---------------|
| Lead Status flow | Matches Phase 1.1 (order and all values) |
| UserStates list | Any listed states exist in Phase 1.1 (may be abbreviated — wrong names are the issue) |
| SOS Healer description | Must say it checks lead *status* fields (`new`, `contacted`, `pending_admin_review`), not FSM state `AWAITING_PRO_APPROVAL` |
| Mongo `$near` vs `$geoNear` | The matching service uses `$geoNear` aggregation, not `$near` |

### `docs/ARCHITECTURE.md`
| Section | What to check |
|---------|---------------|
| APScheduler jobs count (header) | Number matches Phase 1.3 total |
| APScheduler jobs table | Every job from Phase 1.3 is present with correct schedule |
| FSM state table | Every `UserStates` member from Phase 1.1 is listed; descriptions accurate |
| Lead lifecycle diagram and table | Matches `LeadStatus` from Phase 1.1 |
| HTTP routes | Match Phase 1.4 |
| MongoDB collections table | Matches Phase 1.7 |
| Technology stack — scheduler job count | "N cron/interval jobs" matches Phase 1.3 total |

### `docs/TESTING.md`
| Section | What to check |
|---------|---------------|
| Expected test count | Matches Phase 1.6 |
| Test file descriptions | No references to removed features (e.g., `send_interactive_buttons`) |
| Test conventions | Match current `conftest.py` and `pytest.ini` |

### `docs/OPERATIONS_GUIDE.md`
| Section | What to check |
|---------|---------------|
| Pro command references | Match `Messages.Keywords.*` lists from Phase 1.5 |
| Reset keyword references | Must match `RESET_COMMANDS` from Phase 1.5 (not "תפריט") |
| Admin trigger keyword | Matches actual admin trigger (check `admin_flow.py`) |
| SOS/stale thresholds | Match `WorkerConstants` values from Phase 1.1 |
| Scheduler job descriptions | Match Phase 1.3 jobs |

### `docs/API_DOCS.md`
| Section | What to check |
|---------|---------------|
| Endpoint list | Every route from Phase 1.4 has a section; no extra routes listed |
| Supported webhook message types | Does not list `buttonsResponseMessage` (removed — Green API does not support interactive buttons) |
| Health check response JSON | Field names match actual `health.py` response structure |

### `docs/MANUAL_TEST_PLAN.md`
| Section | What to check |
|---------|---------------|
| Reset TCs | Reset keyword used matches `RESET_COMMANDS` ("reset" / "התחלה"), not "תפריט" |
| SOS TCs | SOS keyword used matches `SOS_COMMANDS` ("נציג", "אנושי", "מנהל", "admin", "sos"); "עזרה" is NOT a SOS keyword |
| Pro command TCs | Command keywords match Phase 1.5 (approve, reject, finish, etc.) |
| State names | Any FSM state name referenced exists in Phase 1.1 |

### `docs/SCRIPTS.md`
| Section | What to check |
|---------|---------------|
| Script list vs filesystem | Every script documented exists in `scripts/` (or `tests/` if explicitly noted); no documented script is missing from disk |
| Scripts on disk but not documented | Scripts in Phase 1.8 that have no entry in SCRIPTS.md |

### `docs/SCALING_GUIDE.md`
| Section | What to check |
|---------|---------------|
| Geo operator name | Must say `$geoNear` aggregation, not `$near` (different operator) |
| Distributed lock status | `with_scheduler_lock` decorator is already implemented in `app/scheduler.py` — any text calling this "needed" or "recommended" is stale |
| `MAX_PRO_LOAD` value | Matches Phase 1.1 (`WorkerConstants.MAX_PRO_LOAD`) |

### `docs/PRODUCTION_READINESS.md`
| Section | What to check |
|---------|---------------|
| Test count | Update to Phase 1.6 count (this doc's count is a historical snapshot that keeps getting stale) |
| ARQ `max_tries` value | Check `app/core/arq_worker.py` for the actual `max_tries` value |

### `docs/FINOPS.md`
No code-derived facts — costs and pricing only. **Skip — no checks needed.**

### `docs/DOCUMENTATION_FLOW.md`
| Section | What to check |
|---------|---------------|
| Scheduler jobs table | Every job from Phase 1.3 is listed; missing jobs are added |
| Context-clear trigger keywords | Any reset keyword referenced must match `RESET_COMMANDS` (not "תפריט") |
| Lead lifecycle | Matches Phase 1.1 `LeadStatus` |

---

## Phase 3 — Apply Edits

For every stale claim found in Phase 2:
1. Use `Edit` to apply the minimal fix — change only the stale fragment. Do not rewrite surrounding prose.
2. If a whole table row is missing, insert it. If a row describes a removed feature, delete it.
3. Never create a new `.md` file. Never edit files under `venv/`, `.pytest_cache/`, `.claude/`, or `GEMINI.md`'s skills sections.
4. Do not commit.

---

## Phase 4 — Output Report

Print a structured summary:

```
## Full Sync-Docs Report

### Ground Truth (from code)
- UserStates: <count> states
- LeadStatus: <values>
- WorkerConstants: <count> values
- Services: <list>
- Scheduler jobs: <count> (<names>)
- Routes: <list>
- Test functions: <total>

### Changes Made
- `CLAUDE.md` — <what changed and why> | (no changes needed)
- `README.md` — <what changed and why> | (no changes needed)
- `GEMINI.md` — <what changed and why> | (no changes needed)
- `docs/ARCHITECTURE.md` — <what changed and why> | (no changes needed)
- `docs/TESTING.md` — <what changed and why> | (no changes needed)
- `docs/OPERATIONS_GUIDE.md` — <what changed and why> | (no changes needed)
- `docs/API_DOCS.md` — <what changed and why> | (no changes needed)
- `docs/MANUAL_TEST_PLAN.md` — <what changed and why> | (no changes needed)
- `docs/SCRIPTS.md` — <what changed and why> | (no changes needed)
- `docs/SCALING_GUIDE.md` — <what changed and why> | (no changes needed)
- `docs/PRODUCTION_READINESS.md` — <what changed and why> | (no changes needed)
- `docs/FINOPS.md` — skipped (no code-derived facts)
- `docs/DOCUMENTATION_FLOW.md` — <what changed and why> | (no changes needed)

### Verified Clean (no changes)
- <list of doc files where every checked claim was correct>
```

---

## Rules

- **Code is truth.** If the code says X and a doc says Y, fix the doc.
- **Minimal edits.** Change only the stale fragment — never reformat, reflow, or polish untouched prose.
- **No invention.** Do not add documentation for features not present in the code.
- **No new files.** Do not create `.md` files that don't already exist.
- **No commits.** Leave all changes unstaged for the user to review with `git diff`.
- **Historical docs (`PRODUCTION_READINESS.md`):** Only update factual claims that are wrong (test count, config values). Do not alter the audit narrative or resolved-issues list.
- **Costs docs (`FINOPS.md`):** Skip entirely — no code-verifiable facts.
