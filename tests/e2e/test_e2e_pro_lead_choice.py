"""PRO-212: *אשר* must act on the lead the pro means, and a pro on break can answer.

Both defects were read from the code and then reproduced here, through the
production orchestrator, before the fix.
"""

from datetime import timedelta

import pytest

from app.core.constants import LeadStatus, UserStates, WorkerConstants
from app.core.messages import Messages
from app.core.phone import to_local_phone
from app.services import pro_flow as pro_flow_module, workflow_service
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
PAUSE_WORD = Messages.Keywords.PAUSE_COMMANDS[0]
PAUSE_BOT_WORD = Messages.Keywords.BOT_PAUSE_COMMANDS[0]
BOT_RESUME_WORD = Messages.Keywords.BOT_RESUME_COMMANDS[0]
SOS_WORD = "נציג"
CANCEL_WORD = Messages.Keywords.CANCEL_BOOKED_COMMANDS[0]
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
async def test_the_picker_expires_quickly_and_a_later_digit_acts_on_nothing(world):
    _, pro_chat, older, newer = await _pro_with_two_offers(world)

    await world.send(APPROVE_WORD, chat_id=pro_chat)

    ttl = await world.state_ttl(pro_chat)
    assert 0 < ttl <= WorkerConstants.PRO_ACTION_PICKER_TTL_SECONDS

    await world.set_state(UserStates.IDLE, chat_id=pro_chat)
    await world.send("1", chat_id=pro_chat)

    for lead in (older, newer):
        assert (await world.lead_by_id(lead["_id"]))["status"] == LeadStatus.NEW


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
    from app.services import pro_flow

    _, pro_chat, older, _ = await _pro_with_two_offers(world)
    pro = world.pros[R.PRO_PRIMARY]
    other = world.pros[R.PRO_SECONDARY]
    stale_read = await world.lead_by_id(older["_id"])
    await world.db.leads.update_one(
        {"_id": older["_id"]}, {"$set": {"pro_id": other["_id"]}}
    )

    answer = await pro_flow._approve_lead(
        stale_read,
        pro,
        workflow_service.lead_manager,
        workflow_service.whatsapp,
        picked=True,
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
async def test_sos_on_an_early_lead_sends_the_pro_nothing(world):
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    await world.awaiting_approval_job(
        pro, chat_id=CUSTOMER_A, status=LeadStatus.CONTACTED
    )
    await world.set_state(UserStates.IDLE, chat_id=CUSTOMER_A)

    await world.send(SOS_WORD, chat_id=CUSTOMER_A)

    world.recorder.assert_silent(pro_chat, "the pro was never offered this lead")
    world.assert_paged("SOS from customer", "pro_notified=False")


@pytest.mark.asyncio
async def test_sos_on_an_offered_lead_offers_approve_without_the_customers_words(world):
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    await world.awaiting_approval_job(pro, chat_id=CUSTOMER_A)
    await world.set_state(UserStates.IDLE, chat_id=CUSTOMER_A)

    await world.send(f"{SOS_WORD} 0509876543", chat_id=CUSTOMER_A)

    sent = _bodies(world, pro_chat)
    assert APPROVE_TOKEN in sent
    assert "0509876543" not in sent
    assert to_local_phone(CUSTOMER_A) not in sent
    # Only a pro who holds the number can step into the conversation.
    assert Messages.Pro.PAUSE_NOTIFICATION not in sent
    world.assert_paged("SOS from customer", "pro_notified=True")


@pytest.mark.asyncio
async def test_sos_on_a_booked_lead_still_gives_the_number_and_the_pause_notice(world):
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    await world.booked_job(pro, chat_id=CUSTOMER_A)

    await world.send(SOS_WORD, chat_id=CUSTOMER_A)

    sent = _bodies(world, pro_chat)
    assert to_local_phone(CUSTOMER_A) in sent
    assert Messages.Pro.PAUSE_NOTIFICATION in sent


# ===========================================================================
# A pro on break whose PRO_MODE has expired
# ===========================================================================


async def _pro_on_break(world):
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    await world.db.users.update_one(
        {"_id": pro["_id"]}, {"$set": {"is_active": False, "self_paused": True}}
    )
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


@pytest.mark.asyncio
async def test_pro_on_break_with_two_offers_can_use_the_picker(world):
    """The digit that answers a prompt the pro opened is not a customer's digit."""
    pro, pro_chat = await _pro_on_break(world)
    first = await world.awaiting_approval_job(pro, chat_id=CUSTOMER_A)
    await world.awaiting_approval_job(pro, chat_id=CUSTOMER_B)

    await world.send(APPROVE_WORD, chat_id=pro_chat)
    number = await _number_of(world, pro_chat, "approving_jobs_context", first)
    await world.send(number, chat_id=pro_chat)

    assert (await world.lead_by_id(first["_id"]))["status"] == LeadStatus.BOOKED


@pytest.mark.asyncio
async def test_pro_on_break_answering_which_floor_does_not_finish_a_job(world):
    """AWAITING_ADDRESS is no customer prompt state, and *3* is also סיימתי."""
    pro, pro_chat = await _pro_on_break(world)
    job = await world.booked_job(pro, chat_id=CUSTOMER_A)
    await world.db.leads.insert_one(
        {
            "chat_id": pro_chat,
            "status": LeadStatus.CONTACTED,
            "pro_id": world.pros[R.PRO_SECONDARY]["_id"],
            "issue_type": "נזילה",
            "city": "תל אביב",
            "street": "דיזנגוף",
            "street_number": "50",
            "created_at": world.anchor,
        }
    )
    await world.set_state(UserStates.AWAITING_ADDRESS, chat_id=pro_chat)

    await world.send("3", chat_id=pro_chat)

    assert (await world.lead_by_id(job["_id"]))["status"] == LeadStatus.BOOKED
    assert await world.state(pro_chat) not in workflow_service.PRO_DISPATCH_STATES
    # The address step's own answer: a reply this short never reaches the AI there.
    world.recorder.assert_text_to(pro_chat, Messages.Customer.ADDRESS_INVALID)
    assert world.ai.pending_script == 0


@pytest.mark.asyncio
async def test_pro_on_break_with_a_bare_digit_and_no_request_stays_a_customer(world):
    pro, pro_chat = await _pro_on_break(world)
    job = await world.booked_job(pro, chat_id=CUSTOMER_A)
    world.ai.script(reply("שלום"))

    await world.send("3", chat_id=pro_chat)

    assert (await world.lead_by_id(job["_id"]))["status"] == LeadStatus.BOOKED
    assert await world.state(pro_chat) not in workflow_service.PRO_DISPATCH_STATES
    assert world.ai.pending_script == 0


@pytest.mark.asyncio
async def test_pro_on_break_with_their_own_request_open_keeps_their_words(world):
    """As auto-detect does: their open request owns the conversation."""
    pro, pro_chat = await _pro_on_break(world)
    job = await world.booked_job(pro, chat_id=CUSTOMER_A)
    await world.booked_job(world.pros[R.PRO_SECONDARY], chat_id=pro_chat)
    world.ai.script(reply("מעולה"))

    await world.send(FINISH_WORD, chat_id=pro_chat)

    assert (await world.lead_by_id(job["_id"]))["status"] == LeadStatus.BOOKED
    assert await world.state(pro_chat) != UserStates.PRO_MODE


@pytest.mark.asyncio
async def test_pro_on_break_with_their_own_request_open_can_still_end_the_break(world):
    pro, pro_chat = await _pro_on_break(world)
    await world.booked_job(world.pros[R.PRO_SECONDARY], chat_id=pro_chat)

    await world.send(RESUME_WORD, chat_id=pro_chat)

    world.recorder.assert_text_to(pro_chat, Messages.Pro.STATUS_RESUMED)
    assert await _is_matchable(world, pro)


@pytest.mark.asyncio
async def test_pro_on_break_can_switch_to_customer_mode(world):
    _, pro_chat = await _pro_on_break(world)

    await world.send(Messages.Keywords.CUSTOMER_MODE_COMMANDS[0], chat_id=pro_chat)

    await world.assert_state(UserStates.CUSTOMER_MODE, chat_id=pro_chat)


# ===========================================================================
# The pro's own break versus the admin's decision
# ===========================================================================


@pytest.mark.asyncio
async def test_break_taken_and_ended_by_the_pro_records_who_took_it(world):
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    pro_chat = world.pro_chat(R.PRO_PRIMARY)

    await world.send(PAUSE_WORD, chat_id=pro_chat)

    doc = await world.pro_doc(R.PRO_PRIMARY)
    assert doc["is_active"] is False
    assert doc["self_paused"] is True

    await world.set_state(UserStates.IDLE, chat_id=pro_chat)
    await world.send(RESUME_WORD, chat_id=pro_chat)

    doc = await world.pro_doc(R.PRO_PRIMARY)
    assert doc["is_active"] is True
    assert "self_paused" not in doc
    assert await _is_matchable(world, pro)


async def _deactivated_pro(world, *, self_paused_field=None):
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    fields = {"is_active": False}
    if self_paused_field is not None:
        fields["self_paused"] = self_paused_field
    await world.db.users.update_one({"_id": pro["_id"]}, {"$set": fields})
    return pro, world.pro_chat(R.PRO_PRIMARY)


async def _assert_reached_no_handler(world, pro, pro_chat, offer, job):
    assert (await world.lead_by_id(offer["_id"]))["status"] == LeadStatus.NEW
    assert (await world.lead_by_id(job["_id"]))["status"] == LeadStatus.BOOKED
    assert not (await world.lead_by_id(job["_id"])).get("reminders_sent")
    doc = await world.pro_doc(R.PRO_PRIMARY)
    assert doc["is_active"] is False
    assert not doc.get("self_paused")
    assert not await _is_matchable(world, pro)
    sent = _bodies(world, pro_chat)
    assert to_local_phone(CUSTOMER_A) not in sent
    assert to_local_phone(CUSTOMER_B) not in sent


DEACTIVATED_WORDS = [
    APPROVE_WORD,
    REJECT_WORD,
    FINISH_WORD,
    STILL_WORKING_WORD,
    RESUME_WORD,
    PAUSE_WORD,
    "פרטים",
    "1",
    "3",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("self_paused_field", [None, False], ids=["unset", "false"])
@pytest.mark.parametrize("word", DEACTIVATED_WORDS)
async def test_deactivated_pro_with_no_pro_mode_reaches_no_handler(
    world, word, self_paused_field
):
    pro, pro_chat = await _deactivated_pro(world, self_paused_field=self_paused_field)
    offer = await world.awaiting_approval_job(pro, chat_id=CUSTOMER_A)
    job = await world.booked_job(pro, chat_id=CUSTOMER_B)
    world.ai.script(reply("שלום"))

    await world.send(word, chat_id=pro_chat)

    assert await world.state(pro_chat) != UserStates.PRO_MODE
    await _assert_reached_no_handler(world, pro, pro_chat, offer, job)


@pytest.mark.asyncio
@pytest.mark.parametrize("word", DEACTIVATED_WORDS)
async def test_deactivated_pro_with_a_live_pro_mode_reaches_no_handler(world, word):
    """PRO_MODE from before the deactivation must not outlast it."""
    pro, pro_chat = await _deactivated_pro(world)
    offer = await world.awaiting_approval_job(pro, chat_id=CUSTOMER_A)
    job = await world.booked_job(pro, chat_id=CUSTOMER_B)
    await world.set_state(UserStates.PRO_MODE, chat_id=pro_chat)

    await world.send(word, chat_id=pro_chat)

    world.recorder.assert_text_to(pro_chat, Messages.Pro.RESUME_NEEDS_ADMIN)
    assert await world.state(pro_chat) != UserStates.PRO_MODE
    await _assert_reached_no_handler(world, pro, pro_chat, offer, job)


@pytest.mark.asyncio
async def test_deactivated_pro_cannot_pause_their_way_into_a_resume(world):
    pro, pro_chat = await _deactivated_pro(world)
    await world.set_state(UserStates.PRO_MODE, chat_id=pro_chat)

    await world.send(PAUSE_WORD, chat_id=pro_chat)
    await world.set_state(UserStates.PRO_MODE, chat_id=pro_chat)
    await world.send(RESUME_WORD, chat_id=pro_chat)

    doc = await world.pro_doc(R.PRO_PRIMARY)
    assert doc["is_active"] is False
    assert not doc.get("self_paused")
    assert not await _is_matchable(world, pro)


@pytest.mark.asyncio
async def test_dashboard_offers_resume_only_on_a_break_the_pro_took(world):
    from app.services.pro_flow import _show_pro_dashboard

    pro, _ = await _deactivated_pro(world)
    assert Messages.Pro.CMD_RESUME not in await _show_pro_dashboard(
        await world.pro_doc(R.PRO_PRIMARY)
    )
    await world.db.users.update_one(
        {"_id": pro["_id"]}, {"$set": {"self_paused": True}}
    )
    assert Messages.Pro.CMD_RESUME in await _show_pro_dashboard(
        await world.pro_doc(R.PRO_PRIMARY)
    )


# ===========================================================================
# A digit after a picker answer must not act on the lead left over
# ===========================================================================


@pytest.mark.asyncio
async def test_reject_pick_then_digit_does_not_book_the_other_lead(world):
    """דחה, 2, 1 used to book the remaining lead and deliver its phone number."""
    _, pro_chat, older, newer = await _pro_with_two_offers(world)

    await world.send(REJECT_WORD, chat_id=pro_chat)
    number = await _number_of(world, pro_chat, "rejecting_jobs_context", newer)
    await world.send(number, chat_id=pro_chat)
    world.recorder.clear()
    await world.send("1", chat_id=pro_chat)

    assert (await world.lead_by_id(older["_id"]))["status"] == LeadStatus.NEW
    assert to_local_phone(CUSTOMER_A) not in _bodies(world, pro_chat)
    world.recorder.assert_silent(CUSTOMER_A, "nothing was approved")
    await world.assert_state(UserStates.PRO_SELECTING_JOB_TO_APPROVE, chat_id=pro_chat)


@pytest.mark.asyncio
async def test_double_tapped_digit_books_one_lead_only(world):
    _, pro_chat, older, newer = await _pro_with_two_offers(world)

    await world.send("1", chat_id=pro_chat)
    await world.send("1", chat_id=pro_chat)
    await world.send("1", chat_id=pro_chat)

    statuses = {
        (await world.lead_by_id(lead["_id"]))["status"] for lead in (older, newer)
    }
    assert statuses == {LeadStatus.BOOKED, LeadStatus.NEW}


@pytest.mark.asyncio
async def test_approve_pick_then_digit_does_not_reject_the_other_lead(world):
    pro, pro_chat, older, newer = await _pro_with_two_offers(world)

    await world.send(APPROVE_WORD, chat_id=pro_chat)
    number = await _number_of(world, pro_chat, "approving_jobs_context", older)
    await world.send(number, chat_id=pro_chat)
    await world.send("2", chat_id=pro_chat)

    untouched = await world.lead_by_id(newer["_id"])
    assert untouched["status"] == LeadStatus.NEW
    assert not untouched.get("rejected_by")
    world.recorder.assert_silent(CUSTOMER_B, "nothing happened to their lead")


@pytest.mark.asyncio
async def test_bare_digit_with_one_offer_only_opens_the_list(world):
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    lead = await world.awaiting_approval_job(pro, chat_id=CUSTOMER_A)

    await world.send("1", chat_id=pro_chat)

    assert (await world.lead_by_id(lead["_id"]))["status"] == LeadStatus.NEW
    await world.assert_state(UserStates.PRO_SELECTING_JOB_TO_APPROVE, chat_id=pro_chat)

    await world.send("1", chat_id=pro_chat)

    assert (await world.lead_by_id(lead["_id"]))["status"] == LeadStatus.BOOKED


@pytest.mark.asyncio
async def test_bare_three_with_one_booked_job_only_opens_the_list(world):
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    job = await world.booked_job(pro, chat_id=CUSTOMER_A)

    await world.send("3", chat_id=pro_chat)

    assert (await world.lead_by_id(job["_id"]))["status"] == LeadStatus.BOOKED
    await world.assert_state(UserStates.PRO_SELECTING_JOB_TO_FINISH, chat_id=pro_chat)


# ===========================================================================
# השהה / המשך
# ===========================================================================


async def _pro_with_two_jobs(world):
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    first = await world.booked_job(pro, chat_id=CUSTOMER_A)
    second = await world.booked_job(pro, chat_id=CUSTOMER_B, customer_name="רון")
    return world.pro_chat(R.PRO_PRIMARY), first, second


@pytest.mark.asyncio
async def test_pause_with_two_jobs_pauses_nothing_until_the_pro_chooses(world):
    pro_chat, first, second = await _pro_with_two_jobs(world)

    await world.send(PAUSE_BOT_WORD, chat_id=pro_chat)

    await world.assert_state(UserStates.PRO_SELECTING_JOB_TO_PAUSE, chat_id=pro_chat)
    assert (
        0
        < await world.state_ttl(pro_chat)
        <= (WorkerConstants.PRO_ACTION_PICKER_TTL_SECONDS)
    )
    for lead in (first, second):
        assert not (await world.lead_by_id(lead["_id"])).get("is_paused")
    world.recorder.assert_silent(CUSTOMER_A, "no chat was paused")
    world.recorder.assert_silent(CUSTOMER_B, "no chat was paused")


@pytest.mark.asyncio
async def test_pause_picker_pauses_only_the_chosen_customer(world):
    pro_chat, first, second = await _pro_with_two_jobs(world)

    await world.send(PAUSE_BOT_WORD, chat_id=pro_chat)
    number = await _number_of(world, pro_chat, "pausing_jobs_context", second)
    await world.send(number, chat_id=pro_chat)

    assert (await world.lead_by_id(second["_id"]))["is_paused"] is True
    assert not (await world.lead_by_id(first["_id"])).get("is_paused")
    await world.assert_state(UserStates.PAUSED_FOR_HUMAN, chat_id=CUSTOMER_B)
    world.recorder.assert_text_to(CUSTOMER_B, Messages.Customer.BOT_PAUSED_BY_PRO)
    world.recorder.assert_silent(CUSTOMER_A, "their chat was not paused")
    world.recorder.assert_text_to(
        pro_chat, Messages.Pro.PAUSE_ACK.format(customer_name="רון")
    )


@pytest.mark.asyncio
async def test_pause_picker_answer_on_a_job_that_left_booked_pauses_nothing(world):
    pro_chat, first, _ = await _pro_with_two_jobs(world)

    await world.send(PAUSE_BOT_WORD, chat_id=pro_chat)
    number = await _number_of(world, pro_chat, "pausing_jobs_context", first)
    await world.db.leads.update_one(
        {"_id": first["_id"]}, {"$set": {"status": LeadStatus.COMPLETED}}
    )
    await world.send(number, chat_id=pro_chat)

    assert not (await world.lead_by_id(first["_id"])).get("is_paused")
    world.recorder.assert_text_to(pro_chat, Messages.Pro.SELECTION_PAUSE_LIST_CHANGED)
    world.recorder.assert_silent(CUSTOMER_A, "their chat was not paused")


@pytest.mark.asyncio
async def test_pause_with_one_job_names_the_customer(world):
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    await world.booked_job(pro, chat_id=CUSTOMER_A, customer_name="דנה")

    await world.send(PAUSE_BOT_WORD, chat_id=pro_chat)

    world.recorder.assert_text_to(
        pro_chat, Messages.Pro.PAUSE_ACK.format(customer_name="דנה")
    )


@pytest.mark.asyncio
async def test_resume_finds_the_paused_job_not_the_newest_lead(world):
    """המשך used to look at the newest lead only, and answer "already active"."""
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    paused = await world.booked_job(
        pro,
        chat_id=CUSTOMER_A,
        is_paused=True,
        created_at=world.anchor - timedelta(hours=2),
    )
    await world.set_state(UserStates.PAUSED_FOR_HUMAN, chat_id=CUSTOMER_A, ttl=900)
    await world.booked_job(pro, chat_id=CUSTOMER_B)

    await world.send(BOT_RESUME_WORD, chat_id=pro_chat)

    world.recorder.assert_text_to(pro_chat, Messages.Pro.BOT_RESUMED)
    assert await world.state(CUSTOMER_A) != UserStates.PAUSED_FOR_HUMAN
    assert (await world.lead_by_id(paused["_id"]))["is_paused"] is False


@pytest.mark.asyncio
async def test_a_message_from_the_pro_extends_the_paused_job_not_the_newest_lead(world):
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    await world.booked_job(
        pro,
        chat_id=CUSTOMER_A,
        is_paused=True,
        created_at=world.anchor - timedelta(hours=2),
    )
    await world.set_state(UserStates.PAUSED_FOR_HUMAN, chat_id=CUSTOMER_A, ttl=100)
    await world.booked_job(pro, chat_id=CUSTOMER_B)
    await world.set_state(UserStates.PRO_MODE, chat_id=pro_chat)

    await world.send("שלום", chat_id=pro_chat)

    assert await world.state_ttl(CUSTOMER_A) > 100


async def _pro_with_two_paused_chats(world):
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    first = await world.booked_job(pro, chat_id=CUSTOMER_A, is_paused=True)
    second = await world.booked_job(
        pro, chat_id=CUSTOMER_B, is_paused=True, customer_name="רון"
    )
    await world.set_state(UserStates.PAUSED_FOR_HUMAN, chat_id=CUSTOMER_A, ttl=900)
    await world.set_state(UserStates.PAUSED_FOR_HUMAN, chat_id=CUSTOMER_B, ttl=900)
    return world.pro_chat(R.PRO_PRIMARY), first, second


@pytest.mark.asyncio
async def test_resume_with_two_paused_chats_resumes_nothing_until_the_pro_chooses(
    world,
):
    pro_chat, *_ = await _pro_with_two_paused_chats(world)

    await world.send(BOT_RESUME_WORD, chat_id=pro_chat)

    await world.assert_state(UserStates.PRO_SELECTING_JOB_TO_RESUME, chat_id=pro_chat)
    assert (
        0
        < await world.state_ttl(pro_chat)
        <= (WorkerConstants.PRO_ACTION_PICKER_TTL_SECONDS)
    )
    await world.assert_state(UserStates.PAUSED_FOR_HUMAN, chat_id=CUSTOMER_A)
    await world.assert_state(UserStates.PAUSED_FOR_HUMAN, chat_id=CUSTOMER_B)


@pytest.mark.asyncio
async def test_resume_picker_resumes_only_the_chosen_chat(world):
    pro_chat, first, second = await _pro_with_two_paused_chats(world)

    await world.send(BOT_RESUME_WORD, chat_id=pro_chat)
    number = await _number_of(world, pro_chat, "resuming_jobs_context", second)
    await world.send(number, chat_id=pro_chat)

    world.recorder.assert_text_to(pro_chat, Messages.Pro.BOT_RESUMED)
    assert await world.state(CUSTOMER_B) != UserStates.PAUSED_FOR_HUMAN
    assert (await world.lead_by_id(second["_id"]))["is_paused"] is False
    await world.assert_state(UserStates.PAUSED_FOR_HUMAN, chat_id=CUSTOMER_A)
    assert (await world.lead_by_id(first["_id"]))["is_paused"] is True


@pytest.mark.asyncio
async def test_resume_ignores_a_flag_whose_pause_has_already_expired(world):
    """is_paused outlives the state's TTL, so it must not add a row to choose from."""
    pro_chat, first, second = await _pro_with_two_paused_chats(world)
    await world.set_state(UserStates.IDLE, chat_id=CUSTOMER_A)

    await world.send(BOT_RESUME_WORD, chat_id=pro_chat)

    world.recorder.assert_text_to(pro_chat, Messages.Pro.BOT_RESUMED)
    assert await world.state(CUSTOMER_B) != UserStates.PAUSED_FOR_HUMAN


@pytest.mark.asyncio
async def test_resume_with_only_expired_pauses_says_the_bot_is_active(world):
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    await world.booked_job(pro, chat_id=CUSTOMER_A, is_paused=True)

    await world.send(BOT_RESUME_WORD, chat_id=pro_chat)

    world.recorder.assert_text_to(pro_chat, Messages.Pro.BOT_ALREADY_ACTIVE)


@pytest.mark.asyncio
async def test_resume_picker_answer_on_a_chat_that_left_this_pro_changes_nothing(world):
    pro_chat, first, _ = await _pro_with_two_paused_chats(world)
    other = world.pros[R.PRO_SECONDARY]

    await world.send(BOT_RESUME_WORD, chat_id=pro_chat)
    number = await _number_of(world, pro_chat, "resuming_jobs_context", first)
    await world.db.leads.update_one(
        {"_id": first["_id"]}, {"$set": {"pro_id": other["_id"]}}
    )
    await world.send(number, chat_id=pro_chat)

    world.recorder.assert_text_to(pro_chat, Messages.Pro.SELECTION_RESUME_LIST_CHANGED)
    await world.assert_state(UserStates.PAUSED_FOR_HUMAN, chat_id=CUSTOMER_A)


@pytest.mark.asyncio
async def test_resume_on_an_unapproved_lead_is_not_the_pros_to_lift(world):
    """They can't reach this customer. The pause ends by itself or when they approve."""
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    await world.awaiting_approval_job(pro, chat_id=CUSTOMER_A, is_paused=True)
    await world.set_state(UserStates.PAUSED_FOR_HUMAN, chat_id=CUSTOMER_A, ttl=900)

    await world.send(BOT_RESUME_WORD, chat_id=pro_chat)

    world.recorder.assert_text_to(pro_chat, Messages.Pro.NO_PAUSED_CONVERSATION)
    await world.assert_state(UserStates.PAUSED_FOR_HUMAN, chat_id=CUSTOMER_A)
    assert 0 < await world.state_ttl(CUSTOMER_A) <= WorkerConstants.PAUSE_TTL_SECONDS


@pytest.mark.asyncio
async def test_approving_lifts_a_pause_on_an_unapproved_lead(world):
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    await world.awaiting_approval_job(pro, chat_id=CUSTOMER_A, is_paused=True)
    await world.set_state(UserStates.PAUSED_FOR_HUMAN, chat_id=CUSTOMER_A, ttl=900)

    await world.send(APPROVE_WORD, chat_id=pro_chat)

    assert await world.state(CUSTOMER_A) != UserStates.PAUSED_FOR_HUMAN


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fields",
    [{"is_paused": False}, {"status": LeadStatus.COMPLETED}],
    ids=["no-longer-paused", "left-booked"],
)
async def test_resume_picker_answer_on_a_stale_row_changes_nothing(world, fields):
    pro_chat, first, _ = await _pro_with_two_paused_chats(world)

    await world.send(BOT_RESUME_WORD, chat_id=pro_chat)
    number = await _number_of(world, pro_chat, "resuming_jobs_context", first)
    await world.db.leads.update_one({"_id": first["_id"]}, {"$set": fields})
    await world.send(number, chat_id=pro_chat)

    world.recorder.assert_text_to(pro_chat, Messages.Pro.SELECTION_RESUME_LIST_CHANGED)
    await world.assert_state(UserStates.PAUSED_FOR_HUMAN, chat_id=CUSTOMER_A)


@pytest.mark.asyncio
async def test_a_message_from_the_pro_extends_every_paused_job(world):
    pro_chat, *_ = await _pro_with_two_paused_chats(world)
    await world.set_state(UserStates.PAUSED_FOR_HUMAN, chat_id=CUSTOMER_A, ttl=100)
    await world.set_state(UserStates.PAUSED_FOR_HUMAN, chat_id=CUSTOMER_B, ttl=100)
    await world.set_state(UserStates.PRO_MODE, chat_id=pro_chat)

    await world.send("שלום", chat_id=pro_chat)

    assert await world.state_ttl(CUSTOMER_A) > 100
    assert await world.state_ttl(CUSTOMER_B) > 100


# ===========================================================================
# ביטול is also how a pro leaves a prompt, so it must not cancel by itself
# ===========================================================================


@pytest.mark.asyncio
async def test_exit_word_after_an_expired_prompt_does_not_cancel_a_job(world):
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    await world.awaiting_approval_job(pro, chat_id=CUSTOMER_B)
    job = await world.booked_job(pro, chat_id=CUSTOMER_A)

    await world.send("1", chat_id=pro_chat)
    await world.assert_state(UserStates.PRO_SELECTING_JOB_TO_APPROVE, chat_id=pro_chat)
    await world.set_state(UserStates.IDLE, chat_id=pro_chat)
    world.recorder.clear()

    await world.send(CANCEL_WORD, chat_id=pro_chat)

    assert (await world.lead_by_id(job["_id"]))["status"] == LeadStatus.BOOKED
    world.recorder.assert_silent(CUSTOMER_A, "nothing was cancelled")
    await world.assert_state(UserStates.PRO_SELECTING_JOB_TO_CANCEL, chat_id=pro_chat)


@pytest.mark.asyncio
async def test_a_second_exit_word_leaves_the_prompt_and_cancels_nothing(world):
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    job = await world.booked_job(pro, chat_id=CUSTOMER_A)

    await world.send(CANCEL_WORD, chat_id=pro_chat)
    await world.send(CANCEL_WORD, chat_id=pro_chat)

    assert (await world.lead_by_id(job["_id"]))["status"] == LeadStatus.BOOKED
    world.recorder.assert_text_to(pro_chat, Messages.Pro.ACTION_CANCELLED)
    world.recorder.assert_silent(CUSTOMER_A, "nothing was cancelled")


@pytest.mark.asyncio
async def test_a_second_exit_word_after_expiry_only_opens_the_prompt_again(world):
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    job = await world.booked_job(pro, chat_id=CUSTOMER_A)

    await world.send(CANCEL_WORD, chat_id=pro_chat)
    await world.set_state(UserStates.IDLE, chat_id=pro_chat)
    await world.send(CANCEL_WORD, chat_id=pro_chat)

    assert (await world.lead_by_id(job["_id"]))["status"] == LeadStatus.BOOKED
    await world.assert_state(UserStates.PRO_SELECTING_JOB_TO_CANCEL, chat_id=pro_chat)
    world.recorder.assert_silent(CUSTOMER_A, "nothing was cancelled")


@pytest.mark.asyncio
async def test_cancelling_the_only_job_takes_the_listed_number(world):
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    job = await world.booked_job(pro, chat_id=CUSTOMER_A)

    await world.send(CANCEL_WORD, chat_id=pro_chat)
    await world.send("1", chat_id=pro_chat)

    assert (await world.lead_by_id(job["_id"]))["status"] == LeadStatus.CANCELLED
    world.recorder.assert_text_to(CUSTOMER_A, Messages.Customer.PRO_CANCELLED_BOOKING)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "word, state",
    [
        (FINISH_WORD, UserStates.PRO_SELECTING_JOB_TO_FINISH),
        (CANCEL_WORD, UserStates.PRO_SELECTING_JOB_TO_CANCEL),
    ],
)
async def test_finish_and_cancel_prompts_expire_quickly_too(world, word, state):
    pro_chat, *_ = await _pro_with_two_jobs(world)

    await world.send(word, chat_id=pro_chat)

    await world.assert_state(state, chat_id=pro_chat)
    assert (
        0
        < await world.state_ttl(pro_chat)
        <= (WorkerConstants.PRO_ACTION_PICKER_TTL_SECONDS)
    )


# ===========================================================================
# An active pro with their own request open keeps their digits for it
# ===========================================================================


@pytest.mark.asyncio
async def test_active_pro_with_their_own_request_open_keeps_a_digit(world):
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    job = await world.booked_job(pro, chat_id=CUSTOMER_A)
    await world.db.leads.insert_one(
        {
            "chat_id": pro_chat,
            "status": LeadStatus.CONTACTED,
            "pro_id": world.pros[R.PRO_SECONDARY]["_id"],
            "issue_type": "נזילה",
            "city": "תל אביב",
            "street": "דיזנגוף",
            "street_number": "50",
            "created_at": world.anchor,
        }
    )
    await world.set_state(UserStates.AWAITING_ADDRESS, chat_id=pro_chat)

    await world.send("3", chat_id=pro_chat)

    assert (await world.lead_by_id(job["_id"]))["status"] == LeadStatus.BOOKED
    assert await world.state(pro_chat) not in workflow_service.PRO_DISPATCH_STATES
    # The address step's own answer: a reply this short never reaches the AI there.
    world.recorder.assert_text_to(pro_chat, Messages.Customer.ADDRESS_INVALID)
    assert world.ai.pending_script == 0


@pytest.mark.asyncio
async def test_active_pro_with_their_own_request_open_can_still_use_words(world):
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    job = await world.booked_job(pro, chat_id=CUSTOMER_A)
    await world.booked_job(world.pros[R.PRO_SECONDARY], chat_id=pro_chat)

    await world.send(FINISH_WORD, chat_id=pro_chat)

    assert (await world.lead_by_id(job["_id"]))["status"] == LeadStatus.COMPLETED


# ===========================================================================
# A pro the admin deactivated: the refusal, the SOS, and their open leads
# ===========================================================================


@pytest.mark.asyncio
async def test_deactivated_pro_trying_to_come_back_is_told_why(world):
    """After PRO_MODE expired this used to reach the customer AI."""
    pro, pro_chat = await _deactivated_pro(world)

    await world.send(RESUME_WORD, chat_id=pro_chat)

    world.recorder.assert_text_to(pro_chat, Messages.Pro.RESUME_NEEDS_ADMIN)
    assert await world.state(pro_chat) not in workflow_service.PRO_DISPATCH_STATES
    assert (await world.pro_doc(R.PRO_PRIMARY))["is_active"] is False
    assert not await _is_matchable(world, pro)


@pytest.mark.asyncio
async def test_deactivated_pro_with_their_own_request_open_stays_a_customer(world):
    _, pro_chat = await _deactivated_pro(world)
    await world.booked_job(world.pros[R.PRO_SECONDARY], chat_id=pro_chat)

    await world.send(RESUME_WORD, chat_id=pro_chat)

    assert Messages.Pro.RESUME_NEEDS_ADMIN not in _bodies(world, pro_chat)
    assert await world.state(pro_chat) not in workflow_service.PRO_DISPATCH_STATES
    # Their booked request's own question, asked before any AI turn.
    await world.assert_state(UserStates.AWAITING_NEW_OR_EXISTING, chat_id=pro_chat)


@pytest.mark.asyncio
async def test_sos_on_a_booked_lead_tells_a_deactivated_pro_nothing(world):
    pro, pro_chat = await _deactivated_pro(world)
    await world.booked_job(pro, chat_id=CUSTOMER_A)

    await world.send(f"{SOS_WORD} 0509876543", chat_id=CUSTOMER_A)

    world.recorder.assert_silent(pro_chat, "the admin deactivated this pro")
    world.assert_paged("SOS from customer", "pro_notified=False")


@pytest.mark.asyncio
async def test_sos_on_a_booked_lead_still_reaches_a_pro_on_break(world):
    pro, pro_chat = await _pro_on_break(world)
    await world.booked_job(pro, chat_id=CUSTOMER_A)

    await world.send(SOS_WORD, chat_id=CUSTOMER_A)

    sent = _bodies(world, pro_chat)
    assert to_local_phone(CUSTOMER_A) in sent
    assert Messages.Pro.PAUSE_NOTIFICATION in sent
    world.assert_paged("SOS from customer", "pro_notified=True")


async def _run_approval_sla(world, inside_hours=True):
    from app.services.monitor_service import check_pro_approval_sla

    world.set_business_hours(inside_hours)
    await check_pro_approval_sla()
    await world.settle()


@pytest.mark.asyncio
async def test_sla_moves_an_offer_off_a_deactivated_pro(world):
    pro, pro_chat = await _deactivated_pro(world)
    lead = await world.awaiting_approval_job(pro, chat_id=CUSTOMER_A)

    await _run_approval_sla(world)

    moved = await world.lead_by_id(lead["_id"])
    assert moved["pro_id"] != pro["_id"]
    assert moved["status"] == LeadStatus.NEW
    world.recorder.assert_silent(pro_chat, "the admin deactivated this pro")


@pytest.mark.asyncio
async def test_sla_waits_for_business_hours_to_move_an_offer(world):
    pro, pro_chat = await _deactivated_pro(world)
    lead = await world.awaiting_approval_job(pro, chat_id=CUSTOMER_A)
    await world.age(lead["_id"], 60, "pro_notified_at")

    await _run_approval_sla(world, inside_hours=False)

    still = await world.lead_by_id(lead["_id"])
    assert still["pro_id"] == pro["_id"]
    assert not still.get("approval_nudged")
    world.recorder.assert_silent(CUSTOMER_A, "not at this hour")
    world.recorder.assert_silent(pro_chat, "the admin deactivated this pro")


@pytest.mark.asyncio
async def test_sla_leaves_an_offer_with_a_pro_on_break(world):
    pro, _ = await _pro_on_break(world)
    lead = await world.awaiting_approval_job(pro, chat_id=CUSTOMER_A)

    await _run_approval_sla(world)

    assert (await world.lead_by_id(lead["_id"]))["pro_id"] == pro["_id"]


@pytest.mark.asyncio
async def test_sla_leaves_a_booked_job_with_a_deactivated_pro(world):
    pro, _ = await _deactivated_pro(world)
    job = await world.booked_job(pro, chat_id=CUSTOMER_A)

    await _run_approval_sla(world)

    kept = await world.lead_by_id(job["_id"])
    assert kept["status"] == LeadStatus.BOOKED
    assert kept["pro_id"] == pro["_id"]


@pytest.mark.asyncio
async def test_pause_does_not_turn_a_deactivation_into_the_pros_own_break(world):
    """The read above the write can be stale. The write's filter is what holds."""
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    await world.set_state(UserStates.PRO_MODE, chat_id=pro_chat)
    real_find_one = pro_flow_module.users_collection.find_one

    async def read_then_deactivate(*args, **kwargs):
        doc = await real_find_one(*args, **kwargs)
        await world.db.users.update_one(
            {"_id": pro["_id"]}, {"$set": {"is_active": False}}
        )
        return doc

    world.monkeypatch.setattr(
        pro_flow_module.users_collection, "find_one", read_then_deactivate
    )

    await world.send(PAUSE_WORD, chat_id=pro_chat)

    assert not (await world.db.users.find_one({"_id": pro["_id"]})).get("self_paused")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "flags",
    [{}, {"approval_nudged": True, "reassign_offered": True}],
    ids=["unflagged", "both-flags"],
)
async def test_sla_moves_an_offer_that_waited_overnight_with_the_state_expired(
    world, flags
):
    """AWAITING_PRO_APPROVAL lasts an hour, and business hours can be a night away."""
    pro, pro_chat = await _deactivated_pro(world)
    lead = await world.awaiting_approval_job(pro, chat_id=CUSTOMER_A, **flags)

    await _run_approval_sla(world, inside_hours=False)
    assert (await world.lead_by_id(lead["_id"]))["pro_id"] == pro["_id"]

    await world.set_state(UserStates.IDLE, chat_id=CUSTOMER_A)
    await _run_approval_sla(world, inside_hours=True)

    moved = await world.lead_by_id(lead["_id"])
    assert moved["pro_id"] != pro["_id"]
    assert moved["status"] == LeadStatus.NEW
    # notify_old_pro=False: the deactivated pro hears nothing about it.
    world.recorder.assert_silent(pro_chat, "the admin deactivated this pro")


@pytest.mark.asyncio
async def test_sla_leaves_an_offer_while_the_customer_is_with_a_human(world):
    pro, pro_chat = await _deactivated_pro(world)
    lead = await world.awaiting_approval_job(pro, chat_id=CUSTOMER_A)
    await world.set_state(UserStates.PAUSED_FOR_HUMAN, chat_id=CUSTOMER_A, ttl=900)

    await _run_approval_sla(world)

    assert (await world.lead_by_id(lead["_id"]))["pro_id"] == pro["_id"]
    await world.assert_state(UserStates.PAUSED_FOR_HUMAN, chat_id=CUSTOMER_A)
    world.recorder.assert_silent(CUSTOMER_A, "a human has this conversation")


@pytest.mark.asyncio
async def test_sla_still_ignores_an_active_pros_offer_once_the_state_expired(world):
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    lead = await world.awaiting_approval_job(pro, chat_id=CUSTOMER_A)
    await world.age(lead["_id"], 60, "pro_notified_at")
    await world.set_state(UserStates.IDLE, chat_id=CUSTOMER_A)

    await _run_approval_sla(world)

    kept = await world.lead_by_id(lead["_id"])
    assert kept["pro_id"] == pro["_id"]
    assert not kept.get("approval_nudged")
    assert not kept.get("reassign_offered")


# ===========================================================================
# A list stays open for minutes, and the job on a row can close meanwhile
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "word, context_key",
    [
        (CANCEL_WORD, "cancelling_jobs_context"),
        (FINISH_WORD, "finishing_jobs_context"),
    ],
    ids=["cancel", "finish"],
)
@pytest.mark.parametrize(
    "closed_as", [LeadStatus.CANCELLED, LeadStatus.COMPLETED], ids=["cancelled", "done"]
)
async def test_picking_a_row_that_already_closed_changes_nothing(
    world, word, context_key, closed_as
):
    pro_chat, first, _ = await _pro_with_two_jobs(world)
    await world.send(word, chat_id=pro_chat)
    number = await _number_of(world, pro_chat, context_key, first)
    await world.db.leads.update_one(
        {"_id": first["_id"]}, {"$set": {"status": closed_as}}
    )
    await world.set_state(UserStates.AWAITING_NEW_OR_EXISTING, chat_id=CUSTOMER_A)
    before = await world.lead_by_id(first["_id"])
    world.recorder.clear()

    await world.send(number, chat_id=pro_chat)

    after = await world.lead_by_id(first["_id"])
    assert after["status"] == closed_as
    assert after.get("cancel_reason") == before.get("cancel_reason")
    assert after.get("status_history") == before.get("status_history")
    world.recorder.assert_text_to(pro_chat, Messages.Pro.SELECTION_LIST_CHANGED)
    world.recorder.assert_silent(CUSTOMER_A, "their job had already closed")
    await world.assert_state(UserStates.AWAITING_NEW_OR_EXISTING, chat_id=CUSTOMER_A)
    assert await world.state(pro_chat) != UserStates.PRO_AWAITING_FINAL_PRICE


@pytest.mark.asyncio
async def test_cancelling_a_row_that_closed_keeps_its_slot_taken(world):
    """The slot may belong to whatever replaced this job."""
    pro_chat, first, _ = await _pro_with_two_jobs(world)
    await world.send(CANCEL_WORD, chat_id=pro_chat)
    number = await _number_of(world, pro_chat, "cancelling_jobs_context", first)
    await world.db.leads.update_one(
        {"_id": first["_id"]}, {"$set": {"status": LeadStatus.COMPLETED}}
    )

    await world.send(number, chat_id=pro_chat)

    assert (await world.slot(first["booked_slot_id"]))["is_taken"] is True
