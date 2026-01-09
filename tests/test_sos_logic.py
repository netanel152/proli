import pytest
from unittest.mock import AsyncMock, MagicMock
from app.core.constants import UserStates, LeadStatus, WorkerConstants
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
    user_text = "Help me please"
    
    # Execute
    await process_incoming_message(chat_id, user_text)
    
    # Verify Admin Alert
    admin_phone = WorkerConstants.ADMIN_PHONE
    expected_admin_chat = f"{admin_phone}@c.us"
    expected_msg_content = f"🚨 System SOS from {chat_id}. Msg: {user_text}"
    
    mock_whatsapp_notif.send_message.assert_any_call(expected_admin_chat, expected_msg_content)
    
    # Verify User Response
    mock_whatsapp_wf.send_message.assert_any_call(chat_id, "העברתי את הפרטים לצוות התמיכה, נחזור אליך בהקדם. 👨‍💻")

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
    pro_id = "pro123"
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
        "created_at": "2024-01-01" 
    })

    user_text = "אני צריך עזרה דחוף"
    
    # Execute
    await process_incoming_message(chat_id, user_text)
    
    # Verify Pro Alert
    expected_pro_chat = f"{pro_phone}@c.us"
    expected_msg_content = f"⚠️ Customer {chat_id} needs help. Msg: {user_text}"
    
    mock_whatsapp_notif.send_message.assert_any_call(expected_pro_chat, expected_msg_content)
    
    # Verify User Response
    mock_whatsapp_wf.send_message.assert_any_call(chat_id, "העברתי את הבקשה לאיש המקצוע שלך, הוא ייצור קשר בהקדם. 🛠️")
