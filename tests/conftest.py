import pytest
import mongomock
from unittest.mock import AsyncMock, MagicMock
import os
import sys

# Ensure app is in path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

@pytest.fixture(scope="module")
def mock_db():
    return mongomock.MongoClient().fixi_db

@pytest.fixture(autouse=True)
def patch_dependencies(monkeypatch, mock_db):
    # Patch Database Collections
    # We need to patch them where they are USED or where they are DEFINED if we can intercept before import.
    # Since imports happen at top of file, we might need to patch them in 'app.services.logic' AND 'app.core.database'
    
    # Let's create mock collections
    users = mock_db.users
    messages = mock_db.messages
    leads = mock_db.leads
    slots = mock_db.slots
    settings = mock_db.settings
    
    # Patch in app.core.database (source)
    import app.core.database
    monkeypatch.setattr(app.core.database, "users_collection", users)
    monkeypatch.setattr(app.core.database, "messages_collection", messages)
    monkeypatch.setattr(app.core.database, "leads_collection", leads)
    monkeypatch.setattr(app.core.database, "slots_collection", slots)
    monkeypatch.setattr(app.core.database, "settings_collection", settings)

    # Patch in app.services.logic (destination)
    import app.services.logic
    monkeypatch.setattr(app.services.logic, "users_collection", users)
    monkeypatch.setattr(app.services.logic, "messages_collection", messages)
    monkeypatch.setattr(app.services.logic, "leads_collection", leads)
    monkeypatch.setattr(app.services.logic, "slots_collection", slots)
    
    # Patch in app.scheduler (destination)
    import app.scheduler
    monkeypatch.setattr(app.scheduler, "users_collection", users)
    monkeypatch.setattr(app.scheduler, "leads_collection", leads)
    monkeypatch.setattr(app.scheduler, "settings_collection", settings)

    # Patch External APIs
    # Whatsapp
    # Logic
    monkeypatch.setattr(app.services.logic, "send_whatsapp_message", AsyncMock())
    monkeypatch.setattr(app.services.logic, "send_whatsapp_file", AsyncMock())
    # Scheduler
    monkeypatch.setattr(app.scheduler, "send_whatsapp_message", AsyncMock())
    
    # Google Gemini
    # We need to mock the GenerativeModel
    mock_model = MagicMock()
    mock_chat = MagicMock()
    # Default response
    mock_chat.send_message_async = AsyncMock(return_value=MagicMock(text="Mock AI Response"))
    mock_model.start_chat.return_value = mock_chat
    mock_model.generate_content_async = AsyncMock(return_value=MagicMock(text='{"intent": "UNKNOWN"}'))
    
    monkeypatch.setattr("google.generativeai.GenerativeModel", MagicMock(return_value=mock_model))
    
    # We also need to mock cloudinary
    monkeypatch.setattr(app.services.logic, "cloudinary", MagicMock())
    
    return mock_db
