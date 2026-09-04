"""
Tests for customer_flow.py: completion checks, ratings, reviews.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from bson import ObjectId
from datetime import datetime, timedelta, timezone
from app.core.constants import LeadStatus, Defaults, WorkerConstants
from app.core.messages import Messages
from app.core.phone import to_chat_id
from tests.copy_util import static_prefix, longest_static_chunk
import app.services.customer_flow
from app.services import monitor_service
from app.services.customer_flow import (
    send_customer_completion_check,
    handle_customer_completion_text,
    handle_customer_rating_text,
    handle_customer_review_comment,
    handle_status_query,
    parse_rating,
    is_skip_token,
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
    assert longest_static_chunk(Messages.Customer.COMPLETION_CHECK) in call_args.args[1]


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


# --- PRO-45: "3 / איש המקצוע לא הגיע" no-show report ----------------------
#
# `no_show_count` was structurally dead until this landed: `candidate_score`
# in matching_service has always subtracted `no_shows * 0.5`, but nothing
# ever incremented the field. These tests pin the one production writer it
# now has (`_handle_no_show_report`, reached only through the completion
# check's "3" reply or an unprompted "לא הגיע").


@pytest.fixture
def mock_reassign(monkeypatch):
    """`_handle_no_show_report` hands the lead to `monitor_service.reassign_lead`
    (imported locally inside the function) -- patch the attribute on the real
    module object, not on `customer_flow`, which never binds the name.

    Also stubs `page_operator` (bound directly into `customer_flow` via
    `from ... import page_operator`, so nothing else patches it): every
    no-show report pages the operator, and without a stub each of these tests
    fires a real `page_critical` -- harmless without SENTRY_DSN, but a
    locally configured run would page a human from a test."""
    mock = AsyncMock(return_value=True)
    monkeypatch.setattr(monitor_service, "reassign_lead", mock)
    monkeypatch.setattr(app.services.customer_flow, "page_operator", MagicMock())
    return mock


async def _seed_booked_lead_with_pro(db, chat_id: str, **extra):
    pro_id = ObjectId()
    await db.users.insert_one(
        {
            "_id": pro_id,
            "business_name": "דני חשמלאי",
            "phone_number": "972509999999",
        }
    )
    slot_id = ObjectId()
    await db.slots.insert_one({"_id": slot_id, "is_taken": True})
    lead_id = ObjectId()
    await db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": chat_id,
            "status": LeadStatus.BOOKED,
            "pro_id": pro_id,
            "booked_slot_id": slot_id,
            "created_at": datetime.now(timezone.utc) - timedelta(hours=8),
            **extra,
        }
    )
    return lead_id, pro_id, slot_id


@pytest.mark.asyncio
async def test_no_show_digit_reports_and_drives_every_side_effect(
    flow_db, mock_whatsapp, mock_reassign
):
    """The bare '3', on a lead that was actually nudged, is the whole PRO-45
    chain: penalty recorded, slot freed, old pro told, reassignment handed
    off with notify_old_pro=False (this is not "no response"), and the
    customer gets the receipt copy."""
    chat_id = "972502222410@c.us"
    lead_id, pro_id, slot_id = await _seed_booked_lead_with_pro(
        flow_db,
        chat_id,
        completion_check_sent_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )

    result = await handle_customer_completion_text(chat_id, "3", mock_whatsapp)

    assert result == Messages.Customer.NO_SHOW_ACK

    lead = await flow_db.leads.find_one({"_id": lead_id})
    assert lead["no_show_reported_at"] is not None
    # The claim $unsets the pointer to the slot it is about to release -- the
    # lead survives (unlike every other slot release in the codebase), so a
    # later cancel/reschedule must not free a slot the old pro may since have
    # re-sold.
    assert "booked_slot_id" not in lead

    pro = await flow_db.users.find_one({"_id": pro_id})
    assert pro["no_show_count"] == 1  # scheduling_service.record_no_show, for real

    slot = await flow_db.slots.find_one({"_id": slot_id})
    assert slot["is_taken"] is False

    mock_whatsapp.send_message.assert_called_once_with(
        to_chat_id("972509999999"), Messages.Pro.CUSTOMER_REPORTED_NO_SHOW
    )

    mock_reassign.assert_awaited_once()
    reassign_args = mock_reassign.await_args
    assert reassign_args.args[0]["_id"] == lead_id
    assert reassign_args.kwargs["notify_old_pro"] is False


@pytest.mark.asyncio
async def test_no_show_digit_ignored_without_nudge(
    flow_db, mock_whatsapp, mock_reassign
):
    """The bare '3' stays as narrow as the '2' decline: without a completion
    check actually sent, a stray '3' in some other numeric menu must not
    cancel a live booked job."""
    chat_id = "972502222411@c.us"
    lead_id, pro_id, _ = await _seed_booked_lead_with_pro(flow_db, chat_id)

    result = await handle_customer_completion_text(chat_id, "3", mock_whatsapp)

    assert result is None
    lead = await flow_db.leads.find_one({"_id": lead_id})
    assert "no_show_reported_at" not in lead
    pro = await flow_db.users.find_one({"_id": pro_id})
    assert pro.get("no_show_count", 0) == 0
    mock_reassign.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_show_digit_stale_nudge_ignored(flow_db, mock_whatsapp, mock_reassign):
    """Presence of `completion_check_sent_at` is not enough on its own -- the
    field is stamped once, never expires and is never unset, so without
    `NO_SHOW_REPORT_MAX_AGE_HOURS` a stray '3' from that chat would stay armed
    for the rest of the booking's life. Same trap PRO-122 closed for the
    rating prompt."""
    chat_id = "972502222415@c.us"
    stale = datetime.now(timezone.utc) - timedelta(
        hours=WorkerConstants.NO_SHOW_REPORT_MAX_AGE_HOURS + 1
    )
    lead_id, pro_id, _ = await _seed_booked_lead_with_pro(
        flow_db, chat_id, completion_check_sent_at=stale
    )

    result = await handle_customer_completion_text(chat_id, "3", mock_whatsapp)

    assert result is None
    lead = await flow_db.leads.find_one({"_id": lead_id})
    assert "no_show_reported_at" not in lead
    pro = await flow_db.users.find_one({"_id": pro_id})
    assert pro.get("no_show_count", 0) == 0
    mock_reassign.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "suffix, text",
    [("20", "לא הגיע"), ("21", "איש המקצוע לא הגיע")],
)
async def test_no_show_written_form_reports_when_appointment_due(
    flow_db, mock_whatsapp, mock_reassign, suffix, text
):
    """Unlike the digit, the written forms carry no menu ambiguity, so they
    need no completion-check nudge -- but they do need the appointment to
    have actually come due."""
    chat_id = f"9725022224{suffix}@c.us"
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    lead_id, pro_id, _ = await _seed_booked_lead_with_pro(
        flow_db, chat_id, appointment_datetime=past
    )

    result = await handle_customer_completion_text(chat_id, text, mock_whatsapp)

    assert result == Messages.Customer.NO_SHOW_ACK
    lead = await flow_db.leads.find_one({"_id": lead_id})
    assert lead["no_show_reported_at"] is not None
    pro = await flow_db.users.find_one({"_id": pro_id})
    assert pro["no_show_count"] == 1


@pytest.mark.asyncio
async def test_no_show_written_form_future_appointment_not_due(
    flow_db, mock_whatsapp, mock_reassign
):
    """'לא הגיע' about a job booked for later must not tear the booking down
    before the appointment has even happened."""
    chat_id = "972502222422@c.us"
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    lead_id, pro_id, _ = await _seed_booked_lead_with_pro(
        flow_db, chat_id, appointment_datetime=future
    )

    result = await handle_customer_completion_text(chat_id, "לא הגיע", mock_whatsapp)

    assert result is None
    lead = await flow_db.leads.find_one({"_id": lead_id})
    assert "no_show_reported_at" not in lead
    pro = await flow_db.users.find_one({"_id": pro_id})
    assert pro.get("no_show_count", 0) == 0
    mock_reassign.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "chat_suffix, has_live_nudge, expect_report",
    [("23", True, True), ("24", False, False)],
)
async def test_no_show_written_form_asap_falls_back_to_live_nudge(
    flow_db, mock_whatsapp, mock_reassign, chat_suffix, has_live_nudge, expect_report
):
    """An ASAP lead carries no `appointment_datetime` at all, so the written
    form leans on the same live-nudge evidence the digit uses -- it is not a
    free pass just because there was never a scheduled time to compare against."""
    chat_id = f"9725022224{chat_suffix}@c.us"
    extra = {}
    if has_live_nudge:
        extra["completion_check_sent_at"] = datetime.now(timezone.utc) - timedelta(
            minutes=5
        )
    lead_id, _, _ = await _seed_booked_lead_with_pro(flow_db, chat_id, **extra)

    result = await handle_customer_completion_text(chat_id, "לא הגיע", mock_whatsapp)

    lead = await flow_db.leads.find_one({"_id": lead_id})
    if expect_report:
        assert result == Messages.Customer.NO_SHOW_ACK
        assert lead["no_show_reported_at"] is not None
    else:
        assert result is None
        assert "no_show_reported_at" not in lead


@pytest.mark.asyncio
async def test_no_show_second_report_is_idempotent(
    flow_db, mock_whatsapp, mock_reassign
):
    """The claim requires `no_show_reported_at` to be absent -- a second '3'
    (a double tap, or two worker replicas) must not cost the pro a second
    penalty or trigger a second rematch."""
    chat_id = "972502222412@c.us"
    already_reported = datetime.now(timezone.utc) - timedelta(minutes=1)
    _, pro_id, _ = await _seed_booked_lead_with_pro(
        flow_db,
        chat_id,
        completion_check_sent_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        no_show_reported_at=already_reported,
    )
    await flow_db.users.update_one({"_id": pro_id}, {"$set": {"no_show_count": 1}})

    result = await handle_customer_completion_text(chat_id, "3", mock_whatsapp)

    assert result is None
    pro = await flow_db.users.find_one({"_id": pro_id})
    assert pro["no_show_count"] == 1  # unchanged — not incremented again
    mock_reassign.assert_not_awaited()
    mock_whatsapp.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_no_show_substring_is_not_a_false_positive(
    flow_db, mock_whatsapp, mock_reassign
):
    """PRO-118's lesson, reapplied: matching is an exact set, never a
    substring, so a plumbing complaint that happens to contain the phrase
    ('water didn't arrive at the boiler') can't cancel the job it describes."""
    chat_id = "972502222413@c.us"
    lead_id, _, _ = await _seed_booked_lead_with_pro(flow_db, chat_id)

    result = await handle_customer_completion_text(
        chat_id, "המים לא הגיעו לדוד", mock_whatsapp
    )

    assert result is None
    lead = await flow_db.leads.find_one({"_id": lead_id})
    assert "no_show_reported_at" not in lead
    mock_reassign.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_show_digit_defers_to_pending_rating(
    flow_db, mock_whatsapp, mock_reassign
):
    """PRO-122's guard at the top of the caller runs before PRO-45's branch:
    a bare '3' answering a live 1-5 rating question must not be read as a
    no-show report on some other booked lead."""
    chat_id = "972502222414@c.us"
    lead_id, _, _ = await _seed_booked_lead_with_pro(
        flow_db,
        chat_id,
        completion_check_sent_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    await flow_db.leads.insert_one(
        {
            "_id": ObjectId(),
            "chat_id": chat_id,
            "status": LeadStatus.COMPLETED,
            "waiting_for_rating": True,
            "completed_at": datetime.now(timezone.utc),
        }
    )

    result = await handle_customer_completion_text(chat_id, "3", mock_whatsapp)

    assert result is None
    lead = await flow_db.leads.find_one({"_id": lead_id})
    assert "no_show_reported_at" not in lead  # BOOKED lead untouched
    mock_reassign.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_show_reassign_raises_escalates_to_pending_admin_review(
    flow_db, mock_whatsapp, mock_reassign
):
    """A raised `reassign_lead` must not leave the lead BOOKED under a pro who
    was just told the job was taken away, with its slot already released and
    `no_show_reported_at` blocking any second report -- it escalates to
    PENDING_ADMIN_REVIEW instead."""
    chat_id = "972502222416@c.us"
    mock_reassign.side_effect = RuntimeError("boom")
    lead_id, _, _ = await _seed_booked_lead_with_pro(
        flow_db,
        chat_id,
        completion_check_sent_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )

    result = await handle_customer_completion_text(chat_id, "3", mock_whatsapp)

    # The report itself still succeeded — the receipt copy is unconditional.
    assert result == Messages.Customer.NO_SHOW_ACK
    lead = await flow_db.leads.find_one({"_id": lead_id})
    assert lead["status"] == LeadStatus.PENDING_ADMIN_REVIEW
    assert lead["escalation_reason"] == "no_show_reassign_failed"


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


# --- PRO-122: completion-menu digits vs. a pending rating -----------------
# The completion menu ("1 — כן" / "2 — עדיין לא") and the 1-5 rating scale
# share digits, and the completion handler runs before the rating one. A bare
# digit must defer to the rating question instead of completing an unrelated
# BOOKED lead or being swallowed as "not yet".


@pytest.mark.asyncio
async def test_completion_text_one_defers_to_pending_rating(flow_db, mock_whatsapp):
    chat_id = "972502222301@c.us"
    booked_lead_id = await _seed_booked_lead(flow_db, chat_id)
    await flow_db.leads.insert_one(
        {
            "_id": ObjectId(),
            "chat_id": chat_id,
            "status": LeadStatus.COMPLETED,
            "waiting_for_rating": True,
            "completed_at": datetime.now(timezone.utc),
        }
    )

    result = await handle_customer_completion_text(chat_id, "1", mock_whatsapp)

    assert result is None
    booked_lead = await flow_db.leads.find_one({"_id": booked_lead_id})
    assert booked_lead["status"] == LeadStatus.BOOKED  # untouched


@pytest.mark.asyncio
async def test_completion_text_two_defers_to_pending_rating(flow_db, mock_whatsapp):
    chat_id = "972502222302@c.us"
    stale = datetime.now(timezone.utc) - timedelta(hours=12)
    booked_lead_id = await _seed_booked_lead(
        flow_db,
        chat_id,
        completion_check_sent_count=1,
        completion_check_sent_at=stale,
    )
    await flow_db.leads.insert_one(
        {
            "_id": ObjectId(),
            "chat_id": chat_id,
            "status": LeadStatus.COMPLETED,
            "waiting_for_rating": True,
            "completed_at": datetime.now(timezone.utc),
        }
    )
    seeded = await flow_db.leads.find_one({"_id": booked_lead_id})

    result = await handle_customer_completion_text(chat_id, "2", mock_whatsapp)

    assert result is None  # not the COMPLETION_NOT_YET_ACK path
    booked_lead = await flow_db.leads.find_one({"_id": booked_lead_id})
    assert booked_lead["status"] == LeadStatus.BOOKED
    # Untouched by the (short-circuited) decline path, which would otherwise
    # bump this to "now" and restart the cooldown.
    assert booked_lead["completion_check_sent_at"] == seeded["completion_check_sent_at"]


@pytest.mark.asyncio
async def test_completion_text_skip_token_defers_to_pending_rating(
    flow_db, mock_whatsapp
):
    """'לא' means "don't want to rate" here, not "not yet finished" — the
    decline path (`_NOT_YET_TOKENS ∩ SKIP_TOKENS = {"לא", "no"}`) must not eat
    it before the rating skip ever gets a chance to run."""
    chat_id = "972502222308@c.us"
    stale = datetime.now(timezone.utc) - timedelta(hours=12)
    booked_lead_id = await _seed_booked_lead(
        flow_db,
        chat_id,
        completion_check_sent_count=1,
        completion_check_sent_at=stale,
    )
    await flow_db.leads.insert_one(
        {
            "_id": ObjectId(),
            "chat_id": chat_id,
            "status": LeadStatus.COMPLETED,
            "waiting_for_rating": True,
            "completed_at": datetime.now(timezone.utc),
        }
    )

    result = await handle_customer_completion_text(chat_id, "לא", mock_whatsapp)

    assert result is None
    booked_lead = await flow_db.leads.find_one({"_id": booked_lead_id})
    assert booked_lead["status"] == LeadStatus.BOOKED


@pytest.mark.asyncio
async def test_completion_text_one_completes_booked_when_rating_prompt_expired(
    flow_db, mock_whatsapp
):
    """A rating prompt older than RATING_PROMPT_MAX_AGE_HOURS is dead: it must
    not defer the digit away from a genuinely BOOKED lead."""
    chat_id = "972502222309@c.us"
    pro_id = ObjectId()
    await flow_db.users.insert_one(
        {
            "_id": pro_id,
            "business_name": "אבי אינסטלציה",
            "phone_number": "972500000009",
        }
    )
    booked_lead_id = ObjectId()
    await flow_db.leads.insert_one(
        {
            "_id": booked_lead_id,
            "chat_id": chat_id,
            "status": LeadStatus.BOOKED,
            "pro_id": pro_id,
            "created_at": datetime.now(timezone.utc),
        }
    )
    expired = datetime.now(timezone.utc) - timedelta(
        hours=WorkerConstants.RATING_PROMPT_MAX_AGE_HOURS + 1
    )
    await flow_db.leads.insert_one(
        {
            "_id": ObjectId(),
            "chat_id": chat_id,
            "status": LeadStatus.COMPLETED,
            "waiting_for_rating": True,
            "completed_at": expired,
        }
    )

    rating_result = await handle_customer_rating_text(chat_id, "1")
    assert rating_result is None  # the dead prompt does not swallow the digit

    completion_result = await handle_customer_completion_text(
        chat_id, "1", mock_whatsapp
    )
    assert completion_result is not None
    booked_lead = await flow_db.leads.find_one({"_id": booked_lead_id})
    assert booked_lead["status"] == LeadStatus.COMPLETED


# --- parse_rating (pure) ---------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        # decorated leading/bare digit
        ("5", 5),
        ("5!", 5),
        ("*5*", 5),
        ("5/5", 5),
        (" 5 ", 5),
        # digit backed by a rating-context word
        ("5 כוכבים", 5),
        ("4 מתוך 5", 4),
        ("דירוג 4", 4),
        ("אני נותן 5", 5),
        ("מגיע לו 5 כוכבים", 5),
        # Hebrew number words
        ("חמש", 5),
        ("חמישה כוכבים", 5),
        ("שלוש", 3),
        # stars-only reply counts the stars
        ("⭐⭐⭐⭐", 4),
        # numbers that are clearly something else (address, floor, duration) -> None
        ("רחוב הרצל 5", None),
        ("קומה 2", None),
        ("דירה 3", None),
        ("נזילה בקומה 4", None),
        ("בעוד 3 ימים", None),
        ("5 דקות", None),
        ("3 ימים עברו והוא לא חזר אליי", None),
        ("2 ברזים דולפים אצלי במטבח", None),
        ("1 בבוקר מחר", None),
        ("5 שקל", None),
        # Hebrew number word buried in a sentence, no context word -> None
        ("בעוד שלוש שעות", None),
        # never truncate 4.5 to 4, or accept out-of-range digits
        ("4.5", None),
        ("10", None),
        # sentiment words are deliberately not mapped
        ("מצוין", None),
        ("", None),
        ("בין 8 ל9", None),  # two numbers present, neither a lone 1-5
        ("צריך עוד ארבע שעות של עבודה", None),  # number word in a long sentence
        ("⭐⭐⭐⭐⭐⭐⭐", None),  # out-of-range star count
    ],
)
def test_parse_rating(text, expected):
    assert parse_rating(text) == expected


# --- is_skip_token (pure) ---------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("לא", True),
        ("לא תודה", True),
        ("דלג", True),
        ("skip", True),
        ("No", True),
        ("nope!", True),
        ("אין צורך", True),
        # a genuine negative review must survive as a review, not a skip
        ("לא היה טוב", False),
    ],
)
def test_is_skip_token(text, expected):
    assert is_skip_token(text) == expected


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
            "completed_at": datetime.now(timezone.utc),
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
async def test_rating_invalid_text_no_lead_pending_returns_none(flow_db):
    """An unparseable reply with no rating question outstanding falls through
    untouched — it is very likely just an unrelated message."""
    result = await handle_customer_rating_text("972509090909@c.us", "great")
    assert result is None


@pytest.mark.asyncio
async def test_rating_invalid_text_reprompts_when_lead_pending(flow_db):
    """The same unparseable reply, but this chat was actually asked, gets a
    re-prompt instead of silently falling through — and the lead stays
    waiting_for_rating so a later, readable reply can still land."""
    chat_id = "972509040404@c.us"
    lead_id = ObjectId()
    await flow_db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": chat_id,
            "waiting_for_rating": True,
            "pro_id": ObjectId(),
            "completed_at": datetime.now(timezone.utc),
        }
    )

    result = await handle_customer_rating_text(chat_id, "great")

    assert result == Messages.Customer.RATING_REPROMPT
    lead = await flow_db.leads.find_one({"_id": lead_id})
    assert lead["waiting_for_rating"] is True
    assert lead["rating_reprompt_count"] == 1


@pytest.mark.asyncio
async def test_rating_no_waiting_lead(flow_db):
    result = await handle_customer_rating_text("972509010101@c.us", "5")
    assert result is None


@pytest.mark.asyncio
async def test_rating_skip_token_declines_and_clears_context(flow_db, monkeypatch):
    chat_id = "972509050505@c.us"
    lead_id = ObjectId()
    await flow_db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": chat_id,
            "waiting_for_rating": True,
            "pro_id": ObjectId(),
            "completed_at": datetime.now(timezone.utc),
        }
    )
    mock_ctx = MagicMock()
    mock_ctx.clear_context = AsyncMock()
    monkeypatch.setattr(app.services.customer_flow, "ContextManager", mock_ctx)

    result = await handle_customer_rating_text(chat_id, "לא תודה")

    assert result == Messages.Customer.RATING_SKIPPED
    lead = await flow_db.leads.find_one({"_id": lead_id})
    assert lead["waiting_for_rating"] is False
    assert lead["rating_skipped"] is True
    mock_ctx.clear_context.assert_called_once_with(chat_id)


@pytest.mark.asyncio
async def test_rating_unparseable_at_reprompt_cap_releases_flag(flow_db):
    """The re-prompt flag never clears on its own; once the cap is hit the
    handler must release it and hand the message to the dispatcher instead of
    trapping the customer in an endless re-prompt loop."""
    chat_id = "972509060606@c.us"
    lead_id = ObjectId()
    await flow_db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": chat_id,
            "waiting_for_rating": True,
            "pro_id": ObjectId(),
            "rating_reprompt_count": WorkerConstants.MAX_RATING_REPROMPTS,
            "completed_at": datetime.now(timezone.utc),
        }
    )

    result = await handle_customer_rating_text(chat_id, "still not a number")

    assert result is None
    lead = await flow_db.leads.find_one({"_id": lead_id})
    assert lead["waiting_for_rating"] is False


@pytest.mark.asyncio
async def test_rating_with_two_pending_leads_lands_on_newest(flow_db):
    """Two live unrated jobs for the same chat: the rating must land on the one
    the customer was actually just asked about (newest completed_at), not
    whichever one Mongo happens to return first."""
    chat_id = "972509061616@c.us"
    older_id = ObjectId()
    newer_id = ObjectId()
    newer_pro_id = ObjectId()
    await flow_db.users.insert_one({"_id": newer_pro_id, "business_name": "Newer Pro"})
    await flow_db.leads.insert_one(
        {
            "_id": older_id,
            "chat_id": chat_id,
            "waiting_for_rating": True,
            "pro_id": ObjectId(),
            "completed_at": datetime.now(timezone.utc) - timedelta(hours=2),
        }
    )
    await flow_db.leads.insert_one(
        {
            "_id": newer_id,
            "chat_id": chat_id,
            "waiting_for_rating": True,
            "pro_id": newer_pro_id,
            "completed_at": datetime.now(timezone.utc) - timedelta(minutes=5),
        }
    )

    await handle_customer_rating_text(chat_id, "4")

    older = await flow_db.leads.find_one({"_id": older_id})
    newer = await flow_db.leads.find_one({"_id": newer_id})
    assert older["waiting_for_rating"] is True  # untouched
    assert newer["waiting_for_rating"] is False
    assert newer["rating_given"] == 4


@pytest.mark.asyncio
async def test_rating_unparsed_emergency_keyword_releases_without_reprompt(flow_db):
    """An emergency outranks the closing pleasantries: `is_emergency_detected`
    only runs after this handler, so without this escape hatch "הצפה דחוף"
    would be stalled behind a re-prompt for up to MAX_RATING_REPROMPTS turns."""
    chat_id = "972509062020@c.us"
    lead_id = ObjectId()
    await flow_db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": chat_id,
            "waiting_for_rating": True,
            "pro_id": ObjectId(),
            "completed_at": datetime.now(timezone.utc),
        }
    )

    result = await handle_customer_rating_text(chat_id, "הצפה דחוף")

    assert result is None  # falls through to the dispatcher/emergency path
    lead = await flow_db.leads.find_one({"_id": lead_id})
    assert lead["waiting_for_rating"] is False
    assert lead.get("rating_reprompt_count", 0) == 0


@pytest.mark.asyncio
async def test_rating_unparsed_with_media_falls_through_without_reprompt(flow_db):
    """A photo with an unreadable caption must still reach the media handler
    downstream (step 3) rather than burning a re-prompt on the caption text."""
    chat_id = "972509062121@c.us"
    lead_id = ObjectId()
    await flow_db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": chat_id,
            "waiting_for_rating": True,
            "pro_id": ObjectId(),
            "completed_at": datetime.now(timezone.utc),
        }
    )

    result = await handle_customer_rating_text(chat_id, "תראו את זה", has_media=True)

    assert result is None
    lead = await flow_db.leads.find_one({"_id": lead_id})
    assert lead["waiting_for_rating"] is True  # left pending, untouched
    assert lead.get("rating_reprompt_count", 0) == 0


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


@pytest.mark.asyncio
async def test_review_comment_skip_token_declines_saving_score_without_comment(
    flow_db, monkeypatch
):
    """Declining the free-text review comment must not throw away the *score*
    the customer already gave — the admin analytics per-pro average is
    computed over `reviews.rating`, so dropping the row would silently delete
    it. The literal decline text must never be persisted as the comment."""
    chat_id = "972509070707@c.us"
    pro_id = ObjectId()
    lead_id = ObjectId()
    await flow_db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": chat_id,
            "waiting_for_review_comment": True,
            "pro_id": pro_id,
            "rating_given": 5,
        }
    )
    mock_ctx = MagicMock()
    mock_ctx.clear_context = AsyncMock()
    monkeypatch.setattr(app.services.customer_flow, "ContextManager", mock_ctx)

    result = await handle_customer_review_comment(chat_id, "לא תודה")

    assert result == Messages.Customer.REVIEW_DECLINED
    review = await flow_db.reviews.find_one({"pro_id": pro_id})
    assert review is not None
    assert review["rating"] == 5
    assert review["comment"] == ""
    lead = await flow_db.leads.find_one({"_id": lead_id})
    assert lead["waiting_for_review_comment"] is False
    mock_ctx.clear_context.assert_called_once_with(chat_id)


@pytest.mark.asyncio
async def test_review_comment_negative_sentence_is_saved_not_skipped(flow_db):
    """'לא היה טוב' is a genuine negative review, not a decline — is_skip_token
    only matches the exact opt-out phrases, never a substring of one."""
    chat_id = "972509080808@c.us"
    pro_id = ObjectId()
    lead_id = ObjectId()
    await flow_db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": chat_id,
            "waiting_for_review_comment": True,
            "pro_id": pro_id,
            "rating_given": 2,
        }
    )

    result = await handle_customer_review_comment(chat_id, "לא היה טוב")

    assert result == Messages.Customer.REVIEW_SAVED
    review = await flow_db.reviews.find_one({"pro_id": pro_id, "comment": "לא היה טוב"})
    assert review is not None


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
    assert static_prefix(Messages.Customer.STATUS_NEW) in result


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
    assert static_prefix(Messages.Customer.STATUS_CONTACTED) in result


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
    assert result == Messages.Customer.STATUS_COMPLETED
