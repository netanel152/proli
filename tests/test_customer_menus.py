"""PRO-105 (c, d): the customer's remaining menus as reply buttons and a list.

Unit level, below the end-to-end taps in tests/e2e/test_e2e_customer_menus.py:

* every button set mirrors the replies its text advertises (the digits of its
  ``1 - …`` lines, or the keywords it asks to be written), within Meta's limits;
* the reschedule list's rows, opener and section title fit Meta's hard limits
  on every weekday and every date, and the list never offers more than ten rows;
* the transport: an open window gets an interactive list whose row ids carry
  the ref, anything Meta would refuse goes as the plain text, a closed window
  behaves as text does, and ``DryRunProvider`` records the list;
* the inbound side: a ``list_reply`` reads back as its digit and its ref;
* ``tap_is_stale``, the one rule every guard applies to a tap's ref.

HTTP is an ``httpx.MockTransport`` as in tests/test_reply_buttons.py.
"""

import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio
from pydantic import SecretStr

import app.providers.whatsapp.cloud_api as cloud_api
from app.core.config import settings
from app.core.constants import UserStates
from app.core.messages import Messages
from app.providers.whatsapp import delivery, template_registry
from app.providers.whatsapp.base import ListPicker, ListRow
from app.providers.whatsapp.cloud_api import CloudAPIProvider, normalize_meta_message
from app.providers.whatsapp.dry_run import DryRunProvider
from app.providers.whatsapp.facade import WhatsAppFacade
from app.providers.whatsapp.window import _window_key
from app.services import customer_flow
from tests.copy_util import written_keywords
from app.services.customer_flow import (
    CANCEL_CONFIRM_BUTTONS,
    LOYALTY_BUTTONS,
    NEW_OR_EXISTING_BUTTONS,
    NO_SLOTS_BUTTONS,
    RESCHEDULE_LIST_LIMIT,
    _slot_row,
    tap_is_stale,
)

CHAT_ID = "972501234567@c.us"
REF = "6a1b2c3d4e5f6a7b8c9d0e1f"

# Meta Cloud API hard limits (interactive messages). Above any of them Meta
# answers HTTP 400 and the message is lost.
MAX_BUTTONS = 3
BUTTON_TITLE = 20
LIST_ROWS = 10
LIST_BUTTON = 20
SECTION_TITLE = 24
ROW_TITLE = 24
ROW_DESCRIPTION = 72


# ---------------------------------------------------------------------------
# Every button mirrors a reply its text advertises
# ---------------------------------------------------------------------------

BUTTON_SETS = [
    (CANCEL_CONFIRM_BUTTONS, Messages.Customer.CANCEL_CONFIRM_PROMPT),
    (CANCEL_CONFIRM_BUTTONS, Messages.Customer.CANCEL_REQUEST_CONFIRM_PROMPT),
    (LOYALTY_BUTTONS, Messages.Customer.LOYALTY_OFFER),
    (LOYALTY_BUTTONS, Messages.Customer.LOYALTY_REPROMPT),
    (NEW_OR_EXISTING_BUTTONS, Messages.Customer.EXISTING_JOB_PROMPT),
    (NEW_OR_EXISTING_BUTTONS, Messages.Customer.NEW_OR_EXISTING_REPROMPT),
    (NO_SLOTS_BUTTONS, Messages.Customer.RESCHEDULE_NO_SLOTS),
    (NO_SLOTS_BUTTONS, Messages.Customer.RESCHEDULE_SLOT_TAKEN_NO_SLOTS),
]
BUTTON_IDS = [
    "cancel-booked",
    "cancel-request",
    "loyalty-offer",
    "loyalty-reprompt",
    "existing-job",
    "new-or-existing-reprompt",
    "no-slots",
    "slot-taken-no-slots",
]


@pytest.mark.parametrize("buttons, text", BUTTON_SETS, ids=BUTTON_IDS)
def test_each_button_is_a_reply_its_text_advertises(buttons, text):
    """The digits of the text's ``1 - …`` lines, in order, or the words it asks
    to be written ("אפשר לכתוב ביטול"), in order: nothing a tap offers that
    typing could not, and nothing the text offers left without its button."""
    payloads = [b.payload for b in buttons]
    digits = [r for r in template_registry.advertised_replies(text) if r.isdigit()]
    words = written_keywords(text, {p for p in payloads if not p.isdigit()})
    assert payloads == (digits or words)


@pytest.mark.parametrize("buttons, text", BUTTON_SETS, ids=BUTTON_IDS)
def test_each_button_set_fits_meta_reply_buttons(buttons, text):
    labels = [b.label for b in buttons]
    assert 0 < len(buttons) <= MAX_BUTTONS
    assert all(0 < len(label) <= BUTTON_TITLE for label in labels)
    assert len(set(labels)) == len(labels)
    # And so Cloud really sends them as buttons rather than falling back.
    assert cloud_api._reply_buttons(text, buttons) is not None


def test_the_digit_buttons_are_the_digits_their_guards_read():
    """The cancel, loyalty and new-or-existing guards read a bare 1 / 2; the
    no-slots buttons send the two words that reach the cancel prompt and the
    human handoff."""
    for buttons in (CANCEL_CONFIRM_BUTTONS, LOYALTY_BUTTONS, NEW_OR_EXISTING_BUTTONS):
        assert [b.payload for b in buttons] == ["1", "2"]
    cancel, human = NO_SLOTS_BUTTONS
    assert cancel.payload in Messages.Keywords.CANCEL_KEYWORDS
    assert human.payload in Messages.Keywords.SOS_COMMANDS


# ---------------------------------------------------------------------------
# The reschedule list within Meta's hard limits
# ---------------------------------------------------------------------------


def _week_of_slots():
    """Every weekday, at the widest dates and times a row can show."""
    starts = []
    for first in (datetime(2026, 12, 28, 23, 59), datetime(2026, 1, 1, 0, 0)):
        starts += [first + timedelta(days=d) for d in range(7)]
    return starts


@pytest.mark.parametrize("start", _week_of_slots(), ids=lambda d: d.strftime("%a-%d"))
def test_a_slot_row_fits_meta_list_row_limits(start):
    row = _slot_row(str(RESCHEDULE_LIST_LIMIT), start)
    assert row.id == str(RESCHEDULE_LIST_LIMIT)
    assert row.title.startswith(f"{row.id}. ")
    assert 0 < len(row.title) <= ROW_TITLE
    assert 0 < len(row.description) <= ROW_DESCRIPTION
    # The row says the same instant the text line says.
    assert start.strftime("%H:%M") in row.title
    assert start.strftime("%d/%m/%Y") in row.description


def test_a_slot_row_title_is_the_compact_form():
    row = _slot_row("1", datetime(2026, 11, 29, 10, 0))  # a Sunday
    assert row.title == "1. א׳ 29.11 · 10:00"
    assert row.description == "יום ראשון, 29/11/2026 בשעה 10:00"


def test_the_list_opener_and_section_fit_meta_limits():
    assert 0 < len(Messages.Customer.RESCHEDULE_LIST_BUTTON) <= LIST_BUTTON
    assert 0 < len(Messages.Customer.RESCHEDULE_LIST_SECTION) <= SECTION_TITLE


def test_the_list_never_offers_more_rows_than_meta_takes():
    assert RESCHEDULE_LIST_LIMIT <= LIST_ROWS


def test_weekday_letters_follow_python_weekday_order():
    assert len(Messages.Customer.WEEKDAY_LETTERS) == 7
    assert Messages.Customer.WEEKDAY_LETTERS[datetime(2026, 11, 29).weekday()] == "א׳"
    assert Messages.Pro.WEEKDAY_NAMES[datetime(2026, 11, 29).weekday()] == "ראשון"


@pytest.mark.asyncio
async def test_offer_reschedule_slots_sends_one_row_per_numbered_line(monkeypatch):
    """The text lists the slots as ``1. …`` lines; the list has one row per line,
    with the same number as its id and the prompt's ref, which is stored."""
    starts = [datetime(2026, 11, 29, 10, 0) + timedelta(hours=h) for h in range(3)]
    slots = [{"_id": f"{i:024x}", "start_time": s} for i, s in enumerate(starts)]
    monkeypatch.setattr(
        customer_flow, "get_available_slots", AsyncMock(return_value=slots)
    )
    monkeypatch.setattr(customer_flow, "stored_utc_to_il", lambda d: d)
    whatsapp = MagicMock(send_message=AsyncMock())
    state = MagicMock(set_state=AsyncMock(), set_metadata=AsyncMock())

    assert await customer_flow.offer_reschedule_slots(
        CHAT_ID, "pro", whatsapp, state, Messages.Customer.RESCHEDULE_OFFER
    )

    meta = state.set_metadata.await_args.args[1]
    assert meta["reschedule_slots_context"] == {
        "1": slots[0]["_id"],
        "2": slots[1]["_id"],
        "3": slots[2]["_id"],
    }
    call = whatsapp.send_message.await_args
    text = call.args[1]
    picker = call.kwargs["list_picker"]
    assert call.kwargs["reply_ref"] == meta["prompt_ref"]
    assert [r.id for r in picker.rows] == ["1", "2", "3"]
    for row, start in zip(picker.rows, starts):
        assert f"{row.id}. {start.strftime('%d/%m/%Y %H:%M')}" in text
    assert picker.button == Messages.Customer.RESCHEDULE_LIST_BUTTON
    assert picker.section_title == Messages.Customer.RESCHEDULE_LIST_SECTION
    state.set_state.assert_awaited_once()
    assert state.set_state.await_args.args[1] == UserStates.AWAITING_RESCHEDULE_TIME


@pytest.mark.asyncio
async def test_each_new_list_gets_a_new_ref(monkeypatch):
    slots = [{"_id": "a" * 24, "start_time": datetime(2026, 11, 29, 10, 0)}]
    monkeypatch.setattr(
        customer_flow, "get_available_slots", AsyncMock(return_value=slots)
    )
    whatsapp = MagicMock(send_message=AsyncMock())
    state = MagicMock(set_state=AsyncMock(), set_metadata=AsyncMock())

    for _ in range(2):
        await customer_flow.offer_reschedule_slots(
            CHAT_ID, "pro", whatsapp, state, Messages.Customer.RESCHEDULE_OFFER
        )

    first, second = (
        c.kwargs["reply_ref"] for c in whatsapp.send_message.await_args_list
    )
    assert first != second
    assert template_registry.split_reply_ref(f"1#{first}") == ("1", first)


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


def _picker(rows=3, **overrides):
    """A picker of ``rows`` rows (a count, or the rows themselves)."""
    if isinstance(rows, int):
        rows = tuple(
            ListRow(str(i), f"ה׳ 3.12 · 1{i}:00", f"יום חמישי, 03/12/2026 בשעה 1{i}:00")
            for i in range(1, rows + 1)
        )
    fields = {
        "button": Messages.Customer.RESCHEDULE_LIST_BUTTON,
        "section_title": Messages.Customer.RESCHEDULE_LIST_SECTION,
        "rows": rows,
    }
    fields.update(overrides)
    return ListPicker(**fields)


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


@pytest.mark.asyncio
async def test_an_open_window_gets_an_interactive_list_whose_ids_carry_the_ref(
    facade, graph, pages, fake_redis
):
    await _open_window(fake_redis)
    picker = _picker()

    await facade.send_message(
        CHAT_ID, "1. …\n2. …\n3. …", list_picker=picker, reply_ref=REF
    )

    (sent,) = graph
    assert sent["type"] == "interactive"
    interactive = sent["interactive"]
    assert interactive["type"] == "list"
    assert interactive["body"] == {"text": "1. …\n2. …\n3. …"}
    assert interactive["action"]["button"] == picker.button
    (section,) = interactive["action"]["sections"]
    assert section["title"] == picker.section_title
    assert section["rows"] == [
        {"id": f"{r.id}#{REF}", "title": r.title, "description": r.description}
        for r in picker.rows
    ]
    assert pages.call_count == 0


@pytest.mark.asyncio
async def test_a_row_without_a_description_sends_none(facade, graph, fake_redis):
    await _open_window(fake_redis)
    picker = _picker(rows=(ListRow("1", "א"),))

    await facade.send_message(CHAT_ID, "1. א", list_picker=picker)

    (row,) = graph[0]["interactive"]["action"]["sections"][0]["rows"]
    assert row == {"id": "1", "title": "א"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text, picker",
    [
        ("x" * 1025, _picker()),
        ("רשימה", _picker(rows=LIST_ROWS + 1)),
        ("רשימה", _picker(button="ב" * (LIST_BUTTON + 1))),
        ("רשימה", _picker(button="")),
        ("רשימה", _picker(section_title="ס" * (SECTION_TITLE + 1))),
        ("רשימה", _picker(rows=(ListRow("1", "ש" * (ROW_TITLE + 1)),))),
        ("רשימה", _picker(rows=(ListRow("1", "ש", "ת" * (ROW_DESCRIPTION + 1)),))),
        ("רשימה", _picker(rows=(ListRow("1", "א"), ListRow("1", "ב")))),
        ("רשימה", _picker(rows=(ListRow("1", ""),))),
        ("רשימה", _picker(rows=(ListRow("1" * 201, "א"),))),
        ("רשימה", _picker(rows=())),
    ],
    ids=[
        "long-body",
        "eleven-rows",
        "long-opener",
        "no-opener",
        "long-section",
        "long-row-title",
        "long-description",
        "same-id",
        "empty-title",
        "long-id",
        "no-rows",
    ],
)
async def test_what_meta_would_refuse_goes_as_the_plain_text(
    facade, graph, fake_redis, text, picker
):
    await _open_window(fake_redis)

    await facade.send_message(CHAT_ID, text, list_picker=picker)

    assert {sent["type"] for sent in graph} == {"text"}
    assert "".join(sent["text"]["body"] for sent in graph) == text


@pytest.mark.asyncio
async def test_ten_rows_at_every_limit_still_go_as_a_list(facade, graph, fake_redis):
    await _open_window(fake_redis)
    rows = tuple(
        ListRow(str(i), "ש" * (ROW_TITLE - 1) + str(i), "ת" * ROW_DESCRIPTION)
        for i in range(LIST_ROWS)
    )
    picker = ListPicker("ב" * LIST_BUTTON, "ס" * SECTION_TITLE, rows)

    await facade.send_message(CHAT_ID, "x" * 1024, list_picker=picker)

    assert graph[0]["interactive"]["type"] == "list"


@pytest.mark.asyncio
async def test_a_list_to_a_closed_window_behaves_as_text(
    facade, graph, pages, fake_redis
):
    plain = await facade.send_message(CHAT_ID, "רשימה")
    pages_after_plain = pages.call_count
    with_list = await facade.send_message(CHAT_ID, "רשימה", list_picker=_picker())

    assert plain is None and with_list is None
    assert graph == []
    assert pages_after_plain == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [
        {"buttons": CANCEL_CONFIRM_BUTTONS},
        {"template_key": "completion_check"},
    ],
    ids=["with-buttons", "with-template"],
)
async def test_a_list_takes_neither_buttons_nor_a_template(extra):
    facade = WhatsAppFacade(DryRunProvider())
    with pytest.raises(ValueError):
        await facade.send_message(CHAT_ID, "רשימה", list_picker=_picker(), **extra)


@pytest.mark.asyncio
async def test_dry_run_records_the_list_on_its_text_entry():
    provider = DryRunProvider()
    facade = WhatsAppFacade(provider)
    picker = _picker(rows=2)

    await facade.send_message(CHAT_ID, "1. א\n2. ב", list_picker=picker, reply_ref=REF)

    (entry,) = provider.sent
    assert entry["kind"] == "text"
    assert entry["text"] == "1. א\n2. ב"
    assert entry["list"] == {
        "button": picker.button,
        "section_title": picker.section_title,
        "rows": [(f"{r.id}#{REF}", r.title, r.description) for r in picker.rows],
    }


@pytest.mark.asyncio
async def test_the_base_provider_sends_a_list_as_its_text():
    from app.providers.whatsapp.base import WhatsAppProvider

    sent = []

    class TextOnly(WhatsAppProvider):
        async def send_text(self, chat_id, text):
            sent.append(text)
            return {}

        send_file = send_template = send_interactive = get_state = AsyncMock()
        parse_webhook = MagicMock()

    await TextOnly().send_list(CHAT_ID, "1. א", _picker())
    assert sent == ["1. א"]


def test_with_reply_ref_is_a_no_op_without_one():
    picker = _picker()
    assert picker.with_reply_ref(None) is picker
    assert [r.id for r in picker.with_reply_ref(REF).rows] == [
        f"{i}#{REF}" for i in (1, 2, 3)
    ]


# ---------------------------------------------------------------------------
# Inbound: a list row reads back as its digit and its ref
# ---------------------------------------------------------------------------


def _list_reply(row_id):
    return {
        "from": "972501234567",
        "id": "wamid.in",
        "type": "interactive",
        "interactive": {
            "type": "list_reply",
            "list_reply": {"id": row_id, "title": "א׳ 29.11 · 10:00"},
        },
    }


def test_a_list_row_tap_reads_as_its_digit_and_ref():
    message = normalize_meta_message(_list_reply(f"3#{REF}"))
    assert (message.text, message.reply_ref) == ("3", REF)


def test_a_list_row_tap_without_a_ref_reads_as_its_digit():
    message = normalize_meta_message(_list_reply("3"))
    assert (message.text, message.reply_ref) == ("3", None)


def test_a_button_tap_reads_as_its_digit_and_ref():
    message = normalize_meta_message(
        {
            "from": "972501234567",
            "type": "interactive",
            "interactive": {
                "type": "button_reply",
                "button_reply": {"id": f"1#{REF}", "title": "כן, לבטל"},
            },
        }
    )
    assert (message.text, message.reply_ref) == ("1", REF)


# ---------------------------------------------------------------------------
# The stale-tap rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reply_ref, meta, stale",
    [
        (None, {"prompt_ref": REF}, False),
        (None, {}, False),
        (REF, {"prompt_ref": REF}, False),
        (REF, {"prompt_ref": "f" * 24}, True),
        (REF, {}, True),
        (REF, None, True),
    ],
    ids=[
        "typed",
        "typed-no-prompt",
        "this-prompt",
        "older-prompt",
        "no-ref",
        "no-meta",
    ],
)
def test_tap_is_stale(reply_ref, meta, stale):
    assert tap_is_stale(reply_ref, meta) is stale


def test_new_prompt_refs_are_unique_24_hex():
    refs = {customer_flow.new_prompt_ref() for _ in range(50)}
    assert len(refs) == 50
    for ref in refs:
        assert template_registry.split_reply_ref(f"1#{ref}") == ("1", ref)
        assert customer_flow.is_prompt_ref(ref)


def test_no_lead_id_reads_as_a_prompt_ref():
    """A pro offer's ref is a lead's ObjectId; its leading bytes are a real
    timestamp, never the prompt prefix, so it is never taken for a customer
    prompt's tap."""
    from bson import ObjectId

    assert not customer_flow.is_prompt_ref(str(ObjectId()))
    assert not customer_flow.is_prompt_ref(REF)
    assert not customer_flow.is_prompt_ref(None)
    assert not customer_flow.is_prompt_ref("")
    assert customer_flow.is_prompt_ref(customer_flow.new_prompt_ref().upper())


def test_the_states_that_read_a_ref_are_the_four_customer_prompts():
    assert customer_flow.PROMPT_REF_STATES == {
        UserStates.AWAITING_CANCEL_CONFIRMATION,
        UserStates.AWAITING_RESCHEDULE_TIME,
        UserStates.AWAITING_LOYALTY_CONFIRMATION,
        UserStates.AWAITING_NEW_OR_EXISTING,
    }
