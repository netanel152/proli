import pytest
from unittest.mock import AsyncMock, MagicMock
from bson import ObjectId
from app.core.constants import UserStates, LeadStatus
from app.core.config import settings
from app.core.messages import Messages
from app.services.workflow_service import process_incoming_message
import app.services.notification_service
import app.services.workflow_service

@pytest.mark.asyncio
async def test_sos_admin_alert(mock_db, monkeypatch):
    # Setup mocks
    mock_whatsapp_wf = MagicMock()
    mock_whatsapp_wf.send_message = AsyncMock()
    monkeypatch.setattr(app.services.workflow_service, "whatsapp", mock_whatsapp_wf)

    mock_whatsapp_notif = MagicMock()
    mock_whatsapp_notif.send_message = AsyncMock()
    monkeypatch.setattr(app.services.notification_service, "whatsapp", mock_whatsapp_notif)

    # Patch StateManager to avoid Redis calls
    mock_state_manager = MagicMock()
    mock_state_manager.get_state = AsyncMock(return_value=UserStates.IDLE)
    mock_state_manager.set_state = AsyncMock()
    monkeypatch.setattr(app.services.workflow_service, "StateManager", mock_state_manager)

    chat_id = "123456@c.us"
    user_text = "אני צריך נציג אנושי"

    # Execute
    await process_incoming_message(chat_id, user_text)

    # Verify Admin Alert was sent (Hebrew format with lead details)
    admin_phone = settings.ADMIN_PHONE
    expected_admin_chat = f"{admin_phone}@c.us"

    # Admin should always be notified
    admin_calls = [
        call for call in mock_whatsapp_notif.send_message.call_args_list
        if call.args[0] == expected_admin_chat
    ]
    assert len(admin_calls) == 1, f"Expected 1 admin alert, got {len(admin_calls)}"
    admin_msg = admin_calls[0].args[1]
    assert "קריאת SOS" in admin_msg
    assert "נציג אנושי" in admin_msg

    # Verify User Response — bot paused message
    mock_whatsapp_wf.send_message.assert_any_call(chat_id, Messages.Customer.BOT_PAUSED_BY_CUSTOMER)

@pytest.mark.asyncio
async def test_sos_pro_alert(mock_db, monkeypatch):
    # Setup mocks
    mock_whatsapp_wf = MagicMock()
    mock_whatsapp_wf.send_message = AsyncMock()
    monkeypatch.setattr(app.services.workflow_service, "whatsapp", mock_whatsapp_wf)

    mock_whatsapp_notif = MagicMock()
    mock_whatsapp_notif.send_message = AsyncMock()
    monkeypatch.setattr(app.services.notification_service, "whatsapp", mock_whatsapp_notif)

    # Patch StateManager
    mock_state_manager = MagicMock()
    mock_state_manager.get_state = AsyncMock(return_value=UserStates.IDLE)
    mock_state_manager.set_state = AsyncMock()
    monkeypatch.setattr(app.services.workflow_service, "StateManager", mock_state_manager)

    # Setup Data
    pro_id = ObjectId("65f0a1b2c3d4e5f678901234")
    chat_id = "customer123@c.us"
    pro_phone = "972500000000"

    await mock_db.users.insert_one({
        "_id": pro_id,
        "phone_number": pro_phone
    })

    await mock_db.leads.insert_one({
        "chat_id": chat_id,
        "status": LeadStatus.CONTACTED,
        "pro_id": pro_id,
        "issue_type": "נזילה",
        "full_address": "תל אביב",
        "appointment_time": "10:00",
        "created_at": "2024-01-01"
    })

    user_text = "אני צריך נציג דחוף"

    # Execute
    await process_incoming_message(chat_id, user_text)

    # Verify Pro Alert (Hebrew format)
    expected_pro_chat = f"{pro_phone}@c.us"
    pro_calls = [
        call for call in mock_whatsapp_notif.send_message.call_args_list
        if call.args[0] == expected_pro_chat
    ]
    assert len(pro_calls) == 1, f"Expected 1 pro alert, got {len(pro_calls)}"
    pro_msg = pro_calls[0].args[1]
    assert "הלקוח שלך צריך עזרה" in pro_msg

    # Verify Admin also gets notified (always)
    admin_phone = settings.ADMIN_PHONE
    expected_admin_chat = f"{admin_phone}@c.us"
    admin_calls = [
        call for call in mock_whatsapp_notif.send_message.call_args_list
        if call.args[0] == expected_admin_chat
    ]
    assert len(admin_calls) == 1, f"Expected 1 admin alert, got {len(admin_calls)}"
    admin_msg = admin_calls[0].args[1]
    assert "קריאת SOS" in admin_msg
    assert "נזילה" in admin_msg  # Lead details included

    # Verify User Response — bot paused message
    mock_whatsapp_wf.send_message.assert_any_call(chat_id, Messages.Customer.BOT_PAUSED_BY_CUSTOMER)
