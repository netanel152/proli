# Proli Testing Guide

The test suite uses `pytest` with `pytest-asyncio` in strict mode (`asyncio_mode = strict`). All unit tests use `mongomock_motor` (in-memory MongoDB) — no real database or external API required.

**Current status: 909 passed, 96 skipped, 4 xfailed** (integration tests skipped when `MONGO_TEST_URI` is not set; the remaining skips are the explicit `N/A` cells of the PRO-83 state × input matrix, and the xfails are four product defects that harness documents — see below).

> This line is the **single source of truth** for the test baseline. Agents and commands under `.claude/` read the count from here — when you add tests, update this line in the same PR.

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
| `test_workflow_orchestrator.py` | Central routing: reset commands, pro auto-detect, AWAITING_ADDRESS, AWAITING_PRO_APPROVAL, PAUSED_FOR_HUMAN, SOS→TTL, deal finalization, no-pro fallback, PRO-63 `PENDING_REVIEW_SHORTCIRCUIT_HOURS` recency-bounded short-circuit |
| `test_smart_dispatcher_logic.py` | Dispatcher AI: missing info → clarify, city+issue → handoff to pro, audio transcription flow |
| `test_pro_flow.py` | Pro commands: approve, reject, finish (multi-job selection), pause bot, resume, dashboard fallback, vacation mode, PRO-63 `מצא` reassignment-lifecycle reset after escalation |
| `test_customer_flow.py` | Post-job: completion checks, rating prompts, review collection |
| `test_sos_logic.py` | SOS alerts: admin notification, pro notification, BOT_PAUSED_BY_CUSTOMER message |
| `test_dual_role_routing.py` | Pro-as-customer routing: `לקוח` mode switch, sticky CUSTOMER_MODE while their own lead is open, context-aware keyword bypass, soft-hold escape |

### Matching & Routing

| File | What it covers |
|------|---------------|
| `test_matching_service.py` | `$geoNear` pipeline, progressive radius (10→20→30 km), no-pro-at-max-radius returns None, text fallback, load balancing, excluded pro IDs, rating sort |
| `test_geocoding_service.py` | Static dict lookup, Redis cache hits/misses, Google Maps API calls with bounding-box validation, fallback chain, PRO-19 definitive-vs-transient miss split (`GeocodingUnavailable`, TTL choice) and the `geo:unavailable` circuit breaker |   
| `test_stale_nudger.py` | Periodic reminders for booked leads > 24h old |
| `test_approval_sla.py` | PRO-56 approval SLA: T+10 pro nudge, T+25 customer reassignment offer, emergency-halved thresholds, idempotency, business-hours gate, and the customer 1/2 reply handling |
| `test_reassign_escalation.py` | PRO-63 `reassign_lead`: exhausted `MAX_REASSIGNMENTS` escalates to `PENDING_ADMIN_REVIEW` (never `CLOSED`), immediate admin alert (and best-effort survival if it fails), customer notification, state/context clear, idempotency guard, race-safe `expected_status` write, and that exhaustion is checked before matching/reassigning |
| `test_scheduler_gating.py` | PRO-73 gating primitives: `within_business_hours` (Israel 08–21) and the `_customer_cold_job_allowed` toggle+hours gate (default OFF) for cold customer-facing jobs |
| `test_seed_coverage_matrix.py` | PRO-84 staging coverage matrix: the 27-professional seed's shape, reserved phone block, determinism and `--purge` scoping — plus the **real `determine_best_pro` run against the seeded matrix**, asserting each of the ten routing scenarios' winner by name (rating sort, load balancing, 10→20→30 km expansion, coverage gap, geocoding, text fallback, reverse match, ineligibility filter) |
### Infrastructure

| File | What it covers |
|------|---------------|
| `test_unit_lead_manager.py` | Lead CRUD in isolation |
| `test_booking_and_messaging.py` | Slot booking and messaging flows |
| `test_security_service.py` | Rate limiting (Redis fixed-window) |
| `test_consent_flow.py` | Privacy consent gate |
| `test_media_handler.py` | Media type detection, image download, audio/video URL handling |
| `test_notification_service.py` | WhatsApp notifications (best-effort, no SMS fallback) |
| `test_notification_offer.py` | Shared lead-offer builder: `build_new_lead_message`/`format_lead_extra_info`/`format_media_links` (pure, Hebrew fallbacks) and `notify_pro_new_lead` (offer + navigation link, fail-open) as used by `monitor_service`'s reassignment path and `admin_flow`'s assignment path |
| `test_whatsapp_state_monitor.py` | PRO-20 WhatsApp deauth monitor: `get_state_instance` (incl. a `NotImplementedError` provider reading as `None`, not crashing), `send_oncall_alert` state-guarded WhatsApp routing (no SMS), `check_whatsapp_instance_state` FSM/Redis branches |
| `test_analytics_service.py` | Lead funnel and performance aggregations |
| `test_audit_service.py` | Admin action logging |
| `test_scheduling_service.py` | Recurring templates, slot generation |
| `test_pro_onboarding.py` | WhatsApp self-signup flow |
| `test_data_management.py` | Consent, data export, deletion |
| `test_admin_auth.py` | Password hashing, cookie auth, session tokens |
| `test_admin_kanban.py` | PRO-46 — `pending_admin_review` surfaced on the admin Kanban: column presence, `STATUS_COLORS`/label drift guards, localized-render checks, and the count-query/enum guard |
| `test_ai_parsing.py` | Prompt template formatting (no live API calls) |
| `test_edge_cases.py` | Bad inputs: Gemini failure, WhatsApp down, unsupported file types |
| `test_agent_pack_drift.py` | Anti-drift guard for `.claude/agents/`: `UserStates`/`LeadStatus`/TTL embeds and the flow-tracer dispatch-order section stay in sync with `constants.py` / `workflow_service.py` |
| `test_pre_bash_guard.py` | Bash pre-tool guard `evaluate()`: blocks `git commit`/`push` on main/master, force-push, `rm -rf` on protected paths, `.env` redirects, mongo `drop()`; allows feature-branch work |
| `test_whatsapp_facade.py` | PRO-86 single outbound egress + PRO-82 fail-closed breaker: every outbound method gated, the boot-window regression (absent `wa:instance:state` blocks sending), auto/manual pause keys, Redis-error fail-open, `record_account_state` TTL/write rules, provider selection (`WHATSAPP_DRY_RUN` override, `dryrun`/`cloud`, unknown-name fallback), `DryRunProvider`/`CloudAPIProvider` behavior, and the admin panel's `send_text_sync` bridge |
| `test_health_whatsapp_status.py` | `/health` WhatsApp state mapping: `authorized`→up, `yellowCard`→degraded, else down; a non-transmitting provider→degraded; raw `state`, `provider`, `transmits` surfaced |
| `test_phone.py` | PRO-49 phone helpers: `to_chat_id` / `strip_suffix` / `to_local_phone` across `972…`, `+972…`, leading `0`, already-suffixed, and falsy input (idempotent, None-safe) |
| `test_logger_redaction.py` | PRO-80 log scrubbing: `mask_pii` phone masking + `redact_secrets` (provider token / WEBHOOK_TOKEN redacted in query string & URL path, None-safe) applied by the `_pii_filter` sink |
| `test_settings_secret_masking.py` | PRO-94 secret masking: every credential field is a `SecretStr`, the incident regression (`repr`/`str`/f-string/`model_dump`/an `AttributeError` traceback leak nothing), `MONGO_URI`'s default is wrapped, `iter_secret_values` skips unset and too-short values, the naming-convention guard for credentials that do not exist yet (PRO-89's `META_*`), source scans proving no secret reaches an f-string, a log call or a module-level name, and PRO-99's `hide_input_in_errors=True` regression (construction-time `ValidationError`s — field, secret-field and model validator paths — no longer echo raw input in `str()`/traceback, while `errors()`/`.json()` still carry it and the validator's own actionable message survives) |
| `test_redis_isolation.py` | PRO-78 guard for the autouse `fake_redis` fixture: `get_redis_client()` returns a `fakeredis` instance, each test gets a fresh empty store (no cross-test bleed), and `StateManager` round-trips through the fake |

### Health & Regression

| File | What it covers |
|------|---------------|
| `test_health_leads.py` | Lead status health checks — verifies no leads are stuck in unexpected states |

### Integration & E2E

| File | What it covers |
|------|---------------|
| `test_db_integration.py` | Real MongoDB read/write: lead persistence, status flow, chat history, pro lifecycle |
| `test_full_flow.py` | Complete journey: message → AI → Pro → Booking → Completion → Rating |
| `test_integration_webhook.py` | HTTP POST to `/webhook` endpoint |
| `test_scheduler.py` | Daily reminders, stale monitor timing |
| `test_sos_monitor.py` | Auto-healing and admin reporting for stuck leads |

---

## 3. Mocking Strategy

### `conftest.py` (autouse for all non-integration tests)

- **MongoDB:** `mongomock_motor` (`AsyncMongoMockClient`) — in-memory, no real DB
- **WhatsApp:** `whatsapp` module-level instance mocked with `AsyncMock` for `send_message`, `send_location_link`, `send_interactive_buttons`
- **AI Engine:** `ai.analyze_conversation` returns a predefined `AIResponse` (city=Tel Aviv, issue=Leak, is_deal=False)
- **Consent:** `has_consent` patched to return `True` by default
- **ContextManager:** mocked globally (clears Redis dependency)
- **Redis:** backed by an in-memory `fakeredis` — a fresh instance per test via the autouse `fake_redis` fixture (PRO-78), so no real Redis is required and no state bleeds across tests. Integration tests keep real Redis, same as they keep a real Mongo.

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
> genuine outbound Green API send (PRO-72, the yellowCard). `tests/e2e` now proves
> the same logic offline; manual transport verification lives exclusively in
> `docs/PILOT_E2E_CHECKLIST.md` (PRO-64). Green API itself is gone (PRO-85), and the
> PRO-29 plan for a separate staging instance to gate live-fire automation on is
> cancelled — live-fire automation is blocked until PRO-89 ships a real transmitting
> provider (see `scripts/seed_coverage_matrix.py`, which now guards on
> `provider.transmits` rather than an instance id).

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

Four `xfail(strict=True)` tests document behaviour the system should have and does
not. Strict mode turns each into a hard failure the moment it is fixed.

| Test | Defect |
|---|---|
| `test_pro_rejection_reassigns_to_the_next_pro` | A pro rejection is a silent dead end: the lead goes `REJECTED`, the customer's state is cleared, and nothing notifies them or re-routes — no scheduler job queries `REJECTED`. |
| `test_pro_can_select_which_job_to_finish` | `PRO_SELECTING_JOB_TO_FINISH` is unreachable through the orchestrator — the `PRO_BUSINESS_KEYWORDS` bypass overwrites the state to `PRO_MODE` before `pro_flow` reads it, so "1" runs approve instead of picking job 1. |
| `test_pro_final_price_is_recorded` | `PRO_AWAITING_FINAL_PRICE` is absent from the dispatch, so PRO-33's `final_price` / `commission_amount` can never be captured in production. |
| `test_text_fallback_routing_still_honours_exclusions` | `determine_best_pro`'s reverse-match fallback drops `excluded_pro_ids` and the `pending_approval` guard. |
