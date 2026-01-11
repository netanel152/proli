# Proli Testing Guide 🧪

This document provides a comprehensive guide to testing the Proli Backend. The project uses `pytest` as the test runner and `pytest-asyncio` for handling asynchronous coroutines.

## 1. Test Suite Structure

The `tests/` directory is organized to cover Unit, Integration, and End-to-End (E2E) scenarios:

*   **`conftest.py`**: Global fixtures for DB connections (`mongo_mock`, `redis_mock`) and app overrides.

### Unit Tests
*   **`test_unit_lead_manager.py`**: Tests CRUD operations for Leads in isolation.
*   **`test_admin_auth.py`**: Verifies password hashing and cookie-based authentication logic.
*   **`test_ai_parsing.py`**: Checks if the AI Prompt templates correctly format data (does not call live AI).
*   **`test_edge_cases.py`**: Tests bad inputs, missing fields, and boundary conditions.

### Integration Tests
*   **`test_db_integration.py`**: Verifies actual read/write operations against the Mongo Mock.
*   **`test_scheduler.py`**: Tests the scheduling logic (Daily Reminders, Stale Monitor) using a sped-up clock.
*   **`test_sos_monitor.py`**: Specifically tests the auto-healing and admin reporting logic for stuck leads.
*   **`test_smart_dispatcher_logic.py`**: Verifies the "Matching Service" logic (City + Issue -> Best Pro).

### End-to-End (E2E) / Full Flow
*   **`test_full_flow.py`**: Simulates a complete user journey:
    *   User sends message -> AI routes to Pro -> Pro receives notification -> Pro accepts -> Booking confirmed.
*   **`test_booking_and_messaging.py`**: Focuses on the "Booking" phase and ensures slots are locked correctly.
*   **`test_integration_webhook.py`**: Simulates HTTP POST requests to the `/webhook` endpoint.

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

*   **Database:** `mongomock` is used to simulate MongoDB in memory.
*   **AI Engine:** The `AIEngine` methods are mocked to return predefined JSON responses (e.g., `{"city": "Tel Aviv", "issue": "Leak"}`).
*   **WhatsApp:** `WhatsAppClient` is mocked to verify that "messages would have been sent" without actually sending them.

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
