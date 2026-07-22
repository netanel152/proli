import pytest
import pytest_asyncio
from mongomock_motor import AsyncMongoMockClient
from motor.motor_asyncio import AsyncIOMotorClient
from unittest.mock import AsyncMock, MagicMock
import os
import sys
import certifi
import fakeredis.aioredis
from app.core.config import settings

# Ensure app is in path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock google.genai to avoid ImportErrors if package is missing/conflicted
sys.modules["google.genai"] = MagicMock()
# Also mock google.genai.types
sys.modules["google.genai.types"] = MagicMock()


@pytest.fixture(scope="module")
def mock_db():
    return AsyncMongoMockClient().proli_db


@pytest.fixture(autouse=True)
def fake_redis(request, monkeypatch):
    """PRO-78: back every Redis touch with an in-memory fakeredis, one FRESH
    instance per test — mirroring how mongomock gives each test a clean Mongo.

    Every Redis-using service (StateManager, ContextManager, SecurityService,
    the chat/scheduler locks) funnels through `get_redis_client()`, which returns
    the module-level `_redis_client` singleton without connecting when it is
    already set. Pre-seeding that singleton with a fresh FakeRedis means each test
    reads/writes an isolated in-memory store, so no real-Redis state can bleed
    across tests or runs. That bleed (daily-AI-cap counters crossing
    DAILY_AI_CALL_CAP, leftover sliding-window / lock keys) is what made the suite
    non-deterministic and needed manual `redis-cli` flushes between runs.

    `decode_responses=True` matches the production client so services get `str`
    back, not `bytes`. Integration tests are skipped — they keep real Redis, same
    as they keep a real Mongo.

    Scope note: this isolates the `_redis_client` singleton only. The separate
    `_arq_pool` singleton (used solely by the webhook enqueue path) is NOT seeded
    here — unit tests that hit enqueue must patch `get_arq_pool` themselves (as
    tests/test_integration_webhook.py already does), or they would attempt a real
    `create_pool`.
    """
    if request.node.get_closest_marker("integration"):
        return
    import app.core.redis_client as redis_client_module

    # A fresh instance per test; no explicit close needed (in-memory, GC'd after
    # the test, and monkeypatch reverts the singleton on teardown).
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(redis_client_module, "_redis_client", fake)
    return fake


@pytest.fixture(autouse=True)
def patch_dependencies(request, monkeypatch, mock_db):
    """
    Default mocking for Unit Tests.
    Skips if the test is marked with 'integration'.
    """
    if request.node.get_closest_marker("integration"):
        return

    # Tests must be deterministic regardless of the developer's local .env. A local
    # WHATSAPP_DRY_RUN=true (needed for safe /simulate — see PRO-79) otherwise makes
    # every WhatsApp-client test short-circuit at the dry-run guard before the
    # _send_request/pause-check they assert on. Force the production default here;
    # the dry-run-specific tests opt back in with their own monkeypatch.
    monkeypatch.setattr(settings, "WHATSAPP_DRY_RUN", False)

    # Patch Database Collections
    users = mock_db.users
    messages = mock_db.messages
    leads = mock_db.leads
    slots = mock_db.slots
    settings_col = mock_db.settings
    reviews = mock_db.reviews
    consent = mock_db.consent
    audit_log = mock_db.audit_log
    admins = mock_db.admins

    import app.core.database

    monkeypatch.setattr(app.core.database, "users_collection", users)
    monkeypatch.setattr(app.core.database, "messages_collection", messages)
    monkeypatch.setattr(app.core.database, "leads_collection", leads)
    monkeypatch.setattr(app.core.database, "slots_collection", slots)
    monkeypatch.setattr(app.core.database, "settings_collection", settings_col)
    monkeypatch.setattr(app.core.database, "reviews_collection", reviews)
    monkeypatch.setattr(app.core.database, "consent_collection", consent)
    monkeypatch.setattr(app.core.database, "audit_log_collection", audit_log)
    monkeypatch.setattr(app.core.database, "admins_collection", admins)

    # Patch Scheduler Collections
    import app.scheduler

    monkeypatch.setattr(app.scheduler, "users_collection", users)
    monkeypatch.setattr(app.scheduler, "leads_collection", leads)
    monkeypatch.setattr(app.scheduler, "settings_collection", settings_col)

    # Patch Lead Manager Collections (Since it does 'from ... import ...')
    import app.services.lead_manager_service

    monkeypatch.setattr(app.services.lead_manager_service, "leads_collection", leads)
    monkeypatch.setattr(
        app.services.lead_manager_service, "messages_collection", messages
    )

    # Patch Workflow Collections
    import app.services.workflow_service

    monkeypatch.setattr(app.services.workflow_service, "users_collection", users)
    monkeypatch.setattr(app.services.workflow_service, "leads_collection", leads)

    # Patch extracted flow modules (they import collections directly)
    import app.services.customer_flow

    monkeypatch.setattr(app.services.customer_flow, "users_collection", users)
    monkeypatch.setattr(app.services.customer_flow, "leads_collection", leads)
    monkeypatch.setattr(app.services.customer_flow, "reviews_collection", reviews)

    import app.services.pro_flow

    monkeypatch.setattr(app.services.pro_flow, "users_collection", users)
    monkeypatch.setattr(app.services.pro_flow, "leads_collection", leads)
    monkeypatch.setattr(app.services.pro_flow, "reviews_collection", reviews)

    # Mock ContextManager globally — all services that call clear_context hit Redis otherwise
    mock_ctx = MagicMock()
    mock_ctx.clear_context = AsyncMock()
    mock_ctx.get_history = AsyncMock(return_value=None)
    mock_ctx.update_history = AsyncMock()
    import app.services.context_manager_service

    monkeypatch.setattr(
        app.services.context_manager_service, "ContextManager", mock_ctx
    )
    monkeypatch.setattr(app.services.pro_flow, "ContextManager", mock_ctx)
    monkeypatch.setattr(app.services.customer_flow, "ContextManager", mock_ctx)

    # Patch Matching Service Collections
    import app.services.matching_service

    monkeypatch.setattr(app.services.matching_service, "users_collection", users)
    monkeypatch.setattr(app.services.matching_service, "leads_collection", leads)

    # Patch Notification Service Collections
    import app.services.notification_service

    monkeypatch.setattr(app.services.notification_service, "users_collection", users)
    monkeypatch.setattr(app.services.notification_service, "leads_collection", leads)

    # Patch Pro Onboarding Service Collections
    import app.services.pro_onboarding_service

    monkeypatch.setattr(app.services.pro_onboarding_service, "users_collection", users)

    # Patch Data Management Service Collections
    import app.services.data_management_service

    monkeypatch.setattr(
        app.services.data_management_service, "consent_collection", consent
    )
    monkeypatch.setattr(app.services.data_management_service, "users_collection", users)
    monkeypatch.setattr(app.services.data_management_service, "leads_collection", leads)
    monkeypatch.setattr(
        app.services.data_management_service, "messages_collection", messages
    )
    monkeypatch.setattr(
        app.services.data_management_service, "reviews_collection", reviews
    )
    monkeypatch.setattr(app.services.data_management_service, "slots_collection", slots)

    # Patch Analytics Service Collections
    import app.services.analytics_service

    monkeypatch.setattr(app.services.analytics_service, "leads_collection", leads)
    monkeypatch.setattr(app.services.analytics_service, "messages_collection", messages)
    monkeypatch.setattr(app.services.analytics_service, "users_collection", users)
    monkeypatch.setattr(app.services.analytics_service, "reviews_collection", reviews)

    # Patch Audit Service Collections
    import app.services.audit_service

    monkeypatch.setattr(app.services.audit_service, "audit_log_collection", audit_log)

    # Patch Scheduling Service Collections
    import app.services.scheduling_service

    monkeypatch.setattr(app.services.scheduling_service, "users_collection", users)
    monkeypatch.setattr(app.services.scheduling_service, "slots_collection", slots)
    monkeypatch.setattr(app.services.scheduling_service, "leads_collection", leads)

    # Default: bypass consent check so existing tests pass
    # Tests that specifically test consent flow can override this
    monkeypatch.setattr(
        app.services.workflow_service, "has_consent", AsyncMock(return_value=True)
    )

    # Patch New Services in Workflow
    mock_whatsapp = MagicMock()
    mock_whatsapp.send_message = AsyncMock()
    mock_whatsapp.send_location_link = AsyncMock()
    mock_whatsapp.send_chat_state_typing = AsyncMock()

    mock_ai = MagicMock()
    from app.services.ai_engine_service import AIResponse, ExtractedData

    mock_ai.analyze_conversation = AsyncMock(
        return_value=AIResponse(
            reply_to_user="Mock AI Response",
            extracted_data=ExtractedData(
                city="Tel Aviv",
                issue="Leak",
                full_address="Tel Aviv, Rotshild 10",
                appointment_time="10:00",
            ),
            transcription=None,
            is_deal=False,
        )
    )
    # Default False so existing tests don't accidentally trigger the intent-detection path
    mock_ai.detect_service_intent = AsyncMock(return_value=False)

    import app.services.workflow_service

    monkeypatch.setattr(app.services.workflow_service, "whatsapp", mock_whatsapp)
    monkeypatch.setattr(app.services.workflow_service, "ai", mock_ai)

    # Also patch scheduler's whatsapp instance if it's imported separately
    monkeypatch.setattr(app.scheduler, "whatsapp", mock_whatsapp)

    return mock_db


@pytest_asyncio.fixture
async def integration_db(monkeypatch):
    """
    Fixture for Integration Tests (REAL DB).
    Connects to MONGO_TEST_URI, clears data, and patches app services.
    """
    if not settings.MONGO_TEST_URI:
        pytest.skip("MONGO_TEST_URI is not set in environment/config")

    client = AsyncIOMotorClient(settings.MONGO_TEST_URI)
    db = client.proli_test_db

    # Define Collections
    users = db.users
    messages = db.messages
    leads = db.leads
    slots = db.slots
    settings_col = db.settings
    reviews = db.reviews
    consent = db.consent

    # CLEAR DB before test
    await users.delete_many({})
    await messages.delete_many({})
    await leads.delete_many({})
    await slots.delete_many({})
    await settings_col.delete_many({})
    await reviews.delete_many({})
    await consent.delete_many({})

    # Patch app.core.database
    import app.core.database

    monkeypatch.setattr(app.core.database, "users_collection", users)
    monkeypatch.setattr(app.core.database, "messages_collection", messages)
    monkeypatch.setattr(app.core.database, "leads_collection", leads)
    monkeypatch.setattr(app.core.database, "slots_collection", slots)
    monkeypatch.setattr(app.core.database, "settings_collection", settings_col)
    monkeypatch.setattr(app.core.database, "reviews_collection", reviews)
    monkeypatch.setattr(app.core.database, "consent_collection", consent)

    # Patch Lead Manager
    import app.services.lead_manager_service

    monkeypatch.setattr(app.services.lead_manager_service, "leads_collection", leads)
    monkeypatch.setattr(
        app.services.lead_manager_service, "messages_collection", messages
    )

    # --- Patch Workflow Service ---
    import app.services.workflow_service

    monkeypatch.setattr(app.services.workflow_service, "users_collection", users)
    monkeypatch.setattr(app.services.workflow_service, "leads_collection", leads)

    # Bypass consent for integration tests
    monkeypatch.setattr(
        app.services.workflow_service, "has_consent", AsyncMock(return_value=True)
    )

    import app.services.customer_flow

    monkeypatch.setattr(app.services.customer_flow, "users_collection", users)
    monkeypatch.setattr(app.services.customer_flow, "leads_collection", leads)
    monkeypatch.setattr(app.services.customer_flow, "reviews_collection", reviews)

    import app.services.pro_flow

    monkeypatch.setattr(app.services.pro_flow, "users_collection", users)
    monkeypatch.setattr(app.services.pro_flow, "leads_collection", leads)

    # --- Patch Matching Service ---
    import app.services.matching_service

    monkeypatch.setattr(app.services.matching_service, "users_collection", users)
    monkeypatch.setattr(app.services.matching_service, "leads_collection", leads)

    # --- Patch Notification Service ---
    import app.services.notification_service

    monkeypatch.setattr(app.services.notification_service, "users_collection", users)
    monkeypatch.setattr(app.services.notification_service, "leads_collection", leads)

    # --- Mock External APIs in Workflow ---
    mock_whatsapp = MagicMock()
    mock_whatsapp.send_message = AsyncMock()
    mock_whatsapp.send_location_link = AsyncMock()
    mock_whatsapp.send_chat_state_typing = AsyncMock()
    monkeypatch.setattr(app.services.workflow_service, "whatsapp", mock_whatsapp)

    # Optional: Mock AI to avoid API costs during tests
    mock_ai = MagicMock()
    from app.services.ai_engine_service import AIResponse, ExtractedData

    mock_ai.analyze_conversation = AsyncMock(
        return_value=AIResponse(
            reply_to_user="Mock AI Response",
            extracted_data=ExtractedData(
                city="Tel Aviv",
                issue="Leak",
                full_address="Tel Aviv, Rotshild 10",
                appointment_time="10:00",
            ),
            transcription=None,
            is_deal=False,
        )
    )
    mock_ai.detect_service_intent = AsyncMock(return_value=False)
    monkeypatch.setattr(app.services.workflow_service, "ai", mock_ai)

    # Provide client and db to the test
    yield db

    # Cleanup (optional)
    client.close()
