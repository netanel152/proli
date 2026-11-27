"""PRO-105 (c, d): the customer's remaining menus, tapped end to end.

A tap is a Meta ``interactive`` webhook (``world.tap``, ``kind="list_reply"`` for
a list row) read by the real route, queued, and run by the real worker task.
What this pins, for every menu converted here:

* the menu goes out with buttons (or a list) whose ids are the replies its text
  advertises, carrying the prompt's ``prompt_ref``;
* a tap and the typed reply reach the same branch and leave the same state;
* a tap on an older copy of a question changes nothing and says so, while the
  open question stays answerable; a tap on a question no longer open at all
  changes nothing either, and its digit never reaches a later reader of bare
  digits (the completion check's "1").
"""

from datetime import timedelta

import pytest
from bson import ObjectId

from app.core.constants import LeadStatus, UserStates, WorkerConstants
from app.core.messages import Messages
from app.providers.whatsapp import template_registry
from app.services.customer_flow import (
    CANCEL_CONFIRM_BUTTONS,
    LOYALTY_BUTTONS,
    NEW_OR_EXISTING_BUTTONS,
    NO_SLOTS_BUTTONS,
)
from tests.copy_util import static_prefix
from tests.e2e import reserved_numbers as R

CANCEL_WORD = Messages.Keywords.CANCEL_KEYWORDS[3]  # "ביטול"
# The ref of a prompt sent before the open one: customer_flow.new_prompt_ref's
# shape, but not the stored one.
OLDER_PROMPT_REF = "00000000" + "f" * 16
RESCHEDULE_PHRASE = "אני צריך מועד אחר"


_WAMIDS = iter(range(10**6))


async def _say(world, text):
    """``world.deliver`` with a fresh wamid, so the route's dedupe never drops a
    second message as a redelivery of the first."""
    await world.deliver(text, id_message=f"wamid.menus-{next(_WAMIDS)}")


async def _tap(world, button_id, **kwargs):
    await world.tap(button_id, id_message=f"wamid.menus-{next(_WAMIDS)}", **kwargs)


def _ids(buttons, ref):
    return [
        (b.label, template_registry.with_reply_ref(b.payload, ref)) for b in buttons
    ]


def _ref_of(send):
    """The prompt ref the buttons or rows of ``send`` carry."""
    first = (send.payload.get("buttons") or [(None, None)])[0][1]
    if first is None:
        first = send.payload["list"]["rows"][0][0]
    return template_registry.split_reply_ref(first)[1]


async def _with_completion_check(world, lead):
    """Make a bare "1" mean "the job is done" for ``lead``, so a test can show a
    stale tap's digit never reaches that reader."""
    await world.db.leads.update_one(
        {"_id": lead["_id"]},
        {
            "$set": {
                "completion_check_sent_at": world.anchor - timedelta(hours=1),
                "completion_check_sent_count": 1,
            }
        },
    )


# ===========================================================================
# Reschedule: the slot list
# ===========================================================================


async def _open_picker(world):
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    lead = await world.booked_job(pro)
    world.recorder.clear()
    await _say(world, RESCHEDULE_PHRASE)
    await world.assert_state(UserStates.AWAITING_RESCHEDULE_TIME)
    offer = world.recorder.assert_text_to(
        world.customer, static_prefix(Messages.Customer.RESCHEDULE_OFFER)
    )
    meta = await world.metadata()
    return pro, lead, offer, meta


@pytest.mark.asyncio
async def test_the_slot_list_has_one_row_per_numbered_line(world):
    _, _, offer, meta = await _open_picker(world)

    listed = offer.payload["list"]
    ref = meta["prompt_ref"]
    mapping = meta["reschedule_slots_context"]
    assert listed["button"] == Messages.Customer.RESCHEDULE_LIST_BUTTON
    assert listed["section_title"] == Messages.Customer.RESCHEDULE_LIST_SECTION
    assert [row[0] for row in listed["rows"]] == [f"{n}#{ref}" for n in mapping]
    for number, _title, _desc in listed["rows"]:
        assert f"{number.split('#')[0]}. " in offer.body
    # Meta's hard limits, on what was actually built.
    assert len(listed["rows"]) <= 10
    assert len(listed["button"]) <= 20
    assert len(listed["section_title"]) <= 24
    assert all(len(title) <= 24 for _, title, _ in listed["rows"])
    assert all(len(desc) <= 72 for _, _, desc in listed["rows"])


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["tap", "typed"])
async def test_a_row_tap_books_the_slot_a_typed_digit_books(world, how):
    _, lead, _, meta = await _open_picker(world)
    chosen = ObjectId(meta["reschedule_slots_context"]["2"])
    world.recorder.clear()

    if how == "tap":
        await _tap(world, f"2#{meta['prompt_ref']}", kind="list_reply")
    else:
        await _say(world, "2")

    updated = await world.lead_by_id(lead["_id"])
    assert updated["booked_slot_id"] == chosen
    assert updated["rescheduled_count"] == 1
    assert (await world.slot(chosen))["is_taken"] is True
    world.recorder.assert_text_to(
        world.customer, static_prefix(Messages.Customer.RESCHEDULE_SUCCESS)
    )
    await world.assert_state(UserStates.IDLE)


@pytest.mark.asyncio
async def test_a_row_from_an_earlier_list_books_nothing(world):
    """Slot 1 is taken meanwhile, so the "1" pick gets a rebuilt list whose
    numbers name other slots. A row of the first list books nothing; the new
    list stays open and its own row books its own slot."""
    _, lead, _, first = await _open_picker(world)
    lost = ObjectId(first["reschedule_slots_context"]["1"])
    await world.db.slots.update_one({"_id": lost}, {"$set": {"is_taken": True}})
    await _say(world, "1")
    second = await world.metadata()
    assert second["prompt_ref"] != first["prompt_ref"]
    world.recorder.clear()

    await _tap(world, f"1#{first['prompt_ref']}", kind="list_reply")

    world.recorder.assert_text_to(
        world.customer, Messages.Customer.RESCHEDULE_STALE_CHOICE
    )
    unchanged = await world.lead_by_id(lead["_id"])
    assert unchanged["booked_slot_id"] == lead["booked_slot_id"]
    assert unchanged.get("rescheduled_count") is None
    await world.assert_state(UserStates.AWAITING_RESCHEDULE_TIME)
    assert await world.metadata() == second

    await _tap(world, f"1#{second['prompt_ref']}", kind="list_reply")

    moved = await world.lead_by_id(lead["_id"])
    assert moved["booked_slot_id"] == ObjectId(second["reschedule_slots_context"]["1"])


@pytest.mark.asyncio
async def test_a_row_tapped_after_the_list_lapsed_changes_nothing(world):
    """The picker's TTL ran out. The row's "1" must not be read as the
    completion check's "1 - the job is done", nor as anything else."""
    _, lead, _, meta = await _open_picker(world)
    await _with_completion_check(world, lead)
    await world.expire_state()
    world.recorder.clear()

    await _tap(world, f"1#{meta['prompt_ref']}", kind="list_reply")

    world.recorder.assert_text_to(
        world.customer, Messages.Customer.STALE_BUTTON_TAP_EXPIRED
    )
    after = await world.lead_by_id(lead["_id"])
    assert after["status"] == LeadStatus.BOOKED
    assert after["booked_slot_id"] == lead["booked_slot_id"]
    assert after.get("rescheduled_count") is None
    world.recorder.assert_silent(world.pro_chat(R.PRO_PRIMARY), "nothing changed")


@pytest.mark.asyncio
async def test_no_free_slots_offers_the_two_words_it_names(world):
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    await world.booked_job(pro)
    await world.db.slots.update_many(
        {"pro_id": pro["_id"]}, {"$set": {"is_taken": True}}
    )
    world.recorder.clear()

    await _say(world, RESCHEDULE_PHRASE)

    sent = world.recorder.assert_text_to(
        world.customer, Messages.Customer.RESCHEDULE_NO_SLOTS
    )
    assert sent.payload["buttons"] == _ids(NO_SLOTS_BUTTONS, None)

    world.recorder.clear()
    await _tap(world, NO_SLOTS_BUTTONS[0].payload)

    world.recorder.assert_text_to(
        world.customer, Messages.Customer.CANCEL_CONFIRM_PROMPT
    )
    await world.assert_state(UserStates.AWAITING_CANCEL_CONFIRMATION)


# ===========================================================================
# "Really cancel?"
# ===========================================================================


async def _ask_to_cancel(world, lead=None):
    world.recorder.clear()
    await _say(world, CANCEL_WORD)
    await world.assert_state(UserStates.AWAITING_CANCEL_CONFIRMATION)
    prompt = world.recorder.assert_text_to(
        world.customer, Messages.Customer.CANCEL_CONFIRM_PROMPT
    )
    return prompt, _ref_of(prompt)


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["tap", "typed"])
async def test_the_cancel_prompt_offers_1_and_2_and_a_tap_cancels_as_typing_does(
    world, how
):
    await world.standard_cast()
    lead = await world.booked_job(world.pros[R.PRO_PRIMARY])

    prompt, ref = await _ask_to_cancel(world)
    assert prompt.payload["buttons"] == _ids(CANCEL_CONFIRM_BUTTONS, ref)
    assert ref == (await world.metadata())["prompt_ref"]

    if how == "tap":
        await _tap(world, f"1#{ref}")
    else:
        await _say(world, "1")

    assert (await world.lead_by_id(lead["_id"]))["status"] == LeadStatus.CANCELLED
    await world.assert_state(UserStates.IDLE)


@pytest.mark.asyncio
async def test_a_keep_tap_keeps_the_job(world):
    await world.standard_cast()
    lead = await world.booked_job(world.pros[R.PRO_PRIMARY])
    _, ref = await _ask_to_cancel(world)

    await _tap(world, f"2#{ref}")

    assert (await world.lead_by_id(lead["_id"]))["status"] == LeadStatus.BOOKED
    world.recorder.assert_text_to(world.customer, Messages.Customer.CANCEL_ABORTED)


@pytest.mark.asyncio
async def test_a_tap_on_an_earlier_cancel_prompt_cancels_nothing(world):
    await world.standard_cast()
    lead = await world.booked_job(world.pros[R.PRO_PRIMARY])
    _, old_ref = await _ask_to_cancel(world)
    await _say(world, "2")  # kept
    _, new_ref = await _ask_to_cancel(world)
    assert new_ref != old_ref
    world.recorder.clear()

    await _tap(world, f"1#{old_ref}")

    world.recorder.assert_text_to(world.customer, Messages.Customer.STALE_BUTTON_TAP)
    assert (await world.lead_by_id(lead["_id"]))["status"] == LeadStatus.BOOKED
    # The prompt open now is still the one to answer.
    await world.assert_state(UserStates.AWAITING_CANCEL_CONFIRMATION)
    assert (await world.metadata())["prompt_ref"] == new_ref

    await _tap(world, f"1#{new_ref}")

    assert (await world.lead_by_id(lead["_id"]))["status"] == LeadStatus.CANCELLED


@pytest.mark.asyncio
async def test_a_cancel_tap_after_the_prompt_lapsed_is_not_a_completion_answer(world):
    await world.standard_cast()
    lead = await world.booked_job(world.pros[R.PRO_PRIMARY])
    _, ref = await _ask_to_cancel(world)
    await _with_completion_check(world, lead)
    await world.expire_state()
    world.recorder.clear()

    await _tap(world, f"1#{ref}")

    world.recorder.assert_text_to(
        world.customer, Messages.Customer.STALE_BUTTON_TAP_EXPIRED
    )
    after = await world.lead_by_id(lead["_id"])
    assert after["status"] == LeadStatus.BOOKED


@pytest.mark.asyncio
async def test_the_request_still_with_the_pro_gets_the_same_buttons(world):
    await world.standard_cast()
    lead = await world.awaiting_approval_job(world.pros[R.PRO_PRIMARY])
    world.recorder.clear()

    await _say(world, CANCEL_WORD)

    prompt = world.recorder.assert_text_to(
        world.customer, Messages.Customer.CANCEL_REQUEST_CONFIRM_PROMPT
    )
    ref = _ref_of(prompt)
    assert prompt.payload["buttons"] == _ids(CANCEL_CONFIRM_BUTTONS, ref)

    await _tap(world, f"1#{ref}")

    assert (await world.lead_by_id(lead["_id"]))["status"] == LeadStatus.CANCELLED


# ===========================================================================
# Loyalty: "check with your previous pro?"
# ===========================================================================

LOYALTY_REF = "000000000123456789abcdef"


async def _loyalty_offer_open(world):
    """As the pipeline leaves it: the state, the past pro and the offer's ref."""
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    await world.booked_job(
        pro,
        status=LeadStatus.CONTACTED,
        booked_slot_id=None,
        street="דיזנגוף",
        street_number="50",
        floor="3",
        apartment="12",
    )
    await world.set_state(
        UserStates.AWAITING_LOYALTY_CONFIRMATION,
        ttl=WorkerConstants.LOYALTY_CONFIRM_TTL_SECONDS,
    )
    await world.set_metadata(
        {"past_pro_id": str(pro["_id"]), "prompt_ref": LOYALTY_REF}
    )
    world.recorder.clear()
    return pro


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["tap", "typed"])
async def test_a_loyalty_yes_tap_contacts_the_pro_as_typing_does(world, how):
    pro = await _loyalty_offer_open(world)

    if how == "tap":
        await _tap(world, f"1#{LOYALTY_REF}")
    else:
        await _say(world, "1")

    lead = await world.lead()
    assert lead["status"] == LeadStatus.NEW
    assert lead["pro_id"] == pro["_id"]
    await world.assert_state(UserStates.AWAITING_PRO_APPROVAL)


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["tap", "typed"])
async def test_a_loyalty_no_tap_declines_as_typing_does(world, how):
    await _loyalty_offer_open(world)

    if how == "tap":
        await _tap(world, f"2#{LOYALTY_REF}")
    else:
        await _say(world, "2")

    world.recorder.assert_text_to(world.customer, Messages.Customer.LOYALTY_DECLINED)
    assert (await world.lead())["status"] == LeadStatus.CONTACTED


@pytest.mark.asyncio
async def test_a_tap_on_an_earlier_loyalty_offer_changes_nothing(world):
    await _loyalty_offer_open(world)

    await _tap(world, f"1#{OLDER_PROMPT_REF}")

    world.recorder.assert_text_to(world.customer, Messages.Customer.STALE_BUTTON_TAP)
    assert (await world.lead())["status"] == LeadStatus.CONTACTED
    await world.assert_state(UserStates.AWAITING_LOYALTY_CONFIRMATION)
    world.recorder.assert_silent(world.pro_chat(R.PRO_PRIMARY), "nobody was contacted")


@pytest.mark.asyncio
async def test_the_loyalty_reprompt_carries_the_offers_buttons(world):
    await _loyalty_offer_open(world)

    await _say(world, "אולי")

    sent = world.recorder.assert_text_to(
        world.customer, Messages.Customer.LOYALTY_REPROMPT
    )
    assert sent.payload["buttons"] == _ids(LOYALTY_BUTTONS, LOYALTY_REF)


# ===========================================================================
# "A new request, or about the existing job?"
# ===========================================================================

NEW_OR_EXISTING_REF = "00000000fedcba9876543210"


async def _new_or_existing_open(world):
    await world.standard_cast()
    booked = await world.booked_job(
        world.pros[R.PRO_PRIMARY], status=LeadStatus.BOOKED, booked_slot_id=None
    )
    await world.set_state(
        UserStates.AWAITING_NEW_OR_EXISTING,
        ttl=WorkerConstants.NEW_OR_EXISTING_TTL_SECONDS,
    )
    await world.set_metadata(
        {"booked_lead_id": str(booked["_id"]), "prompt_ref": NEW_OR_EXISTING_REF}
    )
    world.recorder.clear()
    return booked


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["tap", "typed"])
async def test_a_new_request_tap_releases_the_gate_as_typing_does(world, how):
    await _new_or_existing_open(world)

    if how == "tap":
        await _tap(world, f"1#{NEW_OR_EXISTING_REF}")
    else:
        await _say(world, "1")

    world.recorder.assert_text_to(world.customer, Messages.Customer.NEW_REQUEST_ACK)
    await world.assert_state(UserStates.IDLE)


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["tap", "typed"])
async def test_an_existing_job_tap_hands_off_as_typing_does(world, how):
    await _new_or_existing_open(world)

    if how == "tap":
        await _tap(world, f"2#{NEW_OR_EXISTING_REF}")
    else:
        await _say(world, "2")

    await world.assert_state(UserStates.PAUSED_FOR_HUMAN)
    world.recorder.assert_text_to(
        world.pro_chat(R.PRO_PRIMARY),
        static_prefix(Messages.Pro.CUSTOMER_EXISTING_JOB_QUERY),
    )


@pytest.mark.asyncio
async def test_a_tap_on_an_earlier_new_or_existing_question_changes_nothing(world):
    await _new_or_existing_open(world)

    await _tap(world, f"2#{OLDER_PROMPT_REF}")

    world.recorder.assert_text_to(world.customer, Messages.Customer.STALE_BUTTON_TAP)
    await world.assert_state(UserStates.AWAITING_NEW_OR_EXISTING)
    world.recorder.assert_silent(
        world.pro_chat(R.PRO_PRIMARY), "the customer's number was not handed over"
    )


@pytest.mark.asyncio
async def test_the_new_or_existing_reprompt_carries_the_questions_buttons(world):
    await _new_or_existing_open(world)

    await _say(world, "מה?")

    sent = world.recorder.assert_text_to(
        world.customer, Messages.Customer.NEW_OR_EXISTING_REPROMPT
    )
    assert sent.payload["buttons"] == _ids(NEW_OR_EXISTING_BUTTONS, NEW_OR_EXISTING_REF)


# ===========================================================================
# A pro's offer tap is not a customer's stale tap
# ===========================================================================


@pytest.mark.asyncio
async def test_a_pros_offer_tap_still_reaches_the_pro_flow(world):
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    lead = await world.awaiting_approval_job(pro)
    world.recorder.clear()

    await _tap(
        world,
        template_registry.with_reply_ref(
            Messages.Keywords.APPROVE_COMMANDS[0], str(lead["_id"])
        ),
        chat_id=world.pro_chat(R.PRO_PRIMARY),
    )

    assert (await world.lead_by_id(lead["_id"]))["status"] == LeadStatus.BOOKED
    for send in world.recorder.to(world.pro_chat(R.PRO_PRIMARY)):
        assert Messages.Customer.STALE_BUTTON_TAP_EXPIRED not in send.body


# ===========================================================================
# Lapsed prompts never answer a later question (flow-tracer review)
# ===========================================================================


@pytest.mark.asyncio
async def test_a_lapsed_cancel_tap_while_waiting_is_not_a_reassign_answer(world):
    """The customer kept the request ("לא, להשאיר אותה"), the pro stayed silent
    and the SLA offered a reassignment. A later tap on that prompt's "כן, לבטל"
    must not be read as "1 - find another pro"."""
    from app.services.monitor_service import check_pro_approval_sla

    await world.standard_cast()
    world.set_business_hours(True)
    pro = world.pros[R.PRO_PRIMARY]
    lead = await world.awaiting_approval_job(pro)
    world.recorder.clear()
    await _say(world, CANCEL_WORD)
    prompt = world.recorder.assert_text_to(
        world.customer, Messages.Customer.CANCEL_REQUEST_CONFIRM_PROMPT
    )
    ref = _ref_of(prompt)
    await _tap(world, f"2#{ref}")  # keep it
    await world.assert_state(UserStates.AWAITING_PRO_APPROVAL)
    await world.age(
        lead["_id"],
        WorkerConstants.APPROVAL_REASSIGN_OFFER_MINUTES + 1,
        "pro_notified_at",
    )
    await check_pro_approval_sla()
    assert (await world.lead_by_id(lead["_id"]))["reassign_offered"] is True
    world.recorder.clear()

    await _tap(world, f"1#{ref}")

    world.recorder.assert_text_to(
        world.customer, Messages.Customer.STALE_BUTTON_TAP_EXPIRED
    )
    after = await world.lead_by_id(lead["_id"])
    assert after["status"] == LeadStatus.NEW
    assert after["pro_id"] == pro["_id"]
    await world.assert_state(UserStates.AWAITING_PRO_APPROVAL)

    # Typing "1" is the reassign answer, exactly as before.
    await _say(world, "1")
    assert (await world.lead_by_id(lead["_id"]))["pro_id"] != pro["_id"]


@pytest.mark.asyncio
async def test_a_pro_writing_as_a_customer_is_protected_too(world):
    """A pro booked a job for themselves. Their lapsed "כן, לבטל" tap must not
    reach the completion check's "1" just because they are a pro: the ref
    names a prompt, not a lead, whoever sends it."""
    await world.standard_cast()
    me = world.pro_chat(R.PRO_PRIMARY)
    lead = await world.booked_job(world.pros[R.PRO_SECONDARY], chat_id=me)
    await _with_completion_check(world, lead)
    await world.set_state(UserStates.CUSTOMER_MODE, chat_id=me)
    world.recorder.clear()
    await world.deliver(CANCEL_WORD, chat_id=me, id_message="wamid.menus-pro-1")
    prompt = world.recorder.assert_text_to(me, Messages.Customer.CANCEL_CONFIRM_PROMPT)
    ref = _ref_of(prompt)
    await world.expire_state(chat_id=me)
    world.recorder.clear()

    await world.tap(f"1#{ref}", chat_id=me, id_message="wamid.menus-pro-2")

    world.recorder.assert_text_to(me, Messages.Customer.STALE_BUTTON_TAP_EXPIRED)
    after = await world.lead_by_id(lead["_id"])
    assert after["status"] == LeadStatus.BOOKED
    assert not after.get("completed_at")


@pytest.mark.asyncio
async def test_a_pros_offer_tap_during_their_own_cancel_prompt_is_not_stale(world):
    """A pro in their own customer "really cancel?" prompt taps אשר on an offer:
    that ref names a lead, so it is not answered as a stale customer tap."""
    await world.standard_cast()
    me = world.pro_chat(R.PRO_PRIMARY)
    await world.booked_job(world.pros[R.PRO_SECONDARY], chat_id=me)
    offer = await world.awaiting_approval_job(world.pros[R.PRO_PRIMARY])
    await world.set_state(UserStates.CUSTOMER_MODE, chat_id=me)
    await world.deliver(CANCEL_WORD, chat_id=me, id_message="wamid.menus-pro-3")
    await world.assert_state(UserStates.AWAITING_CANCEL_CONFIRMATION, chat_id=me)
    world.recorder.clear()

    await world.tap(
        template_registry.with_reply_ref(
            Messages.Keywords.APPROVE_COMMANDS[0], str(offer["_id"])
        ),
        chat_id=me,
        id_message="wamid.menus-pro-4",
    )

    for send in world.recorder.to(me):
        assert Messages.Customer.STALE_BUTTON_TAP not in send.body
