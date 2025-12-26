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

BUTTON_RESPONSE_PAYLOAD = {
    "typeWebhook": "incomingMessageReceived", # Can be buttonsResponseMessage in some versions, handled by logic
    "senderData": {"chatId": "972501234567@c.us"},
    "messageData": {
        "typeMessage": "buttonsResponseMessage",
        "buttonsResponseMessage": {
            "selectedButtonId": "approve_123",
            "selectedButtonText": "Approve"
        }
    }
}

@pytest.fixture
def mock_background_tasks():
    with patch("app.main.process_incoming_message") as mock_process, \
         patch("app.main.handle_pro_response") as mock_handle:
        yield mock_process, mock_handle

def test_webhook_valid_text_message(mock_background_tasks):
    mock_process, mock_handle = mock_background_tasks
    
    response = client.post("/webhook", json=VALID_PAYLOAD)
    
    assert response.status_code == 200
    assert response.json() == {"status": "processing_message"}
    
    # Ensure background task was added (process_incoming_message)
    # Note: TestClient runs background tasks synchronously by default in recent FastAPI versions,
    # or we can check if the mock was called if we patched it where it is IMPORTED in main.py.
    # We patched app.main.process_incoming_message, so it should be called.
    
    # Wait, FastAPI TestClient executes background tasks?
    # Yes, usually.
    # Let's verify arguments.
    mock_process.assert_called_once_with("972501234567@c.us", "Hello Fixi", None)

def test_webhook_ignored_group_message(mock_background_tasks):
    mock_process, _ = mock_background_tasks
    
    payload = VALID_PAYLOAD.copy()
    payload["senderData"] = {"chatId": "123456789@g.us", "senderName": "Group Chat"}
    
    response = client.post("/webhook", json=payload)
    
    assert response.status_code == 200
    assert response.json() == {"status": "ignored_group"}
    mock_process.assert_not_called()

def test_webhook_button_response(mock_background_tasks):
    mock_process, mock_handle = mock_background_tasks
    
    response = client.post("/webhook", json=BUTTON_RESPONSE_PAYLOAD)
    
    assert response.status_code == 200
    assert response.json() == {"status": "processing_button"}
    
    mock_handle.assert_called_once()
    # Check payload passed to handle_pro_response
    call_args = mock_handle.call_args[0][0]
    assert call_args["messageData"]["buttonsResponseMessage"]["selectedButtonId"] == "approve_123"

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
