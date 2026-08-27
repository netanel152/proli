"""The shared pro lead-offer builder (notification_service).

Three services used to hand-roll this message, and no two agreed: the monitor
path filled missing fields with English ("Unknown", "Pending") inside a Hebrew
message, the admin path dropped the customer's media and the navigation link,
and the media policy differed between paths (files re-sent vs text links).
``build_new_lead_message`` / ``notify_pro_new_lead`` are now the one owner;
these tests pin the unified behaviour, including regression guards against the
disagreements that motivated the change.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.core.messages import Messages
from app.services.notification_service import (
    build_new_lead_message,
    format_lead_extra_info,
    format_media_links,
    notify_pro_new_lead,
)
import app.services.notification_service as notification_service


def _full_lead(**overrides):
    lead = {
        "_id": "lead-1",
        "customer_name": "דנה לוי",
        "full_address": "הרצל 12, תל אביב",
        "floor": "3",
        "apartment": "7",
        "issue_type": "נזילה",
        "appointment_time": "מחר 10:00",
        "is_emergency": False,
        "media_urls": [],
    }
    lead.update(overrides)
    return lead


# ---------------------------------------------------------------------------
# build_new_lead_message — pure, no I/O
# ---------------------------------------------------------------------------


def test_offer_contains_all_lead_fields():
    msg = build_new_lead_message(_full_lead())
    assert "דנה לוי" in msg
    assert "הרצל 12, תל אביב" in msg
    assert Messages.Pro.EXTRA_INFO_LINE.format(floor="3", apartment="7") in msg
    assert "נזילה" in msg
    assert "מחר 10:00" in msg
    assert msg.startswith(Messages.Pro.NEW_LEAD_HEADER)
    assert Messages.Pro.NEW_LEAD_FOOTER.strip() in msg


def test_emergency_lead_gets_emergency_header():
    msg = build_new_lead_message(_full_lead(is_emergency=True))
    assert msg.startswith(Messages.Pro.EMERGENCY_LEAD_HEADER)
    assert Messages.Pro.NEW_LEAD_HEADER not in msg


def test_missing_fields_fall_back_in_hebrew_only():
    """Regression: the monitor path used to render "Unknown"/"Pending" —
    English fallbacks inside a Hebrew message — while the admin path used
    Hebrew. One language now, from Messages.Fallbacks."""
    msg = build_new_lead_message(
        _full_lead(
            customer_name=None,
            full_address=None,
            floor=None,
            apartment=None,
            issue_type=None,
            appointment_time=None,
        )
    )
    assert Messages.Fallbacks.CUSTOMER_NAME in msg
    assert Messages.Fallbacks.UNKNOWN in msg
    assert Messages.Fallbacks.TIME_ASAP in msg
    assert Messages.Pro.EXTRA_INFO_LINE.format(floor="-", apartment="-") in msg
    assert "Unknown" not in msg
    assert "Pending" not in msg


def test_media_appended_as_numbered_text_links():
    msg = build_new_lead_message(
        _full_lead(media_urls=["https://cdn/x.jpg", "https://cdn/y.mp4"])
    )
    assert Messages.Pro.MEDIA_ATTACHED_HEADER in msg
    assert "1. https://cdn/x.jpg" in msg
    assert "2. https://cdn/y.mp4" in msg


def test_no_media_block_when_lead_has_no_media():
    assert Messages.Pro.MEDIA_ATTACHED_HEADER not in build_new_lead_message(
        _full_lead()
    )
    # media_urls can also be absent entirely (legacy leads)
    lead = _full_lead()
    del lead["media_urls"]
    assert Messages.Pro.MEDIA_ATTACHED_HEADER not in build_new_lead_message(lead)


def test_format_media_links_matches_approval_request_shape():
    """workflow_service appends this block after APPROVAL_REQUEST; the leading
    separator must come from the block itself so an empty result adds nothing."""
    assert format_media_links({"media_urls": []}) == ""
    block = format_media_links({"media_urls": ["https://cdn/a.jpg"]})
    assert block.startswith("\n\n" + Messages.Pro.MEDIA_ATTACHED_HEADER)


def test_extra_info_placeholders_keep_line_shape():
    assert format_lead_extra_info({}) == Messages.Pro.EXTRA_INFO_LINE.format(
        floor="-", apartment="-"
    )
    assert format_lead_extra_info(
        {"floor": 2, "apartment": 5}
    ) == Messages.Pro.EXTRA_INFO_LINE.format(floor=2, apartment=5)


# ---------------------------------------------------------------------------
# notify_pro_new_lead — injected whatsapp, no DB
# ---------------------------------------------------------------------------


def _mock_whatsapp():
    mock = MagicMock()
    mock.send_message = AsyncMock()
    mock.send_location_link = AsyncMock()
    return mock


@pytest.mark.asyncio
async def test_notify_sends_offer_and_navigation_link():
    whatsapp = _mock_whatsapp()
    ok = await notify_pro_new_lead(
        _full_lead(), {"phone_number": "0501234567"}, whatsapp
    )
    assert ok is True
    whatsapp.send_message.assert_awaited_once()
    sent_text = whatsapp.send_message.await_args.args[1]
    assert sent_text == build_new_lead_message(_full_lead())
    whatsapp.send_location_link.assert_awaited_once()
    assert whatsapp.send_location_link.await_args.args[1] == "הרצל 12, תל אביב"


@pytest.mark.asyncio
async def test_notify_skips_navigation_link_without_address():
    whatsapp = _mock_whatsapp()
    ok = await notify_pro_new_lead(
        _full_lead(full_address=None), {"phone_number": "0501234567"}, whatsapp
    )
    assert ok is True
    whatsapp.send_message.assert_awaited_once()
    whatsapp.send_location_link.assert_not_awaited()


@pytest.mark.asyncio
async def test_notify_without_pro_phone_returns_false_and_sends_nothing():
    whatsapp = _mock_whatsapp()
    assert await notify_pro_new_lead(_full_lead(), {}, whatsapp) is False
    assert await notify_pro_new_lead(_full_lead(), None, whatsapp) is False
    whatsapp.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_notify_fails_open_on_send_error():
    """Fail-open: the caller has already reassigned the lead, so a failed send
    must be a logged False, not an exception that aborts the flow mid-update."""
    whatsapp = _mock_whatsapp()
    whatsapp.send_message.side_effect = RuntimeError("provider down")
    ok = await notify_pro_new_lead(
        _full_lead(), {"phone_number": "0501234567"}, whatsapp
    )
    assert ok is False


# ---------------------------------------------------------------------------
# PRO-159 — a blocked send (facade returns None) must not be reported as a
# delivered offer.
#
# Before the facade translated a closed 24h service window (and the breaker /
# kill switch cases it already covered) into a plain `None` return instead of
# raising, `notify_pro_new_lead` relied entirely on its `except` clause to turn
# a failed send into `False`. After that change a blocked send no longer
# raises, so the `None` has to be checked explicitly — otherwise the pro gets
# no offer at all and the caller is told it went out.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notify_returns_false_when_offer_send_is_blocked():
    whatsapp = _mock_whatsapp()
    whatsapp.send_message = AsyncMock(return_value=None)
    ok = await notify_pro_new_lead(
        _full_lead(), {"phone_number": "0501234567"}, whatsapp
    )
    assert ok is False
    whatsapp.send_location_link.assert_not_awaited()


@pytest.mark.asyncio
async def test_notify_still_true_when_only_the_nav_link_is_blocked():
    """Only the offer send gates the return — a missing navigation link is a
    degraded offer, not a lost one."""
    whatsapp = _mock_whatsapp()
    whatsapp.send_location_link = AsyncMock(return_value=None)
    ok = await notify_pro_new_lead(
        _full_lead(), {"phone_number": "0501234567"}, whatsapp
    )
    assert ok is True
    whatsapp.send_location_link.assert_awaited_once()


# ---------------------------------------------------------------------------
# _send_best_effort — same blocked-send-is-not-success contract, pinned
# directly since it is the other caller-facing entry point (SOS-style
# best-effort notices) that shares the same failure mode.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_best_effort_returns_false_on_blocked_send(monkeypatch):
    fake_wa = MagicMock()
    fake_wa.send_message = AsyncMock(return_value=None)
    monkeypatch.setattr(notification_service, "whatsapp", fake_wa)

    ok = await notification_service._send_best_effort("972500000001@c.us", "hi")

    assert ok is False


@pytest.mark.asyncio
async def test_send_best_effort_returns_true_on_delivered_send(monkeypatch):
    fake_wa = MagicMock()
    fake_wa.send_message = AsyncMock(return_value={"id": "1"})
    monkeypatch.setattr(notification_service, "whatsapp", fake_wa)

    ok = await notification_service._send_best_effort("972500000001@c.us", "hi")

    assert ok is True
