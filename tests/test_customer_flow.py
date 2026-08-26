"""
Tests for customer_flow.py: completion checks, ratings, reviews.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from bson import ObjectId
from datetime import datetime, timedelta, timezone
from app.core.constants import LeadStatus, Defaults, WorkerConstants
from app.core.messages import Messages
from app.services.customer_flow import (
    send_customer_completion_check,
    handle_customer_completion_text,
    handle_customer_rating_text,
    handle_customer_review_comment,
    handle_status_query,
)


@pytest.fixture
def flow_db(mock_db):
    """Seed DB with common test data."""
    return mock_db


@pytest.fixture
def mock_whatsapp():
    wa = MagicMock()
    wa.send_message = AsyncMock()
    return wa


# --- send_customer_completion_check ---


@pytest.mark.asyncio
async def test_completion_check_sends_text_message(flow_db, mock_whatsapp):
    pro_id = ObjectId()
    await flow_db.users.insert_one({"_id": pro_id, "business_name": "יוסי אינסטלציה"})

    lead_id = ObjectId()
    await flow_db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": "972501111111@c.us",
            "status": LeadStatus.BOOKED,
            "pro_id": pro_id,
        }
    )

    await send_customer_completion_check(str(lead_id), mock_whatsapp)

    mock_whatsapp.send_message.assert_called_once()
    call_args = mock_whatsapp.send_message.call_args
    # Message should contain the numeric reply instructions
    assert "השב *1*" in str(call_args)


@pytest.mark.asyncio
async def test_completion_check_non_booked_skipped(flow_db, mock_whatsapp):
    lead_id = ObjectId()
    await flow_db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": "972501111111@c.us",
            "status": LeadStatus.COMPLETED,
        }
    )

    await send_customer_completion_check(str(lead_id), mock_whatsapp)

    mock_whatsapp.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_completion_check_missing_lead(flow_db, mock_whatsapp):
    await send_customer_completion_check(str(ObjectId()), mock_whatsapp)
    mock_whatsapp.send_message.assert_not_called()


# --- completion-check cap + cooldown -------------------------------------
#
# Regression guard for the "customer nudged every 30 minutes" bug: the stale-job
# monitor re-runs every 30 min and a BOOKED lead stays inside its 6-24h Tier-2
# window until somebody answers, so an uncapped send fired once per open lead on
# every single tick. The pro side has had MAX_PRO_REMINDERS since day one; these
# tests pin the customer-side equivalent.


async def _seed_booked_lead(db, chat_id: str, **extra):
    pro_id = ObjectId()
    await db.users.insert_one({"_id": pro_id, "business_name": "אבי אינסטלציה"})
    lead_id = ObjectId()
    await db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": chat_id,
            "status": LeadStatus.BOOKED,
            "pro_id": pro_id,
            "created_at": datetime.now(timezone.utc) - timedelta(hours=8),
            **extra,
        }
    )
    return lead_id


@pytest.mark.asyncio
async def test_completion_check_stamps_lead_on_send(flow_db, mock_whatsapp):
    lead_id = await _seed_booked_lead(flow_db, "972502222201@c.us")

    await send_customer_completion_check(str(lead_id), mock_whatsapp)

    lead = await flow_db.leads.find_one({"_id": lead_id})
    assert lead["completion_check_sent_count"] == 1
    assert lead["completion_check_sent_at"] is not None


@pytest.mark.asyncio
async def test_completion_check_second_call_within_cooldown_suppressed(
    flow_db, mock_whatsapp
):
    """Two scheduler ticks 30 minutes apart must produce exactly ONE message."""
    lead_id = await _seed_booked_lead(flow_db, "972502222202@c.us")

    await send_customer_completion_check(str(lead_id), mock_whatsapp)
    await send_customer_completion_check(str(lead_id), mock_whatsapp)

    mock_whatsapp.send_message.assert_called_once()
    lead = await flow_db.leads.find_one({"_id": lead_id})
    assert lead["completion_check_sent_count"] == 1


@pytest.mark.asyncio
async def test_completion_check_resends_after_cooldown(flow_db, mock_whatsapp):
    stale = datetime.now(timezone.utc) - timedelta(
        hours=WorkerConstants.CUSTOMER_COMPLETION_CHECK_COOLDOWN_HOURS + 1
    )
    lead_id = await _seed_booked_lead(
        flow_db,
        "972502222203@c.us",
        completion_check_sent_count=1,
        completion_check_sent_at=stale,
    )

    await send_customer_completion_check(str(lead_id), mock_whatsapp)

    mock_whatsapp.send_message.assert_called_once()
    lead = await flow_db.leads.find_one({"_id": lead_id})
    assert lead["completion_check_sent_count"] == 2


@pytest.mark.asyncio
async def test_completion_check_stops_at_cap(flow_db, mock_whatsapp):
    """Cap wins even when the cooldown has long expired."""
    long_ago = datetime.now(timezone.utc) - timedelta(days=3)
    lead_id = await _seed_booked_lead(
        flow_db,
        "972502222204@c.us",
        completion_check_sent_count=WorkerConstants.MAX_CUSTOMER_COMPLETION_CHECKS,
        completion_check_sent_at=long_ago,
    )

    await send_customer_completion_check(str(lead_id), mock_whatsapp)

    mock_whatsapp.send_message.assert_not_called()
    lead = await flow_db.leads.find_one({"_id": lead_id})
    assert (
        lead["completion_check_sent_count"]
        == WorkerConstants.MAX_CUSTOMER_COMPLETION_CHECKS
    )


@pytest.mark.asyncio
async def test_completion_check_manual_trigger_bypasses_cap_but_stamps(
    flow_db, mock_whatsapp
):
    """An operator pressing the admin-panel button is a deliberate human action:
    it ignores cap + cooldown, but still restarts the cooldown so the scheduler
    does not pile on top of it minutes later."""
    recent = datetime.now(timezone.utc) - timedelta(minutes=5)
    lead_id = await _seed_booked_lead(
        flow_db,
        "972502222205@c.us",
        completion_check_sent_count=WorkerConstants.MAX_CUSTOMER_COMPLETION_CHECKS,
        completion_check_sent_at=recent,
    )

    await send_customer_completion_check(
        str(lead_id), mock_whatsapp, triggered_by="admin_panel"
    )

    mock_whatsapp.send_message.assert_called_once()
    lead = await flow_db.leads.find_one({"_id": lead_id})
    assert (
        lead["completion_check_sent_count"]
        == WorkerConstants.MAX_CUSTOMER_COMPLETION_CHECKS + 1
    )
    # Mongo (and mongomock) hand datetimes back as naive UTC.
    assert lead["completion_check_sent_at"] > recent.replace(tzinfo=None)


# --- "2 / עדיין לא" reply -------------------------------------------------


@pytest.mark.asyncio
async def test_decline_reply_acks_and_restarts_cooldown(flow_db, mock_whatsapp):
    stale = datetime.now(timezone.utc) - timedelta(hours=12)
    chat_id = "972502222206@c.us"
    lead_id = await _seed_booked_lead(
        flow_db,
        chat_id,
        completion_check_sent_count=1,
        completion_check_sent_at=stale,
    )

    result = await handle_customer_completion_text(chat_id, "2", mock_whatsapp)

    assert result == Messages.Customer.COMPLETION_NOT_YET_ACK
    lead = await flow_db.leads.find_one({"_id": lead_id})
    assert lead["status"] == LeadStatus.BOOKED  # NOT completed
    assert lead["completion_check_sent_at"] > stale.replace(tzinfo=None)


@pytest.mark.asyncio
async def test_decline_reply_ignored_when_no_check_was_sent(flow_db, mock_whatsapp):
    """A bare '2' typed into some other numeric menu must fall through to the
    normal dispatcher rather than being swallowed here."""
    chat_id = "972502222207@c.us"
    await _seed_booked_lead(flow_db, chat_id)

    result = await handle_customer_completion_text(chat_id, "2", mock_whatsapp)

    assert result is None


# --- handle_customer_completion_text ---


@pytest.mark.asyncio
async def test_handle_completion_confirms(flow_db, mock_whatsapp):
    pro_id = ObjectId()
    await flow_db.users.insert_one(
        {
            "_id": pro_id,
            "business_name": "יוסי",
            "phone_number": "972500000000",
        }
    )

    lead_id = ObjectId()
    await flow_db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": "972501111111@c.us",
            "status": LeadStatus.BOOKED,
            "pro_id": pro_id,
            "created_at": datetime.now(timezone.utc),
        }
    )

    result = await handle_customer_completion_text(
        "972501111111@c.us", "כן, הסתיים", mock_whatsapp
    )

    assert result is not None
    assert "יוסי" in result

    # Lead should be completed
    lead = await flow_db.leads.find_one({"_id": lead_id})
    assert lead["status"] == LeadStatus.COMPLETED
    assert lead["waiting_for_rating"] is True

    # Pro should be notified
    mock_whatsapp.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_handle_completion_survives_pro_notification_window_closed(
    flow_db, fake_redis
):
    """PRO-159 regression (Sentry PYTHON-1A): a closed 24h service window on the
    PRO's completion notification must not crash the customer flow. Before the
    facade translated ServiceWindowClosedError into None, this exception
    propagated straight out of handle_customer_completion_text — AFTER the lead
    had already been set COMPLETED and the context cleared — and ARQ retried the
    whole process_message_task handler, re-running those side effects.

    Uses a real WhatsAppFacade wrapping a stub provider (rather than a bare
    AsyncMock standing in for the facade) so the facade's actual
    exception-to-None translation is what's under test, not a double that was
    made unrealistically forgiving.
    """
    from app.core.phone import to_chat_id
    from app.providers.whatsapp.base import ServiceWindowClosedError, WhatsAppProvider
    from app.providers.whatsapp.facade import WhatsAppFacade

    await fake_redis.set("wa:instance:state", "authorized")

    pro_id = ObjectId()
    pro_phone = "972500000099"
    await flow_db.users.insert_one(
        {
            "_id": pro_id,
            "business_name": "יוסי",
            "phone_number": pro_phone,
        }
    )
    pro_chat_id = to_chat_id(pro_phone)

    lead_id = ObjectId()
    chat_id = "972501111199@c.us"
    await flow_db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": chat_id,
            "status": LeadStatus.BOOKED,
            "pro_id": pro_id,
            "created_at": datetime.now(timezone.utc),
        }
    )

    attempted: list[str] = []

    class _WindowClosedForPro(WhatsAppProvider):
        name = "fake-window-closed-for-pro"
        transmits = True

        async def send_text(self, chat_id, text):
            attempted.append(chat_id)
            if chat_id == pro_chat_id:
                raise ServiceWindowClosedError("closed for this recipient")
            return {"id": "ok"}

        async def send_file(self, chat_id, url, caption="", file_name="media.jpg"):
            return {"id": "ok"}

        async def send_template(self, chat_id, template_name, params=None):
            return {"id": "ok"}

        async def send_interactive(self, chat_id, body, options):
            return {"id": "ok"}

        async def get_state(self):
            return "authorized"

        def parse_webhook(self, payload):
            return None

    whatsapp = WhatsAppFacade(_WindowClosedForPro())

    result = await handle_customer_completion_text(chat_id, "כן, הסתיים", whatsapp)

    assert pro_chat_id in attempted, "the pro notification must actually be attempted"
    assert result == Messages.Customer.COMPLETION_ACK.format(pro_name="יוסי")
    lead = await flow_db.leads.find_one({"_id": lead_id})
    assert lead["status"] == LeadStatus.COMPLETED
    assert lead["waiting_for_rating"] is True


@pytest.mark.asyncio
async def test_handle_completion_numeric_yes(flow_db, mock_whatsapp):
    """Reply '1' triggers completion."""
    pro_id = ObjectId()
    await flow_db.users.insert_one(
        {"_id": pro_id, "business_name": "Test", "phone_number": "972500000001"}
    )

    lead_id = ObjectId()
    await flow_db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": "972501111112@c.us",
            "status": LeadStatus.BOOKED,
            "pro_id": pro_id,
            "created_at": datetime.now(timezone.utc),
        }
    )

    result = await handle_customer_completion_text(
        "972501111112@c.us", "1", mock_whatsapp
    )
    assert result is not None


@pytest.mark.asyncio
async def test_handle_completion_hebrew_yes(flow_db, mock_whatsapp):
    """Reply 'כן' triggers completion."""
    pro_id = ObjectId()
    await flow_db.users.insert_one(
        {"_id": pro_id, "business_name": "Test2", "phone_number": "972500000002"}
    )

    lead_id = ObjectId()
    await flow_db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": "972501111113@c.us",
            "status": LeadStatus.BOOKED,
            "pro_id": pro_id,
            "created_at": datetime.now(timezone.utc),
        }
    )

    result = await handle_customer_completion_text(
        "972501111113@c.us", "כן", mock_whatsapp
    )
    assert result is not None


@pytest.mark.asyncio
async def test_handle_completion_no_match(flow_db, mock_whatsapp):
    result = await handle_customer_completion_text(
        "972501111111@c.us", "שלום", mock_whatsapp
    )
    assert result is None


@pytest.mark.asyncio
async def test_handle_completion_no_booked_lead(flow_db, mock_whatsapp):
    # Use a unique chat_id that has no booked leads
    result = await handle_customer_completion_text(
        "972508888888@c.us", "כן, הסתיים", mock_whatsapp
    )
    assert result is None


# --- handle_customer_rating_text ---


@pytest.mark.asyncio
async def test_rating_valid(flow_db, monkeypatch):
    pro_id = ObjectId()
    await flow_db.users.insert_one(
        {
            "_id": pro_id,
            "business_name": "Test Pro",
            "social_proof": {"rating": 5.0, "review_count": 0},
        }
    )

    lead_id = ObjectId()
    await flow_db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": "972507777777@c.us",
            "waiting_for_rating": True,
            "pro_id": pro_id,
        }
    )

    result = await handle_customer_rating_text("972507777777@c.us", "4")

    assert result is not None
    assert result == Messages.Customer.REVIEW_REQUEST

    # Lead updated
    lead = await flow_db.leads.find_one({"_id": lead_id})
    assert lead["waiting_for_rating"] is False
    assert lead["rating_given"] == 4
    assert lead["waiting_for_review_comment"] is True

    # Pro rating updated in DB
    pro = await flow_db.users.find_one({"_id": pro_id})
    assert pro["social_proof"]["review_count"] == 1
    assert pro["social_proof"]["rating"] == 4.0


@pytest.mark.asyncio
async def test_rating_invalid_text(flow_db):
    result = await handle_customer_rating_text("972501111111@c.us", "great")
    assert result is None


@pytest.mark.asyncio
async def test_rating_no_waiting_lead(flow_db):
    result = await handle_customer_rating_text("972509010101@c.us", "5")
    assert result is None


# --- handle_customer_review_comment ---


@pytest.mark.asyncio
async def test_review_saved(flow_db, monkeypatch):
    pro_id = ObjectId()
    lead_id = ObjectId()
    await flow_db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": "972509020202@c.us",
            "waiting_for_review_comment": True,
            "pro_id": pro_id,
            "rating_given": 5,
        }
    )

    result = await handle_customer_review_comment("972509020202@c.us", "שירות מעולה!")

    assert result == Messages.Customer.REVIEW_SAVED

    # Review inserted
    review = await flow_db.reviews.find_one(
        {"pro_id": pro_id, "comment": "שירות מעולה!"}
    )
    assert review is not None
    assert review["comment"] == "שירות מעולה!"
    assert review["rating"] == 5

    # Lead updated
    lead = await flow_db.leads.find_one({"_id": lead_id})
    assert lead["waiting_for_review_comment"] is False


@pytest.mark.asyncio
async def test_review_no_waiting_lead(flow_db):
    result = await handle_customer_review_comment("972509030303@c.us", "good service")
    assert result is None


# --- handle_status_query ---


@pytest.mark.asyncio
async def test_handle_status_query_new_lead(flow_db):
    chat_id = "972511111111@c.us"
    await flow_db.leads.insert_one(
        {
            "chat_id": chat_id,
            "status": LeadStatus.NEW,
            "issue_type": "נזילה",
            "created_at": datetime.now(timezone.utc),
        }
    )

    result = await handle_status_query(chat_id)

    assert result is not None
    assert "נזילה" in result
    # NEW status template contains "מאתרים"
    assert "מאתרים" in result


@pytest.mark.asyncio
async def test_handle_status_query_contacted_lead(flow_db):
    chat_id = "972511111112@c.us"
    await flow_db.leads.insert_one(
        {
            "chat_id": chat_id,
            "status": LeadStatus.CONTACTED,
            "issue_type": "תקלת חשמל",
            "created_at": datetime.now(timezone.utc),
        }
    )

    result = await handle_status_query(chat_id)

    assert result is not None
    assert "תקלת חשמל" in result
    # CONTACTED template mentions waiting for pro
    assert "ממתינים" in result


@pytest.mark.asyncio
async def test_handle_status_query_booked_lead_includes_pro_name(flow_db):
    chat_id = "972511111113@c.us"
    pro_id = ObjectId()
    await flow_db.users.insert_one({"_id": pro_id, "business_name": "יוסי אינסטלציה"})
    await flow_db.leads.insert_one(
        {
            "chat_id": chat_id,
            "status": LeadStatus.BOOKED,
            "issue_type": "צנרת",
            "pro_id": pro_id,
            "appointment_time": "10:00 15/05/2026",
            "created_at": datetime.now(timezone.utc),
        }
    )

    result = await handle_status_query(chat_id)

    assert result is not None
    assert "יוסי אינסטלציה" in result
    assert "10:00 15/05/2026" in result


@pytest.mark.asyncio
async def test_handle_status_query_no_active_lead_returns_friendly_message(flow_db):
    # A chat_id with no leads at all
    result = await handle_status_query("972599999999@c.us")

    assert result == Messages.Customer.STATUS_NO_ACTIVE_LEAD


@pytest.mark.asyncio
async def test_handle_status_query_falls_back_to_recent_completed_lead(flow_db):
    chat_id = "972511111114@c.us"
    await flow_db.leads.insert_one(
        {
            "chat_id": chat_id,
            "status": LeadStatus.COMPLETED,
            "issue_type": "תיקון דלת",
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
    )

    result = await handle_status_query(chat_id)

    assert result is not None
    # COMPLETED template mentions the work ended
    assert "הסתיימה" in result
