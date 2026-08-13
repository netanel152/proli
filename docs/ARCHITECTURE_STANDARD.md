# Architecture Standard

How code is structured in Proli: where a piece of logic lives, how a service
module is laid out, how outbound traffic leaves the system, where state and
user-facing text belong, and how any of it stays testable.

It exists because structure is the one thing neither the interpreter nor the
test suite enforces. Python will happily accept a 5,000-line service module,
and a green test run says nothing about whether the next change to it will be
safe. The only thing that keeps a codebase navigable is that everyone
assembles it the same way.

This document is the companion to [`ARCHITECTURE.md`](./ARCHITECTURE.md),
which describes what the system *is* — the three processes, the data layer,
the flows. That one is a map; this one is the building code. When the two
disagree, the map is out of date and should be fixed (see
[Documentation](#13-documentation)).
[`REFACTORING_EXAMPLES.md`](./REFACTORING_EXAMPLES.md) shows the standard
applied to real code in this repo, so it can be judged by its results.

## Table of Contents

1. [Status and How to Read This](#1-status-and-how-to-read-this)
2. [Where We Are Today](#2-where-we-are-today)
3. [Principles](#3-principles)
4. [Top-Level Structure](#4-top-level-structure)
5. [The Service Module](#5-the-service-module)
6. [Layer Rules, With Examples](#6-layer-rules-with-examples)
7. [Naming Conventions](#7-naming-conventions)
8. [Size Limits and Decomposition](#8-size-limits-and-decomposition)
9. [State and the FSM](#9-state-and-the-fsm)
10. [User-Facing Text](#10-user-facing-text)
11. [Configuration and Constants](#11-configuration-and-constants)
12. [Failure Policy: Fail-Open vs Fail-Closed](#12-failure-policy-fail-open-vs-fail-closed)
13. [Documentation](#13-documentation)
14. [Testing](#14-testing)
15. [AI Coding Agents](#15-ai-coding-agents)
16. [Migration Plan](#16-migration-plan)
17. [Open Questions](#17-open-questions)

## 1. Status and How to Read This

**This is a proposed standard, drafted from the conventions the codebase
already follows.** Most of what is written here is not invention — it is a
record of decisions already made (PRO-71, PRO-82, PRO-86 and others) plus a
few rules that close gaps those decisions left open. It becomes binding when
the team accepts it; [Open Questions](#17-open-questions) tracks what is still
genuinely undecided.

Requirement levels follow the usual convention:

- **MUST** — required. A reviewer should reject the change without it.
- **SHOULD** — required unless there is a stated reason not to, in the PR
  description.
- **MAY** — allowed, at the author's judgement.

**Scope of enforcement.** The standard applies to:

1. **all new code** — from the day it is agreed;
2. **code you already have open** — bring the parts you touch up to it (see
   [Migration Plan](#16-migration-plan));
3. **existing code you are not touching** — nothing changes until it is
   scheduled. Nobody is asked to stop feature work and refactor.

Some rules here are already enforced harder than review: the CI guard fails
the build on any reference to the old vendor's domain, and the test suite
fails on a regression against the baseline in [`TESTING.md`](./TESTING.md).
Where a rule has teeth, the section says so.

## 2. Where We Are Today

Numbers from the current `master` working tree, so the discussion starts from
facts rather than impressions:

| Metric                                  | Value                          |
| --------------------------------------- | ------------------------------ |
| Python files under `app/`               | 48                             |
| Python files under `admin_panel/`       | 12                             |
| Test files under `tests/`               | 56                             |
| Test functions                          | ~613                           |
| Test baseline (`docs/TESTING.md`)       | 974 passed, 96 skipped, 4 xfail |
| Files over 500 lines                    | 8                              |
| Files over 1,000 lines                  | 2                              |

The largest files:

| File                                | Lines |
| ----------------------------------- | ----- |
| `app/services/workflow_service.py`  | 1,674 |
| `admin_panel/ui/components.py`      | 1,030 |
| `app/services/pro_flow.py`          | 849   |
| `app/services/monitor_service.py`   | 785   |
| `app/core/messages.py`              | 639   |
| `admin_panel/views/home.py`         | 578   |
| `admin_panel/core/config.py`        | 543   |
| `admin_panel/views/analytics.py`    | 527   |

Two observations matter more than the totals.

**The shape is already mostly right.** The service layer exists and is cut by
responsibility, not by screen. Outbound WhatsApp traffic already goes through
one facade (`app/providers/whatsapp/`), the FSM already lives in one place
(`state_manager_service`), user-facing strings already live in one place
(`app/core/messages.py`), and tests already run against in-memory fakes with
no external services. This document's main job is to make those defaults
explicit so they survive the next ten contributors — human or agent.

**The one file everyone is afraid of is `workflow_service.py`.** At 1,674
lines it is the central dispatcher, it is touched by nearly every feature,
and any change inside it is expensive to review. It is also the file where
"just add another branch" is always the path of least resistance, which is
how dispatchers grow. It is the primary decomposition target — see
[Size Limits](#8-size-limits-and-decomposition).

## 3. Principles

Six ideas the rest of this document follows from. Four of them were paid for
in incidents; the tickets are named so the reasoning is not lost.

**1. One door out.** Every outbound WhatsApp message — every one, from every
process — goes through the `app/providers/whatsapp/` facade (PRO-86). The
facade owns the circuit breaker (PRO-71), the fail-closed authorization gate
(PRO-82) and the operator kill switch. A send that bypasses it bypasses all
three, which is precisely what caused the yellowCard incident. There is no
"quick" direct call; there is only an unprotected one.

**2. The webhook does nothing.** The FastAPI process validates, enqueues, and
returns `200 OK`. All logic runs in the worker. This is what keeps webhook
latency flat under load and what makes a worker crash recoverable — the job
is in Redis, not in a request handler's stack frame.

**3. One service, one domain.** `matching_service` matches, `monitor_service`
watches, `pro_flow` handles what a pro types. A service module is named for
the domain it owns, and a reader should be able to guess which file a piece
of logic lives in from its name alone. When a function reads as belonging to
another service's domain, it is in the wrong file — move it, don't grow a
second copy.

**4. Dependencies are passed, not imported.** Flow handlers receive
`whatsapp` and `lead_manager` as parameters from `workflow_service`, which
owns the shared instances. That is what lets every unit test inject a mock
without patching module globals, and it is why the test suite runs with no
network, no Mongo and no Redis. New handlers follow the same shape.

**5. Text-only menus.** Every WhatsApp menu is numeric or keyword replies —
`"Reply '1' to approve, '2' to reject"` — never interactive buttons.
`send_interactive` exists on the provider ABC and the PRO-89 `CloudAPIProvider`
transport can send it, but nothing in any flow may call it: no template is
approved yet (PRO-87 onboarding is still open) and adopting buttons is an
explicit product decision not yet made (PRO-88 catalog). This is a
CLAUDE.md-level rule and reviewers enforce it.

**6. Every safety check states its failure direction.** Rate limiting fails
open; the outbound egress fails closed. Neither is an accident, and neither
direction is a default — see
[Failure Policy](#12-failure-policy-fail-open-vs-fail-closed). A new check
that does not say which way it fails, and why, is not done.

## 4. Top-Level Structure

The existing top level stays as it is. What it means:

| Folder            | Contents                                                                                     |
| ----------------- | -------------------------------------------------------------------------------------------- |
| `app/api/`        | FastAPI routes. Thin: validate, enqueue, return. No business logic.                          |
| `app/core/`       | Cross-cutting infrastructure: config, constants, messages, DB/Redis clients, logging, prompts. |
| `app/providers/`  | External transport adapters. Today: `whatsapp/` — the single outbound egress (PRO-86).       |
| `app/schemas/`    | Pydantic models for inbound payloads.                                                        |
| `app/services/`   | The domain layer. One module per domain responsibility.                                      |
| `app/worker.py`   | Worker entry point; `app/core/arq_worker.py` and `app/scheduler.py` wire ARQ + APScheduler.  |
| `admin_panel/`    | Streamlit admin app: `core/` (auth, rbac, config), `ui/`, `views/`.                          |
| `scripts/`        | Operational one-shots: seeding, indexes, history cleanup. Documented in `docs/SCRIPTS.md`.   |
| `tests/`          | The whole test suite, including `tests/e2e/`. See [Testing](#14-testing).                    |
| `docs/`           | Operator and developer documentation.                                                        |

The rule that governs the split: **`app/core/` is for code with no domain
opinion.** A Redis client, a phone normalizer, a datetime helper — anything
that would be equally at home in a different product. The moment a function
knows what a lead or a pro is, it belongs in `app/services/` (or
`app/providers/` if it talks to the outside world).

Two placements that look wrong and are not:

- **`app/providers/` is deliberately not under `app/services/`.** A provider
  is transport, not domain logic, and keeping it outside the services folder
  is what makes "services never construct providers" a visible import-path
  rule rather than a convention.
- **`app/core/messages.py` holds domain text in a core folder.** The text is
  domain-flavoured but the *mechanism* — one catalog, no inline strings — is
  cross-cutting, and every service imports it. It stays. See
  [User-Facing Text](#10-user-facing-text).

The admin panel MUST NOT import from `app/services/` except through the
seams built for it (`app.providers.whatsapp.sync.send_text_sync` for sends;
direct Mongo reads through its own config). It is a synchronous process
looking at the same database, not a fourth caller of the async service layer.

## 5. The Service Module

A service is a **module of async functions**, not a class. That is the
established shape here and it stays: there is no instance state worth
holding, and functions with injected dependencies are easier to test than
objects with constructor wiring.

### 5.1 The Layout of a Service Module

Top to bottom:

```python
"""What this service owns, in one or two sentences.

If the module encodes a decision with history (a ticket, an incident),
name it here so the next reader knows the constraint is load-bearing.
"""

# imports

# module constants — only ones private to this service; shared ones
# live in app/core/constants.py

# public entry points — what workflow_service (or the scheduler) calls

# private helpers — underscore-prefixed, ordered roughly by call depth
```

Rules:

- **Public functions are the contract.** They are the ones `workflow_service`
  or the scheduler calls, and they SHOULD be few. Everything else is
  underscore-prefixed and free to change.
- **Handlers take their dependencies as parameters.** `whatsapp` and
  `lead_manager` come in from the caller, exactly as `customer_flow` and
  `pro_flow` do today. A handler that imports the facade directly is harder
  to test and couples the module to process-wide state it does not own.
  (Services that only the scheduler calls, such as `monitor_service`, resolve
  the facade at their own entry point — the rule is that *handlers under
  `workflow_service`'s dispatch* receive it.)
- **No service imports another service's private helpers.** An underscore
  name is a promise that nothing outside the module depends on it. If a
  second service needs the logic, promote it: to a public function on the
  owning service if it is domain logic, or to `app/core/` if it has no
  domain opinion.
- **Pure logic goes in plain functions.** Parsing a pro's reply, deciding
  whether a lead is stale, computing a commission — anything that is
  input → output belongs in a function with no `await`, no DB, no send.
  Those are the five-line tests that never flake.

### 5.2 When a Service Outgrows One File

The threshold is not a line count; it is the sentence test. If you cannot
describe the module in one sentence without "and", there are two modules.
`customer_flow` and `pro_flow` are the precedent — they were the customer
and pro halves of what would otherwise be a single unmanageable flow file.

When splitting, split by **domain seam**, not by mechanics. A
`workflow_helpers.py` grab-bag is the same problem in a second file; a
`pro_flow.py` that owns everything a pro can type is a boundary someone can
hold in their head.

## 6. Layer Rules, With Examples

### 6.1 The Webhook Validates and Enqueues. That Is All.

```python
# BAD — logic in the route. This blocks the webhook response on Gemini
# and Mongo, and a worker restart loses nothing but a request handler
# crash loses the message.
@router.post("/webhook")
async def webhook(payload: WebhookPayload):
    user = await get_user(payload.chat_id)
    reply = await ai.generate_reply(payload.text, user)
    await whatsapp.send_message(payload.chat_id, reply)
    return {"ok": True}
```

```python
# GOOD — validate, enqueue, return. The worker owns everything else.
@router.post("/webhook")
async def webhook(payload: WebhookPayload):
    await arq_pool.enqueue_job("process_message_task", payload.model_dump())
    return {"ok": True}
```

Anything added to the webhook path MUST be justified by needing to run
*before* the 200 is returned — auth token checks and rate-limit shields
qualify; nothing that touches Mongo or an AI model does.

### 6.2 All Sends Go Through the Facade

This is the rule with an incident behind it, and it is absolute.

```python
# BAD — a direct HTTP call to a vendor endpoint. No circuit breaker,
# no kill switch, no dry-run: the exact shape of the yellowCard incident.
async with httpx.AsyncClient() as client:
    await client.post(f"{VENDOR_URL}/messages", json={...})
```

```python
# BAD — constructing a provider. Same problem one layer up: the provider
# transmits, but nothing checks wa:instance:state or the pause keys first.
provider = CloudAPIProvider()
await provider.send_text(chat_id, text)
```

```python
# GOOD — async callers use the process-wide facade.
from app.providers.whatsapp import get_whatsapp

whatsapp = get_whatsapp()
await whatsapp.send_message(chat_id, text)
```

```python
# GOOD — the synchronous admin panel uses the bridge built for it.
from app.providers.whatsapp.sync import send_text_sync

send_text_sync(chat_id, text)
```

Rules:

- Nothing outside `app/providers/whatsapp/` constructs a provider. Ever.
- No HTTP client at a vendor messaging endpoint anywhere in the repo.
- `send_interactive` is defined on the ABC and MUST NOT be called from any
  flow — the PRO-89 transport exists, but no template is approved (PRO-87)
  and adopting buttons is a product decision not yet made (PRO-88).
- Two CI guards enforce this mechanically (the "Guard" steps in
  `.github/workflows/tests.yml`): the build fails on any reference to the
  old vendor's domain, on an `httpx`/`requests` import under
  `app/services/` (allowlist: `geocoding_service.py`, a non-vendor HTTP
  consumer), and on provider construction outside `app/providers/whatsapp/`.
  The `send_interactive` rule remains review-enforced.

### 6.3 `workflow_service` Dispatches. It Does Not Implement.

`workflow_service.py` is the router: it resolves who is talking (customer,
pro, admin), reads the FSM state, and delegates to the flow module that owns
that state. Its failure mode is accretion — each feature adds "just one
branch" of real logic inline, and the dispatcher becomes the implementation.

- A new handler longer than a screen goes in the flow module that owns the
  state, not inline in the dispatch.
- `workflow_service` MUST NOT contain domain logic that has an owning
  service. Emergency bypass and loyalty checks live there today because they
  are routing decisions; a commission calculation would not be.

### 6.4 The Scheduler Schedules. The Service Does the Work.

Every periodic job in `app/scheduler.py` is one line of wiring pointing at a
public function on a service (`monitor_service.check_pro_approval_sla`, and
so on). Job logic in the scheduler file has the same problem as logic in the
webhook: it is in a file named for its trigger, not its domain, and nobody
looks for it there. Intervals are `WorkerConstants`, not literals in the
scheduler.

### 6.5 The Admin Panel Reads; Its Writes Go Through the Seams

Streamlit views MAY read Mongo directly — they are dashboards. Anything that
*sends* goes through `send_text_sync`; anything that mutates a lead SHOULD go
through the same service function the worker would use, so the audit trail
and status transitions stay identical regardless of who triggered them.

## 7. Naming Conventions

The codebase is consistent; this table records the convention so it stays
that way.

| Kind                       | Convention                              | Example                          |
| -------------------------- | --------------------------------------- | -------------------------------- |
| Service module             | `snake_case` + `_service.py`            | `matching_service.py`            |
| Flow module                | `snake_case` + `_flow.py`               | `pro_flow.py`                    |
| Public handler             | `handle_` prefix                        | `handle_pro_text_command`        |
| Scheduled check            | `check_` prefix                         | `check_whatsapp_instance_state`  |
| Private helper             | `_` prefix                              | `_execute_finish`                |
| Enum class                 | `PascalCase`, values `snake_case`/`UPPER` as existing | `LeadStatus`, `UserStates` |
| Constant                   | `UPPER_SNAKE` on `WorkerConstants`      | `MAX_PRO_LOAD`                   |
| Message                    | `UPPER_SNAKE` on a `Messages` subclass  | `Messages.Customer.PRO_FOUND`    |
| Redis key                  | `namespace:subject[:qualifier]`         | `wa:instance:paused:manual`      |
| Test file                  | `test_` + subject module                | `test_matching_service.py`       |

Two rules with reasons:

- **A `handle_` function is an entry point; a `_handle_` function is a
  branch of one.** `pro_flow` uses exactly this split — one public
  `handle_pro_text_command`, twenty private `_handle_*` branches — and it is
  the right shape: the module's surface is one function, and the branches
  are free to be reorganised.
- **Redis keys are part of the architecture, not string literals.** A key
  that two modules read (`wa:instance:paused`) MUST be defined once and
  imported — the monitor imports `_PAUSE_KEY` from the facade for exactly
  this reason. Two spellings of one key is an outage with a delay on it.

## 8. Size Limits and Decomposition

| Threshold     | Level       | What it means                                                       |
| ------------- | ----------- | ------------------------------------------------------------------- |
| **500 lines** | **Notice**  | The sentence test applies. Say in the PR what the module owns.      |
| **800 lines** | **Warning** | Decomposition is overdue. Raise it in review; splitting may be a separate task. |
| **1,200 lines** | **Critical** | Do not add to the file without a plan to split it, stated in the PR. |

The numbers are calibrated to this repo, not copied from a frontend
standard: a Python service module and a React component have different
natural sizes. Against the current tree, `workflow_service.py` (1,674) is
critical, `admin_panel/ui/components.py` (1,030) is warning, and `pro_flow`
(849) sits at the warning line; `monitor_service` (785) is at notice.

**A line count is a symptom, not the disease.** `app/core/messages.py` at
639 lines is a string catalog — splitting it by count alone would achieve
nothing, and it is exempt for the same reason a declaration table is. The
real questions:

- Can you describe the module in one sentence without "and"?
- Do two unrelated features force changes to the same file?
- Would a test for one function have to arrange state the function never
  reads?

Any "yes" means there is a split waiting, whatever the count says.

**How to split, in order of preference:**

1. **Pull pure logic out first.** Decision functions — no `await`, no I/O —
   into module-level functions or, when shared, into `app/core/`. This
   removes volume and adds testability in the same move.
2. **Then split by domain seam**, the way `customer_flow`/`pro_flow` split
   from a single flow file. The seam is "who owns this state", never "these
   functions are all long".
3. **Only then consider mechanical splits** (a `_dashboard.py` next to
   `pro_flow.py`), and only when a real seam exists.

The enforcement mechanism (lint gate vs review) is
[question 2](#17-open-questions).

## 9. State and the FSM

The FSM is the backbone of every conversation, and its integrity rules are
absolute:

- **All state reads and writes go through `state_manager_service`.** No
  service touches the Redis state keys directly. The state manager owns the
  key shape, the TTL logic and the `UserStates` validation.
- **Every state is declared in `UserStates`** (`app/core/constants.py`)
  before anything sets it. A state string invented at a call site is a
  branch the dispatcher does not know about — the bug reports as "the bot
  stopped answering".
- **Every state has exactly one owner.** The module that sets a state is the
  module whose handler consumes it, and the dispatcher routes that state to
  that module. `PRO_AWAITING_FINAL_PRICE` is set by `pro_flow` and handled
  by `pro_flow`; that pattern is the rule.
- **A state with a deadline gets a TTL, and the TTL is a constant.**
  `PAUSE_TTL_SECONDS`, `FINAL_PRICE_TTL_SECONDS` — the timeout is part of
  the product behaviour, so it lives in `WorkerConstants` where it can be
  found, reviewed and tested, not inline at the `set_state` call.
- **A new state ships with its escape hatch.** Ask, before merging: what
  happens if the user never replies? If the answer is "they are stuck",
  the state needs a TTL, a scheduled sweep, or both. The SOS healer exists
  because states without exits become tickets.

Chat context (last 20 messages) is `context_manager_service`'s job and is
not FSM state. Do not encode conversational memory into states; that is what
the context window is for.

## 10. User-Facing Text

**Every string a user can see lives in `app/core/messages.py`.** No Hebrew
(or English) user-facing literal at a call site, ever.

```python
# BAD — inline text. Untranslatable, unreviewable by anyone reading only
# the message catalog, and unfindable when product asks "where does the
# bot say X?"
await whatsapp.send_message(chat_id, "תודה! איש מקצוע יחזור אליך בקרוב.")
```

```python
# GOOD — the catalog is the single place product copy lives.
await whatsapp.send_message(chat_id, Messages.Customer.PRO_ASSIGNED)
```

- Placeholders use `str.format` names (`{pro_name}`), and the format call
  happens at the send site, where the values are.
- **Every menu is text-only** — numbered or keyword replies, with the
  options spelled out in the message itself. See Principle 5; there are no
  exceptions pending PRO-88/89.
- Messages are grouped by audience (`Customer`, `Pro`, `Admin`, `Errors`)
  and new ones join the right group rather than a new top-level.
- RTL and emoji conventions follow the surrounding catalog; a message that
  looks different from its neighbours is a review comment.

## 11. Configuration and Constants

Two files, two jobs, and the distinction is the rule:

- **`app/core/config.py`** (pydantic-settings) holds anything that varies by
  *environment* — URIs, keys, provider selection, feature toggles an
  operator flips. It validates at startup and refuses to boot on invalid
  combinations (`ENVIRONMENT` validation, the PRO-86 `WEBHOOK_TOKEN`
  requirement in prod-like environments). New settings follow that pattern:
  **a misconfiguration that would be dangerous at runtime MUST be a startup
  failure instead.**
- **`app/core/constants.py`** holds anything that varies by *product
  decision* — thresholds, intervals, radii, caps, enums. These change in a
  PR, with review, not in an environment file at 2 a.m.

Rules:

- **No magic numbers in service code.** A `600` at a call site is a bug
  report waiting for the person who changes one of its two copies. If the
  number has a meaning, it has a name on `WorkerConstants`, and the name
  says what the number does (`PRO_SEARCH_RATE_LIMIT_SECONDS`, not
  `TIMEOUT_2`).
- **A constant that gates product behaviour gets a comment or a docstring
  line saying what breaks if it changes.** The existing file does this
  well; keep it up.
- Settings are read from `settings`, never from `os.environ` in service
  code — the settings object is the one place defaults, validation and
  documentation coexist.
- **Every credential-bearing setting is a `SecretStr` (PRO-94).** pydantic's
  default `__repr__` prints all field values, so before this rule any
  traceback that touched the settings object — a pytest `AttributeError`, a
  Sentry exception context, a Railway deploy log — dumped the whole secret
  set in plaintext. It happened on 2026-08-09 and forced a full rotation.
  The convention is enforced by name: any field ending in `TOKEN`, `KEY`,
  `SECRET`, `PASSWORD`, `DSN`, `_URI` or `_URL` must be `SecretStr`, and
  `tests/test_settings_secret_masking.py` fails the build otherwise.

```python
# BAD — a bare str. Masks nothing; one traceback away from a rotation cycle.
META_ACCESS_TOKEN: str | None = None

# GOOD — masked in repr(), str() and f-strings; unwrapped only where used.
META_ACCESS_TOKEN: SecretStr | None = None
```

- **Unwrap at the point of use, never into a name that outlives it.**
  `settings.MONGO_URI.get_secret_value()` handed straight to a driver
  constructor is fine; assigning it to a module-level constant produces a
  plain `str` that the next `logger.info(f"...")` will happily print. The
  log-redaction filter in `app/core/logger.py` is the second layer, sourced
  from the `SecretStr` fields automatically — a new credential is covered the
  moment it is typed correctly, with no list to remember.
- **`SecretStr` only protects an object that already exists.** A
  `ValidationError` raised *during* `Settings` construction (e.g. PRO-96's
  environment cross-check) fires before any field is wrapped, so pydantic's
  default error text echoed the raw input — env vars included — under
  `input_value=`. `Settings.model_config` sets `hide_input_in_errors=True`
  (PRO-99) to close that gap; it covers `__str__`/`__repr__`/tracebacks only
  — `ValidationError.errors()`/`.json()` still carry the raw input dict, so
  no boot handler may render either.

## 12. Failure Policy: Fail-Open vs Fail-Closed

This codebase makes both choices, on purpose, and the pattern generalises:

| Check                                        | Direction    | Why                                                                     |
| -------------------------------------------- | ------------ | ----------------------------------------------------------------------- |
| Rate limiting (`security_service`)           | Fail-open    | A Redis blip must not silence every customer. Worst case: brief over-serving. |
| Daily AI cost cap                            | Fail-open    | Same: availability beats cost control for minutes-long windows.         |
| Outbound egress on missing `wa:instance:state` | Fail-closed | Sending through a deauthorized instance is the incident (PRO-82). Worst case of blocking: a delayed message. |
| Kill switch on Redis error                   | Fail-open    | The pause key is an operator tool; its absence of *evidence* must not halt the product. |
| `ENVIRONMENT` / `WEBHOOK_TOKEN` validation   | Fail-closed (at boot) | An unauthenticated prod webhook must never come up at all.        |

The rule for new work: **every new gate, check or guard states its failure
direction in its docstring, with the one-line reason** — which is worse, the
check wrongly passing or the check wrongly blocking? A check whose author
never chose has chosen whatever the exception handler happens to do.

Corollaries:

- Fail-open checks MUST log when they fail open (the security service's
  abuse-trip escalation is the model — repeated trips escalate from
  `warning` to `error`, which reaches Sentry).
- Fail-closed checks MUST page (`send_oncall_alert`) rather than silently
  block, because a silent fail-closed is an outage nobody is looking at.

## 13. Documentation

- **`CLAUDE.md` is the operational contract** — the rules that must survive
  every session, human or agent. It stays short; this document carries the
  depth.
- **`docs/ARCHITECTURE.md` describes; this file prescribes.** A change that
  alters the system's shape updates both.
- **`docs/TESTING.md` is the single source of truth for the test baseline.**
  A PR that changes the pass/skip count updates that line in the same PR.
- **Docstrings carry the why, not the what.** The provider package's
  docstrings are the house style: name the ticket or incident when the code
  is shaped by one, so the constraint reads as load-bearing rather than
  arbitrary. A comment that restates the next line is noise; a comment that
  explains why the obvious alternative is wrong is the whole point.
- After a code-change task, the **docs-syncer** subagent (incremental mode)
  brings stale `.md` files up to date — this is already the session rule in
  `CLAUDE.md`.

## 14. Testing

### 14.1 What the Structure Buys

The dependency-injection convention (Principle 4) is what makes the suite
cheap: handlers take `whatsapp` and `lead_manager` as parameters, so a test
passes mocks and asserts on calls — no patching of module internals, no
network, no services. The in-memory substitutions are project-wide:
`mongomock_motor` for Mongo, `fakeredis` for Redis (PRO-78), `monkeypatch`
for the `whatsapp` and `ai` instances, auto-applied by `conftest.py` to
every non-integration test.

| Layer                         | Test style                                        | Needs services? |
| ----------------------------- | ------------------------------------------------- | --------------- |
| Pure decision functions       | plain unit test, input → output                   | No              |
| Flow handlers                 | inject mocks, assert sends and status transitions | No (in-memory)  |
| `$geoNear` matching           | mock `aggregate` as an async generator (mongomock cannot run it) | No |
| Integration (`@pytest.mark.integration`) | real `MONGO_TEST_URI`, cleared per run | Mongo           |
| E2E (`tests/e2e/`)            | scripted conversations through the real dispatch  | No (in-memory)  |

### 14.2 Rules

- **The baseline is a floor.** `docs/TESTING.md` holds the current
  pass/skip/xfail counts; a PR that lowers passed or raises skipped without
  updating the line and saying why is a regression.
- **New logic ships with its tests in the same PR.** A new handler, branch
  or bugfix without coverage is not done; the test-writer subagent exists
  for exactly this and writes only under `tests/`.
- **Test the seam that exists.** Handlers are tested through their public
  entry point with injected mocks — not by importing their `_private`
  branches, which are free to be reorganised.
- **Test names describe behaviour**, in the style already in the suite:
  what the user did, what the system must do. Never a ticket number as the
  whole name.
- **xfail is for documented product defects**, with the defect described at
  the marker — the four current xfails are the model. It is not a way to
  mute a broken test.
- `asyncio_mode = strict` is set; every async test is explicitly marked or
  fixture-driven, no implicit event loops.

## 15. AI Coding Agents

A significant share of changes here are written with AI assistance, and an
agent's failure mode is predictable: given no context it produces something
**plausible** — a direct HTTP call to a messaging endpoint, an inline Hebrew
string, a state set by name at a call site, a second spelling of a Redis
key. Locally reasonable, globally wrong, and fast.

The mitigations are already part of the repository, and keeping them true is
part of every conventions change:

- **`CLAUDE.md` is the agent rulebook.** It is loaded every session, it
  carries the absolute rules (text-only menus, single egress, the CI guard's
  trip condition), and it is updated in the same PR as any change to what it
  describes. A stale rulebook is worse than none — an agent follows it
  literally, at scale.
- **The CI guard is the pattern to extend.** A rule an agent can violate
  plausibly deserves a grep-level gate, not a review comment; the vendor-
  domain guard already proves the shape works. Candidates are
  [question 3](#17-open-questions).
- **The subagents encode the workflows** — test-runner for the baseline,
  test-writer for coverage, docs-syncer for stale docs, code-reviewer for
  the checklist. Using them is not optional ceremony; they are how the
  conventions survive a session that never read this file.
- **Never invent.** No invented Redis keys, states, constants, settings or
  message names. If it cannot be found, say so — a stated gap costs a
  question; a fabricated one costs a debugging session.

## 16. Migration Plan

**No big-bang refactor.** The standard is mostly a record of existing
practice, so the migration is small — but the parts that are new are
sequenced so nothing blocks feature work.

### Phase 0 — Agree and record

Review this document, close the [open questions](#17-open-questions), merge
it. Update `CLAUDE.md` to point here for depth. Nothing in the application
code changes.

### Phase 1 — New code only

Every new service, handler, state, constant and check follows the standard
from day one — including the failure-direction docstring rule
([12](#12-failure-policy-fail-open-vs-fail-closed)), which is the only
genuinely new writing obligation.

### Phase 2 — Boy scout rule on touched code

When working inside an existing file: extract the pure logic you are
modifying, name the magic number you are changing, move the inline string
you are editing into `messages.py`, add the failure-direction line to the
check you are touching. Do **not** restructure code you are not otherwise
changing in the same PR — it buries the real diff.

### Phase 3 — Scheduled refactors

One file earns a scheduled task of its own:

| Priority | File                               | Lines | Note                                                             |
| -------- | ---------------------------------- | ----- | ---------------------------------------------------------------- |
| 1        | `app/services/workflow_service.py` | 1,674 | The dispatcher. Split by domain seam; dispatch table stays, implementations move to their owning flows. |
| 2        | `admin_panel/ui/components.py`     | 1,030 | Split by view area; lower risk, lower urgency.                   |

Each refactor is behaviour-preserving, lands as small reviewable PRs, and
MUST NOT share a PR with a behaviour change. The e2e state-matrix suite is
the regression net for the dispatcher work — run it before and after every
step.

## 17. Open Questions

| #   | Question                                             | Status |
| --- | ---------------------------------------------------- | ------ |
| 1   | Are the size thresholds in [8](#8-size-limits-and-decomposition) right for this repo? | Open — proposed 500/800/1,200 |
| 2   | Are size limits lint-enforced or review-enforced?    | Open — proposal: review-enforced now, revisit if it slips |
| 3   | Which egress rules get a CI guard?                   | **Decided — implemented.** Both candidates guard the build; see [6.2](#62-all-sends-go-through-the-facade) |
| 4   | Should `admin_panel/core/config.py` (543 lines) share `app/core/config.py` instead of duplicating settings? | Open — needs a look at what actually overlaps |
| 5   | Message catalog growth: split `Messages` by audience into modules at some size, or never? | Open — no action below ~1,000 lines |

---

**1. Size thresholds.** 500/800/1,200 is calibrated against the current
tree so that the dispatcher is critical, the known-heavy modules are
warnings, and everything healthy is untouched. If the team prefers one
number, 800 is the one to keep.

**2. Enforcement.** A lint gate (`flake8` plugin or a CI line count) is
cheap to add but needs an exception list from day one (`messages.py`).
Review enforcement costs nothing today and fails silently later. Proposal:
review now, and the first time a file crosses a threshold without anyone
noticing, add the gate.

**3. CI guards for the egress.** The vendor-domain guard proves the
mechanism. A grep for `httpx`/`requests` under `app/services/` (with a
stated allowlist) and for provider construction outside the package would
turn the two BAD examples in [6.2](#62-all-sends-go-through-the-facade) from
review comments into build failures. Cheap, and worth doing.

> **Decided: implemented.** Both guards run as the "single egress" Guard
> step in `.github/workflows/tests.yml`. The allowlist is
> `geocoding_service.py` (Nominatim); tests are outside the guard's scope
> because they construct providers in order to test them.

**4. Admin panel config duplication.** `admin_panel/core/config.py` is the
sixth-largest file in the repo and at least part of it restates what
`app/core/config.py` already knows. Whether the overlap is real or
superficial needs an hour of reading before it needs a decision.

**5. Splitting the message catalog.** One file is currently an asset — one
place product copy lives. It stops being an asset when merge conflicts on it
become routine. No threshold proposed; revisit when it hurts.
