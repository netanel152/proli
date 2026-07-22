# Proli Testing Guide

The test suite uses `pytest` with `pytest-asyncio` in strict mode (`asyncio_mode = strict`). All unit tests use `mongomock_motor` (in-memory MongoDB) — no real database or external API required.

**Current status: 501 passed, 6 skipped** (integration tests skipped when `MONGO_TEST_URI` is not set).

> This line is the **single source of truth** for the test baseline. Agents and commands under `.claude/` read the count from here — when you add tests, update this line in the same PR.

---

## 1. Running Tests

```bash
# All unit tests
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
| `test_geocoding_service.py` | Static dict lookup, Redis cache hits/misses, Google Maps API calls with bounding-box validation, fallback chain |   
| `test_stale_nudger.py` | Periodic reminders for booked leads > 24h old |
| `test_approval_sla.py` | PRO-56 approval SLA: T+10 pro nudge, T+25 customer reassignment offer, emergency-halved thresholds, idempotency, business-hours gate, and the customer 1/2 reply handling |
| `test_reassign_escalation.py` | PRO-63 `reassign_lead`: exhausted `MAX_REASSIGNMENTS` escalates to `PENDING_ADMIN_REVIEW` (never `CLOSED`), immediate admin alert (and best-effort survival if it fails), customer notification, state/context clear, idempotency guard, race-safe `expected_status` write, and that exhaustion is checked before matching/reassigning |
| `test_scheduler_gating.py` | PRO-73 gating primitives: `within_business_hours` (Israel 08–21) and the `_customer_cold_job_allowed` toggle+hours gate (default OFF) for cold customer-facing jobs |
### Infrastructure

| File | What it covers |
|------|---------------|
| `test_unit_lead_manager.py` | Lead CRUD in isolation |
| `test_booking_and_messaging.py` | Slot booking and messaging flows |
| `test_security_service.py` | Rate limiting (Redis fixed-window) |
| `test_consent_flow.py` | Privacy consent gate |
| `test_media_handler.py` | Media type detection, image download, audio/video URL handling |
| `test_notification_service.py` | WhatsApp notifications (best-effort, no SMS fallback) |
| `test_whatsapp_state_monitor.py` | PRO-20 Green API deauth monitor: `get_state_instance`, `send_oncall_alert` state-guarded WhatsApp routing (no SMS), `check_whatsapp_instance_state` FSM/Redis branches |
| `test_analytics_service.py` | Lead funnel and performance aggregations |
| `test_audit_service.py` | Admin action logging |
| `test_scheduling_service.py` | Recurring templates, slot generation |
| `test_pro_onboarding.py` | WhatsApp self-signup flow |
| `test_data_management.py` | Consent, data export, deletion |
| `test_admin_auth.py` | Password hashing, cookie auth, session tokens |
| `test_ai_parsing.py` | Prompt template formatting (no live API calls) |
| `test_edge_cases.py` | Bad inputs: Gemini failure, WhatsApp down, unsupported file types |
| `test_agent_pack_drift.py` | Anti-drift guard for `.claude/agents/`: `UserStates`/`LeadStatus`/TTL embeds and the flow-tracer dispatch-order section stay in sync with `constants.py` / `workflow_service.py` |
| `test_pre_bash_guard.py` | Bash pre-tool guard `evaluate()`: blocks `git commit`/`push` on main/master, force-push, `rm -rf` on protected paths, `.env` redirects, mongo `drop()`; allows feature-branch work |
| `test_whatsapp_client_circuit_breaker.py` | PRO-71 outbound breaker: `send_message`/`send_file_by_url` suppress (no HTTP) when `wa:instance:paused` is set; fail-open when Redis is down |
| `test_health_whatsapp_status.py` | `/health` WhatsApp state mapping: `authorized`→up, `yellowCard`→degraded, else down; raw `state` surfaced |
| `test_phone.py` | PRO-49 phone helpers: `to_chat_id` / `strip_suffix` / `to_local_phone` across `972…`, `+972…`, leading `0`, already-suffixed, and falsy input (idempotent, None-safe) |
| `test_logger_redaction.py` | PRO-80 log scrubbing: `mask_pii` phone masking + `redact_secrets` (GREEN_API_TOKEN / WEBHOOK_TOKEN redacted in query string & URL path, None-safe) applied by the `_pii_filter` sink |

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
- **Redis:** not mocked — `StateManager` / `ContextManager` fail gracefully with logged errors

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
| Webhook simulator | `python scripts/simulate_webhook.py` | Interactive — craft any message and POST to local backend |
| Automated scenarios | `python tests/simulate_test.py` | Runs TC1–TC12: consent, rejection, SOS, media, idempotency |
| Environment reset | `python scripts/reset_test.py` | Clear test leads, Redis state/context/webhook keys |
| DB seeding | `python scripts/seed_db.py` | Populate with sample professionals |
| Manual test plan | `docs/MANUAL_TEST_PLAN.md` | Step-by-step via real WhatsApp |
