"""
Tests for notification_service.py: pro reminders, SOS alerts, best-effort WhatsApp delivery.

PRO-75 removed SMS entirely — WhatsApp is now the only delivery channel and
failures are swallowed (best-effort), not retried via SMS.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from bson import ObjectId
from datetime import datetime, timezone
from app.core.constants import LeadStatus, WorkerConstants
from app.core.messages import Messages
from app.core.config import settings
from app.services.notification_service import send_pro_reminder, send_sos_alert
import app.services.notification_service


@pytest.fixture
def notif_mocks(monkeypatch, mock_db):
    mock_wa = MagicMock()
    mock_wa.send_message = AsyncMock()
    monkeypatch.setattr(app.services.notification_service, "whatsapp", mock_wa)

    return mock_wa, mock_db


# --- send_pro_reminder ---


@pytest.mark.asyncio
async def test_pro_reminder_booked_lead(notif_mocks):
    mock_wa, db = notif_mocks
    pro_id = ObjectId()

    await db.users.insert_one({"_id": pro_id, "phone_number": "972500000000"})
    lead_id = ObjectId()
    await db.leads.insert_one(
        {
            "_id": lead_id,
            "status": LeadStatus.BOOKED,
            "pro_id": pro_id,
        }
    )

    await send_pro_reminder(str(lead_id))

    mock_wa.send_message.assert_called_once()
    assert mock_wa.send_message.call_args.args[0] == "972500000000@c.us"
    assert "סיימת" in mock_wa.send_message.call_args.args[1]


@pytest.mark.asyncio
async def test_pro_reminder_non_booked_skipped(notif_mocks):
    mock_wa, db = notif_mocks
    lead_id = ObjectId()
    await db.leads.insert_one({"_id": lead_id, "status": LeadStatus.COMPLETED})

    await send_pro_reminder(str(lead_id))

    mock_wa.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_pro_reminder_missing_lead(notif_mocks):
    mock_wa, _ = notif_mocks
    await send_pro_reminder(str(ObjectId()))
    mock_wa.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_pro_reminder_no_pro_phone(notif_mocks):
    mock_wa, db = notif_mocks
    pro_id = ObjectId()
    await db.users.insert_one({"_id": pro_id})  # No phone_number

    lead_id = ObjectId()
    await db.leads.insert_one(
        {"_id": lead_id, "status": LeadStatus.BOOKED, "pro_id": pro_id}
    )

    await send_pro_reminder(str(lead_id))
    mock_wa.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_pro_reminder_at_cap_skipped(notif_mocks):
    """reminder_sent_count >= MAX_PRO_REMINDERS must skip sending entirely."""
    mock_wa, db = notif_mocks
    pro_id = ObjectId()
    await db.users.insert_one({"_id": pro_id, "phone_number": "972500000000"})

    lead_id = ObjectId()
    await db.leads.insert_one(
        {
            "_id": lead_id,
            "status": LeadStatus.BOOKED,
            "pro_id": pro_id,
            "reminder_sent_count": WorkerConstants.MAX_PRO_REMINDERS,
        }
    )

    await send_pro_reminder(str(lead_id))

    mock_wa.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_pro_reminder_below_cap_sends_and_increments(notif_mocks):
    mock_wa, db = notif_mocks
    pro_id = ObjectId()
    await db.users.insert_one({"_id": pro_id, "phone_number": "972500000000"})

    lead_id = ObjectId()
    await db.leads.insert_one(
        {
            "_id": lead_id,
            "status": LeadStatus.BOOKED,
            "pro_id": pro_id,
            "reminder_sent_count": WorkerConstants.MAX_PRO_REMINDERS - 1,
        }
    )

    await send_pro_reminder(str(lead_id))

    mock_wa.send_message.assert_called_once()
    updated = await db.leads.find_one({"_id": lead_id})
    assert updated["reminder_sent_count"] == WorkerConstants.MAX_PRO_REMINDERS


# --- send_sos_alert ---


@pytest.mark.asyncio
async def test_sos_alert_with_pro_and_lead(notif_mocks):
    mock_wa, db = notif_mocks
    pro_id = ObjectId()
    await db.users.insert_one({"_id": pro_id, "phone_number": "972500000000"})
    await db.leads.insert_one(
        {
            "chat_id": "972501111111@c.us",
            "status": LeadStatus.CONTACTED,
            "issue_type": "נזילה",
            "full_address": "תל אביב",
            "appointment_time": "10:00",
            "created_at": datetime.now(timezone.utc),
        }
    )

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
    mock_wa, _ = notif_mocks

    await send_sos_alert("972501111111@c.us", "help!", None)

    # Only admin alert
    assert mock_wa.send_message.call_count == 1
    admin_chat = f"{settings.ADMIN_PHONE}@c.us"
    assert mock_wa.send_message.call_args.args[0] == admin_chat


@pytest.mark.asyncio
async def test_sos_alert_no_active_lead(notif_mocks):
    mock_wa, _ = notif_mocks

    await send_sos_alert("972506666666@c.us", "שלום", None)

    msg = mock_wa.send_message.call_args.args[1]
    assert "אין פנייה פעילה" in msg


@pytest.mark.asyncio
async def test_sos_phone_formatting(notif_mocks):
    """Phone number 972501111111@c.us should display as 0501111111."""
    mock_wa, _ = notif_mocks

    await send_sos_alert("972501111111@c.us", "test", None)

    msg = mock_wa.send_message.call_args.args[1]
    assert "0501111111" in msg


# --- best-effort delivery (PRO-75: no SMS fallback anymore) ---


@pytest.mark.asyncio
async def test_pro_reminder_whatsapp_failure_swallowed_no_sms(notif_mocks):
    """WhatsApp send failing must not raise, and the reminder counter still
    increments (best-effort: the failure is logged, not retried via SMS)."""
    mock_wa, db = notif_mocks
    mock_wa.send_message.side_effect = Exception("WhatsApp down")

    pro_id = ObjectId()
    await db.users.insert_one({"_id": pro_id, "phone_number": "972500000000"})
    lead_id = ObjectId()
    await db.leads.insert_one(
        {"_id": lead_id, "status": LeadStatus.BOOKED, "pro_id": pro_id}
    )

    # Must not raise
    await send_pro_reminder(str(lead_id))

    mock_wa.send_message.assert_called_once()
    assert not hasattr(app.services.notification_service, "sms_client")


@pytest.mark.asyncio
async def test_sos_alert_pro_whatsapp_failure_does_not_block_admin_alert(notif_mocks):
    """If the pro's WhatsApp send fails, the admin must still be alerted
    (best-effort per-recipient, no SMS fallback)."""
    mock_wa, db = notif_mocks
    pro_id = ObjectId()
    await db.users.insert_one({"_id": pro_id, "phone_number": "972500000000"})

    async def send_side_effect(chat_id, message):
        if chat_id == "972500000000@c.us":
            raise Exception("WhatsApp down for pro")
        return None

    mock_wa.send_message.side_effect = send_side_effect

    await send_sos_alert("972501111111@c.us", "help!", pro_id)

    # Both attempted: pro (failed) + admin (succeeded)
    assert mock_wa.send_message.call_count == 2
    admin_chat = f"{settings.ADMIN_PHONE}@c.us"
    calls = [c.args[0] for c in mock_wa.send_message.call_args_list]
    assert admin_chat in calls
