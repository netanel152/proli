"""The send path from a call site to Meta: free-form text while the window is open,
the approved template when it is closed, exactly today's page and drop otherwise.

HTTP is an ``httpx.MockTransport``, as in tests/test_cloud_api_provider.py, and the
facade above the provider is the real one, breaker included.
"""

import dataclasses
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import mongomock
import pytest
import pytest_asyncio
from pydantic import SecretStr

import admin_panel.core.utils as panel_utils
import admin_panel.views.professionals as professionals
import app.providers.whatsapp.cloud_api as cloud_api
import app.services.workflow_service
from app.core.config import settings
from app.core.constants import LeadStatus, UserStates
from app.core.messages import Messages
from app.providers.whatsapp import delivery, quality, sync, template_registry
from app.providers.whatsapp.cloud_api import CloudAPIProvider
from app.providers.whatsapp.dry_run import DryRunProvider
from app.providers.whatsapp.facade import WhatsAppFacade
from app.providers.whatsapp.template_registry import TemplateStatus
from app.providers.whatsapp.window import _window_key
from app.services import monitor_service, notification_service
from app.services.workflow_service import process_incoming_message

CHAT_ID = "972501234567@c.us"
OFFER_PARAMS = {
    "urgency": "רגילה",
    "price_estimate": "לא ניתנה הערכה",
    "appointment_time": "מחר",
    "issue_type": "נזילה\nחזקה",
    "full_address": "הרצל 1, תל אביב",
    "customer_name": "",
}


def _approve(monkeypatch, key="lead_offer"):
    monkeypatch.setattr(
        template_registry,
        "TEMPLATES",
        {
            **template_registry.TEMPLATES,
            key: dataclasses.replace(
                template_registry.TEMPLATES[key], status=TemplateStatus.APPROVED
            ),
        },
    )


@pytest.fixture
def graph(monkeypatch, mock_db):
    monkeypatch.setattr(settings, "META_ACCESS_TOKEN", SecretStr("test-token"))
    monkeypatch.setattr(settings, "META_PHONE_NUMBER_ID", "1234567890")
    monkeypatch.setattr(settings, "META_GRAPH_API_VERSION", "v23.0")
    monkeypatch.setattr(delivery, "wa_delivery_collection", mock_db.wa_delivery)
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"messages": [{"id": "wamid.1"}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(cloud_api, "get_http_client", AsyncMock(return_value=client))
    return sent


@pytest.fixture
def pages(monkeypatch):
    page = MagicMock()
    monkeypatch.setattr(cloud_api, "page_critical", page)
    return page


@pytest_asyncio.fixture
async def facade(fake_redis):
    """A real facade over a real provider, with the breaker open for sending."""
    await fake_redis.set("wa:instance:state", "authorized")
    return WhatsAppFacade(CloudAPIProvider())


async def _open_window(fake_redis):
    await fake_redis.set(_window_key(CHAT_ID), "2026-01-01T00:00:00+00:00", ex=100)


async def _send(facade):
    return await facade.send_message(
        CHAT_ID, "טקסט חופשי", template_key="lead_offer", params=OFFER_PARAMS
    )


# ---------------------------------------------------------------------------
# With every template DRAFT, nothing changes
# ---------------------------------------------------------------------------


def test_every_registry_entry_is_still_draft():
    assert {s.status for s in template_registry.TEMPLATES.values()} == {
        TemplateStatus.DRAFT
    }


@pytest.mark.asyncio
async def test_draft_open_window_sends_the_identical_text_payload(
    facade, graph, fake_redis
):
    await _open_window(fake_redis)

    await facade.send_message(CHAT_ID, "טקסט חופשי")
    plain = graph.pop()
    result = await _send(facade)

    assert graph == [plain]
    assert plain["type"] == "text"
    assert "sent_as_template" not in result


@pytest.mark.asyncio
async def test_draft_closed_window_pages_and_returns_none_like_a_plain_send(
    facade, graph, pages, fake_redis
):
    assert await _send(facade) is None
    assert graph == []
    assert pages.call_count == 1
    assert "blocked" in pages.call_args.args[0]


@pytest.mark.asyncio
async def test_draft_never_reads_the_window_an_extra_time(
    facade, graph, fake_redis, monkeypatch
):
    await _open_window(fake_redis)
    reads = AsyncMock(return_value=True)
    monkeypatch.setattr(cloud_api, "is_service_window_open", reads)

    await _send(facade)

    assert reads.await_count == 1  # send_text's own read, and no other


@pytest.mark.asyncio
async def test_a_send_that_names_no_template_ignores_an_approved_one(
    facade, graph, pages, fake_redis, monkeypatch
):
    _approve(monkeypatch)
    assert await facade.send_message(CHAT_ID, "טקסט חופשי") is None
    assert graph == []


# ---------------------------------------------------------------------------
# Approved
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approved_open_window_still_sends_the_text(
    facade, graph, fake_redis, monkeypatch
):
    _approve(monkeypatch)
    await _open_window(fake_redis)

    result = await _send(facade)

    assert [m["type"] for m in graph] == ["text"]
    assert "sent_as_template" not in result


@pytest.mark.asyncio
async def test_approved_closed_window_sends_params_in_the_declared_order(
    facade, graph, pages, fake_redis, monkeypatch
):
    """OFFER_PARAMS is written backwards on purpose. Dict order must not matter."""
    _approve(monkeypatch)

    result = await _send(facade)

    assert result["sent_as_template"] == "lead_offer"
    assert pages.call_count == 0
    template = graph[0]["template"]
    assert template["name"] == "proli_lead_offer"
    assert template["language"] == {"code": "he"}
    assert [p["text"] for p in template["components"][0]["parameters"]] == [
        Messages.Fallbacks.UNKNOWN,  # the empty customer_name
        "הרצל 1, תל אביב",
        "נזילה חזקה",  # the newline flattened
        "מחר",
        "לא ניתנה הערכה",
        "רגילה",
    ]


@pytest.mark.asyncio
async def test_the_messaging_limit_counter_counts_a_template_and_not_the_text(
    facade, graph, fake_redis, monkeypatch
):
    """The quality watchdog counts in `delivery.record_outbound`, so this path
    has to reach it with kind "template" while the window is still closed."""
    _approve(monkeypatch)

    await _send(facade)
    assert await quality.business_initiated_count() == 1

    await _open_window(fake_redis)
    await _send(facade)
    assert await quality.business_initiated_count() == 1


@pytest.mark.asyncio
async def test_a_4000_char_sos_is_cut_and_still_sent(
    facade, graph, fake_redis, monkeypatch
):
    _approve(monkeypatch, "sos_alert")

    await facade.send_message(
        CHAT_ID,
        "טקסט",
        template_key="sos_alert",
        params={"phone": "0501234567", "last_message": "א" * 4000},
    )

    phone, last_message = graph[0]["template"]["components"][0]["parameters"]
    assert phone["text"] == "0501234567"
    assert last_message["text"] == "א" * 700


@pytest.mark.asyncio
async def test_a_template_meta_later_fails_pages_once_per_recipient(
    pages, mock_db, monkeypatch, fake_redis
):
    monkeypatch.setattr(delivery, "wa_delivery_collection", mock_db.wa_delivery)
    monkeypatch.setattr(delivery, "page_critical", pages)
    logged = MagicMock()
    monkeypatch.setattr(delivery.logger, "error", logged)
    for wamid in ("wamid.F1", "wamid.F2"):
        await delivery.record_outbound(wamid, CHAT_ID, "template")
        await delivery.apply_status_event(
            {
                "wa_message_id": wamid,
                "chat_id": CHAT_ID,
                "status": "failed",
                "error_code": 131026,
                "error_title": "undeliverable",
            }
        )

    assert pages.call_count == 1
    text = pages.call_args.args[0]
    assert "wamid.F1" in text
    assert CHAT_ID not in text
    assert "undeliverable" not in text
    assert logged.call_count == 1  # the second is reported, not paged


@pytest.mark.asyncio
async def test_approved_template_with_no_variables_sends_no_components(
    facade, graph, fake_redis, monkeypatch
):
    _approve(monkeypatch, "finish_reminder")

    await facade.send_message(CHAT_ID, "טקסט", template_key="finish_reminder")

    assert "components" not in graph[0]["template"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [{}, {"customer_name": "דנה"}, {**OFFER_PARAMS, "extra": "x"}],
    ids=["none", "missing", "extra"],
)
async def test_wrong_variables_page_and_send_nothing(
    facade, graph, pages, fake_redis, monkeypatch, params
):
    _approve(monkeypatch)

    result = await facade.send_message(
        CHAT_ID, "טקסט", template_key="lead_offer", params=params
    )

    assert result is None
    assert graph == []
    assert pages.call_count == 1


@pytest.mark.asyncio
async def test_a_vendor_rejection_still_propagates(
    facade, fake_redis, monkeypatch, mock_db
):
    """#110's escalation depends on this: a failed offer is an exception or None."""
    monkeypatch.setattr(settings, "META_ACCESS_TOKEN", SecretStr("test-token"))
    monkeypatch.setattr(settings, "META_PHONE_NUMBER_ID", "1234567890")
    _approve(monkeypatch)
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(400, json={}))
    )
    monkeypatch.setattr(cloud_api, "get_http_client", AsyncMock(return_value=client))

    with pytest.raises(httpx.HTTPStatusError):
        await _send(facade)


# ---------------------------------------------------------------------------
# The facade's guarantees
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["wa:instance:paused", "wa:instance:paused:manual"])
async def test_breaker_and_kill_switch_stop_a_template_too(
    facade, graph, fake_redis, monkeypatch, key
):
    _approve(monkeypatch)
    await fake_redis.set(key, "1")

    assert await _send(facade) is None
    assert graph == []


@pytest.mark.asyncio
async def test_dry_run_records_the_text_and_transmits_nothing(graph):
    provider = DryRunProvider()

    result = await WhatsAppFacade(provider).send_message(
        CHAT_ID, "טקסט חופשי", template_key="lead_offer", params=OFFER_PARAMS
    )

    assert result is not None
    assert [(e["kind"], e["text"]) for e in provider.sent] == [("text", "טקסט חופשי")]
    assert graph == []


# ---------------------------------------------------------------------------
# What a template offer cannot carry arrives with the approval
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sent",
    [None, {"idMessage": "x"}, MagicMock()],
    ids=["blocked", "text", "a mock result"],
)
async def test_nothing_is_kept_unless_the_template_went_out(mock_db, sent):
    lead_id = (await mock_db.leads.insert_one({"status": "new"})).inserted_id

    await notification_service.keep_withheld_details({"_id": lead_id}, sent, "קישור")

    assert "offer_withheld_details" not in await mock_db.leads.find_one(
        {"_id": lead_id}
    )


@pytest.mark.asyncio
async def test_a_template_offer_keeps_its_media_links_for_the_approval(
    mock_db, monkeypatch
):
    lead_id = (
        await mock_db.leads.insert_one(
            {
                "status": LeadStatus.NEW,
                "media_urls": ["https://example.com/a.jpg"],
                "full_address": "הרצל 1",
                "issue_type": "נזילה",
            }
        )
    ).inserted_id
    lead = await mock_db.leads.find_one({"_id": lead_id})
    whatsapp = MagicMock()
    whatsapp.send_message = AsyncMock(
        return_value={"idMessage": "x", "sent_as_template": "lead_offer_reassigned"}
    )

    notified = await notification_service.notify_pro_new_lead(
        lead, {"phone_number": "972500000000"}, whatsapp
    )

    assert notified is True
    stored = await mock_db.leads.find_one({"_id": lead_id})
    assert "https://example.com/a.jpg" in stored["offer_withheld_details"]


@pytest_asyncio.fixture
async def wf(monkeypatch, mock_db):
    """The dispatcher with a fresh FSM: nothing set by a prior turn."""
    # mock_db is module-scoped, and these tests look people up by phone number.
    await mock_db.users.delete_many({})
    await mock_db.leads.delete_many({})
    whatsapp = MagicMock()
    whatsapp.send_message = AsyncMock()
    whatsapp.send_chat_state_typing = AsyncMock()
    monkeypatch.setattr(app.services.workflow_service, "whatsapp", whatsapp)
    monkeypatch.setattr(
        app.services.workflow_service,
        "has_consent",
        AsyncMock(return_value=True),
    )
    return whatsapp


async def _pro(mock_db, phone="972500000001", **extra):
    return (
        await mock_db.users.insert_one(
            {
                "phone_number": phone,
                "role": "professional",
                "is_active": True,
                "business_name": "שירותי דוגמה",
                **extra,
            }
        )
    ).inserted_id


async def _lead(mock_db, **fields):
    defaults = {
        "chat_id": CHAT_ID,
        "status": LeadStatus.NEW,
        "created_at": datetime.now(timezone.utc),
        "full_address": "הרצל 1, תל אביב",
        "issue_type": "נזילה",
    }
    return (await mock_db.leads.insert_one({**defaults, **fields})).inserted_id


def _replies(whatsapp, chat_id):
    return [
        c.args[1] for c in whatsapp.send_message.call_args_list if c.args[0] == chat_id
    ]


PRO_CHAT = "972500000001@c.us"


@pytest.mark.asyncio
async def test_approve_sends_the_contact_card_and_the_withheld_details(wf, mock_db):
    pro_id = await _pro(mock_db)
    await _lead(
        mock_db,
        pro_id=pro_id,
        customer_phone="972501234567",
        offer_withheld_details="\n\n*מדיה מצורפת:*\n1. https://example.com/a.jpg",
    )

    await process_incoming_message(PRO_CHAT, "אשר")

    reply = _replies(wf, PRO_CHAT)[0]
    assert "*פרטי הלקוח:*" in reply
    assert "https://wa.me/972501234567" in reply
    assert "https://example.com/a.jpg" in reply


@pytest.mark.asyncio
async def test_approve_ends_by_asking_the_pro_to_make_contact(wf, mock_db):
    pro_id = await _pro(mock_db)
    await _lead(
        mock_db,
        pro_id=pro_id,
        customer_phone="972501234567",
        floor="3",
        apartment="7",
        offer_withheld_details="\n\n*מדיה מצורפת:*\n1. https://example.com/a.jpg",
    )

    await process_incoming_message(PRO_CHAT, "אשר")

    reply = _replies(wf, PRO_CHAT)[0]
    assert reply.endswith(Messages.Pro.CONTACT_CARD_CLOSING)
    # The floor line stays with the card's fields, ahead of the media block.
    assert reply.index("*פרטים נוספים:*") < reply.index("*מדיה מצורפת:*")
    # §6: the navigation link still ends its own line.
    assert "\n*פרטים נוספים:*" in reply


@pytest.mark.asyncio
async def test_approve_clears_the_withheld_details(wf, mock_db):
    pro_id = await _pro(mock_db)
    lead_id = await _lead(mock_db, pro_id=pro_id, offer_withheld_details="\n\nקישור")

    await process_incoming_message(PRO_CHAT, "אשר")

    stored = await mock_db.leads.find_one({"_id": lead_id})
    assert "offer_withheld_details" not in stored


@pytest.mark.asyncio
async def test_approve_names_the_pro_found_template_for_the_customer(wf, mock_db):
    pro_id = await _pro(mock_db)
    await _lead(
        mock_db, pro_id=pro_id, appointment_time="מחר", customer_phone="972501234567"
    )

    await process_incoming_message(PRO_CHAT, "אשר")

    call = next(c for c in wf.send_message.call_args_list if c.args[0] == CHAT_ID)
    assert call.kwargs["template_key"] == "pro_found"
    assert call.kwargs["params"]["pro_phone"] == "0500000001"
    assert call.kwargs["params"]["issue_type"] == "נזילה"
    assert (
        call.kwargs["params"]["price_estimate"] == Messages.Fallbacks.PRICE_NOT_QUOTED
    )
    assert Messages.Fallbacks.PRICE_NOT_QUOTED == "לא ניתנה הערכה"


@pytest.mark.asyncio
async def test_approve_without_a_template_offer_adds_nothing(wf, mock_db):
    pro_id = await _pro(mock_db)
    await _lead(mock_db, pro_id=pro_id, media_urls=["https://example.com/a.jpg"])

    await process_incoming_message(PRO_CHAT, "אשר")

    assert "https://example.com/a.jpg" not in _replies(wf, PRO_CHAT)[0]


@pytest.mark.asyncio
async def test_reject_from_a_fresh_fsm_reaches_its_handler(wf, mock_db, monkeypatch):
    pro_id = await _pro(mock_db)
    await _lead(mock_db, pro_id=pro_id)
    monkeypatch.setattr(monitor_service, "reassign_lead", AsyncMock(return_value=True))

    await process_incoming_message(PRO_CHAT, "דחה")

    assert _replies(wf, PRO_CHAT) == [Messages.Pro.REJECT_SUCCESS]


@pytest.mark.asyncio
async def test_finish_from_a_fresh_fsm_reaches_its_handler(wf, mock_db):
    pro_id = await _pro(mock_db)
    await _lead(mock_db, pro_id=pro_id, status=LeadStatus.BOOKED)

    await process_incoming_message(PRO_CHAT, "סיימתי")

    assert Messages.Pro.FINISH_SUCCESS_ASK_PRICE in _replies(wf, PRO_CHAT)


@pytest.mark.asyncio
async def test_still_working_from_a_fresh_fsm_reaches_its_handler(wf, mock_db):
    pro_id = await _pro(mock_db)
    await _lead(mock_db, pro_id=pro_id, status=LeadStatus.BOOKED)

    await process_incoming_message(PRO_CHAT, "עדיין עובד")

    assert _replies(wf, PRO_CHAT) == [Messages.Pro.STILL_WORKING_ACK]


@pytest.mark.asyncio
async def test_jobs_from_a_fresh_fsm_reaches_its_handler(wf, mock_db):
    pro_id = await _pro(mock_db)
    await _lead(mock_db, pro_id=pro_id, status=LeadStatus.BOOKED)

    await process_incoming_message(PRO_CHAT, "עבודות")

    assert Messages.Pro.ACTIVE_JOBS_HEADER in _replies(wf, PRO_CHAT)[0]


@pytest.mark.asyncio
async def test_a_pro_on_break_does_not_reach_the_keyword_handlers(wf, mock_db):
    """Pins a defect the free-form path has too. is_active False once the PRO_MODE
    state has expired: `_is_registered_pro` and auto-detect both skip this pro, so a
    reply to a finish reminder goes to the customer side. Delete this when decided.
    """
    pro_id = await _pro(mock_db, is_active=False)
    await _lead(mock_db, pro_id=pro_id, status=LeadStatus.BOOKED)

    await process_incoming_message(PRO_CHAT, "עדיין עובד")

    assert _replies(wf, PRO_CHAT) != [Messages.Pro.STILL_WORKING_ACK]


@pytest.mark.asyncio
async def test_sos_alert_pending_approve_works_on_an_offered_lead(wf, mock_db):
    pro_id = await _pro(mock_db)
    await _lead(mock_db, pro_id=pro_id, status=LeadStatus.NEW)

    await process_incoming_message(PRO_CHAT, "אשר")

    assert Messages.Pro.APPROVE_SUCCESS in _replies(wf, PRO_CHAT)[0]


@pytest.mark.asyncio
async def test_sos_alert_pending_approve_finds_nothing_on_an_early_lead(wf, mock_db):
    """Pins a defect the free-form alert has too. The alert goes out for CONTACTED
    (an early lead the pro was never offered) and says אשר, and אשר only looks at
    NEW. Delete this when that is decided."""
    pro_id = await _pro(mock_db)
    await _lead(mock_db, pro_id=pro_id, status=LeadStatus.CONTACTED)

    await process_incoming_message(PRO_CHAT, "אשר")

    assert _replies(wf, PRO_CHAT) == [Messages.Pro.NO_PENDING_APPROVALS]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply, expected_status",
    [("1", LeadStatus.COMPLETED), ("2", LeadStatus.BOOKED)],
)
async def test_completion_check_digits_reach_the_handler_from_a_fresh_fsm(
    wf, mock_db, reply, expected_status
):
    pro_id = await _pro(mock_db)
    lead_id = await _lead(
        mock_db,
        pro_id=pro_id,
        status=LeadStatus.BOOKED,
        completion_check_sent_at=datetime.now(timezone.utc),
    )

    await process_incoming_message(CHAT_ID, reply)

    assert (await mock_db.leads.find_one({"_id": lead_id}))["status"] == expected_status
    assert _replies(wf, CHAT_ID)


@pytest.mark.asyncio
async def test_completion_check_three_reports_a_no_show_from_a_fresh_fsm(
    wf, mock_db, monkeypatch
):
    pro_id = await _pro(mock_db)
    lead_id = await _lead(
        mock_db,
        pro_id=pro_id,
        status=LeadStatus.BOOKED,
        completion_check_sent_at=datetime.now(timezone.utc),
    )
    monkeypatch.setattr(monitor_service, "reassign_lead", AsyncMock(return_value=True))

    await process_incoming_message(CHAT_ID, "3")

    assert (await mock_db.leads.find_one({"_id": lead_id})).get("no_show_reported_at")
    assert Messages.Customer.NO_SHOW_ACK in _replies(wf, CHAT_ID)


@pytest.mark.asyncio
async def test_a_rejected_pro_writing_נציג_is_paged_as_a_customer(
    wf, mock_db, monkeypatch
):
    """Reject deletes the profile, so this chat is a plain consented customer."""
    page = MagicMock()
    monkeypatch.setattr(notification_service, "page_operator", page)

    await process_incoming_message("972500000002@c.us", "נציג")

    assert page.call_count == 1
    assert _replies(wf, "972500000002@c.us") == [
        Messages.Customer.BOT_PAUSED_BY_CUSTOMER
    ]
    state = await app.services.workflow_service.StateManager.get_state(
        "972500000002@c.us"
    )
    assert state == UserStates.PAUSED_FOR_HUMAN


# ---------------------------------------------------------------------------
# send_sos_alert: which template, and the phone only once the lead is BOOKED
# ---------------------------------------------------------------------------


@pytest.fixture
def sos_pages(monkeypatch):
    page = MagicMock()
    monkeypatch.setattr(notification_service, "page_operator", page)
    return page


@pytest.mark.asyncio
async def test_sos_alert_on_a_booked_lead_names_the_template_with_the_phone(
    wf, mock_db, monkeypatch, sos_pages
):
    whatsapp = MagicMock()
    whatsapp.send_message = AsyncMock(return_value={"idMessage": "x"})
    monkeypatch.setattr(notification_service, "whatsapp", whatsapp)
    pro_id = await _pro(mock_db)
    await _lead(mock_db, pro_id=pro_id, status=LeadStatus.BOOKED)

    await notification_service.send_sos_alert(CHAT_ID, "עזרה", str(pro_id))

    call = whatsapp.send_message.call_args
    assert call.kwargs["template_key"] == "sos_alert"
    assert call.kwargs["params"] == {"phone": "0501234567", "last_message": "עזרה"}
    assert "0501234567" in call.args[1]
    assert "pro_notified=True" in sos_pages.call_args.args[0]


@pytest.mark.asyncio
async def test_sos_alert_on_an_unapproved_offer_carries_no_phone_and_no_words(
    wf, mock_db, monkeypatch, sos_pages
):
    """PRO-59 and PRO-212: a customer can type their number, so a pro who has not
    approved gets neither it nor the message. The template has no variable to
    carry either in."""
    whatsapp = MagicMock()
    whatsapp.send_message = AsyncMock(return_value={"idMessage": "x"})
    monkeypatch.setattr(notification_service, "whatsapp", whatsapp)
    pro_id = await _pro(mock_db)
    await _lead(mock_db, pro_id=pro_id, status=LeadStatus.NEW)

    await notification_service.send_sos_alert(
        CHAT_ID, "תתקשרו אליי 0501234567", str(pro_id)
    )

    call = whatsapp.send_message.call_args
    assert call.args[1] == Messages.SOS.PRO_ALERT_PENDING
    assert call.kwargs == {"template_key": "sos_alert_pending"}
    assert template_registry.TEMPLATES["sos_alert_pending"].params == ()
    assert "0501234567" not in call.args[1]
    assert "pro_notified=True" in sos_pages.call_args.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", [LeadStatus.CONTACTED, None], ids=["contacted", "no-lead"]
)
async def test_sos_alert_sends_the_pro_nothing_for_a_lead_never_offered(
    wf, mock_db, monkeypatch, sos_pages, status
):
    """PRO-212: אשר would find nothing, so no template is reached either."""
    whatsapp = MagicMock()
    whatsapp.send_message = AsyncMock(return_value={"idMessage": "x"})
    monkeypatch.setattr(notification_service, "whatsapp", whatsapp)
    pro_id = await _pro(mock_db)
    if status is not None:
        await _lead(mock_db, pro_id=pro_id, status=status)

    await notification_service.send_sos_alert(CHAT_ID, "עזרה", str(pro_id))

    whatsapp.send_message.assert_not_called()
    assert "pro_notified=False" in sos_pages.call_args.args[0]


@pytest.mark.asyncio
async def test_send_sos_alert_reports_a_blocked_send_as_not_notified(
    wf, mock_db, monkeypatch, sos_pages
):
    whatsapp = MagicMock()
    whatsapp.send_message = AsyncMock(return_value=None)
    monkeypatch.setattr(notification_service, "whatsapp", whatsapp)
    pro_id = await _pro(mock_db)
    await _lead(mock_db, pro_id=pro_id, status=LeadStatus.BOOKED)

    await notification_service.send_sos_alert(CHAT_ID, "א" * 4000, str(pro_id))

    whatsapp.send_message.assert_called_once()
    assert "pro_notified=False" in sos_pages.call_args.args[0]


# ---------------------------------------------------------------------------
# The admin panel's sends: an operator's click, through the sync bridge
# ---------------------------------------------------------------------------

PANEL_PRO_PHONE = "972500000003"


@pytest.fixture
def panel(graph, pages, monkeypatch):
    """The real Cloud provider behind the panel's bridge, the breaker letting
    sends through. ``window`` is what the recipient's 24h window answers."""
    facade = WhatsAppFacade(CloudAPIProvider())
    monkeypatch.setattr(
        facade, "_outbound_blocked_reason", AsyncMock(return_value=None)
    )
    window = AsyncMock(return_value=False)
    monkeypatch.setattr(cloud_api, "is_service_window_open", window)
    monkeypatch.setattr(sync, "get_whatsapp", lambda: facade)
    return SimpleNamespace(graph=graph, pages=pages, window=window)


VERDICTS = [
    (professionals._notify_pro_approved, "onboarding_approved"),
    (professionals._notify_pro_rejected, "onboarding_rejected"),
]


@pytest.mark.parametrize("notify, key", VERDICTS, ids=[k for _, k in VERDICTS])
def test_a_panel_verdict_reaches_a_closed_window_once_its_template_is_approved(
    panel, monkeypatch, notify, key
):
    _approve(monkeypatch, key)

    assert notify(PANEL_PRO_PHONE) is True

    template = panel.graph[0]["template"]
    assert panel.graph[0]["to"] == PANEL_PRO_PHONE
    assert template["name"] == template_registry.TEMPLATES[key].meta_name
    assert "components" not in template  # neither body has a variable
    assert panel.pages.call_count == 0


@pytest.mark.parametrize("notify, key", VERDICTS, ids=[k for _, k in VERDICTS])
def test_a_draft_panel_verdict_on_a_closed_window_is_still_not_sent(panel, notify, key):
    assert notify(PANEL_PRO_PHONE) is False

    assert panel.graph == []
    assert panel.pages.call_count == 1


@pytest.mark.parametrize("notify, key", VERDICTS, ids=[k for _, k in VERDICTS])
def test_a_draft_panel_verdict_on_an_open_window_is_the_same_text_payload(
    panel, notify, key
):
    panel.window.return_value = True
    spec = template_registry.TEMPLATES[key]
    free_form = {
        "onboarding_approved": Messages.Onboarding.APPROVED_NOTIFICATION,
        "onboarding_rejected": Messages.Onboarding.REJECTED_NOTIFICATION,
    }[spec.key]

    assert sync.send_text_sync(PANEL_PRO_PHONE + "@c.us", free_form) is True
    plain = panel.graph.pop()
    assert notify(PANEL_PRO_PHONE) is True

    assert panel.graph == [plain]
    assert plain["type"] == "text"


def _panel_booked_lead(monkeypatch):
    db = mongomock.MongoClient()["panel"]
    pro_id = db["users"].insert_one({"business_name": "שירותי דוגמה"}).inserted_id
    lead_id = (
        db["leads"]
        .insert_one({"chat_id": CHAT_ID, "pro_id": pro_id, "status": "booked"})
        .inserted_id
    )
    monkeypatch.setattr(panel_utils, "leads_collection", db["leads"])
    monkeypatch.setattr(panel_utils, "users_collection", db["users"])
    return db, lead_id


def test_the_operators_completion_check_reaches_a_closed_window_once_approved(
    panel, monkeypatch
):
    _approve(monkeypatch, "completion_check")
    db, lead_id = _panel_booked_lead(monkeypatch)

    assert panel_utils.send_completion_check_sync(str(lead_id)) is True

    template = panel.graph[0]["template"]
    assert template["name"] == "proli_completion_check"
    assert [p["text"] for p in template["components"][0]["parameters"]] == [
        "שירותי דוגמה"
    ]
    # Stamped like any check that went, so the monitor's cooldown restarts.
    assert db["leads"].find_one({"_id": lead_id})["completion_check_sent_count"] == 1


def test_a_draft_completion_check_on_a_closed_window_is_not_sent_or_stamped(
    panel, monkeypatch
):
    db, lead_id = _panel_booked_lead(monkeypatch)

    assert panel_utils.send_completion_check_sync(str(lead_id)) is False

    assert panel.graph == []
    assert "completion_check_sent_count" not in db["leads"].find_one({"_id": lead_id})


def test_the_bridge_passes_no_template_argument_unless_one_is_named(monkeypatch):
    """A send that names no template is the call it always was."""
    facade = MagicMock()
    facade.send_message = AsyncMock(return_value={"id": "1"})
    monkeypatch.setattr(sync, "get_whatsapp", lambda: facade)

    assert sync.send_text_sync(CHAT_ID, "hi") is True
    assert sync.send_text_sync(CHAT_ID, "hi", template_key="completion_check") is True

    plain, named = facade.send_message.await_args_list
    assert plain.args == (CHAT_ID, "hi") and plain.kwargs == {}
    assert named.kwargs == {"template_key": "completion_check", "params": None}
