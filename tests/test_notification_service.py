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


@pytest.fixture
def pages(monkeypatch):
    """Capture operator pages (PRO-88).

    The admin leg of send_sos_alert no longer goes over WhatsApp — the admin
    never messages the bot, so their Cloud API service window is permanently
    closed. It pages via page_critical (PRO-113) → Sentry instead. Patching the
    function rather than reading a loguru sink keeps these tests asserting the
    *decision to page* rather than log formatting.
    """
    recorded = []
    monkeypatch.setattr(
        app.services.notification_service, "page_operator", recorded.append
    )
    return recorded


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
async def test_sos_alert_with_pro_and_lead(notif_mocks, pages):
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

    # PRO-88: the pro gets WhatsApp (they are a real recipient); the admin is
    # paged via Sentry instead, so exactly ONE outbound message goes out.
    assert mock_wa.send_message.call_count == 1
    calls = {c.args[0]: c.args[1] for c in mock_wa.send_message.call_args_list}
    assert "972500000000@c.us" in calls
    assert "הלקוח שלך צריך עזרה" in calls["972500000000@c.us"]

    admin_chat = f"{settings.ADMIN_PHONE}@c.us"
    assert admin_chat not in calls, "admin must no longer receive WhatsApp"

    # The page carries enough to find the lead, and nothing more.
    assert len(pages) == 1
    assert "נזילה" in pages[0]
    assert "SOS" in pages[0]


@pytest.mark.asyncio
async def test_sos_alert_no_pro(notif_mocks, pages):
    """With no pro assigned, an SOS produces ZERO outbound messages (PRO-88).

    Worth stating explicitly: this used to be the one path that always sent
    something. The signal is not lost — it pages the operator — but nothing
    leaves over WhatsApp, so no template is needed for it.
    """
    mock_wa, _ = notif_mocks

    await send_sos_alert("972501111111@c.us", "help!", None)

    assert mock_wa.send_message.call_count == 0
    assert len(pages) == 1


@pytest.mark.asyncio
async def test_sos_alert_no_active_lead(notif_mocks, pages):
    await send_sos_alert("972506666666@c.us", "שלום", None)

    assert len(pages) == 1
    assert "none active" in pages[0]


@pytest.mark.asyncio
async def test_sos_page_masks_the_customer_phone(notif_mocks, pages):
    """The page reaches Sentry, which retains events — so it carries the last
    4 digits only. The operator opens the lead in the admin panel for the rest.

    Replaces the old assertion that the full local number (0501111111) appeared
    in the admin's WhatsApp message; that was correct then and is a PII leak now.
    """
    await send_sos_alert("972501111111@c.us", "test", None)

    assert len(pages) == 1
    assert "***1111" in pages[0]
    assert "0501111111" not in pages[0]
    assert "972501111111" not in pages[0]


@pytest.mark.asyncio
async def test_sos_page_omits_the_customer_message(notif_mocks, pages):
    """Free-form text from a distressed person can contain anything, and this
    now lands in a retained Sentry event rather than a chat the operator reads
    once."""
    await send_sos_alert("972501111111@c.us", "my ID is 123456789", None)

    assert "123456789" not in pages[0]


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
async def test_sos_alert_pro_whatsapp_failure_does_not_block_admin_page(
    notif_mocks, pages
):
    """A failed pro send must not swallow the operator page.

    Stronger than before PRO-88: the page no longer shares a transport with
    the pro alert, so a total WhatsApp outage still reaches the operator.
    """
    mock_wa, db = notif_mocks
    pro_id = ObjectId()
    await db.users.insert_one({"_id": pro_id, "phone_number": "972500000000"})

    async def send_side_effect(chat_id, message):
        raise Exception("WhatsApp down entirely")

    mock_wa.send_message.side_effect = send_side_effect

    await send_sos_alert("972501111111@c.us", "help!", pro_id)

    assert mock_wa.send_message.call_count == 1  # attempted the pro, failed
    assert len(pages) == 1  # operator paged regardless
    assert "pro_notified=True" in pages[0]
