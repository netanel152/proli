"""
Tests for notification_service.py: pro reminders, SOS alerts, SMS fallback.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from bson import ObjectId
from datetime import datetime, timezone
from app.core.constants import LeadStatus
from app.core.messages import Messages
from app.core.config import settings
from app.services.notification_service import send_pro_reminder, send_sos_alert
import app.services.notification_service


@pytest.fixture
def notif_mocks(monkeypatch, mock_db):
    mock_wa = MagicMock()
    mock_wa.send_message = AsyncMock()
    monkeypatch.setattr(app.services.notification_service, "whatsapp", mock_wa)

    mock_sms = MagicMock()
    mock_sms.is_configured = False
    mock_sms.send_sms = AsyncMock(return_value=False)
    monkeypatch.setattr(app.services.notification_service, "sms_client", mock_sms)

    return mock_wa, mock_sms, mock_db


# --- send_pro_reminder ---

@pytest.mark.asyncio
async def test_pro_reminder_booked_lead(notif_mocks):
    mock_wa, _, db = notif_mocks
    pro_id = ObjectId()

    await db.users.insert_one({"_id": pro_id, "phone_number": "972500000000"})
    lead_id = ObjectId()
    await db.leads.insert_one({
        "_id": lead_id, "status": LeadStatus.BOOKED, "pro_id": pro_id,
    })

    await send_pro_reminder(str(lead_id))

    mock_wa.send_message.assert_called_once()
    assert mock_wa.send_message.call_args.args[0] == "972500000000@c.us"
    assert "סיימת" in mock_wa.send_message.call_args.args[1]


@pytest.mark.asyncio
async def test_pro_reminder_non_booked_skipped(notif_mocks):
    mock_wa, _, db = notif_mocks
    lead_id = ObjectId()
    await db.leads.insert_one({"_id": lead_id, "status": LeadStatus.COMPLETED})

    await send_pro_reminder(str(lead_id))

    mock_wa.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_pro_reminder_missing_lead(notif_mocks):
    mock_wa, _, _ = notif_mocks
    await send_pro_reminder(str(ObjectId()))
    mock_wa.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_pro_reminder_no_pro_phone(notif_mocks):
    mock_wa, _, db = notif_mocks
    pro_id = ObjectId()
    await db.users.insert_one({"_id": pro_id})  # No phone_number

    lead_id = ObjectId()
    await db.leads.insert_one({"_id": lead_id, "status": LeadStatus.BOOKED, "pro_id": pro_id})

    await send_pro_reminder(str(lead_id))
    mock_wa.send_message.assert_not_called()


# --- send_sos_alert ---

@pytest.mark.asyncio
async def test_sos_alert_with_pro_and_lead(notif_mocks):
    mock_wa, _, db = notif_mocks
    pro_id = ObjectId()
    await db.users.insert_one({"_id": pro_id, "phone_number": "972500000000"})
    await db.leads.insert_one({
        "chat_id": "972501111111@c.us",
        "status": LeadStatus.CONTACTED,
        "issue_type": "נזילה",
        "full_address": "תל אביב",
        "appointment_time": "10:00",
        "created_at": datetime.now(timezone.utc),
    })

    await send_sos_alert("972501111111@c.us", "אני צריך עזרה", pro_id)

    # Both pro and admin should be alerted
    assert mock_wa.send_message.call_count == 2

    calls = {c.args[0]: c.args[1] for c in mock_wa.send_message.call_args_list}

    # Pro alert
    assert "972500000000@c.us" in calls
    assert "הלקוח שלך צריך עזרה" in calls["972500000000@c.us"]

    # Admin alert
    admin_chat = f"{settings.ADMIN_PHONE}@c.us"
    assert admin_chat in calls
    assert "קריאת SOS" in calls[admin_chat]
    assert "נזילה" in calls[admin_chat]


@pytest.mark.asyncio
async def test_sos_alert_no_pro(notif_mocks):
    mock_wa, _, _ = notif_mocks

    await send_sos_alert("972501111111@c.us", "help!", None)

    # Only admin alert
    assert mock_wa.send_message.call_count == 1
    admin_chat = f"{settings.ADMIN_PHONE}@c.us"
    assert mock_wa.send_message.call_args.args[0] == admin_chat


@pytest.mark.asyncio
async def test_sos_alert_no_active_lead(notif_mocks):
    mock_wa, _, _ = notif_mocks

    await send_sos_alert("972506666666@c.us", "שלום", None)

    msg = mock_wa.send_message.call_args.args[1]
    assert "אין פנייה פעילה" in msg


@pytest.mark.asyncio
async def test_sos_phone_formatting(notif_mocks):
    """Phone number 972501111111@c.us should display as 0501111111."""
    mock_wa, _, _ = notif_mocks

    await send_sos_alert("972501111111@c.us", "test", None)

    msg = mock_wa.send_message.call_args.args[1]
    assert "0501111111" in msg


# --- SMS Fallback ---

@pytest.mark.asyncio
async def test_sms_fallback_on_whatsapp_failure(notif_mocks):
    mock_wa, mock_sms, db = notif_mocks
    mock_wa.send_message.side_effect = Exception("WhatsApp down")
    mock_sms.is_configured = True
    mock_sms.send_sms.return_value = True

    pro_id = ObjectId()
    await db.users.insert_one({"_id": pro_id, "phone_number": "972500000000"})
    lead_id = ObjectId()
    await db.leads.insert_one({"_id": lead_id, "status": LeadStatus.BOOKED, "pro_id": pro_id})

    await send_pro_reminder(str(lead_id))

    # SMS should have been called as fallback
    mock_sms.send_sms.assert_called_once()


@pytest.mark.asyncio
async def test_sms_not_configured_no_fallback(notif_mocks):
    mock_wa, mock_sms, db = notif_mocks
    mock_wa.send_message.side_effect = Exception("WhatsApp down")
    mock_sms.is_configured = False

    pro_id = ObjectId()
    await db.users.insert_one({"_id": pro_id, "phone_number": "972500000000"})
    lead_id = ObjectId()
    await db.leads.insert_one({"_id": lead_id, "status": LeadStatus.BOOKED, "pro_id": pro_id})

    await send_pro_reminder(str(lead_id))

    mock_sms.send_sms.assert_not_called()
