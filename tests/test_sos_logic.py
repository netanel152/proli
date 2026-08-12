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
    mock_whatsapp_wf.send_chat_state_typing = AsyncMock()
    monkeypatch.setattr(app.services.workflow_service, "whatsapp", mock_whatsapp_wf)

    mock_whatsapp_notif = MagicMock()
    mock_whatsapp_notif.send_message = AsyncMock()
    monkeypatch.setattr(
        app.services.notification_service, "whatsapp", mock_whatsapp_notif
    )

    # Patch StateManager to avoid Redis calls
    mock_state_manager = MagicMock()
    mock_state_manager.get_state = AsyncMock(return_value=UserStates.IDLE)
    mock_state_manager.set_state = AsyncMock()
    monkeypatch.setattr(
        app.services.workflow_service, "StateManager", mock_state_manager
    )

    # PRO-88: the admin is paged via Sentry, not WhatsApp — their Cloud API
    # service window is permanently closed, so this would have needed its own
    # approved template.
    pages = []
    monkeypatch.setattr(
        app.services.notification_service, "page_operator", pages.append
    )

    chat_id = "123456@c.us"
    user_text = "אני צריך נציג אנושי"

    # Execute
    await process_incoming_message(chat_id, user_text)

    # Admin is always notified — now out of band
    assert len(pages) == 1, f"Expected 1 operator page, got {len(pages)}"
    assert "SOS" in pages[0]

    admin_calls = [
        call
        for call in mock_whatsapp_notif.send_message.call_args_list
        if call.args[0] == f"{settings.ADMIN_PHONE}@c.us"
    ]
    assert admin_calls == [], "admin must no longer receive WhatsApp"

    # The customer's own words stay out of the page — it is retained in Sentry.
    assert "נציג אנושי" not in pages[0]

    # Verify User Response — bot paused message
    mock_whatsapp_wf.send_message.assert_any_call(
        chat_id, Messages.Customer.BOT_PAUSED_BY_CUSTOMER
    )


@pytest.mark.asyncio
async def test_sos_pro_alert(mock_db, monkeypatch):
    # Setup mocks
    mock_whatsapp_wf = MagicMock()
    mock_whatsapp_wf.send_message = AsyncMock()
    mock_whatsapp_wf.send_chat_state_typing = AsyncMock()
    monkeypatch.setattr(app.services.workflow_service, "whatsapp", mock_whatsapp_wf)

    mock_whatsapp_notif = MagicMock()
    mock_whatsapp_notif.send_message = AsyncMock()
    monkeypatch.setattr(
        app.services.notification_service, "whatsapp", mock_whatsapp_notif
    )

    # Patch StateManager
    mock_state_manager = MagicMock()
    mock_state_manager.get_state = AsyncMock(return_value=UserStates.IDLE)
    mock_state_manager.set_state = AsyncMock()
    monkeypatch.setattr(
        app.services.workflow_service, "StateManager", mock_state_manager
    )

    # PRO-88: admin paging moved off WhatsApp — capture it out of band.
    pages = []
    monkeypatch.setattr(
        app.services.notification_service, "page_operator", pages.append
    )

    # Setup Data
    pro_id = ObjectId("65f0a1b2c3d4e5f678901234")
    chat_id = "customer123@c.us"
    pro_phone = "972500000000"

    await mock_db.users.insert_one({"_id": pro_id, "phone_number": pro_phone})

    await mock_db.leads.insert_one(
        {
            "chat_id": chat_id,
            "status": LeadStatus.CONTACTED,
            "pro_id": pro_id,
            "issue_type": "נזילה",
            "full_address": "תל אביב",
            "appointment_time": "10:00",
            "created_at": "2024-01-01",
        }
    )

    user_text = "אני צריך נציג דחוף"

    # Execute
    await process_incoming_message(chat_id, user_text)

    # Verify Pro Alert (Hebrew format)
    expected_pro_chat = f"{pro_phone}@c.us"
    pro_calls = [
        call
        for call in mock_whatsapp_notif.send_message.call_args_list
        if call.args[0] == expected_pro_chat
    ]
    assert len(pro_calls) == 1, f"Expected 1 pro alert, got {len(pro_calls)}"
    pro_msg = pro_calls[0].args[1]
    assert "הלקוח שלך צריך עזרה" in pro_msg

    # Verify Admin also gets notified (always) — via Sentry since PRO-88
    assert len(pages) == 1, f"Expected 1 operator page, got {len(pages)}"
    assert "נזילה" in pages[0]  # lead details still identify the job
    admin_calls = [
        call
        for call in mock_whatsapp_notif.send_message.call_args_list
        if call.args[0] == f"{settings.ADMIN_PHONE}@c.us"
    ]
    assert admin_calls == [], "admin must no longer receive WhatsApp"

    # Verify User Response — bot paused message
    mock_whatsapp_wf.send_message.assert_any_call(
        chat_id, Messages.Customer.BOT_PAUSED_BY_CUSTOMER
    )
