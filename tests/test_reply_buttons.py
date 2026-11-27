"""PRO-104 and PRO-105 (a, b): reply buttons on a flow's free-form send, and a
tap that names the lead it answers.

Unit level, below the end-to-end taps in tests/e2e/test_e2e_reply_buttons.py:

* the reply reference: ``"<keyword>#<24 hex>"`` in, keyword and lead id out,
  anything else untouched (``normalize_meta_message``);
* the hop from the webhook to the flow: the enqueue, the worker task (old jobs
  included), ``process_incoming_message``;
* the transport: an open window gets interactive reply buttons with ids
  ``payload#ref``, a closed window the template with ``#ref`` on each quick-reply
  payload, an over-limit message plain text; ``DryRunProvider`` records them;
* the pro flow refusing a tap whose lead is not this pro's to answer;
* the consent and completion-check buttons mirroring the words their handlers
  accept.

HTTP is an ``httpx.MockTransport`` as in tests/test_business_initiated_send.py.
"""

import dataclasses
import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import mongomock
import pytest
import pytest_asyncio
from bson import ObjectId
from pydantic import SecretStr

import admin_panel.core.utils as panel_utils
import app.core.arq_worker as arq_worker
import app.providers.whatsapp.cloud_api as cloud_api
from app.core.config import settings
from app.core.constants import LeadStatus
from app.core.messages import Messages
from app.providers.whatsapp import delivery, sync, template_registry
from app.providers.whatsapp.cloud_api import CloudAPIProvider, normalize_meta_message
from app.providers.whatsapp.dry_run import DryRunProvider
from app.providers.whatsapp.facade import WhatsAppFacade
from app.providers.whatsapp.template_registry import QuickReply, TemplateStatus
from app.providers.whatsapp.window import _window_key
from app.services.dispatch_guards import CONSENT_BUTTONS
from app.services.pro_flow import handle_pro_text_command

CHAT_ID = "972501234567@c.us"
LEAD_ID = "6a1b2c3d4e5f6a7b8c9d0e1f"
APPROVE = Messages.Keywords.APPROVE_COMMANDS[0]
REJECT = Messages.Keywords.REJECT_COMMANDS[0]
OFFER_BUTTONS = template_registry.buttons_for("lead_offer")
OFFER_PARAMS = {
    "urgency": "רגילה",
    "price_estimate": "לא ניתנה הערכה",
    "appointment_time": "מחר",
    "issue_type": "נזילה",
    "full_address": "הרצל 1, תל אביב",
    "customer_name": "דנה",
}


# ---------------------------------------------------------------------------
# The reply reference
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    [
        (f"{APPROVE}#{LEAD_ID}", (APPROVE, LEAD_ID)),
        (f"{REJECT}#{LEAD_ID.upper()}", (REJECT, LEAD_ID)),
        (APPROVE, (APPROVE, None)),
        (f"{APPROVE}#{LEAD_ID[:-1]}", (f"{APPROVE}#{LEAD_ID[:-1]}", None)),
        (f"{APPROVE}#{LEAD_ID}0", (f"{APPROVE}#{LEAD_ID}0", None)),
        (f"{APPROVE}#{'z' * 24}", (f"{APPROVE}#{'z' * 24}", None)),
        (f"#{LEAD_ID}", (f"#{LEAD_ID}", None)),
        ("", ("", None)),
    ],
    ids=["ref", "upper-hex", "bare", "short", "long", "not-hex", "no-keyword", "empty"],
)
def test_split_reply_ref(value, expected):
    assert template_registry.split_reply_ref(value) == expected


def test_with_reply_ref_round_trips_and_is_a_no_op_without_one():
    assert template_registry.with_reply_ref(APPROVE, None) == APPROVE
    joined = template_registry.with_reply_ref(APPROVE, LEAD_ID)
    assert template_registry.split_reply_ref(joined) == (APPROVE, LEAD_ID)


def _interactive(button_id):
    return {
        "from": "972501234567",
        "id": "wamid.TAP",
        "type": "interactive",
        "interactive": {
            "type": "button_reply",
            "button_reply": {"id": button_id, "title": "אשר"},
        },
    }


def _template_tap(payload, label=Messages.Templates.Buttons.APPROVE):
    return {
        "from": "972501234567",
        "id": "wamid.TAP",
        "type": "button",
        "button": {"payload": payload, "text": label},
    }


@pytest.mark.parametrize("build", [_interactive, _template_tap], ids=["reply", "tpl"])
def test_a_tap_naming_a_lead_normalizes_to_the_keyword_and_its_ref(build):
    message = normalize_meta_message(build(f"{APPROVE}#{LEAD_ID}"))

    assert message.text == APPROVE
    assert message.reply_ref == LEAD_ID


@pytest.mark.parametrize("build", [_interactive, _template_tap], ids=["reply", "tpl"])
@pytest.mark.parametrize("payload", [f"{APPROVE}#123", f"{APPROVE}#{'g' * 24}"])
def test_a_malformed_suffix_is_read_whole_with_no_ref(build, payload):
    message = normalize_meta_message(build(payload))

    assert message.text == payload
    assert message.reply_ref is None


def test_a_label_only_template_tap_still_maps_back_with_no_ref():
    message = normalize_meta_message(
        {
            "from": "972501234567",
            "id": "wamid.TAP",
            "type": "button",
            "button": {"text": Messages.Templates.Buttons.APPROVE},
        }
    )

    assert message.text == APPROVE
    assert message.reply_ref is None


def test_a_typed_message_has_no_ref():
    message = normalize_meta_message(
        {
            "from": "972501234567",
            "id": "wamid.T",
            "type": "text",
            "text": {"body": f"{APPROVE}#{LEAD_ID}"},
        }
    )

    assert message.text == f"{APPROVE}#{LEAD_ID}"
    assert message.reply_ref is None


# ---------------------------------------------------------------------------
# Webhook → worker → flow
# ---------------------------------------------------------------------------


@pytest.fixture
def capturing_pool(monkeypatch):
    pool = AsyncMock()

    async def _get_pool():
        return pool

    monkeypatch.setattr("app.api.routes.meta_webhook.get_arq_pool", _get_pool)
    return pool


async def _post_message(message):
    from app.main import app

    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_ID",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "messages": [message],
                        },
                    }
                ],
            }
        ],
    }
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.post(
            "/webhook/meta",
            content=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )


@pytest.mark.asyncio
async def test_the_route_enqueues_the_ref_of_a_tap(fake_redis, capturing_pool):
    resp = await _post_message(_interactive(f"{APPROVE}#{LEAD_ID}"))

    assert resp.status_code == 200
    call = capturing_pool.enqueue_job.await_args
    assert call.args[:3] == ("process_message_task", CHAT_ID, APPROVE)
    assert call.kwargs["reply_ref"] == LEAD_ID


@pytest.mark.asyncio
async def test_the_route_enqueues_no_ref_for_a_message_without_one(
    fake_redis, capturing_pool
):
    await _post_message(_interactive(APPROVE))

    assert "reply_ref" not in capturing_pool.enqueue_job.await_args.kwargs


@pytest.mark.asyncio
async def test_the_worker_passes_the_ref_to_the_turn(monkeypatch):
    turn = AsyncMock(return_value=None)
    monkeypatch.setattr(arq_worker, "process_incoming_message", turn)

    await arq_worker.process_message_task(
        {}, CHAT_ID, APPROVE, None, message_id="wamid.1", reply_ref=LEAD_ID
    )

    turn.assert_awaited_once_with(
        CHAT_ID, APPROVE, None, message_id="wamid.1", reply_ref=LEAD_ID
    )


@pytest.mark.asyncio
async def test_an_old_job_without_the_ref_still_runs(monkeypatch):
    turn = AsyncMock(return_value=None)
    monkeypatch.setattr(arq_worker, "process_incoming_message", turn)

    await arq_worker.process_message_task({}, CHAT_ID, APPROVE, None, "wamid.1")

    turn.assert_awaited_once_with(CHAT_ID, APPROVE, None, message_id="wamid.1")


@pytest.mark.asyncio
async def test_process_incoming_message_carries_the_ref_to_the_pro_command(
    monkeypatch, mock_db
):
    from app.services import workflow_service

    phone = "972500777001"
    await mock_db.users.delete_many({"phone_number": phone})
    await mock_db.users.insert_one(
        {
            "_id": ObjectId(),
            "phone_number": phone,
            "role": "professional",
            "is_active": True,
        }
    )
    handled = AsyncMock(return_value="")
    monkeypatch.setattr(workflow_service, "_handle_pro_cmd", handled)

    await workflow_service.process_incoming_message(
        f"{phone}@c.us", APPROVE, reply_ref=LEAD_ID
    )

    assert handled.await_args.kwargs["reply_ref"] == LEAD_ID


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


def _approve_template(monkeypatch, key="lead_offer"):
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
    monkeypatch.setattr(settings, "WHATSAPP_ARMED_TEMPLATES", key)


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
    await fake_redis.set("wa:instance:state", "authorized")
    return WhatsAppFacade(CloudAPIProvider())


async def _open_window(fake_redis):
    await fake_redis.set(_window_key(CHAT_ID), "2026-01-01T00:00:00+00:00", ex=100)


async def _offer(facade, text="הצעת עבודה", buttons=OFFER_BUTTONS):
    return await facade.send_message(
        CHAT_ID,
        text,
        template_key="lead_offer",
        params=OFFER_PARAMS,
        buttons=buttons,
        reply_ref=LEAD_ID,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("armed", [False, True], ids=["draft", "armed"])
async def test_an_open_window_gets_interactive_buttons_whose_ids_carry_the_ref(
    facade, graph, pages, fake_redis, monkeypatch, armed
):
    if armed:
        _approve_template(monkeypatch)
    await _open_window(fake_redis)

    await _offer(facade)

    (sent,) = graph
    assert sent["type"] == "interactive"
    interactive = sent["interactive"]
    assert interactive["type"] == "button"
    assert interactive["body"] == {"text": "הצעת עבודה"}
    assert interactive["action"]["buttons"] == [
        {"type": "reply", "reply": {"id": f"{word}#{LEAD_ID}", "title": b.label}}
        for word, b in zip([APPROVE, REJECT], OFFER_BUTTONS)
    ]
    assert pages.call_count == 0


@pytest.mark.asyncio
async def test_a_closed_window_gets_the_template_with_the_ref_on_each_payload(
    facade, graph, pages, fake_redis, monkeypatch
):
    _approve_template(monkeypatch)

    result = await _offer(facade)

    assert result["sent_as_template"] == "lead_offer"
    (sent,) = graph
    buttons = [c for c in sent["template"]["components"] if c["type"] == "button"]
    assert [c["parameters"][0]["payload"] for c in buttons] == [
        f"{APPROVE}#{LEAD_ID}",
        f"{REJECT}#{LEAD_ID}",
    ]
    # The registry itself is untouched, so the parity rule still reads the bare
    # keywords.
    assert (
        template_registry.button_rule_violations(
            template_registry.TEMPLATES["lead_offer"]
        )
        == []
    )


@pytest.mark.asyncio
async def test_a_closed_window_on_a_draft_pages_and_sends_nothing(
    facade, graph, pages, fake_redis
):
    assert await _offer(facade) is None
    assert graph == []
    assert pages.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text, buttons",
    [
        ("x" * 1025, OFFER_BUTTONS),
        ("הצעה", (QuickReply("א" * 21, APPROVE), OFFER_BUTTONS[1])),
        ("הצעה", (QuickReply("אשר", APPROVE), QuickReply("אשר", REJECT))),
        ("הצעה", tuple(QuickReply(f"ב{i}", str(i)) for i in range(4))),
    ],
    ids=["long-body", "long-title", "same-title", "four-buttons"],
)
async def test_what_meta_would_refuse_goes_as_the_plain_text(
    facade, graph, fake_redis, text, buttons
):
    await _open_window(fake_redis)

    await _offer(facade, text=text, buttons=buttons)

    assert {sent["type"] for sent in graph} == {"text"}
    assert "".join(sent["text"]["body"] for sent in graph) == text


@pytest.mark.asyncio
async def test_without_buttons_an_offer_is_the_plain_text_it_always_was(
    facade, graph, fake_redis
):
    await _open_window(fake_redis)

    await facade.send_message(
        CHAT_ID, "הצעה", template_key="lead_offer", params=OFFER_PARAMS
    )

    assert graph[0]["type"] == "text"


@pytest.mark.asyncio
async def test_buttons_without_a_template_are_interactive_on_an_open_window(
    facade, graph, fake_redis
):
    await _open_window(fake_redis)

    await facade.send_message(CHAT_ID, "בקשת הסכמה", buttons=CONSENT_BUTTONS)

    (sent,) = graph
    assert [b["reply"] for b in sent["interactive"]["action"]["buttons"]] == [
        {"id": b.payload, "title": b.label} for b in CONSENT_BUTTONS
    ]


@pytest.mark.asyncio
async def test_buttons_without_a_template_to_a_closed_window_behave_as_text(
    facade, graph, pages, fake_redis
):
    plain = await facade.send_message(CHAT_ID, "בקשת הסכמה")
    pages_after_plain = pages.call_count
    with_buttons = await facade.send_message(
        CHAT_ID, "בקשת הסכמה", buttons=CONSENT_BUTTONS
    )

    assert plain is None and with_buttons is None
    assert graph == []
    assert pages_after_plain == 1


@pytest.mark.asyncio
async def test_dry_run_records_the_buttons_on_its_text_entry():
    provider = DryRunProvider()
    facade = WhatsAppFacade(provider)

    await facade.send_message(
        CHAT_ID,
        "הצעה",
        template_key="lead_offer",
        params=OFFER_PARAMS,
        buttons=OFFER_BUTTONS,
        reply_ref=LEAD_ID,
    )
    await facade.send_message(CHAT_ID, "בקשה", buttons=CONSENT_BUTTONS)
    await facade.send_message(CHAT_ID, "רגיל")

    offer, consent, plain = provider.sent
    assert offer["kind"] == "text" and offer["text"] == "הצעה"
    assert offer["buttons"] == [
        (OFFER_BUTTONS[0].label, f"{APPROVE}#{LEAD_ID}"),
        (OFFER_BUTTONS[1].label, f"{REJECT}#{LEAD_ID}"),
    ]
    assert offer["template_buttons"] == [(b.label, b.payload) for b in OFFER_BUTTONS]
    assert consent == {
        "kind": "text",
        "chat_id": CHAT_ID,
        "text": "בקשה",
        "buttons": [(b.label, b.payload) for b in CONSENT_BUTTONS],
    }
    assert plain == {"kind": "text", "chat_id": CHAT_ID, "text": "רגיל"}


def test_the_panel_completion_check_offers_the_three_buttons(monkeypatch, graph):
    facade = WhatsAppFacade(CloudAPIProvider())
    monkeypatch.setattr(
        facade, "_outbound_blocked_reason", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        cloud_api, "is_service_window_open", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(sync, "get_whatsapp", lambda: facade)
    db = mongomock.MongoClient()["panel"]
    pro_id = db["users"].insert_one({"business_name": "שירותי דוגמה"}).inserted_id
    lead_id = (
        db["leads"]
        .insert_one({"chat_id": CHAT_ID, "pro_id": pro_id, "status": "booked"})
        .inserted_id
    )
    monkeypatch.setattr(panel_utils, "leads_collection", db["leads"])
    monkeypatch.setattr(panel_utils, "users_collection", db["users"])

    assert panel_utils.send_completion_check_sync(str(lead_id)) is True

    (sent,) = graph
    assert [b["reply"]["id"] for b in sent["interactive"]["action"]["buttons"]] == [
        "1",
        "2",
        "3",
    ]


# ---------------------------------------------------------------------------
# The pro flow: a tap acts on its own lead or on none
# ---------------------------------------------------------------------------

PRO_ID = ObjectId()
OTHER_PRO_ID = ObjectId()
PRO_PHONE = "972500888001"
PRO_CHAT = f"{PRO_PHONE}@c.us"


@pytest_asyncio.fixture
async def pro(mock_db):
    doc = {
        "_id": PRO_ID,
        "phone_number": PRO_PHONE,
        "role": "professional",
        "business_name": "בדיקה",
        "is_active": True,
    }
    await mock_db.users.delete_many({"_id": PRO_ID})
    await mock_db.leads.delete_many({"pro_id": {"$in": [PRO_ID, OTHER_PRO_ID]}})
    await mock_db.users.insert_one(doc)
    return doc


async def _lead(mock_db, **overrides):
    doc = {
        "pro_id": PRO_ID,
        "status": LeadStatus.NEW,
        "chat_id": "972501230000@c.us",
        "issue_type": "נזילה",
        "full_address": "הרצל 1, תל אביב",
        "appointment_time": "10:00",
    }
    doc.update(overrides)
    res = await mock_db.leads.insert_one(doc)
    return await mock_db.leads.find_one({"_id": res.inserted_id})


@pytest.fixture
def lead_manager():
    lm = MagicMock()
    lm.update_lead_status = AsyncMock()
    return lm


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"pro_id": OTHER_PRO_ID},
        {"status": LeadStatus.BOOKED},
        {"status": LeadStatus.PENDING_ADMIN_REVIEW},
    ],
    ids=["reassigned", "booked", "escalated"],
)
@pytest.mark.parametrize("word", [APPROVE, REJECT])
async def test_a_tap_on_a_lead_not_this_pros_to_answer_does_nothing(
    mock_db, pro, lead_manager, change, word
):
    waiting = await _lead(mock_db)
    tapped = await _lead(mock_db, **change)
    wa = MagicMock(send_message=AsyncMock())

    reply = await handle_pro_text_command(
        PRO_CHAT, word, wa, lead_manager, reply_ref=str(tapped["_id"])
    )

    # Another offer of this pro's is still waiting, so "the list changed".
    assert reply == Messages.Pro.SELECTION_OFFER_LIST_CHANGED
    lead_manager.update_lead_status.assert_not_called()
    for lead in (waiting, tapped):
        assert await mock_db.leads.find_one({"_id": lead["_id"]}) == lead


@pytest.mark.asyncio
async def test_a_tap_for_a_lead_that_does_not_exist_does_nothing(
    mock_db, pro, lead_manager
):
    await _lead(mock_db)

    reply = await handle_pro_text_command(
        PRO_CHAT, APPROVE, MagicMock(), lead_manager, reply_ref=str(ObjectId())
    )

    assert reply == Messages.Pro.SELECTION_OFFER_LIST_CHANGED
    lead_manager.update_lead_status.assert_not_called()


@pytest.mark.asyncio
async def test_an_approve_tap_pins_status_and_owner_at_the_write(
    mock_db, pro, lead_manager, monkeypatch
):
    """PRO-123 still holds on the tap path: a change between the read and the
    write books nothing."""
    lead = await _lead(mock_db)
    lead_manager.update_lead_status.return_value = None

    await handle_pro_text_command(
        PRO_CHAT, APPROVE, MagicMock(), lead_manager, reply_ref=str(lead["_id"])
    )

    kwargs = lead_manager.update_lead_status.await_args.kwargs
    assert kwargs["expected_status"] == LeadStatus.NEW
    assert kwargs["expected_pro_id"] == PRO_ID


@pytest.mark.asyncio
async def test_a_reject_tap_pins_the_owner_at_the_write(mock_db, pro, monkeypatch):
    from app.services import pro_flow

    lead = await _lead(mock_db)
    claim = AsyncMock(return_value=None)
    monkeypatch.setattr(pro_flow, "set_lead_status", claim)

    await handle_pro_text_command(
        PRO_CHAT, REJECT, MagicMock(), MagicMock(), reply_ref=str(lead["_id"])
    )

    assert claim.await_args.kwargs["expected_status"] == LeadStatus.NEW
    assert claim.await_args.kwargs["expected_pro_id"] == PRO_ID


# ---------------------------------------------------------------------------
# Every button mirrors a reply its handler accepts
# ---------------------------------------------------------------------------


def test_the_consent_buttons_are_the_words_the_gate_accepts():
    accept, decline = CONSENT_BUTTONS
    assert accept.payload in Messages.Consent.ACCEPT_KEYWORDS
    assert decline.payload in Messages.Consent.DECLINE_KEYWORDS
    # The request advertises both words, so a tap offers nothing typing could
    # not. PRO-245: asked for in the sentence ("אפשר לכתוב כן"), not in bold.
    from tests.copy_util import written_keywords

    assert written_keywords(
        Messages.Consent.REQUEST, {accept.payload, decline.payload}
    ) == [accept.payload, decline.payload]
    assert all(len(b.label) <= 20 for b in CONSENT_BUTTONS)


def test_the_completion_buttons_are_the_digits_its_handler_reads():
    buttons = template_registry.buttons_for("completion_check")
    assert [b.payload for b in buttons] == ["1", "2", "3"]
    # PRO-245: the free-form check now uses the `1 - text` lines its template
    # uses, which the registry's own reader parses.
    assert template_registry.advertised_replies(Messages.Customer.COMPLETION_CHECK) == [
        b.payload for b in buttons
    ]
    assert all(len(b.label) <= 20 for b in buttons)


@pytest.mark.parametrize(
    "key",
    ["lead_offer", "lead_offer_reassigned", "approval_nudge", "sos_alert_pending"],
)
def test_every_pro_offer_button_fits_an_interactive_reply(key):
    buttons = template_registry.buttons_for(key)
    assert [b.payload for b in buttons] == [APPROVE, REJECT]
    assert all(len(b.label) <= 20 for b in buttons)


def test_buttons_for_an_unknown_or_buttonless_key_is_empty():
    assert template_registry.buttons_for("no_such_template") == ()
    assert template_registry.buttons_for("pro_lost_lead") == ()


def test_offer_buttons_name_the_lead():
    from app.services.notification_service import offer_buttons

    lead_id = ObjectId()
    assert offer_buttons("lead_offer", {"_id": lead_id}) == {
        "buttons": OFFER_BUTTONS,
        "reply_ref": str(lead_id),
    }
    assert offer_buttons("pro_lost_lead", {"_id": lead_id}) == {}


def test_only_the_three_flows_send_reply_buttons():
    """CLAUDE.md: free-form reply buttons go out on the pro lead offers (offer,
    nudge, pending-SOS alert), the consent request and the completion check,
    and nowhere else until after launch. A new caller is added here on purpose."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    callers = set()
    for folder in ("app/services", "app/api", "app/core", "admin_panel"):
        for path in (root / folder).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "buttons=" in text or "offer_buttons(" in text:
                callers.add(path.relative_to(root).as_posix())

    assert callers == {
        "app/services/notification_service.py",  # offer, pending-SOS alert
        "app/services/workflow_service.py",  # first offer (_finalize_deal)
        "app/services/monitor_service.py",  # approval nudge
        "app/services/dispatch_guards.py",  # consent request
        "app/services/customer_flow.py",  # completion check
        "admin_panel/core/utils.py",  # completion check from the panel
    }
