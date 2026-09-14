---
name: dispatch-guard
description: Add, move or change a guard in the inbound-message dispatch chain (app/services/dispatch_guards.py GUARD_CHAIN) or a step in the customer pipeline it falls through to. Use whenever a task adds a new keyword, a new UserStates holding state, a new pre-gate, or changes which branch answers a message first.
paths:
  - "app/services/dispatch_guards.py"
  - "app/services/conversation_pipeline.py"
  - "app/services/workflow_service.py"
---

# Adding or moving a dispatch guard

The chain is data: `GUARD_CHAIN` in `app/services/dispatch_guards.py` is an ordered tuple
of `(name, guard)` pairs, and **its order is the product** — PRO-121's emergency hoist,
for one, is defined by what it runs after and before. Read the module docstring first; it
says why a tuple and not a state-keyed dict. Nothing below is a value to trust over the
code: every name, count and position is owned by `dispatch_guards.py` and the tests that
pin it.

## What a guard is

```python
async def guard_<name>(ctx: DispatchContext, deps: GuardDeps):
    if <not my case>:
        return None          # fall through to the next guard
    ...                      # answer via deps.whatsapp / deps.state_manager
    return HANDLED           # dispatch stops here
```

- `DispatchContext` (`chat_id`, `user_text`, `normalized_text`, `media_url`,
  `is_emergency_detected`, `current_state`, `is_exempt`, `emergency_inbound_logged`) is the
  shared mutable state. A guard that clears state **re-reads `ctx.current_state`** for the
  guards after it — see how the zero-touch and reset guards do it.
- `GuardDeps` carries the injected collaborators (`whatsapp`, `state_manager`,
  `context_manager`, `users_collection`, `security`, `settings`). Use them, never module
  globals, so the guard is testable against fakes without monkeypatching.
- Helpers that still live in `workflow_service` are resolved **per call** through the module
  (`from app.services import workflow_service as wf; wf._handle_status_query(...)`), never
  imported at module top — late monkeypatching in the suite depends on it.
- Return exactly `HANDLED` or `None`. `run_guard_chain` raises on anything else.

## Procedure

1. **Decide the position by what must outrank it.** Write down which existing guards may
   *not* be bypassed by the new one (a live human handoff, consent, the rate limiter, the
   admin wizard usually outrank everything) and which holding state would swallow its
   trigger if it ran later. That sentence becomes the guard's docstring.
2. **Define the function directly above the guard that follows it in the chain.**
   `tests/test_agent_pack_drift.py::test_guard_chain_runs_in_source_definition_order`
   pins `GUARD_CHAIN`'s order to source definition order, so chain order and file order
   must agree.
3. **Insert the `(name, guard)` pair** at that position in `GUARD_CHAIN`.
4. **Update the three order pins, in the same commit:**
   - `tests/test_dispatch_guards.py::test_guard_chain_order_is_pinned` — the full list of
     names; plus any relative-position test that names your neighbours
     (`test_pro_bypass_runs_immediately_before_pro_mode_routing`, the emergency-hoist
     position test, the holding-states membership test).
   - `tests/test_agent_pack_drift.py::_DISPATCH_SEQUENCE` — a `(bold label, unique source
     anchor)` pair for the new branch; the anchor must occur **exactly once** across
     `dispatch_guards.py` + `workflow_service.py` + `conversation_pipeline.py`.
   - `.claude/agents/flow-tracer.md` `## Dispatch Order` — a numbered line whose bold label
     matches the sequence entry exactly, and the entry count in the intro paragraph.
5. **Write the guard's own tests** in `tests/test_dispatch_guards.py` against a `deps`
   fixture built from fakes: the case it handles (returns `HANDLED`, sends the right
   `Messages.*` constant), the case it must ignore (returns `None`, touches nothing), and
   the state it must not break (a message from PRO_MODE, from an ADMIN_ state).
6. **If it introduces a holding state**, give it a TTL in `WorkerConstants` with a comment
   saying what expiry means for the customer (see `CANCEL_CONFIRM_TTL_SECONDS`), add the
   state to `UserStates`, and update `flow-tracer.md`'s `## UserStates` list — drift tests
   fail otherwise.
7. Any message it sends follows the `whatsapp-copy` skill; any keyword it accepts is a
   `Messages.Keywords` list, never a literal.

## Run before committing

```bash
pytest tests/test_dispatch_guards.py tests/test_agent_pack_drift.py tests/e2e/test_e2e_state_matrix.py -q
```

The PRO-83 state × input matrix (`tests/e2e/test_e2e_state_matrix.py`) is the wide net:
a new guard that steals a message from an existing state shows up there as a changed cell.
