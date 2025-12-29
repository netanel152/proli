import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch
from app.main import app

client = TestClient(app)

# Payload Templates
VALID_PAYLOAD = {
    "typeWebhook": "incomingMessageReceived",
    "instanceData": {"idInstance": 123, "wid": "1234567890@c.us", "typeInstance": "whatsapp"},
    "timestamp": 1234567890,
    "idMessage": "F1234567890",
    "senderData": {"chatId": "972501234567@c.us", "senderName": "Test User"},
    "messageData": {
        "typeMessage": "textMessage",
        "textMessageData": {"textMessage": "Hello Fixi"}
    }
}

@pytest.fixture
def mock_background_tasks():
    with patch("app.main.process_incoming_message") as mock_process:
        yield mock_process

def test_webhook_valid_text_message(mock_background_tasks):
    mock_process = mock_background_tasks
    
    response = client.post("/webhook", json=VALID_PAYLOAD)
    
    assert response.status_code == 200
    assert response.json() == {"status": "processing_message"}
    
    mock_process.assert_called_once_with("972501234567@c.us", "Hello Fixi", None)

def test_webhook_ignored_group_message(mock_background_tasks):
    mock_process = mock_background_tasks
    
    payload = VALID_PAYLOAD.copy()
    payload["senderData"] = {"chatId": "123456789@g.us", "senderName": "Group Chat"}
    
    response = client.post("/webhook", json=payload)
    
    assert response.status_code == 200
    assert response.json() == {"status": "ignored_group"}
    mock_process.assert_not_called()

def test_webhook_invalid_json():
    response = client.post("/webhook", content="{invalid_json}")
    assert response.status_code == 422 # Validation error

def test_webhook_missing_fields():
    # Payload missing senderData
    payload = {
        "typeWebhook": "incomingMessageReceived",
        "messageData": {"typeMessage": "textMessage"}
    }
    # Pydantic validation passes (optional fields), but logic handles it
    response = client.post("/webhook", json=payload)
    assert response.status_code == 200
    assert response.json() == {"status": "ignored_no_data"}