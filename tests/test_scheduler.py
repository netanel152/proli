import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from datetime import datetime, timedelta
import pytz
from bson import ObjectId
from app.scheduler import monitor_unfinished_jobs, send_daily_reminders
from app.core.constants import LeadStatus

IL_TZ = pytz.timezone("Asia/Jerusalem")


@pytest.fixture
def mock_collections():
    with patch("app.scheduler.leads_collection") as mock_leads, patch(
        "app.scheduler.users_collection"
    ) as mock_users, patch("app.scheduler.settings_collection") as mock_settings:

        # Setup Async cursors
        mock_leads.find = MagicMock()
        mock_users.find = MagicMock()
        mock_leads.update_many = AsyncMock()
        mock_leads.update_many.return_value.modified_count = 0

        # Setup settings mock
        mock_settings.find_one = AsyncMock()
        mock_settings.find_one.return_value = {
            "stale_monitor_active": True,
            "sos_healer_active": True,
            "sos_reporter_active": True,
        }

        yield mock_leads, mock_users, mock_settings


@pytest.fixture
def mock_actions():
    with patch(
        "app.scheduler.send_pro_reminder", new_callable=AsyncMock
    ) as mock_remind, patch(
        "app.scheduler.send_customer_completion_check", new_callable=AsyncMock
    ) as mock_check, patch(
        "app.scheduler.whatsapp"
    ) as mock_whatsapp:

        mock_whatsapp.send_message = AsyncMock()

        yield mock_remind, mock_check, mock_whatsapp


@pytest.mark.asyncio
async def test_monitor_unfinished_jobs_tier1(mock_collections, mock_actions):
    mock_leads, _, _ = mock_collections
    mock_remind, mock_check, _ = mock_actions

    # Mock datetime to be 12:00 PM IL time (Business hours)
    fixed_now = datetime(2023, 1, 1, 12, 0, 0, tzinfo=IL_TZ)

    with patch("app.scheduler.datetime") as mock_datetime:
        mock_datetime.now.return_value = fixed_now
        mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)

        # Setup mock leads for Tier 1 (4-6 hours old)
        lead_id = ObjectId()
        mock_cursor = AsyncMock()
        mock_cursor.__aiter__.return_value = [{"_id": lead_id, "status": "booked"}]
        mock_leads.find.return_value = mock_cursor

        # Run Scheduler
        await monitor_unfinished_jobs()

        mock_remind.assert_called()


@pytest.mark.asyncio
async def test_monitor_unfinished_jobs_tier3(mock_collections, mock_actions):
    mock_leads, _, _ = mock_collections

    # Mock datetime 12:00 PM
    fixed_now = datetime(2023, 1, 1, 12, 0, 0, tzinfo=IL_TZ)

    with patch("app.scheduler.datetime") as mock_datetime:
        mock_datetime.now.return_value = fixed_now

        mock_leads.update_many.return_value.modified_count = 5

        # Empty cursors for find
        empty_cursor = AsyncMock()
        empty_cursor.__aiter__.return_value = []
        mock_leads.find.return_value = empty_cursor

        await monitor_unfinished_jobs()

        mock_leads.update_many.assert_called()
        args = mock_leads.update_many.call_args
        assert args[0][0]["flag"] == {"$ne": "requires_admin"}
        assert args[0][1]["$set"]["flag"] == "requires_admin"


@pytest.mark.asyncio
async def test_send_daily_reminders(mock_collections, mock_actions):
    mock_leads, mock_users, _ = mock_collections
    _, _, mock_whatsapp = mock_actions

    # Mock Users: explicit cursor setup
    pro = {
        "_id": ObjectId(),
        "business_name": "Mario Plumbing",
        "phone_number": "972500000000",
        "is_active": True,
    }
    users_cursor = MagicMock()
    users_cursor.to_list = AsyncMock(return_value=[pro])
    mock_users.find.return_value = users_cursor

    # Mock Leads (Booked Today) — PRO-9: agenda now renders from appointment_datetime
    job = {
        "created_at": datetime.now(pytz.utc),
        "appointment_datetime": datetime.now(pytz.utc),
        "chat_id": "12345@c.us",
        "details": "Leaky Faucet",
    }

    # Setup chained cursor mock for Leads
    # The code does: leads_collection.find(...).sort(...).to_list(...)
    leads_cursor = MagicMock()
    sort_cursor = MagicMock()
    sort_cursor.to_list = AsyncMock(return_value=[job])
    leads_cursor.sort.return_value = sort_cursor
    mock_leads.find.return_value = leads_cursor

    with patch("app.scheduler.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2023, 1, 1, 8, 0, 0, tzinfo=IL_TZ)

        await send_daily_reminders()

        # Verify intermediate calls
        mock_users.find.assert_called()
        mock_leads.find.assert_called()
        leads_cursor.sort.assert_called()
        sort_cursor.to_list.assert_called()

        mock_whatsapp.send_message.assert_called_once()
        msg_sent = mock_whatsapp.send_message.call_args[0][1]
        assert "Mario Plumbing" in msg_sent
        assert "Leaky Faucet" in msg_sent


# --- PRO-9: appointment_datetime is the source of truth for the daily agenda ---
#
# These tests use the real mongomock-backed collections (via the `mock_db`
# fixture / conftest.py's autouse patching of app.scheduler.leads_collection /
# users_collection / whatsapp) instead of the hand-rolled cursor mocks above,
# because they need genuine query filtering (Mongo range comparison on
# appointment_datetime) rather than a canned return value.


@pytest.mark.asyncio
async def test_send_daily_reminders_includes_booked_lead_with_appointment_today(
    mock_db,
):
    await mock_db.users.delete_many({})
    await mock_db.leads.delete_many({})

    pro_id = ObjectId()
    await mock_db.users.insert_one(
        {
            "_id": pro_id,
            "business_name": "Today Plumbing",
            "phone_number": "972500000001",
            "is_active": True,
        }
    )

    now_il = datetime.now(IL_TZ)
    today = now_il.date()
    appt_local = IL_TZ.localize(datetime(today.year, today.month, today.day, 15, 0, 0))
    appt_utc = appt_local.astimezone(pytz.utc)

    await mock_db.leads.insert_one(
        {
            "pro_id": pro_id,
            "status": LeadStatus.BOOKED,
            "appointment_datetime": appt_utc,
            "chat_id": "972500000002@c.us",
            "details": "Today's Leak Job",
        }
    )

    import app.scheduler as scheduler_module

    await scheduler_module.send_daily_reminders()

    scheduler_module.whatsapp.send_message.assert_called_once()
    msg_sent = scheduler_module.whatsapp.send_message.call_args[0][1]
    assert "15:00" in msg_sent
    assert "Today's Leak Job" in msg_sent


@pytest.mark.asyncio
async def test_send_daily_reminders_excludes_booked_lead_with_appointment_tomorrow(
    mock_db,
):
    await mock_db.users.delete_many({})
    await mock_db.leads.delete_many({})

    pro_id = ObjectId()
    await mock_db.users.insert_one(
        {
            "_id": pro_id,
            "business_name": "Tomorrow Plumbing",
            "phone_number": "972500000003",
            "is_active": True,
        }
    )

    now_il = datetime.now(IL_TZ)
    tomorrow = (now_il + timedelta(days=1)).date()
    appt_local = IL_TZ.localize(
        datetime(tomorrow.year, tomorrow.month, tomorrow.day, 15, 0, 0)
    )
    appt_utc = appt_local.astimezone(pytz.utc)

    await mock_db.leads.insert_one(
        {
            "pro_id": pro_id,
            "status": LeadStatus.BOOKED,
            "appointment_datetime": appt_utc,
            "chat_id": "972500000004@c.us",
            "details": "Tomorrow's Job",
        }
    )

    import app.scheduler as scheduler_module

    await scheduler_module.send_daily_reminders()

    scheduler_module.whatsapp.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_send_daily_reminders_excludes_booked_lead_without_appointment_datetime(
    mock_db,
):
    await mock_db.users.delete_many({})
    await mock_db.leads.delete_many({})

    pro_id = ObjectId()
    await mock_db.users.insert_one(
        {
            "_id": pro_id,
            "business_name": "No Appointment Plumbing",
            "phone_number": "972500000005",
            "is_active": True,
        }
    )

    # Booked lead with created_at = now but no appointment_datetime at all —
    # proves the query no longer keys off created_at (PRO-9 regression guard).
    await mock_db.leads.insert_one(
        {
            "pro_id": pro_id,
            "status": LeadStatus.BOOKED,
            "created_at": datetime.now(pytz.utc),
            "chat_id": "972500000006@c.us",
            "details": "No Appointment Job",
        }
    )

    import app.scheduler as scheduler_module

    await scheduler_module.send_daily_reminders()

    scheduler_module.whatsapp.send_message.assert_not_called()
