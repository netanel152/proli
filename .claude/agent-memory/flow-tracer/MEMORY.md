# flow-tracer memory

## Pattern: instrumented GUARD_CHAIN trace
The fastest way to prove dispatch order is to monkeypatch `dispatch_guards.GUARD_CHAIN`
with wrapped guards recording `(name, ctx.current_state before/after, HANDLED|None)`,
then drive `process_incoming_message` with the `wf_mocks`-style fixture from
`tests/test_dual_role_routing.py`. `run_guard_chain` reads the module global, so the
patch takes. Back the fake StateManager with a dict so Redis-vs-local divergence shows.

## Pattern: env needed to run pytest here
No `.env` is checked in. Prefix runs with
`GEMINI_API_KEY=x CLOUDINARY_CLOUD_NAME=x CLOUDINARY_API_KEY=x CLOUDINARY_API_SECRET=x`
or `Settings` refuses to construct and collection fails.

## Pattern: late-bound `wf.<name>` guard references
Guards resolve collaborators via a call-time `from app.services import workflow_service as wf`,
so a typo fails at runtime, not import, and flake8 reports the workflow-side imports as unused.
Audit with a regex over `inspect.getsource` of every guard + `hasattr(wf, n)` — 33 refs today,
all resolving. Same trick for `deps.` and `ctx.` attribute names.

## Confirmed invariant: bypass must sit immediately before pro_mode_routing
`guard_pro_business_keyword_bypass` never returns HANDLED — it only mutates
`ctx.current_state` to PRO_MODE for the next guard. PRO_MODE is in PRO_DISPATCH_STATES,
so `pro_mode_routing` always catches the mutation; nothing may be inserted between.
Pinned by `test_pro_bypass_runs_immediately_before_pro_mode_routing`.

## Edge case: CUSTOMER_PROMPT_STATES make the bypass deferral partly unreachable
All four `CUSTOMER_PROMPT_STATES` (reschedule, loyalty, new-or-existing, cancel-confirm)
are terminally handled by guards at indices 13-16, above the bypass at 19. So the
state half of `_customer_prompt_pending` never decides anything from the bypass; only
the `waiting_for_rating` / `waiting_for_review_comment` lead lookup can. Pre-existing,
position does the work the deferral was written for.

## Edge case: AWAITING_ADDRESS no-lead fall-through leaves Redis and ctx disagreeing
`guard_awaiting_address` clears Redis (-> IDLE) but leaves `ctx.current_state` at
AWAITING_ADDRESS, so `pro_registration_keyword` and `pro_autodetect` (both IDLE-keyed)
do not fire on that pass. Deliberately preserved from the inline original (PRO-181).
Harmless today: the only downstream reader is `current_state != UserStates.PRO_MODE`
in the PRO-116 Q3 booked-job gate (conversation_pipeline.py:182 since PRO-182; it was
workflow_service.py:806), and IDLE and AWAITING_ADDRESS satisfy it identically.

## Known edge case: emergency hoist + PENDING_ADMIN_REVIEW double user-log
Hoist "released" path logs the inbound and sets `ctx.emergency_inbound_logged`;
`guard_pending_admin_review_shortcircuit` logs the inbound again unconditionally
(it does not consult that flag). Reachable when a chat holds both a live NEW/CONTACTED
(or BOOKED + menu state) lead and a <24h PENDING_ADMIN_REVIEW lead. Pre-existing,
not introduced by PRO-181. Step 1's own log is correctly skipped.

## Pattern: verifying a PRO-139 extraction slice
Three cheap checks settle a "moved verbatim" claim. (1) Slice the pre-image tail out of
`git show HEAD:<file>` and `diff -u` it against the new body from the first shared marker
line — empty diff means byte-identical. (2) AST-walk the new function for free Names minus
stores/args/module imports; anything left but the `except ... as e` name is unbound.
(3) Check every `wf.<attr>` in the prologue against module-level names in workflow_service.

## Confirmed invariant: ctx hand-off is safe because nothing defers
`run_customer_pipeline`'s prologue reads seven facts off `ctx` after the chain returns.
Only three ctx fields are ever written (`is_exempt`, `emergency_inbound_logged`,
`current_state` — 2 direct writes + 4 `refresh_state` calls), all inside awaited guards.
The only `spawn_background_task` on this path is the typing indicator and it never
touches ctx, so no guard can mutate ctx after the prologue has read it.

## Edge case: MagicMock hasattr defeats an AsyncMock fallback
`sec = security or MagicMock(); if not hasattr(sec, "check_and_increment_daily_ai_cap")`
never installs the AsyncMock — MagicMock auto-creates any attribute, so hasattr is always
True and the pipeline awaits a plain MagicMock. Use `AsyncMock()` as the default, or
`MagicMock(spec=SecurityService)`. Seen at tests/test_conversation_pipeline.py:66.
