import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, AsyncMock, ANY
from app.core.constants import LeadStatus, WorkerConstants
from app.services.monitor_service import remind_stale_booked_leads


@pytest.fixture
def mock_whatsapp():
    with patch("app.services.monitor_service.whatsapp") as mock:
        mock.send_message = AsyncMock()
        yield mock


@pytest.mark.asyncio
async def test_remind_stale_booked_lead(mock_db, monkeypatch, mock_whatsapp):
    """
    Test that stale booked leads get a reminder.
    """
    monkeypatch.setattr("app.services.monitor_service.leads_collection", mock_db.leads)
    monkeypatch.setattr("app.services.monitor_service.users_collection", mock_db.users)

    await mock_db.leads.delete_many({})
    await mock_db.users.delete_many({})

    stale_time = datetime.now(timezone.utc) - timedelta(
        hours=WorkerConstants.STALE_BOOKED_LEAD_HOURS + 1
    )

    pro_id = "pro_123"
    await mock_db.users.insert_one(
        {"_id": pro_id, "phone_number": "972500000000", "business_name": "Test Pro"}
    )

    lead_id = await mock_db.leads.insert_one(
        {
            "chat_id": "customer@c.us",
            "status": LeadStatus.BOOKED,
            "appointment_datetime": stale_time,
            "pro_id": pro_id,
            "customer_name": "John Doe",
        }
    )

    await remind_stale_booked_leads()

    # Verify pro notification
    mock_whatsapp.send_message.assert_called_once_with("972500000000@c.us", ANY)

    # Verify lead updated
    updated_lead = await mock_db.leads.find_one({"_id": lead_id.inserted_id})
    assert updated_lead["reminders_sent"] == 1
    assert "last_reminder_at" in updated_lead


@pytest.mark.asyncio
async def test_nudger_respects_max_reminders(mock_db, monkeypatch, mock_whatsapp):
    """
    Test that nudger stops after MAX_PRO_REMINDERS.
    """
    monkeypatch.setattr("app.services.monitor_service.leads_collection", mock_db.leads)
    monkeypatch.setattr("app.services.monitor_service.users_collection", mock_db.users)

    await mock_db.leads.delete_many({})

    stale_time = datetime.now(timezone.utc) - timedelta(
        hours=WorkerConstants.STALE_BOOKED_LEAD_HOURS + 1
    )

    await mock_db.leads.insert_one(
        {
            "status": LeadStatus.BOOKED,
            "appointment_datetime": stale_time,
            "reminders_sent": WorkerConstants.MAX_PRO_REMINDERS,
            "pro_id": "pro_123",
        }
    )

    await remind_stale_booked_leads()

    mock_whatsapp.send_message.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "last_reminder_ago,expect_sent,expected_reminders_sent",
    [
        pytest.param(timedelta(minutes=10), False, 1, id="within_cooldown_boot_replay"),
        pytest.param(
            timedelta(hours=WorkerConstants.STALE_LEAD_REMINDER_COOLDOWN_HOURS + 1),
            True,
            2,
            id="cooldown_expired",
        ),
    ],
)
async def test_nudger_respects_reminder_cooldown(
    mock_db,
    monkeypatch,
    mock_whatsapp,
    last_reminder_ago,
    expect_sent,
    expected_reminders_sent,
):
    """
    PRO-176 — remind_stale_booked_leads now also fires once shortly after
    every worker boot, so the reminder count cap alone would let repeated
    deploys minutes apart burn through MAX_PRO_REMINDERS at once. A lead
    reminded inside STALE_LEAD_REMINDER_COOLDOWN_HOURS is skipped (the boot
    replay case); once the cooldown has elapsed it's nudged normally.
    """
    monkeypatch.setattr("app.services.monitor_service.leads_collection", mock_db.leads)
    monkeypatch.setattr("app.services.monitor_service.users_collection", mock_db.users)

    await mock_db.leads.delete_many({})
    await mock_db.users.delete_many({})

    stale_time = datetime.now(timezone.utc) - timedelta(
        hours=WorkerConstants.STALE_BOOKED_LEAD_HOURS + 1
    )
    last_reminder_at = datetime.now(timezone.utc) - last_reminder_ago

    pro_id = "pro_123"
    await mock_db.users.insert_one(
        {"_id": pro_id, "phone_number": "972500000000", "business_name": "Test Pro"}
    )

    lead_id = await mock_db.leads.insert_one(
        {
            "chat_id": "customer@c.us",
            "status": LeadStatus.BOOKED,
            "appointment_datetime": stale_time,
            "pro_id": pro_id,
            "customer_name": "John Doe",
            "reminders_sent": 1,
            "last_reminder_at": last_reminder_at,
        }
    )

    await remind_stale_booked_leads()

    if expect_sent:
        mock_whatsapp.send_message.assert_called_once_with("972500000000@c.us", ANY)
    else:
        mock_whatsapp.send_message.assert_not_called()

    updated_lead = await mock_db.leads.find_one({"_id": lead_id.inserted_id})
    assert updated_lead["reminders_sent"] == expected_reminders_sent


@pytest.mark.asyncio
async def test_nudger_ignores_fresh_leads(mock_db, monkeypatch, mock_whatsapp):
    """
    Test that fresh leads (within 24h) are ignored.
    """
    monkeypatch.setattr("app.services.monitor_service.leads_collection", mock_db.leads)
    monkeypatch.setattr("app.services.monitor_service.users_collection", mock_db.users)

    await mock_db.leads.delete_many({})

    fresh_time = datetime.now(timezone.utc) - timedelta(
        hours=WorkerConstants.STALE_BOOKED_LEAD_HOURS - 1
    )

    await mock_db.leads.insert_one(
        {
            "status": LeadStatus.BOOKED,
            "appointment_datetime": fresh_time,
            "pro_id": "pro_123",
        }
    )

    await remind_stale_booked_leads()

    mock_whatsapp.send_message.assert_not_called()
