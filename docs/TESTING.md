# Proli Testing Guide

The test suite uses `pytest` with `pytest-asyncio` in strict mode (`asyncio_mode = strict`). All unit tests use `mongomock_motor` (in-memory MongoDB) — no real database or external API required.

**Current status:** no count lives in this file. CI fails a PR that passes fewer tests than `dev` and keeps that number itself; see "The test floor" in [`CI_WORKFLOWS.md`](./CI_WORKFLOWS.md).

**Skips:** integration tests skip when `MONGO_TEST_URI` is not set. The other skips are cells of the PRO-83 state × input matrix, each carrying a documented reason from `NA_REASONS` and still visible in the generated table. **All of them are deliberate**: a cell is N/A when another cell already proves the same thing (the whole `race` column, the `ttl-class`/`same-reprompt`/`same-exit` collapses, and `pro-text-only`/`admin-menu` for input types those menus never receive). PRO-186 lit up the last 15 that were not by design: `defect-finish`, `defect-cancel` and `defect-price` are gone, along with the two strict xfails that documented the same defect.

---

## 1. Running Tests

```bash
# Canonical way to run the suite (from the project virtualenv) — PRO-50
# Unit tests need neither a real MongoDB nor a real Redis.
pytest

# Verbose (show each test name)
pytest -v

# Short traceback
pytest --tb=short

# Single file
pytest tests/test_matching_service.py

# Filter by name
pytest -k "sos"

# Stop on first failure
pytest -x

# Integration tests (requires MONGO_TEST_URI in .env)
pytest -m integration
```

---

## 2. Test File Index

### Core Flow

| File | What it covers |
|------|---------------|
| `test_workflow_orchestrator.py` | Central routing: reset commands, pro auto-detect, AWAITING_ADDRESS, AWAITING_PRO_APPROVAL, PAUSED_FOR_HUMAN, SOS→TTL, deal finalization, no-pro fallback, PRO-63 `PENDING_REVIEW_SHORTCIRCUIT_HOURS` recency-bounded short-circuit; PRO-121 emergency escalation out of holding states (soft hold, address gate with/without a known city, loyalty menu), the once-per-lead paused-for-human operator page, and expedited dispatch (assigned-pro fast path and Smart Dispatcher) that finalizes without a `[DEAL]` marker |
| `test_conversation_pipeline.py` | PRO-182 (PRO-139 slice B) `conversation_pipeline.run_customer_pipeline` extraction seam: collaborators are resolved per call so late monkeypatching still works, `workflow_service` is never imported at module level, `emergency_inbound_logged` still suppresses step 1's duplicate log, `is_exempt` gates the daily AI cap at both in-pipeline call sites (dispatcher phase and the assigned-pro fast path), and the pipeline receives the same `ctx` object the guard chain mutated rather than a copy |
| `test_smart_dispatcher_logic.py` | Dispatcher AI: missing info → clarify, city+issue → handoff to pro, audio transcription flow |
| `test_pro_flow.py` | Pro commands: approve, reject (PRO-117 rematch handoff), finish (multi-job selection), pause bot, resume, dashboard fallback, vacation mode, PRO-63 `מצא` reassignment-lifecycle reset after escalation |
| `test_pro_flow_reject_rematch.py` | PRO-117 pro-reject → rematch: atomic claim (`expected_status=NEW`), handoff to `monitor_service.reassign_lead(notify_old_pro=False)`, `rejected_by` exclusion accumulating across reject hops, double-tap fat-finger guard, no-replacement/`reassign_lead`-raises/`reassign_lead`-returns-False fallback to `_escalate_rejected_lead` (PENDING_ADMIN_REVIEW + admin page + customer message), approval-SLA re-arm on success, context not cleared on success |
| `test_customer_flow.py` | Post-job: completion checks, rating prompts, review collection |
| `test_dual_role_routing.py` | Pro-as-customer routing: `לקוח` mode switch, sticky CUSTOMER_MODE while their own lead is open, context-aware keyword bypass, soft-hold escape |

### Matching & Routing

| File | What it covers |
|------|---------------|
| `test_matching_service.py` | `$geoNear` pipeline, progressive radius (10→20→30 km), no-pro-at-max-radius returns None, text fallback, load balancing, excluded pro IDs, rating sort |
| `test_geocoding_service.py` | Static dict lookup, Redis cache hits/misses, Google Maps API calls with bounding-box validation, fallback chain, PRO-19 definitive-vs-transient miss split (`GeocodingUnavailable`, TTL choice) and the `geo:unavailable` circuit breaker; PRO-27's status-bearing `geocode()` (`GEOCODE_RESOLVED`/`UNRESOLVED`/`UNAVAILABLE`, the `__RETRY__` transient sentinel) and the pro-service-area helpers — `resolve_service_areas`/`resolve_service_areas_sync` sorting and dedup, `ServiceAreaResolution.location`/`clean`/`needs_recheck`/`blocks_approval`/`mongo_update`, and `parse_service_areas` |
| `test_check_pro_service_areas.py` | PRO-27 `scripts/check_pro_service_areas.py`: per-pro verdict (`OK`/`MISSING_LOCATION`/`PARTIAL`/`UNREACHABLE`/`UNAVAILABLE`), `--apply` writes (`location` only when missing unless `--overwrite-location`, plus the verdict fields), `--include-inactive` filter scope, the exit-code contract (0/1/2/3), and the empty-population path — zero matching pros exits `EXIT_NOTHING_CHECKED` rather than a vacuous `OK`, and the report names which population is empty (none on record / all awaiting approval / approved-but-paused, with `--include-inactive` pointed at) |
| `test_pro_phone_normalisation.py` | PRO-224: `to_il_mobile` maps every typed format (`050-…`, `+972 50…`, `+972 050…`, `00972…`, suffixed) to `9725…` and rejects landlines, `072`, wrong lengths and the coverage-matrix reserved block; the panel's `check_pro_phone` seam (sync mongomock) saves that form, rejects an invalid phone and one another pro holds in any stored form, and lets a pro be saved over its own number; a normalised pro is found by `start_onboarding`'s `[phone, chat_id]` lookup; `scripts/normalize_pro_phones.py` dry run (masked, no writes), `--apply`, invalid-left-alone, duplicate refusal and empty population |
| `test_stale_nudger.py` | Periodic reminders for booked leads > 24h old |
| `test_approval_sla.py` | PRO-56 approval SLA: T+10 pro nudge, T+25 customer reassignment offer, emergency-halved thresholds, idempotency, business-hours gate, and the customer 1/2 reply handling |
| `test_reassign_escalation.py` | PRO-63/PRO-117 `reassign_lead`: exhausted `MAX_REASSIGNMENTS` escalates to `PENDING_ADMIN_REVIEW` (never `CLOSED`), immediate admin alert (and best-effort survival if it fails), customer notification including the no-usable-location branch, `notify_old_pro` toggle, PRO-56 SLA re-arm on success (state/context clear only for CONTACTED leads), idempotency guard, race-safe `expected_status` write, and that exhaustion is checked before matching/reassigning |
| `test_scheduler_gating.py` | PRO-73 gating primitives: `within_business_hours` (Israel 08–21) and the `_customer_cold_job_allowed` toggle+hours gate (default OFF) for cold customer-facing jobs |
| `test_seed_coverage_matrix.py` | PRO-84 staging coverage matrix: the 27-professional seed's shape, reserved phone block, determinism and `--purge` scoping — plus the **real `determine_best_pro` run against the seeded matrix**, asserting each of the ten routing scenarios' winner by name (rating sort, load balancing, 10→20→30 km expansion, coverage gap, geocoding, text fallback, reverse match, ineligibility filter) |
### Infrastructure

| File | What it covers |
|------|---------------|
| `test_unit_lead_manager.py` | Lead CRUD in isolation |
| `test_booking_and_messaging.py` | Slot booking and messaging flows |
| `test_security_service.py` | Rate limiting (Redis fixed-window) |
| `test_consent_flow.py` | Privacy consent gate |
| `e2e/test_e2e_chat_lock.py` | A real arq worker with an upload longer than the chat lock and a queued follower: the follower is not lost (real seconds, about 13s). The lock's owner token and its renewal during a turn |
| `e2e/test_e2e_consent_hold.py` | Pre-consent messages, against real Redis/Mongo fakes: before an answer, on decline and on expiry the words are in no Mongo collection, no other Redis key, no log line and no AI call; an accept replays them through the pipeline exactly once, including on a double accept, a job that dies before the log (retried, and the next message is not asked for consent again) and after it (not repeated, and an emergency pages the operator); a guard below the gate cannot fire twice or lose the opening; Meta media is not fetched or uploaded before consent; a real arq worker run leaves the job's args in neither `arq:result` nor the logs |
| `e2e/test_e2e_pro_lead_choice.py` | PRO-212, through the orchestrator: with two NEW leads *אשר*/*דחה* act on nothing until the pro picks, and only the chosen customer's details arrive; a pick or claim on a lead that moved away reveals nothing (PRO-59); a digit past the list does not run a command, and a bare digit outside a list opens one rather than acting; the SOS alert reaches a pro only for a NEW or BOOKED lead, and the NEW one carries no customer words; a pro on break with no `PRO_MODE` reaches the pro handlers and stays out of matching, while an application awaiting review and a pro the admin deactivated reach none of them; *השהה*/*המשך* ask which job when several qualify; *ביטול* never cancels by itself, even after an expired prompt; an active pro with their own request open keeps their digits; the approval SLA moves a deactivated pro's offer and leaves a booked job |
| `test_media_handler.py` | Media type detection, image download, audio/video URL handling |
| `test_notification_service.py` | WhatsApp notifications (best-effort, no SMS fallback) |
| `test_notification_offer.py` | Shared lead-offer builder: `build_new_lead_message`/`format_lead_extra_info`/`format_media_links` (pure, Hebrew fallbacks) and `notify_pro_new_lead` (offer only, no navigation link since PRO-59, fail-open) as used by `monitor_service`'s reassignment path and `admin_flow`'s assignment path |
| `test_whatsapp_state_monitor.py` | PRO-20 WhatsApp deauth monitor: `get_state_instance` (incl. a `NotImplementedError` provider reading as `None`, not crashing), `send_oncall_alert` state-guarded WhatsApp routing (no SMS), `check_whatsapp_instance_state` FSM/Redis branches |
| `test_analytics_queries.py` | Admin-panel analytics aggregations (sync, PRO-140) — funnel, GMV/commission, pro performance, FinOps |
| `test_admin_lead_queries.py` | PRO-161 `admin_panel/core/lead_queries.save_lead_edits`: row identity read from the returned editor frame (not a stale snapshot), status-history append on real transitions only, `details_summary` → `details`/`issue_type`, unassign nulls `pro_id`, out-of-range/non-integer/unparseable row keys and no-op/lead-deleted-under-you rows all skip with a reason instead of crashing, audit called once per successful row |
| `test_admin_fast_reruns.py` | The real `AppSession`, `ScriptRunner` and view with a blocking stubbed offer and a real `AppSession.request_rerun` during it, on each of the strip, the Edit form and the table: with `runner.fastReruns` false the outcome reaches the operator once and no second run starts during the send; with it true the same assertion fails, which is why the setting exists. Also pins `.streamlit/config.toml` (read back by Streamlit itself in a subprocess), that the Docker build keeps it, that no start command overrides it or leaves the repo root, and the startup log |
| `test_admin_rerun_during_send.py` | The real view on the real `ScriptRunner` and `SafeSessionState` with a stubbed offer that queues a real rerun mid-send: the strip, the Edit form and the table each keep the audit entry and the outcome sentence, the table finishes both rows in one script run, and outcomes and the held-pro warning reach a toast. Only `st.data_editor` (which AppTest cannot type into) and `st.rerun` (which AppTest loops on after a click) are replaced |
| `test_admin_muted_send.py` | Panel sends through the real `DryRunProvider`, built by the real `build_provider` from both the unset-provider and the `WHATSAPP_DRY_RUN=true` configurations: `send_text_sync` returns False, a completion check is neither reported nor stamped (and is stamped when a transmitting provider sends it), `transmits=False` turns the core's "sent" into `ASSIGN_NOT_SENT_MUTED`, an offer through the strip and the Edit form on the real view, and through the table's real `save_lead_edits`, ends as `ASSIGN_NOT_SENT_MUTED` with the lead left assigned, approval and rejection flashes say the pro was not told, with the plain sentence for a pro with no phone, the worker's `notify_pro_new_lead` and the WhatsApp wizard are unchanged, and the boot report is WARNING when prod-like and fires once per process |
| `test_admin_set_pro.py` | Setting a pro from the panel: `lead_queries.apply_lead_changes` routes a real pro change on a lead that is and stays `PENDING_ADMIN_REVIEW` through the injected offer path (other fields written first, no bare `pro_id`, `assign_lead` audited) and any other pro change to a reported `record_only` write, including a caller with no offer path; a held pro left on a waiting lead warns even on a details-only edit; a replayed Save and clearing a pro send nothing; a no-op row is `SKIP_NO_CHANGE`, a row hitting a database error is `SKIP_SAVE_FAILED` without losing earlier outcomes, an interrupted batch keeps its outcomes and counts what is unconfirmed, a failing audit loses nothing, a pro off the eligible list does not unassign, and a stale frame cannot overwrite, clear or offer over a newer owner; `build_edit_form_payload` writes nothing for untouched boxes; `assignment.offer_pending_lead` re-reads eligibility, never raises, and reports a failed re-read as `ASSIGN_PRO_LOOKUP_FAILED`; source pins that the table and the Edit form both pass `_offer_pending`, that the view has no direct lead update, and that the Create form warns |
| `test_admin_assignment.py` | PRO-188 `admin_panel/core/assignment.assign_pending_lead_sync`: maps the shared `assign_lead_to_pro` core's tri-state `offer_sent` (and the `LEAD_ALREADY_TAKEN` sentinel) onto the `ASSIGN_*` outcomes, the `concurrent.futures.TimeoutError` → `ASSIGN_TIMED_OUT` path, and that a raise from the injected `assign`/`run`/`whatsapp_factory` collaborators is caught as `ASSIGN_FAILED` rather than propagating |
| `test_admin_audit_queries.py` | `admin_panel/core/audit_queries.py` (PRO-142, sync, injected collection) — `write_audit_entry` fail-open behavior, `build_audit_filter`, pagination (`count_audit_entries`/`fetch_audit_page`/`page_count`/`clamp_page`), and `format_audit_rows` timestamp handling |
| `test_scheduling_service.py` | Recurring templates, slot generation |
| `test_pro_onboarding.py` | WhatsApp self-signup flow |
| `test_data_management.py` | Consent, data export, deletion |
| `test_admin_auth.py` | Password hashing, cookie auth, session tokens |
| `test_admin_kanban.py` | PRO-46 — `pending_admin_review` surfaced on the admin Kanban: column presence, `STATUS_COLORS`/label drift guards, localized-render checks, and the count-query/enum guard |
| `test_admin_view_call_arity.py` | Static `ast` guard: every resolvable call in `admin_panel/` (same-module and imported from the panel) must bind its target signature. Streamlit views never execute under the unit suite, so a wrong-arity call is otherwise invisible until an operator opens the page — PRO-188 shipped `_render_pending_review_strip(T, pro_names, pro_map_name_to_id)` against a one-parameter def and took down the Dashboard tab on staging. Includes checker self-tests so the guard cannot rot into a no-op |
| `test_admin_responsive.py` | S1+S2 of the mobile pass: the three breakpoints exist and the `responsive.py` block is appended **last** inside `load_css`'s `<style>` (appended-last is what lets it win the cascade without more `!important`); no fixed `width: NNNpx !important` survives outside a media query (the sidebar rule that covered the whole phone screen); `render_metric_grid` escapes and preserves order; an empty Kanban column is marked, packed small on a tablet and hidden only on a phone; the board wrapper is a class carrying an explicit `dir` (PRO-46). Also guards the S5 screenshot tooling — both scripts parse, all four viewports and both languages are shot, `artifacts/` stays gitignored, playwright stays dev-only, and the preview app never reaches for Mongo (its whole value is rendering the layout without one), and reads only translation keys `TRANS` defines — seven near-miss keys had every Hebrew screenshot showing "Table / New / Client / Save" in English |
| `test_admin_rtl.py` | Hebrew (RTL) rendering and the sidebar. Every defect it pins was measured in a real browser, not read off the sheet: the hamburger opened a right-hand sidebar from the left corner (and once moved, sat on top of Streamlit's own top-right menu, so both mirror); the sidebar's collapse button sat on the screen edge instead of the content edge; nav rows were as wide as their text, with the radio marker on a line of its own above it; HTML tables forced `text-align: left` on Hebrew; the alert accent was `border-width` with no `border-style` and so drew nothing; `.chat-meta` flipped twice and put the speaker icon back on the left; the slider thumb rendered ~400px outside its own track; `STATUS_COLORS_DARK` was read by nothing. Also pins the claim `rtl.py` makes about itself — that it emits no declarations at all in LTR, so a rule that is not direction-specific cannot hide there. After S6: the caption `label` rule is scoped to `stWidgetLabel` (an unscoped one stacked every checkbox and toggle's box above its text — the same root cause as the radio row, fixed at the root this time; the test walks every rule and fails on any `label` selector forcing `display: block` outside the caption one), and the button rules reach `st.form_submit_button`, whose `kind="primaryFormSubmit"` had left the login button in Streamlit's default red |
| `test_admin_action_rows.py` | S3/S4 — the rows that must not stack, the login page, the tab strip. Below 640px Streamlit puts `min-width: calc(100% - 22.5px)` on every column, so **every** row wraps and nothing in the DOM tells a Yes/No pair from a row of text inputs: a confirm rendered as two full-width buttons with the destructive one on top. `mark_row_inline()` is the missing handle, and this pins both halves of it — the CSS (clears `min-width`, leaves `flex-basis` alone so `[2, 2, 1]` stays 2:2:1, hides the marker at *every* width because a zero-height flex item still earns the block's `gap`) and the source contract the CSS cannot check for itself: a `+` sibling rule needs the marker to be the statement immediately before its `st.columns`. Also records two measurements that killed planned rules — `st.data_editor` self-caps at 402px whether given 30 rows or 200, so the planned `60vh` cap would never fire; and an unclosed `<div>` in one `st.markdown` wraps nothing, so the login page is capped on `.block-container` instead. Also pins that the preview app marks its dashboard confirm the way `home.py` does — unmarked, it screenshotted a stacking regression that was not there. Every guard mutation-checked |
| `test_admin_panel_invariants.py` | Source-level pins for the defects the post-S6 UX review found by reading, none of which any executing gate can see (view bodies never run under pytest): the weekly-template widgets must be keyed with the pro id (a keyed widget ignores `value=` once its key exists, so a shared key showed pro A's hours under pro B and Save wrote them onto B); the assign options must come from `APPROVED_PRO_FILTER` while display names still cover every professional (a bare `users_collection.find()` offered customers and pros still awaiting approval); no view renders `business_name` without a fallback (self-onboarding writes `""`, on which `dict.get(k, default)` never fires — the `pro_option_label`/`pro_option_map` helpers in `labels.py`, unit-tested in `test_admin_labels.py`, keep two unnamed pros apart and two same-named pros reachable); the logout button is a plain `st.button` so it lands in its sidebar column; login does not `sleep(1)` for a message the rerun discards; admin role/delete flashes are gated on the write and delete is two-step. Each guard reads code with `#` comments stripped, because the comments quote the very shapes the guards forbid. Every guard mutation-checked |
| `test_ai_parsing.py` | Prompt template formatting (no live API calls) |
| `test_ai_media_upload.py` | Static `ast` guard on both sides: every `client.aio.files.*` call in `ai_engine_service.py` must bind the installed `google-genai` `AsyncFiles` signature. The File API is reached only for audio and video, and the whole media block sits in one `except Exception: logger.error(...)` — so a wrong keyword raised a `TypeError` that was caught and discarded, `current_parts` stayed empty, and the "transcribe this audio" prompt still went to Gemini with no audio attached (today the same failure surfaces to the customer as a file notice (`MEDIA_UPLOAD_FAILED` or `MEDIA_UPLOAD_FAILED_TEXT_ONLY`), see `test_ai_media_failure.py`). PRO-204 shipped exactly that: `files.upload(path=…)` against a keyword-only `file=`, and every voice note and video silently reached the model as text alone — nothing crashed and nothing failed CI. Neither side is imported: `conftest.py` replaces `google.genai` with a `MagicMock` for the whole unit suite and a mock accepts every keyword, so a guard that imported the SDK would pass whatever the call site said. Reading the SDK's own source also catches the next rename in a version bump rather than restating `file=` |
| `test_ai_media_failure.py` | PRO-204: a `media_url` with no media part attached (`media_failed`) gets a `Messages.Errors` file notice (`MEDIA_UPLOAD_FAILED`, `MEDIA_UPLOAD_FAILED_TEXT_ONLY` or the one-line `MEDIA_FAILED_TEXT_ANSWERED`) — a media-only turn answers without a model call, a turn with text answers the text behind the one-line notice, all models refusing a media part (a 4xx other than 429, or unparseable JSON) retries once as text only while any other failure returns `AI_OVERLOAD` with no second round, a file lost before the engine (`media_lost`) is told on the reply rather than by a pre-AI send, and a file lost before attaching plus all models failing gets the two-line notice plus `AI_OVERLOAD`; a successful upload changes nothing, and a fetch failure already apologised for (`ai_media_url=None` from the pipeline) is not repeated |
| `test_edge_cases.py` | Bad inputs: Gemini failure, WhatsApp down, unsupported file types |
| `test_agent_pack_drift.py` | Anti-drift guard for `.claude/agents/`: `UserStates`/`LeadStatus`/TTL embeds and the flow-tracer dispatch-order section stay in sync with `constants.py` / `dispatch_guards.py` / `workflow_service.py`; `test_guard_chain_runs_in_source_definition_order` pins `dispatch_guards.GUARD_CHAIN`'s order to the order the guard functions are defined, so the concatenated-source anchor scan stays an honest proxy for execution order |
| `test_claude_config.py` | Shared Claude Code project config (`.claude/settings.json`, `.mcp.json`): both parse; every MCP server declares the fields its transport needs and keeps env values as `${VAR}` placeholders; every hook command is portable (`CLAUDE_PROJECT_DIR`) and points at a script that exists — including the one `run-hook.sh` dispatches to; no hook script is orphaned; every `.claude/commands/*.md` carries a `description` frontmatter; no duplicate allowlist entries; every `.claude/rules/*.md` is path-scoped and its globs match a file; every `.claude/skills/*/SKILL.md` has a description and matching globs; the web-setup `SessionStart` hook is matcher-scoped away from `compact` while the git-context one is not; the five mutating operator workflows (`take-issue`, `triage`, `cleanup-worktrees`, `add-pro`, `full-sync-docs`) carry `disable-model-invocation: true`; every repo path cited in `CLAUDE.md`, a rule or a skill exists; `CLAUDE.md` stays under its size budget; per-developer Claude files stay gitignored |
| `test_post_edit_format.py` | PostToolUse formatter hook: `is_checkable()` (only `.py`, skipping `venv`/`.venv`/`__pycache__`/`.pytest_cache`/`node_modules` as path *segments*, Windows separators normalized) and `build_reason()` — silent on a clean file and on a bare black reformat, and on a flake8 finding returns the documented `{"decision": "block", "reason": …}` so the finding reaches Claude rather than a stderr line only the user sees; the reformat note rides along on that message because the model's copy of the file is then stale. Fails open on bad stdin, a missing linter or an unreadable file |
| `test_stop_docs_guard.py` | Stop hook `evaluate()`: blocks (exit 2, once — `stop_hook_active` releases it) when the session wrote a `.py` under `app/`, `admin_panel/` or `scripts/` and no `.md` was written, dirty or committed on the branch; another session's dirty source never blocks; transcript reader (write tools only, cwd-relative), NUL-porcelain parsing incl. renames and spaces, dirty-tree fallback when the transcript is unreadable; fails open on bad stdin, missing git or a raising helper |
| `test_pre_bash_guard.py` | Bash pre-tool guard `evaluate()`: blocks `git commit`/`push` on main/master, force-push, `rm -rf` on protected paths, `.env` redirects, mongo `drop()`; allows feature-branch work |
| `test_whatsapp_quality.py` | PRO-90 quality watchdog: the Graph reads, paging at most once per cooldown when standing, flapping or under a Redis outage, a Sentry fingerprint per page, production-only acting, a failed or stale poll never reading healthy, `window_state`, the moving-24h counter and 80% alert, the rating-prompt and loyalty gate leaving dispatch and the scheduled completion check alone, and `/health` showing the fields only behind the token |
| `test_whatsapp_facade.py` | PRO-86 single outbound egress + PRO-82 fail-closed breaker: every outbound method gated, the boot-window regression (absent `wa:instance:state` blocks sending), auto/manual pause keys, Redis-error fail-open, `record_account_state` TTL/write rules, provider selection (`WHATSAPP_DRY_RUN` override, `dryrun`/`cloud`, unknown-name fallback), `DryRunProvider`/`CloudAPIProvider` behavior, and the admin panel's `send_text_sync` bridge |
| `test_health_whatsapp_status.py` | `/health` WhatsApp state mapping: `authorized`→up, `yellowCard`→degraded, else down; a non-transmitting provider→degraded; raw `state`, `provider`, `transmits` surfaced |
| `test_phone.py` | PRO-49 phone helpers: `to_chat_id` / `strip_suffix` / `to_local_phone` across `972…`, `+972…`, leading `0`, already-suffixed, and falsy input (idempotent, None-safe); PRO-89 `mask_chat_id` (strips the `@c.us` suffix before the last-4 mask, falsy/suffix-only → `"?"`); PRO-224's `to_il_mobile` is covered in `test_pro_phone_normalisation.py` |
| `test_text_matching.py` | PRO-118 `contains_keyword`: whole-token/phrase matching only — the exact false positives that motivated it (`"טעות" ⊂ "בטעות"`, `"מנהל"`/`"admin"` as mid-word fragments) now False; true positives incl. multi-word phrases and inflected cancel forms; `exclude_phrases` (`"מנהל עבודה"`, incl. repeated/back-to-back and start/end-of-text); punctuation/quote stripping; empty/None text and empty keyword list; PRO-121 `is_emergency_text`: clitic-prefixed stems (`"השריפה"`, `"מהצפה"`, stacked clitics) match, bare `"קצר"` substrings and `EMERGENCY_EXCLUDE_PHRASES` (`"לא דחוף"`, `"לדחוף"`) do not |
| `test_cloud_api_provider.py` | PRO-89 `CloudAPIProvider`: text/file/template/interactive sends via the Graph API, the 24h service-window gate and its window-closed page/raise path, template registry resolution (draft/unknown both refuse to transmit), text chunking and interactive button/list/text-menu degradation at Meta's size limits, `get_state()` status mapping, and Meta webhook parsing (`parse_meta_webhook`/`parse_status_events`, `fetch_meta_media`'s two-hop authorized fetch, HTTPS-only and 25MB-cap enforcement) |
| `test_business_initiated_send.py` | The send path through the real facade and provider: every template DRAFT means an unchanged text payload and an unchanged page-and-drop; closed window + APPROVED sends params in the spec's order, flattened; wrong variables page and send nothing; breaker, kill switch and dry-run hold; each advertised reply reaches its handler from a fresh FSM |
| `test_template_bodies.py` | Every template body against Meta's review rules, and the rules against bad bodies; advertised replies pinned to `Messages.Keywords`; every call site's `params` against its spec (`app/` and `admin_panel/`, no exception list); each admin-panel send names a template; no call site names an unsubmitted template; no submitted template without a call site |
| `test_submit_whatsapp_templates.py` | `scripts/submit_whatsapp_templates.py` against a fake Graph: dry run calls nothing, `--apply` refuses a test account, the token never prints, payloads carry a sample per variable and sequential `{{n}}`, `--status` names each kind of drift and the category Meta holds, exit codes (3 on any template outside UTILITY) |
| `test_offer_withheld_details.py` | `offer_withheld_details` is stored only when an offer went out as a template and is cleared on every change of owner |
| `test_cross_party_templates.py` | The nine notices sent to the other party of a conversation: each call site sends its unchanged free-form text with the right `template_key` and `params`, and the five sends that follow a written state change (pro cancel, reschedule, rating prompt, reported completion, pro pause) no longer abort it when the send raises |
| `test_meta_webhook_route.py` | PRO-89 `GET`/`POST /webhook/meta`: the `hub.challenge` handshake, `X-Hub-Signature-256` HMAC verification (incl. prod-like fail-closed when `META_APP_SECRET` is unset, and a non-ASCII forged header), service-window opening on every inbound, wamid idempotency, and the 200-vs-503 response policy |
| `test_privacy_route.py` | PRO-87 `GET /privacy` public privacy page: 200 + exact `Cache-Control`, both languages present, the 90-day retention copy (coupled to the `messages` TTL index in `scripts/create_indexes.py`), the mailto contact link, no auth, and self-containment (no external `<link>`/`<script>`/`<img>` resources) |
| `test_terms_route.py` | `GET /terms`: 404 while the text is a draft or `TERMS_ENABLED` is off, then 200 with both Hebrew sections, each under 400 words; the policy links in the consent and onboarding messages with `PUBLIC_BASE_ORIGIN` unset and set; the menu surviving the links; the origin validator (malformed or non-public values boot with no link and are reported at ERROR without the value; unset and empty stay silent); blank `TERMS_ENABLED` as off; the boot report for a flag set on a draft. `test_shipped_terms_are_still_a_draft` is deleted by the PR carrying approved text |
| `test_settings_meta_cloud_provider.py` | PRO-89 `Settings.require_cloud_provider_config`: `cloud` without Meta credentials fails boot, `WHATSAPP_DRY_RUN=true` exempts the transmit tier, prod-like environments additionally require `META_APP_SECRET`/`META_VERIFY_TOKEN`, and `dryrun` never requires any of it |
| `test_arq_worker_meta_media.py` | PRO-89 `media_handler.resolve_inbound_media`: a `meta-media://` marker is fetched from Meta and re-hosted on Cloudinary, and `process_message_task` leaves that to the consent gate; failure degrades to text-only, never a crashed task |
| `test_logger_redaction.py` | PRO-80 log scrubbing: `mask_pii` phone masking + `redact_secrets` (provider token / WEBHOOK_TOKEN redacted in query string & URL path, None-safe) applied by the `_pii_filter` sink |
| `test_log_pii.py` | PRO-195 floor (PRO-191 started it as a ratchet): an AST scan of every `logger`-bound f-string in `app/` and `admin_panel/` flags an unmasked `chat_id` or a structural reference to `full_address`/`street`/`street_number`/`customer_name`/`display_name`/`transcription`/`pro_phone`/`phone_number`/`user_text`/`latitude`/`longitude` (`city` is deliberately allowed), plus `name` **only** as a record-field access (`doc["name"]`, `doc.get("name")`) — bare `name`/`obj.name` is a provider, a city or an enum member 16 times out of 17, and a guard that cries wolf gets switched off; zero violations anywhere, no per-file exemption — the `KNOWN_VIOLATIONS` allowlist that once carried 38 lines across 12 files was deleted once PRO-195 paid it down, and a scan-non-vacuity assertion guards against the floor silently walking nothing; the detector itself is pinned by unit tests on in-process source snippets |
| `test_settings_admin_phone.py` | PRO-48 `Settings.admin_phone_unconfigured` and `app/core/startup_checks.py`: the check is **provenance, not value** — unset (and explicitly empty, the `VAR=` shape Railway allows) reports, while a value supplied by env var or kwarg stays silent *even when it equals the default*, which is production's real configuration and what a comparison against the default would wrongly fire on; plus the report firing only in a prod-like environment, naming the number by its last four only, and both boot paths still running it |
| `test_settings_secret_masking.py` | PRO-94 secret masking: every credential field is a `SecretStr`, the incident regression (`repr`/`str`/f-string/`model_dump`/an `AttributeError` traceback leak nothing), `MONGO_URI`'s default is wrapped, `iter_secret_values` skips unset and too-short values, the naming-convention guard for credential-shaped fields that don't exist yet (already covering PRO-89's `META_*`, added as `SecretStr` from the start), source scans proving no secret reaches an f-string, a log call or a module-level name, and PRO-99's `hide_input_in_errors=True` regression (construction-time `ValidationError`s — field, secret-field and model validator paths — no longer echo raw input in `str()`/traceback, while `errors()`/`.json()` still carry it and the validator's own actionable message survives) |
| `test_redis_isolation.py` | PRO-78 guard for the autouse `fake_redis` fixture: `get_redis_client()` returns a `fakeredis` instance, each test gets a fresh empty store (no cross-test bleed), and `StateManager` round-trips through the fake |
| `test_background_tasks.py` | PRO-143 `spawn_background_task`: a task survives GC with no local reference held, the strong-ref set drains on completion, a failure logs ERROR with task name + exception type, cancellation logs at debug only, the default timeout cancels and reports a hung task, `timeout=None` opts out, and calling with no running loop raises `RuntimeError` and closes the coroutine |
| `test_background_task_isolation.py` | PRO-187 harness guard: the autouse `_isolate_background_tasks` fixture (`conftest.py`) clears `app.core.background_tasks`'s module-level registry on setup and evicts-without-cancelling on teardown into a quarantine list, so one test's in-flight `pending_background_tasks()` are never visible to the next; includes a source-level regression guard against a test draining the whole registry again |
| `test_promote_workflow.py` | PRO-183 `promote-to-production.yml`: the `gate` step's shell is extracted verbatim from the parsed YAML and run under `bash` (empty/whitespace/short/valid/trimmed `skip_reason`, Hebrew character-not-byte counting under a forced `C` locale, delimiter-injection safety). The **"Assert dev's CI is green"** step is extracted and run the same way, against a stubbed `gh` on `PATH` that records its argv: the three reasons a commit can fail the `pytest|completed|success` requirement — never tested, CI still running, CI completed without passing — must produce mutually distinct annotations, a re-run in flight after a failure must read as *still running* rather than *fix dev* (the arms are order-dependent), an earlier failed run of the `promote` job itself must not block, and the queried URL must be the right repo and SHA, plus structural pins (`skip_deploy_check` fully removed, `skip_reason` input shape, verification steps gate on `!= 'false'` not `== 'true'`, the `gate` step stays unconditional, the assert-green step passes `SHA`/`SELF`/`REPO` via `env:` and keeps `${{ }}` out of its `run:` body so the behavioural tests cannot decay into testing a paraphrase, `run-name` stays single-line and flags unverified runs, the no-baseline "healthy but unproven" reporting path) |
| `test_requirements_pins.py` | `pyarrow` stays pinned to an exact version in `requirements.txt`. It is not imported by this repo — it arrives through `streamlit==1.31.1`, which declares only `pyarrow>=7.0` and caps `numpy<2` — so an unpinned fresh install resolved 26.0.0 (2026-10), which refuses to import against NumPy 1.x and takes out the admin panel, its Docker build and five test files with no change in this repo |
| `test_stop_workflow.py` | `stop_railway_services.yml`: the resolve step's shell is extracted verbatim from the parsed YAML and run under `bash` — `dev` resolves to staging, `production` to production, and any other branch (`main`, `master`, `staging`, a feature branch, a case or whitespace variant, empty) refuses with no target written; the branch name is treated as data not shell; structural pins that the workflow takes no inputs (the 2026-09-08 confirm-box failure class cannot return), that the branch reaches the step through `env:` rather than `${{ }}`, that the `railway down` step keys its token/environment on `steps.target.outputs.target` rather than `github.ref_name`, and that `run-name` is a single line naming the target Plus the **Stop Services** step run under `bash -e` against a fake `railway` on `PATH`: an already-stopped service ("No deployments found") is success so a second click stays green, but a rejected token — which a blanket `|| echo "no active deployment"` once turned into "All services stopped" and a green job, measured on staging 2026-09-22 and 2026-10-06 — fails the job, names the service, still tries the rest, and writes a job summary saying it is still running The Railway CLI install is pinned to an exact `x.y.z` and prints its version (an unpinned `latest` let the failure mode change between 2026-10-06 and 2026-10-28 with no commit in this repo) |
| `test_check_pro_service_areas_workflow.py` | `check_pro_service_areas.yml`: the resolve step's shell is extracted verbatim from the parsed YAML and run under `bash -e` — matching GitHub's own `bash -e {0}`, without which the harness cannot see errexit aborting a step, the bug that left a retry, a summary write and an exit-code interpretation as dead code through a day of failing runs. `dev` resolves to staging, `production` to production, and a feature branch, an unset or empty `REF_NAME`, `" production "` and `production-hotfix` all refuse with no target written. The exit-code `case` block is sliced out and run standalone for 0/1/2/3 and an unmapped code. The connection-settings step is covered the same way: an empty `MONGO_URI` refuses with an error naming both the repository-secret vs. Environments distinction and the reason for refusing — `Settings.MONGO_URI` defaults to localhost, so proceeding would read an empty database on the runner and call it clean; the database name is parsed and printed for an Atlas `mongodb+srv://` URI, a plain one, and one with no database in the path; and a URI carrying a distinctive username, password and host leaks none of the three, only the database name. Structural pins: the only input is the boolean `apply` (no free-text confirmation box can return), the branch reaches the shell through `env:` rather than `${{ }}`, `MONGO_URI`/`REDIS_URL` key on `steps.target.outputs.target` rather than `github.ref_name`, `--apply` is only reached through the `APPLY` gate, the exit code is captured via `PIPESTATUS` rather than `tee`'s, `run-name` is a single line, and no step is named after or actively reads a Railway credential (scoped to real use, so a comment quoting the historical refusal message is not read as a dependency) |
| `test_check_health_leads.py` | `scripts/check_health_leads.py` and `health-leads-monitor.yml`: the 30-minute backlog and stuck-climb rules, re-page throttling, and `main()` over real HTTP against a local server standing in for `/health/leads` and Sentry — a missing secret, a wrong token, a redirect or a non-production response is red, a refused page is retried, and public logs never carry counts or secrets |
| `test_vendor_guard_workflow.py` | The `vendor-guard` ("Guard — no legacy WhatsApp vendor references") step in `.github/workflows/tests.yml`: same extraction shape as `test_promote_workflow.py`/`test_stop_workflow.py`, run under `bash -e` against a throwaway tree. Either retired hostname still fails the build from code, a script, a test fixture or config; `*.md` is excluded (2026-09-14) so prose naming the retired vendor passes, and a markdown mention in one file does not mask a `.py` doing the same elsewhere. Structural pins: `--exclude='*.md'` is present, `.github` stays excluded from its own scan, and `app`/`scripts`/`tests`/`admin_panel` are never excluded |
| `test_ci_workflows.py` | Every workflow declares `permissions` and a job timeout, pins each action to a SHA with a version comment on one version across files, keeps `${{ }}` out of `run:` bodies, and avoids `pull_request_target`; only promote's checkout keeps credentials; Dependabot groups action updates; `tests.yml` cancels only PR runs and gives each push its own group; actionlint is pinned and checksum-verified. The floor's count and guard steps run verbatim under `bash -e`: fewer passed than `dev` fails, at or above is silent, the label turns the failure into a warning, a missing or corrupt floor warns that nothing was checked, and an unparseable pytest summary fails |

### Health & Regression

| File | What it covers |
|------|---------------|
| `test_health_leads.py` | Lead status health checks — verifies no leads are stuck in unexpected states |

### Integration & E2E

| File | What it covers |
|------|---------------|
| `test_db_integration.py` | Real MongoDB read/write: lead persistence, status flow, chat history, pro lifecycle |
| `test_integration_webhook.py` | HTTP POST to `/webhook` endpoint |
| `test_scheduler.py` | Daily reminders, stale monitor timing |
| `test_sos_monitor.py` | Auto-healing and admin reporting for stuck leads |

---

## 3. Mocking Strategy

### `conftest.py` (autouse for all non-integration tests)

- **MongoDB:** `mongomock_motor` (`AsyncMongoMockClient`) — in-memory, no real DB
- **WhatsApp:** `whatsapp` module-level instance mocked with `AsyncMock` for `send_message`, `send_location_link`
- **AI Engine:** `ai.analyze_conversation` returns a predefined `AIResponse` (city=Tel Aviv, issue=Leak, is_deal=False)
- **Consent:** `has_consent` patched to return `True` by default
- **ContextManager:** mocked globally (clears Redis dependency)
- **Redis:** backed by an in-memory `fakeredis` — a fresh instance per test via the autouse `fake_redis` fixture (PRO-78), so no real Redis is required and no state bleeds across tests. Integration tests keep real Redis, same as they keep a real Mongo.
- **Background tasks:** the autouse `_isolate_background_tasks` fixture (PRO-187) clears `app.core.background_tasks`'s module-level registry before each test and evicts any still-pending task (without cancelling it) after — so a test that spawns fire-and-forget work and returns early can never leak a task, bound to a closed event loop, into whichever test runs next. A test reading `pending_background_tasks()` sees only its own.

### Per-test overrides (common patterns)

```python
# Override AI response for a specific test
mock_ai.analyze_conversation.return_value = AIResponse(
    reply_to_user="...", is_deal=True,
    extracted_data=ExtractedData(city="Tel Aviv", issue="Leak", street="Rothschild", street_number="10", floor="3", apartment="5", appointment_time="Now"),
    transcription=None,
)

# Sequence of responses (dispatcher then pro)
mock_ai.analyze_conversation.side_effect = [dispatcher_resp, pro_resp]

# Override state
mock_state.get_state = AsyncMock(return_value=UserStates.AWAITING_PRO_APPROVAL)
```

### Collection patching

Each service that uses `from app.core.database import X_collection` imports the collection at load time, so each module needs its own patch:

```python
monkeypatch.setattr(app.services.matching_service, "users_collection", mock_db.users)
monkeypatch.setattr(app.services.workflow_service, "leads_collection", mock_db.leads)
# etc.
```

`conftest.py` handles all standard services. Tests that need non-standard overrides (e.g., specific aggregate behavior) add their own `monkeypatch.setattr` calls.

---

## 4. Writing New Tests

```python
import pytest
from unittest.mock import AsyncMock

@pytest.mark.asyncio
async def test_something(mock_db, monkeypatch):
    # mock_db is the in-memory MongoDB (mongomock_motor)
    # conftest.py autouse fixture already patches all standard collections

    # Insert test data
    await mock_db.leads.insert_one({
        "chat_id": "972501111111@c.us",
        "status": "new",
        ...
    })

    # Override AI response if needed
    import app.services.workflow_service
    mock_ai = app.services.workflow_service.ai
    mock_ai.analyze_conversation = AsyncMock(return_value=...)

    # Run
    result = await my_function(...)

    # Assert
    assert result == expected
    updated = await mock_db.leads.find_one({"chat_id": "972501111111@c.us"})
    assert updated["status"] == "booked"
```

**Key rules:**
- Always use `@pytest.mark.asyncio`
- Use `mock_db` fixture for DB access in unit tests
- Use `monkeypatch` (not `unittest.mock.patch`) to stay compatible with the autouse fixture
- For `$geoNear` tests, mock `users_collection.aggregate` as an async generator (mongomock does not support `$geoNear`)
- **Never anchor time at module scope.** A `time.time()` / `datetime.now()` constant at the top of a test file is evaluated once at import, so a long suite run drifts the wall clock away from it before the test executes — the test then passes or fails on how long the suite took. Compute time anchors inside the test or a fixture. Prefer margins expressed as a fraction of the threshold under test rather than a fixed offset, so tightening the constant can't silently erase the margin (PRO-68).

---

## 5. Manual & E2E Tools

| Tool | Command | Purpose |
|------|---------|---------|
| Offline E2E harness | `pytest tests/e2e` | Automated full-flow coverage, zero real sends — see §6 |
| Webhook simulator | `python tests/simulate_webhook.py` | Interactive — craft any message and POST to local backend |
| Environment reset | `python scripts/reset_test.py` | Clear test leads, Redis state/context/webhook keys |
| DB seeding | `python scripts/seed_db.py` | Populate with sample professionals |
| Manual test plan | `docs/MANUAL_TEST_PLAN.md` | Step-by-step via real WhatsApp |

> **Deleted in PRO-83:** `tests/simulate_test.py` and `tests/smoke_test_railway.py`.
> Both claimed their target customer number was "virtual". It was a real handset —
> only the *inbound* leg was simulated, so every simulated webhook produced a
> genuine outbound WhatsApp send (PRO-72, the yellowCard). `tests/e2e` now proves
> the same logic offline; manual transport verification lives exclusively in
> `docs/PILOT_E2E_CHECKLIST.md` (PRO-64). That vendor itself is gone (PRO-85), and the
> PRO-29 plan for a separate staging instance to gate live-fire automation on is
> cancelled — the PRO-89 `CloudAPIProvider` is code-complete, but live-fire automation
> is still blocked until PRO-87 (Meta Business Portfolio + template approval) puts a real
> transmitting account behind it (see `scripts/seed_coverage_matrix.py`, which now guards
> on `provider.transmits` rather than an instance id).

---

## 6. Offline E2E Harness (`tests/e2e`, PRO-83)

Drives the **real** orchestrator with synthetic inbound WhatsApp webhooks and asserts
on the fully rendered Hebrew each participant would have received, the resulting
Mongo state, and the Redis FSM state. Runs in ~7 seconds on every PR.

### Real-send fidelity

The harness does **not** mock the outbound egress. It runs the real `WhatsAppFacade`
and swaps only the *provider* underneath it for a `RecordingProvider` (PRO-86,
re-based from the pre-PRO-86 httpx `MockTransport` under a real `WhatsAppClient`), so
the rendered message body, the recipient, and the PRO-71/82 circuit-breaker check are
all the real code path — only the last step (handing bytes to a socket) is
substituted. `tests/e2e/conftest.py` seeds the `wa:instance:state` confirmation the
fail-closed breaker (PRO-82) requires, so a world starts out representing a healthy
account; individual tests take it away again to exercise the breaker.

### Zero real sends — three independent layers

1. `settings.WHATSAPP_DRY_RUN` is forced on, so even a freshly built facade defaults
   to the `DryRunProvider`.
2. Every module-level `whatsapp` singleton is rebound to a `WhatsAppFacade` wrapping
   the `RecordingProvider`.
3. `httpx`'s real transports are patched to **raise** for the whole package. A
   request either reaches the recorder or fails the run.

Test numbers are `9720…`, which cannot be a real Israeli MSISDN (the digit after
`972` is never `0`). `test_e2e_safety.py` scans the whole harness tree for the two
numbers implicated in PRO-72.

### What is deliberately not real

| Substituted | Why | Where |
|---|---|---|
| Gemini | CI must be free, offline and byte-identical between runs | recorded fixtures in `ai_replay.py`; re-record with `PROLI_E2E_RECORD=1` |
| Media upload/analysis | same, plus no live API key in CI | fixtures in `world.py`; real media is verified by hand in `docs/PILOT_E2E_CHECKLIST.md` |
| Google geocoding | paid external API | `fake_resolve_city_to_coords`, which resolves the same static cities production does so routing still takes the `$geoNear` path |
| `$geoNear` | mongomock does not implement it | `geo_shim.py` emulates only that operator, so the real 10→20→30 km stepping and load balancing still execute |

### Time

No new dependency and no wall-clock sleeps. Tests backdate the stored timestamps
the scheduled jobs read (`pro_notified_at`, `paused_at`, `created_at`) against an
anchor computed **inside** the fixture, satisfying PRO-68.

### The state × input matrix

`test_e2e_state_matrix.py` drives every `UserStates` value against eight realistic
input classes (expected keyword, free text, off-topic, wrong media type, emoji-only,
silence, interruption keyword, race). The table in that module's docstring is
**generated from the executable matrix** and pinned by a guard test, so a cell
cannot go silently missing — adding a state fails the suite until the regenerated
table is pasted back. Regenerate with:

```bash
python -m tests.e2e.test_e2e_state_matrix
```

### Defects it found

**The table is empty — every defect this harness found has been fixed.** It is kept
because the mechanism is the point: a defect is pinned as an `xfail(strict=True)`
test asserting the behaviour the system *should* have, and strict mode turns that
test into a hard failure the moment somebody fixes it. Nothing has to remember to
come back and re-enable it.

That has now happened three times. PRO-123 fixed the reverse-match fallback and
`test_text_fallback_routing_still_honours_exclusions` started passing. PRO-186 then
fixed the two below together — they were one defect wearing two faces, the pro
holding states having no seat in `workflow_service`'s dispatch:

| Test | Defect it pinned | Fixed by |
|---|---|---|
| `test_pro_can_select_which_job_to_finish` | `PRO_SELECTING_JOB_TO_FINISH` was unreachable through the orchestrator — the `PRO_BUSINESS_KEYWORDS` bypass overwrote the state with `PRO_MODE` before `pro_flow` read it, so "1" ran approve instead of picking job 1. | PRO-186 |
| `test_pro_final_price_is_recorded` | `PRO_AWAITING_FINAL_PRICE` was absent from the dispatch, so the pro's price reply reached the *customer* dispatcher and PRO-33's `final_price` / `commission_amount` could never be captured in production. | PRO-186 |

Both are now ordinary regression tests, and the 15 matrix cells that were dark for
the same reason (`defect-finish`, `defect-cancel`, `defect-price`) are live.
