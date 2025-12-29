import pytest
from mongomock_motor import AsyncMongoMockClient
from unittest.mock import AsyncMock, MagicMock
import os
import sys

# Ensure app is in path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock google.genai to avoid ImportErrors if package is missing/conflicted
sys.modules["google.genai"] = MagicMock()
# Also mock google.genai.types
sys.modules["google.genai.types"] = MagicMock()

@pytest.fixture(scope="module")
def mock_db():
    return AsyncMongoMockClient().fixi_db

@pytest.fixture(autouse=True)
def patch_dependencies(monkeypatch, mock_db):
    # Patch Database Collections
    users = mock_db.users
    messages = mock_db.messages
    leads = mock_db.leads
    slots = mock_db.slots
    settings = mock_db.settings
    
    import app.core.database
    monkeypatch.setattr(app.core.database, "users_collection", users)
    monkeypatch.setattr(app.core.database, "messages_collection", messages)
    monkeypatch.setattr(app.core.database, "leads_collection", leads)
    monkeypatch.setattr(app.core.database, "slots_collection", slots)
    monkeypatch.setattr(app.core.database, "settings_collection", settings)

    # Patch Scheduler Collections
    import app.scheduler
    monkeypatch.setattr(app.scheduler, "users_collection", users)
    monkeypatch.setattr(app.scheduler, "leads_collection", leads)
    monkeypatch.setattr(app.scheduler, "settings_collection", settings)

    # Patch Lead Manager Collections (Since it does 'from ... import ...')
    import app.services.lead_manager
    monkeypatch.setattr(app.services.lead_manager, "leads_collection", leads)
    monkeypatch.setattr(app.services.lead_manager, "messages_collection", messages)

    # Patch Workflow Collections
    import app.services.workflow
    monkeypatch.setattr(app.services.workflow, "users_collection", users)
    monkeypatch.setattr(app.services.workflow, "leads_collection", leads)

    # Patch New Services in Workflow
    # We need to mock the instances: whatsapp, ai, lead_manager in app.services.workflow
    
    mock_whatsapp = MagicMock()
    mock_whatsapp.send_message = AsyncMock()
    mock_whatsapp.send_location_link = AsyncMock()

    mock_ai = MagicMock()
    from app.services.ai_engine import AIResponse, ExtractedData
    mock_ai.analyze_conversation = AsyncMock(return_value=AIResponse(
        reply_to_user="Mock AI Response",
        extracted_data=ExtractedData(city="Tel Aviv", issue="Leak", full_address="Tel Aviv, Rotshild 10", appointment_time="10:00"),
        transcription=None
    ))

    import app.services.workflow
    monkeypatch.setattr(app.services.workflow, "whatsapp", mock_whatsapp)
    monkeypatch.setattr(app.services.workflow, "ai", mock_ai)
    
    # Also patch scheduler's whatsapp instance if it's imported separately
    monkeypatch.setattr(app.scheduler, "whatsapp", mock_whatsapp)
    
    return mock_db