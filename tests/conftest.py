import pytest
from mongomock_motor import AsyncMongoMockClient
from unittest.mock import AsyncMock, MagicMock
import os
import sys

# Ensure app is in path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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

    import app.services.logic
    monkeypatch.setattr(app.services.logic, "users_collection", users)
    monkeypatch.setattr(app.services.logic, "messages_collection", messages)
    monkeypatch.setattr(app.services.logic, "leads_collection", leads)
    monkeypatch.setattr(app.services.logic, "slots_collection", slots)
    
    import app.scheduler
    monkeypatch.setattr(app.scheduler, "users_collection", users)
    monkeypatch.setattr(app.scheduler, "leads_collection", leads)
    monkeypatch.setattr(app.scheduler, "settings_collection", settings)

    # Patch External APIs
    monkeypatch.setattr(app.services.logic, "send_whatsapp_message", AsyncMock())
    monkeypatch.setattr(app.services.logic, "send_whatsapp_file", AsyncMock())
    monkeypatch.setattr(app.scheduler, "send_whatsapp_message", AsyncMock())
    
    # Google GenAI Mock
    mock_client = MagicMock()
    mock_client.models = MagicMock()
    mock_client.files = MagicMock()
    
    # Default behavior
    mock_client.models.generate_content = AsyncMock(return_value=MagicMock(text="Mock AI Response"))
    mock_client.files.upload = AsyncMock(return_value=MagicMock(uri="http://mock", mime_type="image/jpeg"))

    # Patch class
    monkeypatch.setattr("google.genai.Client", MagicMock(return_value=mock_client))
    
    # Patch instances
    monkeypatch.setattr(app.services.logic, "client", mock_client)

    # Mock Cloudinary
    monkeypatch.setattr(app.services.logic, "cloudinary", MagicMock())
    
    return mock_db