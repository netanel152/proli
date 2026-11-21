"""An approval that could not reserve the requested hour says so.

`pro_flow._approve_lead` writes BOOKED first and only then asks
`matching_service.book_slot_for_lead` for a slot within
±`SLOT_SEARCH_WINDOW_HOURS` of the requested time. It used to read the answer
only for `booked_slot_id` and one calendar line in the pro's reply: a pro with
no free slot near the hour got a reply that merely lacked that line, the
customer was told the requested hour anyway, and two customers approved onto
one free slot were both told the same hour. A slot claimed at another hour was
recorded while the lead and both messages kept the original one.

The owner's call: the approval still goes through, the pro is told plainly
that no hour is held, the lead carries `slot_unreserved`, and the customer is
never told an hour nobody reserved. A slot at another hour moves the lead's
time fields, and both sides are told that hour.

The leads here carry a concrete `appointment_datetime` (the e2e chat fixtures
all take the ASAP branch), so they are seeded directly through
`world.awaiting_approval_job`.
"""

from datetime import timedelta

import pytest

from app.core.constants import LeadStatus
from app.core.datetime_utils import stored_utc_to_il
from app.core.messages import Messages
from tests.copy_util import longest_static_chunk
from tests.e2e import reserved_numbers as R

APPROVE = Messages.Keywords.APPROVE_COMMANDS[0]


def _hour(world, offset_hours: int):
    return world.anchor.replace(minute=0, second=0, microsecond=0) + timedelta(
        hours=offset_hours
    )


def _shown(dt) -> str:
    """How the lead's display time renders an instant (the reschedule format)."""
    return stored_utc_to_il(dt).strftime("%d/%m/%Y %H:%M")


async def _offer_at(world, pro, requested, chat_id=None):
    """A NEW lead offered to ``pro`` for the concrete hour ``requested``."""
    overrides = {
        "appointment_datetime": requested,
        "appointment_time": _shown(requested),
    }
    if chat_id:
        overrides["chat_id"] = chat_id
    return await world.awaiting_approval_job(pro, **overrides)


def _customer_found_message(world, chat_id):
    return world.recorder.assert_text_to(
        chat_id, longest_static_chunk(Messages.Customer.PRO_FOUND)
    )


@pytest.mark.asyncio
async def test_approval_with_no_free_slot_is_booked_flagged_and_said_plainly(world):
    await world.add_pro(R.PRO_PRIMARY, name="נתנאל אינסטלציה", with_slots=0)
    await world.grant_consent()
    pro = world.pros[R.PRO_PRIMARY]
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    requested = _hour(world, 2)
    lead = await _offer_at(world, pro, requested)

    await world.send(APPROVE, chat_id=pro_chat)

    booked = await world.lead_by_id(lead["_id"])
    assert booked["status"] == LeadStatus.BOOKED
    assert booked["slot_unreserved"] is True
    assert not booked.get("booked_slot_id")

    reply = world.recorder.assert_text_to(pro_chat, Messages.Pro.APPROVE_SUCCESS)
    assert Messages.Pro.APPROVE_NO_SLOT in reply.body
    assert Messages.Pro.CALENDAR_UPDATE_SUCCESS not in reply.body

    found = _customer_found_message(world, world.customer)
    assert Messages.Fallbacks.TIME_TO_COORDINATE in found.body
    assert _shown(requested) not in found.body, "an hour nobody reserved"


@pytest.mark.asyncio
async def test_two_customers_one_slot_only_the_first_is_told_the_hour(world):
    await world.add_pro(R.PRO_PRIMARY, name="נתנאל אינסטלציה", with_slots=0)
    pro = world.pros[R.PRO_PRIMARY]
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    requested = _hour(world, 2)
    (slot,) = await world.add_slots(pro, count=1, first_offset_hours=2)
    customer_b = R.chat(R.CUSTOMER_B)
    await world.grant_consent()
    await world.grant_consent(customer_b)
    first = await _offer_at(world, pro, requested)
    second = await _offer_at(world, pro, requested, chat_id=customer_b)

    await world.tap(
        f"{APPROVE}#{first['_id']}", chat_id=pro_chat, id_message="wamid.e2e-tap-1"
    )
    await world.tap(
        f"{APPROVE}#{second['_id']}", chat_id=pro_chat, id_message="wamid.e2e-tap-2"
    )

    first_doc = await world.lead_by_id(first["_id"])
    second_doc = await world.lead_by_id(second["_id"])
    assert first_doc["status"] == second_doc["status"] == LeadStatus.BOOKED
    assert first_doc["booked_slot_id"] == slot["_id"]
    assert not first_doc.get("slot_unreserved")
    assert second_doc["slot_unreserved"] is True
    assert not second_doc.get("booked_slot_id")

    replies = world.recorder.bodies_to(pro_chat)
    assert any(Messages.Pro.CALENDAR_UPDATE_SUCCESS in r for r in replies)
    assert any(Messages.Pro.APPROVE_NO_SLOT in r for r in replies)

    assert _shown(requested) in _customer_found_message(world, world.customer).body
    second_found = _customer_found_message(world, customer_b)
    assert _shown(requested) not in second_found.body, "the hour is the first's"
    assert Messages.Fallbacks.TIME_TO_COORDINATE in second_found.body


@pytest.mark.asyncio
async def test_a_slot_at_another_hour_moves_the_lead_and_both_are_told(world):
    await world.add_pro(R.PRO_PRIMARY, name="נתנאל אינסטלציה", with_slots=0)
    await world.grant_consent()
    pro = world.pros[R.PRO_PRIMARY]
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    requested = _hour(world, 2)
    (slot,) = await world.add_slots(pro, count=1, first_offset_hours=3)
    lead = await _offer_at(world, pro, requested)

    await world.send(APPROVE, chat_id=pro_chat)

    booked = await world.lead_by_id(lead["_id"])
    assert booked["status"] == LeadStatus.BOOKED
    assert booked["booked_slot_id"] == slot["_id"]
    assert not booked.get("slot_unreserved")
    claimed = _shown(slot["start_time"])
    assert booked["appointment_time"] == claimed
    assert _shown(booked["appointment_datetime"]) == claimed

    reply = world.recorder.assert_text_to(pro_chat, Messages.Pro.APPROVE_SUCCESS)
    assert (
        Messages.Pro.CALENDAR_UPDATE_MOVED.format(appointment_time=claimed)
        in reply.body
    )
    found = _customer_found_message(world, world.customer)
    assert claimed in found.body
    assert _shown(requested) not in found.body


@pytest.mark.asyncio
async def test_a_reschedule_onto_a_slot_clears_the_flag(world):
    """The flag describes the booking: once the lead holds a slot it goes."""
    await world.add_pro(R.PRO_PRIMARY, name="נתנאל אינסטלציה", with_slots=0)
    await world.grant_consent()
    pro = world.pros[R.PRO_PRIMARY]
    lead = await world.booked_job(pro, slot_unreserved=True)
    assert lead["booked_slot_id"] is None
    await world.add_slots(pro, count=2, first_offset_hours=5)

    await world.send("אני צריך מועד אחר")
    await world.send("1")

    updated = await world.lead_by_id(lead["_id"])
    assert updated["booked_slot_id"]
    assert "slot_unreserved" not in updated
