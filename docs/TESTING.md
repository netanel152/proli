# Proli Testing Guide

The test suite uses `pytest` with `pytest-asyncio` in strict mode (`asyncio_mode = strict`). All unit tests use `mongomock_motor` (in-memory MongoDB) — no real database or external API required.

**Current status: 162 passed, 6 skipped** (integration tests skipped when `MONGO_TEST_URI` is not set).

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
| `test_workflow_orchestrator.py` | Central routing: reset commands, pro auto-detect, AWAITING_ADDRESS, AWAITING_PRO_APPROVAL, PAUSED_FOR_HUMAN, SOS→TTL, deal finalization, no-pro fallback |
| `test_smart_dispatcher_logic.py` | Dispatcher AI: missing info → clarify, city+issue → handoff to pro, audio transcription flow |
| `test_pro_flow.py` | Pro commands: approve, reject, finish, pause bot, resume, button handlers |
| `test_customer_flow.py` | Post-job: completion checks, rating prompts, review collection |
| `test_sos_logic.py` | SOS alerts: admin notification, pro notification, BOT_PAUSED_BY_CUSTOMER message |

### Matching & Routing

| File | What it covers |
|------|---------------|
| `test_matching_service.py` | `$geoNear` pipeline, progressive radius (10→20→30 km), no-pro-at-max-radius returns None, text fallback, load balancing, excluded pro IDs, rating sort |

### Infrastructure

| File | What it covers |
|------|---------------|
| `test_unit_lead_manager.py` | Lead CRUD in isolation |
| `test_booking_and_messaging.py` | Slot booking, `send_interactive_buttons` payload (`sendButtons` endpoint) |
| `test_security_service.py` | Rate limiting (Redis fixed-window) |
| `test_consent_flow.py` | Privacy consent gate |
| `test_media_handler.py` | Media type detection, image download, audio/video URL handling |
| `test_notification_service.py` | WhatsApp + SMS notifications |
| `test_analytics_service.py` | Lead funnel and performance aggregations |
| `test_audit_service.py` | Admin action logging |
| `test_scheduling_service.py` | Recurring templates, slot generation |
| `test_pro_onboarding.py` | WhatsApp self-signup flow |
| `test_data_management.py` | Consent, data export, deletion |
| `test_admin_auth.py` | Password hashing, cookie auth, session tokens |
| `test_ai_parsing.py` | Prompt template formatting (no live API calls) |
| `test_edge_cases.py` | Bad inputs: Gemini failure, WhatsApp down, unsupported file types |

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
    extracted_data=ExtractedData(city="Tel Aviv", issue="Leak", ...),
    transcription=None,
)

# Sequence of responses (dispatcher then pro)
mock_ai.analyze_conversation.side_effect = [dispatcher_resp, pro_resp]

# Override settings flag
monkeypatch.setattr(settings, "WHATSAPP_BUTTONS_ENABLED", True)

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

---

## 5. Manual & E2E Tools

| Tool | Command | Purpose |
|------|---------|---------|
| Webhook simulator | `python scripts/simulate_webhook.py` | Interactive — craft any message and POST to local backend |
| Automated scenarios | `python scripts/simulate_test.py` | Runs TC1–TC12: consent, rejection, SOS, media, idempotency |
| Environment reset | `python scripts/reset_test.py` | Clear test leads, Redis state/context/webhook keys |
| DB seeding | `python scripts/seed_db.py` | Populate with sample professionals |
| Manual test plan | `docs/MANUAL_TEST_PLAN.md` | Step-by-step via real WhatsApp |
