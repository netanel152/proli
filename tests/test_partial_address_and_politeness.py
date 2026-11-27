"""Partial-address follow-ups, private homes, and the politeness guard's precision.

The address gate needs five fields (street, number, city, floor, apartment)
before a standard dispatch. Two ways it used to fail the customer:

- a partial reply ("הרצל 15 רמת גן") was answered with a comma-separated field
  list instead of one sentence naming only what was still missing;
- a private home has no floor or apartment, so "בית פרטי" left both empty and
  the gate asked for them again, forever, unless the model happened to fill
  them in.

The AWAITING_ADDRESS tests run the real StateManager and LeadManager on
mongomock/fakeredis; only WhatsApp and the model are faked, and the fake model
never fills floor/apartment for a private-home reply, so the deterministic
backstop is what is under test.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId

import app.services.workflow_service as workflow_service
from app.core.constants import LeadStatus, UserStates
from app.core.messages import Messages
from app.core.prompts import Prompts
from app.services.ai_engine_service import AIResponse, ExtractedData
from app.services.dispatch_guards import (
    HANDLED,
    DispatchContext,
    GuardDeps,
    guard_politeness,
)
from app.services.lead_manager_service import (
    fill_private_home,
    is_address_complete,
    private_home_parts,
)
from app.services.notification_service import format_lead_extra_info
from app.services.state_manager_service import StateManager

F = Messages.Fallbacks
LABELS = Messages.Customer.ADDRESS_FIELD_LABELS
PRIVATE_HOME = {"floor": F.PRIVATE_HOME_FLOOR, "apartment": F.PRIVATE_HOME_APARTMENT}

# The replies the task names, plus the hint's own keyword.
PRIVATE_HOME_REPLIES = [
    "בית פרטי",
    "קרקע",
    "צמוד קרקע",
    "וילה",
    "אין קומה",
    "אין דירה",
    "זה בית פרטי, אין קומה",
]


def _parts(**kw):
    return SimpleNamespace(
        **{
            f: kw.get(f)
            for f in ("street", "street_number", "city", "floor", "apartment")
        }
    )


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------


@pytest.mark.parametrize("text", PRIVATE_HOME_REPLIES)
def test_private_home_replies_settle_floor_and_apartment(text):
    assert private_home_parts(text) == PRIVATE_HOME


@pytest.mark.parametrize("text", ["קומת קרקע", "הדירה בקומת קרקע", "ground floor"])
def test_ground_floor_in_a_building_settles_only_the_floor(text):
    assert private_home_parts(text) == {"floor": F.GROUND_FLOOR}


@pytest.mark.parametrize(
    "text",
    [
        "הרצל 15 רמת גן",
        "קומה 3 דירה 12",
        "יש לי דירה",  # "דירה" alone is not "אין דירה"
        "קרקעית הכיור נסדקה",  # whole-token: not "קרקע"
        "",
        None,
    ],
)
def test_other_text_settles_nothing(text):
    assert private_home_parts(text) == {}


def test_fill_private_home_never_overwrites_a_given_number():
    parts = {"street": "הרצל", "floor": "2", "apartment": ""}
    assert fill_private_home(parts, "וילה") == {
        "street": "הרצל",
        "floor": "2",
        "apartment": F.PRIVATE_HOME_APARTMENT,
    }
    assert fill_private_home({"apartment": "4"}, "קומת קרקע") == {
        "apartment": "4",
        "floor": F.GROUND_FLOOR,
    }


def test_fill_private_home_without_a_phrase_is_identity():
    parts = {"street": "הרצל", "floor": None}
    assert fill_private_home(parts, "הרצל 15") == parts


def test_partial_address_asks_only_for_the_missing_fields_in_one_sentence():
    ok, reason = is_address_complete(
        _parts(street="הרצל", street_number="15", city="רמת גן")
    )
    assert not ok
    assert reason == (
        Messages.Customer.ADDRESS_MISSING_PARTS.format(
            missing_fields=f"{LABELS['floor']} ו{LABELS['apartment']}"
        )
        + Messages.Customer.ADDRESS_PRIVATE_HOME_HINT
    )
    for present in ("street", "city"):
        assert LABELS[present] not in reason.replace(LABELS["street_number"], "")


def test_missing_fields_are_joined_as_a_hebrew_list():
    _, two = is_address_complete(_parts(street="הרצל", floor="2", apartment="5"))
    assert two == Messages.Customer.ADDRESS_MISSING_PARTS.format(
        missing_fields=f"{LABELS['street_number']} ו{LABELS['city']}"
    )
    _, three = is_address_complete(_parts(floor="2", apartment="5"))
    assert three == Messages.Customer.ADDRESS_MISSING_PARTS.format(
        missing_fields=(
            f"{LABELS['street']}, {LABELS['street_number']} ו{LABELS['city']}"
        )
    )


def test_private_home_hint_only_when_the_apartment_is_missing():
    _, floor_only = is_address_complete(
        _parts(street="הרצל", street_number="15", city="רמת גן", apartment="4")
    )
    assert Messages.Customer.ADDRESS_PRIVATE_HOME_HINT not in floor_only
    assert floor_only == Messages.Customer.ADDRESS_MISSING_PARTS.format(
        missing_fields=LABELS["floor"]
    )


def test_the_hint_advertises_a_reply_the_backstop_reads():
    advertised = Messages.Keywords.PRIVATE_HOME_PHRASES[0]
    assert advertised in Messages.Customer.ADDRESS_PRIVATE_HOME_HINT
    assert private_home_parts(advertised) == PRIVATE_HOME


def test_private_home_passes_the_five_field_gate():
    parts = fill_private_home(
        {"street": "הרצל", "street_number": "15", "city": "רמת גן"}, "בית פרטי"
    )
    assert is_address_complete(_parts(**parts)) == (True, "")


def test_pro_card_says_private_home_instead_of_floor_and_apartment():
    assert format_lead_extra_info(PRIVATE_HOME) == Messages.Pro.EXTRA_INFO_PRIVATE_HOME
    assert format_lead_extra_info(
        {"floor": "3", "apartment": "12"}
    ) == Messages.Pro.EXTRA_INFO_LINE.format(floor="3", apartment="12")


@pytest.mark.parametrize("prompt", ["DISPATCHER_SYSTEM", "PRO_BASE_SYSTEM"])
def test_prompts_give_the_model_the_same_private_home_values(prompt):
    text = getattr(Prompts, prompt)
    assert f'floor = "{F.PRIVATE_HOME_FLOOR}"' in text
    assert f'apartment = "{F.PRIVATE_HOME_APARTMENT}"' in text


def test_pro_response_backstop_fills_empty_floor_and_apartment():
    response = SimpleNamespace(
        extracted_data=SimpleNamespace(floor=None, apartment="", street="הרצל")
    )
    workflow_service._apply_private_home(response, "בית פרטי None")
    assert response.extracted_data.floor == F.PRIVATE_HOME_FLOOR
    assert response.extracted_data.apartment == F.PRIVATE_HOME_APARTMENT

    given = SimpleNamespace(extracted_data=SimpleNamespace(floor="1", apartment="2"))
    workflow_service._apply_private_home(given, "וילה")
    assert (given.extracted_data.floor, given.extracted_data.apartment) == ("1", "2")

    workflow_service._apply_private_home(SimpleNamespace(), "וילה")  # no crash


# --------------------------------------------------------------------------
# AWAITING_ADDRESS end to end (guard_awaiting_address)
# --------------------------------------------------------------------------


def _fake_model_reply(history, user_text, **_):
    """Reads street/number/city and an explicit floor/apartment; never fills
    floor/apartment for a private-home reply."""
    data = {"city": None, "issue": None}
    text = user_text or ""
    if "הרצל 15" in text:
        data.update(street="הרצל", street_number="15")
    if "רמת גן" in text:
        data["city"] = "רמת גן"
    if "דירה 4" in text:
        data["apartment"] = "4"
    return AIResponse(
        reply_to_user="קיבלנו",
        extracted_data=ExtractedData(full_address=None, appointment_time=None, **data),
        transcription=None,
        is_deal=False,
    )


@pytest.fixture
def turn(monkeypatch):
    mock_wa = MagicMock()
    mock_wa.send_message = AsyncMock(return_value=True)
    mock_wa.send_location_link = AsyncMock()
    mock_wa.indicate_typing = AsyncMock()
    monkeypatch.setattr(workflow_service, "whatsapp", mock_wa)
    monkeypatch.setattr(workflow_service, "has_consent", AsyncMock(return_value=True))
    mock_ai = MagicMock()
    mock_ai.analyze_conversation = AsyncMock(side_effect=_fake_model_reply)
    mock_ai.detect_service_intent = AsyncMock(return_value=True)
    monkeypatch.setattr(workflow_service, "ai", mock_ai)

    async def send(chat_id, text):
        mock_wa.send_message.reset_mock()
        await workflow_service.process_incoming_message(chat_id, text)
        return [str(c.args[1]) for c in mock_wa.send_message.call_args_list]

    return send


async def _awaiting_address(db, **stored):
    chat_id = f"{ObjectId()}@c.us"
    lead_id = ObjectId()
    # No pro_id: once complete, _continue_after_address leaves the lead to the
    # next turn, so these tests see the gate and nothing after it.
    await db.leads.insert_one(
        {
            "_id": lead_id,
            "chat_id": chat_id,
            "status": LeadStatus.CONTACTED,
            "issue_type": "נזילה",
            "city": "רמת גן",
            **stored,
        }
    )
    await StateManager.set_state(chat_id, UserStates.AWAITING_ADDRESS)
    return chat_id, lead_id


@pytest.mark.asyncio
async def test_partial_reply_is_asked_only_for_floor_and_apartment(turn, mock_db):
    chat_id, lead_id = await _awaiting_address(mock_db)

    sent = await turn(chat_id, "הרצל 15 רמת גן")

    _, expected = is_address_complete(
        _parts(street="הרצל", street_number="15", city="רמת גן")
    )
    assert sent == [expected]
    assert await StateManager.get_state(chat_id) == UserStates.AWAITING_ADDRESS
    lead = await mock_db.leads.find_one({"_id": lead_id})
    assert (lead["street"], lead["street_number"]) == ("הרצל", "15")


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", PRIVATE_HOME_REPLIES)
async def test_private_home_reply_completes_the_address(turn, mock_db, reply):
    chat_id, lead_id = await _awaiting_address(
        mock_db, street="הרצל", street_number="15", address_city="רמת גן"
    )

    sent = await turn(chat_id, reply)

    assert sent == [Messages.Customer.ADDRESS_SAVED]
    lead = await mock_db.leads.find_one({"_id": lead_id})
    assert (lead["floor"], lead["apartment"]) == (
        F.PRIVATE_HOME_FLOOR,
        F.PRIVATE_HOME_APARTMENT,
    )
    assert lead["full_address"] == "הרצל 15, רמת גן"
    assert await StateManager.get_state(chat_id) != UserStates.AWAITING_ADDRESS


@pytest.mark.asyncio
async def test_whole_address_and_private_home_in_one_reply(turn, mock_db):
    chat_id, lead_id = await _awaiting_address(mock_db)

    sent = await turn(chat_id, "הרצל 15 רמת גן, בית פרטי")

    assert sent == [Messages.Customer.ADDRESS_SAVED]
    assert (await mock_db.leads.find_one({"_id": lead_id}))[
        "full_address"
    ] == "הרצל 15, רמת גן"


@pytest.mark.asyncio
async def test_ground_floor_in_a_building_still_asks_for_the_apartment(turn, mock_db):
    chat_id, lead_id = await _awaiting_address(
        mock_db, street="הרצל", street_number="15", address_city="רמת גן"
    )

    sent = await turn(chat_id, "קומת קרקע")

    assert sent == [
        Messages.Customer.ADDRESS_MISSING_PARTS.format(
            missing_fields=LABELS["apartment"]
        )
        + Messages.Customer.ADDRESS_PRIVATE_HOME_HINT
    ]
    lead = await mock_db.leads.find_one({"_id": lead_id})
    assert lead["floor"] == F.GROUND_FLOOR and not lead.get("apartment")
    assert await StateManager.get_state(chat_id) == UserStates.AWAITING_ADDRESS


@pytest.mark.asyncio
async def test_private_home_never_overwrites_a_stated_apartment(turn, mock_db):
    chat_id, lead_id = await _awaiting_address(
        mock_db, street="הרצל", street_number="15", address_city="רמת גן"
    )

    await turn(chat_id, "וילה, דירה 4")

    lead = await mock_db.leads.find_one({"_id": lead_id})
    assert (lead["floor"], lead["apartment"]) == (F.PRIVATE_HOME_FLOOR, "4")


# --------------------------------------------------------------------------
# guard_politeness precision (Guard 7)
# --------------------------------------------------------------------------


@pytest.fixture
def deps():
    return GuardDeps(
        whatsapp=AsyncMock(),
        state_manager=AsyncMock(),
        context_manager=AsyncMock(),
        users_collection=AsyncMock(),
        security=MagicMock(),
        settings=SimpleNamespace(ADMIN_PHONE="972524828796"),
    )


def _ctx(text, state=UserStates.IDLE):
    return DispatchContext(
        chat_id="972500000001@c.us",
        user_text=text,
        media_url=None,
        normalized_text=(text or "").strip().lower(),
        is_emergency_detected=False,
        current_state=state,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["תודה", "תודה רבה", "אחלה", "  Thank you  "])
async def test_standalone_thanks_is_answered(deps, text):
    assert await guard_politeness(_ctx(text), deps) is HANDLED
    deps.whatsapp.send_message.assert_awaited_once_with(
        "972500000001@c.us", Messages.Customer.YOU_ARE_WELCOME
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "תודה, מתי הוא מגיע?",
        "תודה, שכחתי לציין שזה קומה 3",
        "תודה רבה, יש לי עוד נזילה במטבח",
        "אחלה, הרצל 15 רמת גן",
        "תודה?",
        "מעולה, ומה המחיר?",
    ],
)
async def test_thanks_with_a_question_or_new_detail_falls_through(deps, text):
    assert await guard_politeness(_ctx(text), deps) is None
    deps.whatsapp.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_thanks_from_a_pro_is_not_intercepted(deps):
    assert await guard_politeness(_ctx("תודה", UserStates.PRO_MODE), deps) is None
    deps.whatsapp.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_thanks_with_a_floor_reaches_the_address_guard(turn, mock_db):
    """The mixed message from the task, end to end: in AWAITING_ADDRESS it is
    read as an address reply, not swallowed by "בכיף!"."""
    chat_id, _ = await _awaiting_address(
        mock_db, street="הרצל", street_number="15", address_city="רמת גן"
    )

    sent = await turn(chat_id, "תודה, שכחתי לציין שזה קומה 3")

    assert Messages.Customer.YOU_ARE_WELCOME not in sent
    assert await StateManager.get_state(chat_id) == UserStates.AWAITING_ADDRESS
