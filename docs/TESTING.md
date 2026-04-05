# Proli Testing Guide

This document provides a comprehensive guide to testing the Proli Backend. The project uses `pytest` as the test runner and `pytest-asyncio` for handling asynchronous coroutines. `asyncio_mode = strict` is set in `pytest.ini`.

## 1. Test Suite Structure

The `tests/` directory is organized to cover Unit, Integration, and End-to-End (E2E) scenarios:

*   **`conftest.py`**: Global fixtures — auto-patches all DB collections (via `mongomock_motor`) and mocks `whatsapp`/`ai` instances for non-integration tests using `autouse=True`.

### Unit Tests
*   **`test_unit_lead_manager.py`**: Tests CRUD operations for Leads in isolation.
*   **`test_admin_auth.py`**: Verifies password hashing, cookie-based authentication, and session token logic.
*   **`test_ai_parsing.py`**: Checks if the AI Prompt templates correctly format data (does not call live AI).
*   **`test_edge_cases.py`**: Tests bad inputs (Gemini failure, WhatsApp down, unsupported file types).
*   **`test_matching_service.py`**: Tests geo-spatial routing, load balancing ($group aggregation), and fallback logic.

### Integration Tests
*   **`test_db_integration.py`**: Verifies actual read/write operations against a real MongoDB (requires `MONGO_TEST_URI`). Tests lead persistence, status flow, chat history, pro lifecycle commands, pro assignment, and stale lead queries.
*   **`test_scheduler.py`**: Tests the scheduling logic (Daily Reminders, Stale Monitor) using a sped-up clock.
*   **`test_sos_monitor.py`**: Tests the auto-healing and admin reporting logic for stuck leads.
*   **`test_sos_logic.py`**: Tests SOS alert routing and admin notification.
*   **`test_smart_dispatcher_logic.py`**: Verifies the "Matching Service" logic (City + Issue -> Best Pro).

### End-to-End (E2E) / Full Flow
*   **`test_full_flow.py`**: Simulates a complete user journey (message -> AI -> Pro -> Booking -> Completion -> Rating).
*   **`test_booking_and_messaging.py`**: Focuses on the "Booking" phase, interactive buttons, and slot locking.
*   **`test_integration_webhook.py`**: Simulates HTTP POST requests to the `/webhook` endpoint.

### Known Failures (Pre-existing)
*   **`test_full_lifecycle`**: `StopAsyncIteration` — mock `side_effect` list exhausted (insufficient mock responses for multi-step flow).
*   **`test_sos_pro_alert`**: Uses string `"pro123"` as ObjectId (invalid format).

## 2. Running Tests

### Prerequisites
Make sure you have the test dependencies installed (included in `requirements.txt`):
```bash
pip install pytest pytest-asyncio httpx mongomock
```

### Common Commands

**Run All Tests:**
```bash
pytest
```

**Run with Verbose Output (Show individual tests):**
```bash
pytest -v
```

**Run a Specific Test File:**
```bash
pytest tests/test_full_flow.py
```

**Run Tests Matching a Keyword:**
```bash
pytest -k "sos"  # Runs only SOS-related tests
```

**Stop on First Failure:**
```bash
pytest -x
```

## 3. Mocking Strategy

To keep tests fast and deterministic, we avoid calling external APIs (Green API, Google Gemini) during testing.

*   **Database:** `mongomock_motor` (`AsyncMongoMockClient`) simulates MongoDB in memory for unit tests.
*   **AI Engine:** The `ai` instance in `workflow_service` is mocked via `monkeypatch` to return predefined `AIResponse` objects.
*   **WhatsApp:** The `whatsapp` instance in `workflow_service` is mocked to verify messages without sending.
*   **Redis:** Not mocked — Redis calls gracefully fail (logged as errors) since `StateManager` and `ContextManager` have fallback behavior.
*   **Extracted modules:** `conftest.py` patches collections in `customer_flow`, `pro_flow`, `matching_service`, `notification_service`, and `data_management_service` (they use `from ... import` so each module needs its own patch).
*   **Consent:** `has_consent` is mocked to return `True` by default so existing tests bypass the consent gate in `workflow_service`.
*   **New collections:** `consent_collection`, `audit_log_collection`, and `admins_collection` are patched in conftest.
*   **Integration tests:** Use a real `MONGO_TEST_URI` database. Marked with `@pytest.mark.integration` and skipped when `MONGO_TEST_URI` is not set.

## 4. Writing New Tests

1.  **Create a file** starting with `test_` in the `tests/` folder.
2.  **Import `pytest`**.
3.  **Use `async` functions**:
    ```python
    import pytest

    @pytest.mark.asyncio
    async def test_something_cool():
        result = await my_async_function()
        assert result == "expected"
    ```
4.  **Use Fixtures**: Add `client` or `mock_db` as arguments to your test function if needed (defined in `conftest.py`).

## 5. Manual & E2E Testing Tools

In addition to `pytest`, the project includes tools for full environment testing:

*   **Manual Test Plan:** See `docs/MANUAL_TEST_PLAN.md` for structured test cases to execute via the actual WhatsApp interface.
*   **Automated Webhook Simulation:** Use `python scripts/simulate_test.py` to run automated functional flows over HTTP without requiring a live WhatsApp connection.
*   **Environment Reset:** Use `python scripts/reset_test.py` to clear out test leads, chat history, and Redis states between test runs.
*   **Demo Seeding:** Use `python scripts/seed_db.py` to populate the database with sample professionals for testing.
