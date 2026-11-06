"""PRO-212: *אשר* must act on the lead the pro means, and a pro on break can answer.

Both defects were read from the code and then reproduced here, through the
production orchestrator, before the fix.
"""

from datetime import timedelta

import pytest

from app.core.constants import LeadStatus, UserStates
from app.core.messages import Messages
from app.core.phone import to_local_phone
from app.services.matching_service import APPROVED_PRO_FILTER
from tests.copy_util import static_prefix
from tests.e2e import reserved_numbers as R
from tests.e2e.ai_replay import reply

APPROVE_WORD = Messages.Keywords.APPROVE_COMMANDS[0]
APPROVE_TOKEN = f"*{APPROVE_WORD}*"
REJECT_WORD = Messages.Keywords.REJECT_COMMANDS[0]
FINISH_WORD = Messages.Keywords.FINISH_COMMANDS[0]
STILL_WORKING_WORD = "עדיין עובד"
RESUME_WORD = "חזרתי"
SOS_WORD = "נציג"
CUSTOMER_A = R.chat(R.CUSTOMER)
CUSTOMER_B = R.chat(R.CUSTOMER_B)


async def _pro_with_two_offers(world):
    """Older offer to customer A, newer offer to customer B."""
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    older = await world.awaiting_approval_job(
        pro,
        chat_id=CUSTOMER_A,
        created_at=world.anchor - timedelta(hours=2),
    )
    newer = await world.awaiting_approval_job(
        pro, chat_id=CUSTOMER_B, customer_name="רון", city="חולון"
    )
    return pro, world.pro_chat(R.PRO_PRIMARY), older, newer


async def _number_of(world, pro_chat, context_key, lead):
    mapping = (await world.metadata(pro_chat))[context_key]
    return next(k for k, v in mapping.items() if v == str(lead["_id"]))


def _bodies(world, chat_id):
    return "\n".join(world.recorder.bodies_to(chat_id))


# ===========================================================================
# Two offers: אשר asks which one, and only the chosen customer's details arrive
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("word", [APPROVE_WORD, "1"])
async def test_approve_with_two_offers_books_nothing_until_the_pro_chooses(world, word):
    _, pro_chat, older, newer = await _pro_with_two_offers(world)

    await world.send(word, chat_id=pro_chat)

    for lead in (older, newer):
        assert (await world.lead_by_id(lead["_id"]))["status"] == LeadStatus.NEW
    sent = _bodies(world, pro_chat)
    assert to_local_phone(CUSTOMER_A) not in sent
    assert to_local_phone(CUSTOMER_B) not in sent
    world.recorder.assert_silent(CUSTOMER_A, "nothing was approved yet")
    world.recorder.assert_silent(CUSTOMER_B, "nothing was approved yet")
    await world.assert_state(UserStates.PRO_SELECTING_JOB_TO_APPROVE, chat_id=pro_chat)
    world.recorder.assert_text_to(
        pro_chat, static_prefix(Messages.Pro.SELECT_JOB_TO_APPROVE)
    )


@pytest.mark.asyncio
async def test_approve_picker_books_the_chosen_lead_and_reveals_only_its_customer(
    world,
):
    """The older lead, which the old newest-first read could never have booked."""
    _, pro_chat, older, newer = await _pro_with_two_offers(world)

    await world.send(APPROVE_WORD, chat_id=pro_chat)
    number = await _number_of(world, pro_chat, "approving_jobs_context", older)
    await world.send(number, chat_id=pro_chat)

    assert (await world.lead_by_id(older["_id"]))["status"] == LeadStatus.BOOKED
    assert (await world.lead_by_id(newer["_id"]))["status"] == LeadStatus.NEW
    sent = _bodies(world, pro_chat)
    assert to_local_phone(CUSTOMER_A) in sent
    assert to_local_phone(CUSTOMER_B) not in sent
    assert world.recorder.bodies_to(CUSTOMER_A)
    world.recorder.assert_silent(CUSTOMER_B, "their lead was not the one approved")
    assert await world.state(pro_chat) != UserStates.PRO_SELECTING_JOB_TO_APPROVE


@pytest.mark.asyncio
async def test_reject_with_two_offers_touches_nothing_until_the_pro_chooses(world):
    _, pro_chat, older, newer = await _pro_with_two_offers(world)

    await world.send(REJECT_WORD, chat_id=pro_chat)

    for lead in (older, newer):
        assert not (await world.lead_by_id(lead["_id"])).get("rejected_by")
    await world.assert_state(UserStates.PRO_SELECTING_JOB_TO_REJECT, chat_id=pro_chat)
    world.recorder.assert_text_to(
        pro_chat, static_prefix(Messages.Pro.SELECT_JOB_TO_REJECT)
    )


@pytest.mark.asyncio
async def test_reject_picker_rejects_only_the_chosen_lead(world):
    pro, pro_chat, older, newer = await _pro_with_two_offers(world)

    await world.send(REJECT_WORD, chat_id=pro_chat)
    number = await _number_of(world, pro_chat, "rejecting_jobs_context", older)
    await world.send(number, chat_id=pro_chat)

    assert pro["_id"] in (await world.lead_by_id(older["_id"]))["rejected_by"]
    untouched = await world.lead_by_id(newer["_id"])
    assert untouched["status"] == LeadStatus.NEW
    assert untouched["pro_id"] == pro["_id"]
    assert not untouched.get("rejected_by")
    world.recorder.assert_silent(CUSTOMER_B, "their lead was not the one rejected")


@pytest.mark.asyncio
async def test_picker_exit_word_leaves_both_offers_open(world):
    _, pro_chat, older, newer = await _pro_with_two_offers(world)

    await world.send(APPROVE_WORD, chat_id=pro_chat)
    await world.send("ביטול", chat_id=pro_chat)

    for lead in (older, newer):
        assert (await world.lead_by_id(lead["_id"]))["status"] == LeadStatus.NEW
    world.recorder.assert_text_to(pro_chat, Messages.Pro.ACTION_CANCELLED)


@pytest.mark.asyncio
async def test_a_digit_past_the_list_does_not_finish_an_unrelated_job(world):
    """*3* is also סיימתי. Off the end of a two-row picker, abandoning the prompt
    for the command would complete the pro's only booked job."""
    pro, pro_chat, *_ = await _pro_with_two_offers(world)
    job = await world.booked_job(pro, chat_id=R.chat(R.PRO_THIRD))

    await world.send(APPROVE_WORD, chat_id=pro_chat)
    await world.send("3", chat_id=pro_chat)

    assert (await world.lead_by_id(job["_id"]))["status"] == LeadStatus.BOOKED
    await world.assert_state(UserStates.PRO_SELECTING_JOB_TO_APPROVE, chat_id=pro_chat)
    world.recorder.assert_text_to(pro_chat, Messages.Pro.INVALID_JOB_SELECTION)


@pytest.mark.asyncio
async def test_a_word_command_still_abandons_the_picker(world):
    """PRO-186's escape hatch, unchanged for the new prompts."""
    _, pro_chat, *_ = await _pro_with_two_offers(world)

    await world.send(REJECT_WORD, chat_id=pro_chat)
    await world.send(APPROVE_WORD, chat_id=pro_chat)

    await world.assert_state(UserStates.PRO_SELECTING_JOB_TO_APPROVE, chat_id=pro_chat)


# ===========================================================================
# One offer: unchanged
# ===========================================================================


@pytest.mark.asyncio
async def test_approve_with_one_offer_still_books_it_directly(world):
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    lead = await world.awaiting_approval_job(pro, chat_id=CUSTOMER_A)

    await world.send(APPROVE_WORD, chat_id=pro_chat)

    assert (await world.lead_by_id(lead["_id"]))["status"] == LeadStatus.BOOKED
    world.recorder.assert_text_to(pro_chat, Messages.Pro.APPROVE_SUCCESS)
    assert await world.state(pro_chat) != UserStates.PRO_SELECTING_JOB_TO_APPROVE


# ===========================================================================
# PRO-59 through the picker: a pro who loses the lead learns nothing about it
# ===========================================================================


@pytest.mark.asyncio
async def test_picker_answer_after_a_reassignment_reveals_nothing(world):
    _, pro_chat, older, _ = await _pro_with_two_offers(world)
    other = world.pros[R.PRO_SECONDARY]

    await world.send(APPROVE_WORD, chat_id=pro_chat)
    number = await _number_of(world, pro_chat, "approving_jobs_context", older)
    await world.db.leads.update_one(
        {"_id": older["_id"]}, {"$set": {"pro_id": other["_id"]}}
    )
    world.recorder.clear()
    await world.send(number, chat_id=pro_chat)

    moved = await world.lead_by_id(older["_id"])
    assert moved["status"] == LeadStatus.NEW
    assert moved["pro_id"] == other["_id"]
    assert to_local_phone(CUSTOMER_A) not in _bodies(world, pro_chat)
    world.recorder.assert_silent(CUSTOMER_A, "no approval happened")
    # The other offer is still waiting, so "nothing pending" would be false.
    world.recorder.assert_text_to(pro_chat, Messages.Pro.SELECTION_OFFER_LIST_CHANGED)


@pytest.mark.asyncio
async def test_picker_answer_on_a_lead_that_left_new_reveals_nothing(world):
    _, pro_chat, older, _ = await _pro_with_two_offers(world)

    await world.send(APPROVE_WORD, chat_id=pro_chat)
    number = await _number_of(world, pro_chat, "approving_jobs_context", older)
    await world.db.leads.update_one(
        {"_id": older["_id"]}, {"$set": {"status": LeadStatus.CANCELLED}}
    )
    world.recorder.clear()
    await world.send(number, chat_id=pro_chat)

    assert (await world.lead_by_id(older["_id"]))["status"] == LeadStatus.CANCELLED
    assert to_local_phone(CUSTOMER_A) not in _bodies(world, pro_chat)
    world.recorder.assert_silent(CUSTOMER_A, "no approval happened")


@pytest.mark.asyncio
async def test_the_claim_is_atomic_when_the_lead_moves_after_it_was_read(world):
    """The window between the picker's read and the write: only the conditional
    update stands between this pro and the customer's phone number."""
    from app.services import pro_flow, workflow_service

    _, pro_chat, older, _ = await _pro_with_two_offers(world)
    pro = world.pros[R.PRO_PRIMARY]
    other = world.pros[R.PRO_SECONDARY]
    stale_read = await world.lead_by_id(older["_id"])
    await world.db.leads.update_one(
        {"_id": older["_id"]}, {"$set": {"pro_id": other["_id"]}}
    )

    answer = await pro_flow._approve_lead(
        stale_read, pro, workflow_service.lead_manager, workflow_service.whatsapp
    )

    assert answer == Messages.Pro.SELECTION_OFFER_LIST_CHANGED
    moved = await world.lead_by_id(older["_id"])
    assert moved["status"] == LeadStatus.NEW
    assert moved["pro_id"] == other["_id"]
    world.recorder.assert_silent(CUSTOMER_A, "the race was lost")
    world.recorder.assert_silent(pro_chat, "nothing was sent directly")


# ===========================================================================
# The SOS alert offers אשר only for a lead אשר can act on
# ===========================================================================


@pytest.mark.asyncio
async def test_sos_alert_for_an_early_lead_does_not_offer_approve(world):
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    await world.awaiting_approval_job(
        pro, chat_id=CUSTOMER_A, status=LeadStatus.CONTACTED
    )
    await world.set_state(UserStates.IDLE, chat_id=CUSTOMER_A)

    await world.send(SOS_WORD, chat_id=CUSTOMER_A)

    sent = _bodies(world, pro_chat)
    assert SOS_WORD in sent, "the alert still carries what the customer wrote"
    assert APPROVE_TOKEN not in sent
    assert to_local_phone(CUSTOMER_A) not in sent
    world.recorder.assert_text_to(
        pro_chat, static_prefix(Messages.SOS.PRO_ALERT_NOT_OFFERED)
    )


@pytest.mark.asyncio
async def test_sos_alert_for_an_offered_lead_still_offers_approve(world):
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    await world.awaiting_approval_job(pro, chat_id=CUSTOMER_A)
    await world.set_state(UserStates.IDLE, chat_id=CUSTOMER_A)

    await world.send(SOS_WORD, chat_id=CUSTOMER_A)

    sent = _bodies(world, pro_chat)
    assert APPROVE_TOKEN in sent
    assert to_local_phone(CUSTOMER_A) not in sent


# ===========================================================================
# A pro on break whose PRO_MODE has expired
# ===========================================================================


async def _pro_on_break(world):
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    await world.db.users.update_one({"_id": pro["_id"]}, {"$set": {"is_active": False}})
    return pro, world.pro_chat(R.PRO_PRIMARY)


async def _is_matchable(world, pro):
    return await world.db.users.count_documents(
        {**APPROVED_PRO_FILTER, "_id": pro["_id"]}
    )


@pytest.mark.asyncio
async def test_pro_on_break_can_approve_with_no_pro_mode(world):
    pro, pro_chat = await _pro_on_break(world)
    lead = await world.awaiting_approval_job(pro, chat_id=CUSTOMER_A)

    await world.send(APPROVE_WORD, chat_id=pro_chat)

    assert (await world.lead_by_id(lead["_id"]))["status"] == LeadStatus.BOOKED
    world.recorder.assert_text_to(pro_chat, Messages.Pro.APPROVE_SUCCESS)
    assert not await _is_matchable(world, pro)


@pytest.mark.asyncio
async def test_pro_on_break_can_reject_with_no_pro_mode(world):
    pro, pro_chat = await _pro_on_break(world)
    lead = await world.awaiting_approval_job(pro, chat_id=CUSTOMER_A)

    await world.send(REJECT_WORD, chat_id=pro_chat)

    assert pro["_id"] in (await world.lead_by_id(lead["_id"]))["rejected_by"]
    assert not await _is_matchable(world, pro)


@pytest.mark.asyncio
async def test_pro_on_break_can_finish_with_no_pro_mode(world):
    pro, pro_chat = await _pro_on_break(world)
    lead = await world.booked_job(pro, chat_id=CUSTOMER_A)

    await world.send(FINISH_WORD, chat_id=pro_chat)

    assert (await world.lead_by_id(lead["_id"]))["status"] == LeadStatus.COMPLETED
    assert not await _is_matchable(world, pro)


@pytest.mark.asyncio
async def test_pro_on_break_can_say_still_working_with_no_pro_mode(world):
    pro, pro_chat = await _pro_on_break(world)
    await world.booked_job(pro, chat_id=CUSTOMER_A)

    await world.send(STILL_WORKING_WORD, chat_id=pro_chat)

    world.recorder.assert_text_to(pro_chat, Messages.Pro.STILL_WORKING_ACK)
    assert not await _is_matchable(world, pro)


@pytest.mark.asyncio
async def test_pro_on_break_can_end_the_break_with_no_pro_mode(world):
    pro, pro_chat = await _pro_on_break(world)

    await world.send(RESUME_WORD, chat_id=pro_chat)

    world.recorder.assert_text_to(pro_chat, Messages.Pro.STATUS_RESUMED)
    assert await _is_matchable(world, pro)


@pytest.mark.asyncio
async def test_pending_applicant_cannot_activate_themselves(world):
    """is_active False is also what an application awaiting review looks like, so
    the bypass has to tell them apart: *חזרתי* must not skip the admin's approval."""
    await world.standard_cast()
    applicant = await world.add_pro(
        R.PRO_UNAPPROVED, name="מועמד", is_active=False, pending_approval=True
    )
    chat_id = R.chat(R.PRO_UNAPPROVED)
    world.ai.script(reply("שלום"))

    await world.send(RESUME_WORD, chat_id=chat_id)

    assert (await world.pro_doc(R.PRO_UNAPPROVED))["is_active"] is False
    assert not await _is_matchable(world, applicant)
    assert await world.state(chat_id) != UserStates.PRO_MODE
    assert Messages.Pro.STATUS_RESUMED not in _bodies(world, chat_id)


@pytest.mark.asyncio
async def test_pro_on_break_with_a_customer_prompt_open_keeps_their_digit(world):
    """The ambiguous-keyword yield applies to a pro on break as to any other: a
    rating prompt on their own job owns the bare digit."""
    pro, pro_chat = await _pro_on_break(world)
    other = world.pros[R.PRO_SECONDARY]
    offered = await world.awaiting_approval_job(pro, chat_id=CUSTOMER_A)
    own = await world.booked_job(
        other,
        chat_id=pro_chat,
        status=LeadStatus.COMPLETED,
        waiting_for_rating=True,
        completed_at=world.anchor,
    )

    await world.send("1", chat_id=pro_chat)

    assert (await world.lead_by_id(offered["_id"]))["status"] == LeadStatus.NEW
    assert (await world.lead_by_id(own["_id"]))["waiting_for_rating"] is False
