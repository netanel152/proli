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
    mock_whatsapp.send_message.assert_called_once_with(
        "972500000000@c.us", ANY, template_key="stale_lead_reminder", params=ANY
    )

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
        mock_whatsapp.send_message.assert_called_once_with(
            "972500000000@c.us", ANY, template_key="stale_lead_reminder", params=ANY
        )
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


async def _seed_stale_booked_lead(mock_db, monkeypatch):
    monkeypatch.setattr("app.services.monitor_service.leads_collection", mock_db.leads)
    monkeypatch.setattr("app.services.monitor_service.users_collection", mock_db.users)
    await mock_db.leads.delete_many({})
    await mock_db.users.delete_many({})

    stale_time = datetime.now(timezone.utc) - timedelta(
        hours=WorkerConstants.STALE_BOOKED_LEAD_HOURS + 1
    )
    await mock_db.users.insert_one(
        {"_id": "pro_123", "phone_number": "972500000000", "business_name": "Pro"}
    )
    result = await mock_db.leads.insert_one(
        {
            "chat_id": "customer@c.us",
            "status": LeadStatus.BOOKED,
            "appointment_datetime": stale_time,
            "pro_id": "pro_123",
            "customer_name": "John Doe",
        }
    )
    return result.inserted_id


@pytest.fixture
def nudger_logs():
    from loguru import logger

    records = []
    handler_id = logger.add(lambda m: records.append(m.record), level="DEBUG")
    yield records
    logger.remove(handler_id)


@pytest.mark.asyncio
async def test_delivered_reminder_is_counted(
    mock_db, monkeypatch, mock_whatsapp, nudger_logs
):
    """A send the facade reports as delivered counts against MAX_PRO_REMINDERS
    and logs success."""
    mock_whatsapp.send_message.return_value = "wamid.OK"
    lead_id = await _seed_stale_booked_lead(mock_db, monkeypatch)

    await remind_stale_booked_leads()

    lead = await mock_db.leads.find_one({"_id": lead_id})
    assert lead["reminders_sent"] == 1
    assert "last_reminder_at" in lead
    assert "last_reminder_blocked_at" not in lead
    assert any("Sent reminder" in r["message"] for r in nudger_logs)


@pytest.mark.asyncio
async def test_blocked_reminder_is_not_counted(
    mock_db, monkeypatch, mock_whatsapp, nudger_logs
):
    """None is the facade's "blocked, not sent" (PRO-159): a closed window
    with `stale_lead_reminder` unarmed, the breaker or the kill switch. It must
    not burn a reminder or log as sent, but it starts the cooldown so the next
    tick does not retry (and re-page) the same closed window."""
    mock_whatsapp.send_message.return_value = None
    lead_id = await _seed_stale_booked_lead(mock_db, monkeypatch)

    await remind_stale_booked_leads()

    mock_whatsapp.send_message.assert_called_once()
    lead = await mock_db.leads.find_one({"_id": lead_id})
    assert "reminders_sent" not in lead
    assert "last_reminder_at" not in lead
    assert "last_reminder_blocked_at" in lead
    assert not any("Sent reminder" in r["message"] for r in nudger_logs)
    assert any(
        r["level"].name == "WARNING" and "not counted" in r["message"]
        for r in nudger_logs
    )

    # Inside the cooldown the next tick leaves the lead alone.
    mock_whatsapp.send_message.reset_mock()
    await remind_stale_booked_leads()
    mock_whatsapp.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_blocked_cooldown_expires(mock_db, monkeypatch, mock_whatsapp):
    """Once STALE_LEAD_REMINDER_COOLDOWN_HOURS has passed since a blocked send,
    the lead is tried again, and a delivered retry is counted."""
    lead_id = await _seed_stale_booked_lead(mock_db, monkeypatch)
    await mock_db.leads.update_one(
        {"_id": lead_id},
        {
            "$set": {
                "last_reminder_blocked_at": datetime.now(timezone.utc)
                - timedelta(
                    hours=WorkerConstants.STALE_LEAD_REMINDER_COOLDOWN_HOURS + 1
                )
            }
        },
    )
    mock_whatsapp.send_message.return_value = "wamid.OK"

    await remind_stale_booked_leads()

    mock_whatsapp.send_message.assert_called_once()
    lead = await mock_db.leads.find_one({"_id": lead_id})
    assert lead["reminders_sent"] == 1
