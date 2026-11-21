"""PRO-104 and PRO-105 (a, b): the reply buttons a flow sends, tapped end to end.

A tap is a Meta ``interactive``/``button_reply`` webhook (``world.tap``), read by
the real route, queued, and run by the real worker task. What this pins:

* PRO-104: a pro's tap on an offer's אשר/דחה carries its lead (``#<lead_id>``)
  and acts on that lead alone, never opening the PRO-212 picker; a tap on an
  offer that is no longer this pro's does nothing and says so. Typing אשר is
  unchanged.
* PRO-105 (a): the consent request offers two buttons whose ids are the words
  the gate accepts typed, and a tap gives consent exactly as typing does, with
  nothing stored before it.
* PRO-105 (b): the completion check offers three buttons whose ids the
  completion handler reads.
"""

from datetime import timedelta

import pytest

from app.core.constants import LeadStatus, UserStates
from app.core.messages import Messages
from app.core.phone import to_local_phone
from app.providers.whatsapp import template_registry
from app.services import notification_service, workflow_service
from app.services.customer_flow import send_customer_completion_check
from app.services.dispatch_guards import CONSENT_BUTTONS
from tests.copy_util import static_prefix
from tests.e2e import reserved_numbers as R
from tests.e2e.ai_replay import reply

APPROVE_WORD = Messages.Keywords.APPROVE_COMMANDS[0]
REJECT_WORD = Messages.Keywords.REJECT_COMMANDS[0]
CUSTOMER_A = R.chat(R.CUSTOMER)
CUSTOMER_B = R.chat(R.CUSTOMER_B)


def _id(word, lead):
    return template_registry.with_reply_ref(word, str(lead["_id"]))


async def _pro_with_two_offers(world):
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    older = await world.awaiting_approval_job(
        pro, chat_id=CUSTOMER_A, created_at=world.anchor - timedelta(hours=2)
    )
    newer = await world.awaiting_approval_job(
        pro, chat_id=CUSTOMER_B, customer_name="רון", city="חולון"
    )
    return pro, world.pro_chat(R.PRO_PRIMARY), older, newer


def _buttons_to(world, chat_id):
    return [s.payload.get("buttons") for s in world.recorder.to(chat_id)]


# ===========================================================================
# PRO-104: the offer's buttons name their lead
# ===========================================================================


@pytest.mark.asyncio
async def test_an_offer_carries_approve_and_reject_buttons_naming_its_lead(world):
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    lead = await world.awaiting_approval_job(pro, chat_id=CUSTOMER_A)

    assert await notification_service.notify_pro_new_lead(
        lead, pro, workflow_service.whatsapp
    )

    sent = world.recorder.last_to(world.pro_chat(R.PRO_PRIMARY))
    assert sent.payload["buttons"] == [
        (Messages.Templates.Buttons.APPROVE, _id(APPROVE_WORD, lead)),
        (Messages.Templates.Buttons.REJECT, _id(REJECT_WORD, lead)),
    ]


@pytest.mark.asyncio
async def test_a_tap_with_two_offers_books_the_tapped_one_without_a_picker(world):
    _, pro_chat, older, newer = await _pro_with_two_offers(world)

    await world.tap(_id(APPROVE_WORD, newer), chat_id=pro_chat)

    assert (await world.lead_by_id(newer["_id"]))["status"] == LeadStatus.BOOKED
    assert (await world.lead_by_id(older["_id"]))["status"] == LeadStatus.NEW
    assert await world.state(pro_chat) != UserStates.PRO_SELECTING_JOB_TO_APPROVE
    sent = "\n".join(world.recorder.bodies_to(pro_chat))
    assert Messages.Pro.APPROVE_SUCCESS in sent
    assert to_local_phone(CUSTOMER_B) in sent
    assert to_local_phone(CUSTOMER_A) not in sent
    world.recorder.assert_silent(CUSTOMER_A, "their offer was not the one tapped")


@pytest.mark.asyncio
async def test_a_reject_tap_with_two_offers_rejects_only_the_tapped_one(world):
    pro, pro_chat, older, newer = await _pro_with_two_offers(world)

    await world.tap(_id(REJECT_WORD, older), chat_id=pro_chat)

    assert pro["_id"] in (await world.lead_by_id(older["_id"]))["rejected_by"]
    untouched = await world.lead_by_id(newer["_id"])
    assert untouched["status"] == LeadStatus.NEW
    assert untouched["pro_id"] == pro["_id"]
    assert not untouched.get("rejected_by")
    assert await world.state(pro_chat) != UserStates.PRO_SELECTING_JOB_TO_REJECT


@pytest.mark.asyncio
async def test_typed_approve_with_two_offers_still_opens_the_picker(world):
    _, pro_chat, older, newer = await _pro_with_two_offers(world)

    await world.deliver(APPROVE_WORD, chat_id=pro_chat)

    for lead in (older, newer):
        assert (await world.lead_by_id(lead["_id"]))["status"] == LeadStatus.NEW
    await world.assert_state(UserStates.PRO_SELECTING_JOB_TO_APPROVE, chat_id=pro_chat)
    world.recorder.assert_text_to(
        pro_chat, static_prefix(Messages.Pro.SELECT_JOB_TO_APPROVE)
    )


@pytest.mark.asyncio
async def test_a_tap_while_the_picker_is_open_still_acts_on_its_own_lead(world):
    _, pro_chat, older, newer = await _pro_with_two_offers(world)
    await world.deliver(APPROVE_WORD, chat_id=pro_chat, id_message="wamid.typed")

    await world.tap(_id(APPROVE_WORD, older), chat_id=pro_chat)

    assert (await world.lead_by_id(older["_id"]))["status"] == LeadStatus.BOOKED
    assert (await world.lead_by_id(newer["_id"]))["status"] == LeadStatus.NEW


@pytest.mark.asyncio
@pytest.mark.parametrize("moved_to", ["another_pro", "booked", "cancelled"])
async def test_a_tap_on_an_offer_no_longer_this_pros_books_nothing(world, moved_to):
    pro, pro_chat, older, newer = await _pro_with_two_offers(world)
    if moved_to == "another_pro":
        change = {"pro_id": world.pros[R.PRO_SECONDARY]["_id"]}
    elif moved_to == "booked":
        change = {"status": LeadStatus.BOOKED}
    else:
        change = {"status": LeadStatus.CANCELLED}
    await world.db.leads.update_one({"_id": newer["_id"]}, {"$set": change})

    await world.tap(_id(APPROVE_WORD, newer), chat_id=pro_chat)

    after = await world.lead_by_id(newer["_id"])
    assert after["status"] == change.get("status", LeadStatus.NEW)
    assert after["pro_id"] == change.get("pro_id", pro["_id"])
    # Never the other waiting offer instead.
    assert (await world.lead_by_id(older["_id"]))["status"] == LeadStatus.NEW
    world.recorder.assert_text_to(pro_chat, Messages.Pro.SELECTION_OFFER_LIST_CHANGED)
    sent = "\n".join(world.recorder.bodies_to(pro_chat))
    assert to_local_phone(CUSTOMER_A) not in sent
    assert to_local_phone(CUSTOMER_B) not in sent
    assert await world.state(pro_chat) != UserStates.PRO_SELECTING_JOB_TO_APPROVE


@pytest.mark.asyncio
@pytest.mark.parametrize("moved_to", ["another_pro", "booked", "cancelled"])
async def test_a_reject_tap_on_an_offer_no_longer_this_pros_rejects_nothing(
    world, moved_to
):
    pro, pro_chat, older, newer = await _pro_with_two_offers(world)
    if moved_to == "another_pro":
        change = {"pro_id": world.pros[R.PRO_SECONDARY]["_id"]}
    elif moved_to == "booked":
        change = {"status": LeadStatus.BOOKED}
    else:
        change = {"status": LeadStatus.CANCELLED}
    await world.db.leads.update_one({"_id": newer["_id"]}, {"$set": change})
    before = await world.lead_by_id(newer["_id"])

    await world.tap(_id(REJECT_WORD, newer), chat_id=pro_chat)

    # Not rejected, not rematched, not escalated: the lead is exactly as it was.
    assert await world.lead_by_id(newer["_id"]) == before
    assert not before.get("rejected_by")
    # Never the other waiting offer instead.
    untouched = await world.lead_by_id(older["_id"])
    assert untouched["status"] == LeadStatus.NEW
    assert untouched["pro_id"] == pro["_id"]
    assert not untouched.get("rejected_by")
    world.recorder.assert_text_to(pro_chat, Messages.Pro.SELECTION_OFFER_LIST_CHANGED)
    world.recorder.assert_silent(CUSTOMER_A, "nothing happened to their request")
    world.recorder.assert_silent(CUSTOMER_B, "nothing happened to their request")
    assert await world.state(pro_chat) != UserStates.PRO_SELECTING_JOB_TO_REJECT


@pytest.mark.asyncio
async def test_a_tap_on_the_only_offer_once_reassigned_answers_no_pending(world):
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    lead = await world.awaiting_approval_job(
        world.pros[R.PRO_SECONDARY], chat_id=CUSTOMER_A
    )

    await world.tap(_id(APPROVE_WORD, lead), chat_id=pro_chat)

    after = await world.lead_by_id(lead["_id"])
    assert after["status"] == LeadStatus.NEW
    assert after["pro_id"] == world.pros[R.PRO_SECONDARY]["_id"]
    assert pro["_id"] != after["pro_id"]
    world.recorder.assert_text_to(pro_chat, Messages.Pro.NO_PENDING_APPROVALS)


# ===========================================================================
# PRO-105 (a): consent
# ===========================================================================


@pytest.mark.asyncio
async def test_the_consent_request_offers_the_two_words_the_gate_accepts(world):
    await world.deliver("שלום")

    sent = world.recorder.last_to(world.customer)
    assert sent.body.startswith(Messages.Consent.REQUEST)
    ids = [button_id for _, button_id in sent.payload["buttons"]]
    assert ids == [
        Messages.Consent.ACCEPT_KEYWORDS[0],
        Messages.Consent.DECLINE_KEYWORDS[0],
    ]
    assert ids[0] in Messages.Consent.ACCEPT_KEYWORDS
    assert ids[1] in Messages.Consent.DECLINE_KEYWORDS
    assert sent.payload["buttons"] == [(b.label, b.payload) for b in CONSENT_BUTTONS]


@pytest.mark.asyncio
async def test_nothing_is_stored_before_the_consent_tap(world):
    await world.deliver("יש לי נזילה במטבח")

    assert await world.db.messages.count_documents({}) == 0
    assert await world.db.consent.count_documents({}) == 0
    assert await world.lead() is None
    assert world.ai.calls == []


@pytest.mark.asyncio
async def test_a_consent_tap_gives_consent_exactly_as_typing_does(world):
    await world.deliver("שלום", id_message="wamid.first")
    world.ai.script(reply("במה אפשר לעזור?"))

    await world.tap(CONSENT_BUTTONS[0].payload)

    consent = await world.db.consent.find_one({"chat_id": world.customer})
    assert consent and consent["accepted"] is True
    assert [call["user_text"] for call in world.ai.calls] == ["שלום"]


@pytest.mark.asyncio
async def test_a_decline_tap_declines_and_stores_nothing(world):
    await world.deliver("יש לי נזילה במטבח", id_message="wamid.first")

    await world.tap(CONSENT_BUTTONS[1].payload)

    world.recorder.assert_text_to(world.customer, Messages.Consent.DECLINED)
    assert await world.db.messages.count_documents({}) == 0
    assert world.ai.calls == []


# ===========================================================================
# PRO-105 (b): the completion check
# ===========================================================================


@pytest.mark.asyncio
async def test_the_completion_check_offers_three_buttons_the_handler_reads(world):
    await world.standard_cast()
    lead = await world.booked_job(world.pros[R.PRO_PRIMARY])

    await send_customer_completion_check(
        str(lead["_id"]), workflow_service.whatsapp, triggered_by="operator"
    )

    sent = world.recorder.last_to(world.customer)
    assert sent.payload["buttons"] == [
        (Messages.Templates.Buttons.JOB_DONE, "1"),
        (Messages.Templates.Buttons.NOT_YET, "2"),
        (Messages.Templates.Buttons.PRO_NO_SHOW, "3"),
    ]


@pytest.mark.asyncio
async def test_a_job_done_tap_completes_the_job(world):
    await world.standard_cast()
    lead = await world.booked_job(world.pros[R.PRO_PRIMARY])
    await send_customer_completion_check(
        str(lead["_id"]), workflow_service.whatsapp, triggered_by="operator"
    )

    await world.tap("1")

    after = await world.lead_by_id(lead["_id"])
    assert after["status"] == LeadStatus.COMPLETED
    world.recorder.assert_text_to(
        world.customer, static_prefix(Messages.Customer.COMPLETION_ACK)
    )
