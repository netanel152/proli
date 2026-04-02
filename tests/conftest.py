import pytest
import pytest_asyncio
from mongomock_motor import AsyncMongoMockClient
from motor.motor_asyncio import AsyncIOMotorClient
from unittest.mock import AsyncMock, MagicMock
import os
import sys
import certifi
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
def patch_dependencies(request, monkeypatch, mock_db):
    """
    Default mocking for Unit Tests.
    Skips if the test is marked with 'integration'.
    """
    if request.node.get_closest_marker("integration"):
        return

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
    monkeypatch.setattr(app.services.lead_manager_service, "messages_collection", messages)

    # Patch Workflow Collections
    import app.services.workflow_service
    monkeypatch.setattr(app.services.workflow_service, "users_collection", users)
    monkeypatch.setattr(app.services.workflow_service, "leads_collection", leads)
    monkeypatch.setattr(app.services.workflow_service, "reviews_collection", reviews)

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
    monkeypatch.setattr(app.services.context_manager_service, "ContextManager", mock_ctx)
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
    monkeypatch.setattr(app.services.data_management_service, "consent_collection", consent)
    monkeypatch.setattr(app.services.data_management_service, "users_collection", users)
    monkeypatch.setattr(app.services.data_management_service, "leads_collection", leads)
    monkeypatch.setattr(app.services.data_management_service, "messages_collection", messages)
    monkeypatch.setattr(app.services.data_management_service, "reviews_collection", reviews)
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
    monkeypatch.setattr(app.services.workflow_service, "has_consent", AsyncMock(return_value=True))

    # Patch New Services in Workflow
    mock_whatsapp = MagicMock()
    mock_whatsapp.send_message = AsyncMock()
    mock_whatsapp.send_location_link = AsyncMock()

    mock_ai = MagicMock()
    from app.services.ai_engine_service import AIResponse, ExtractedData
    mock_ai.analyze_conversation = AsyncMock(return_value=AIResponse(
        reply_to_user="Mock AI Response",
        extracted_data=ExtractedData(city="Tel Aviv", issue="Leak", full_address="Tel Aviv, Rotshild 10", appointment_time="10:00"),
        transcription=None,
        is_deal=False
    ))

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
    monkeypatch.setattr(app.services.lead_manager_service, "messages_collection", messages)

    # --- Patch Workflow Service ---
    import app.services.workflow_service
    monkeypatch.setattr(app.services.workflow_service, "users_collection", users)
    monkeypatch.setattr(app.services.workflow_service, "leads_collection", leads)
    monkeypatch.setattr(app.services.workflow_service, "reviews_collection", reviews)

    # Bypass consent for integration tests
    monkeypatch.setattr(app.services.workflow_service, "has_consent", AsyncMock(return_value=True))

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
    monkeypatch.setattr(app.services.workflow_service, "whatsapp", mock_whatsapp)

    # Optional: Mock AI to avoid API costs during tests
    mock_ai = MagicMock()
    from app.services.ai_engine_service import AIResponse, ExtractedData
    mock_ai.analyze_conversation = AsyncMock(return_value=AIResponse(
        reply_to_user="Mock AI Response",
        extracted_data=ExtractedData(city="Tel Aviv", issue="Leak", full_address="Tel Aviv, Rotshild 10", appointment_time="10:00"),
        transcription=None,
        is_deal=False
    ))
    monkeypatch.setattr(app.services.workflow_service, "ai", mock_ai)

    # Provide client and db to the test
    yield db

    # Cleanup (optional)
    client.close()