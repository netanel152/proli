"""Cancelling a request that is still waiting for the pro's approval.

After a deal is finalized the lead is NEW, offered to a pro, and the customer is
parked in AWAITING_PRO_APPROVAL. The soft hold used to answer a cancel keyword
there with "still waiting", nothing else could withdraw the offer once that
state lapsed, and a silent התחלה left it live: in each case a later אשר booked
the customer and sent the pro their phone number. These drive the real
orchestrator end to end, through the same PRO-118 confirmation prompt a BOOKED
job gets.
"""

import pytest

from app.core.constants import LeadStatus, UserStates, WorkerConstants
from app.core.messages import Messages
from app.core.phone import to_local_phone
from tests.copy_util import static_prefix
from tests.e2e import reserved_numbers as R
from tests.e2e.ai_replay import deal, reply


async def _deal_to_offer(world) -> dict:
    """Chat through to a finalized deal: a NEW lead offered to the primary pro."""
    world.ai.script(
        reply("שלום! אני פרולי. איך קוראים לך ומה קרה?"),
        reply(
            "נעים מאוד דנה. מה הכתובת המדויקת ומתי נוח לך?",
            customer_name="דנה",
            city="תל אביב",
            issue="נזילה מתחת לכיור",
        ),
        deal(
            "מצוין דנה, קבענו למחר ב-10:00.",
            customer_name="דנה",
            city="תל אביב",
            issue="נזילה מתחת לכיור",
            street="דיזנגוף",
            street_number="50",
            floor="3",
            apartment="12",
            appointment_time="מחר ב-10:00",
        ),
    )
    await world.send("היי")
    await world.send("יש לי נזילה מתחת לכיור בתל אביב")
    await world.send("דיזנגוף 50, קומה 3 דירה 12, מחר ב-10")
    lead = await world.lead()
    assert lead["status"] == LeadStatus.NEW
    assert lead["pro_id"] == world.pros[R.PRO_PRIMARY]["_id"]
    await world.assert_state(UserStates.AWAITING_PRO_APPROVAL)
    world.recorder.clear()
    return lead


@pytest.mark.asyncio
async def test_cancel_while_awaiting_approval_withdraws_the_offer(world):
    """The main case: the cancel asks, '1' cancels, both sides are told, and the
    pro's later אשר books nothing and reveals nothing."""
    await world.standard_cast()
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    lead = await _deal_to_offer(world)

    await world.send("אני רוצה לבטל את ההזמנה")

    world.recorder.assert_text_to(
        world.customer, Messages.Customer.CANCEL_REQUEST_CONFIRM_PROMPT
    )
    assert (await world.lead_by_id(lead["_id"]))["status"] == LeadStatus.NEW
    await world.assert_state(UserStates.AWAITING_CANCEL_CONFIRMATION)
    assert await world.state_ttl() <= WorkerConstants.CANCEL_CONFIRM_TTL_SECONDS
    world.recorder.assert_silent(pro_chat, "nothing is withdrawn before the '1'")

    world.recorder.clear()
    await world.send("1")

    cancelled = await world.lead_by_id(lead["_id"])
    assert cancelled["status"] == LeadStatus.CANCELLED
    assert cancelled["cancel_reason"] == "customer_requested"
    assert cancelled["pro_id"] == world.pros[R.PRO_PRIMARY]["_id"]
    assert (await world.status_history(lead["_id"]))[-1] == LeadStatus.CANCELLED
    await world.assert_state(UserStates.IDLE)
    world.recorder.assert_text_to(world.customer, Messages.Customer.REQUEST_WITHDRAWN)
    notice = world.recorder.assert_text_to(
        pro_chat,
        static_prefix(Messages.Pro.CUSTOMER_WITHDREW_OFFER),
        "נזילה מתחת לכיור",
    )
    assert to_local_phone(world.customer) not in notice.body

    world.recorder.clear()
    await world.send("אשר", chat_id=pro_chat)

    assert (await world.lead_by_id(lead["_id"]))["status"] == LeadStatus.CANCELLED
    world.recorder.assert_text_to(pro_chat, Messages.Pro.NO_PENDING_APPROVALS)
    world.recorder.assert_silent(world.customer, "a withdrawn request books nobody")
    world.recorder.assert_never_contains(
        to_local_phone(world.customer), "the pro never earned the customer's phone"
    )
    world.recorder.assert_never_contains(
        Messages.Pro.APPROVE_SUCCESS, "nothing was approved"
    )


@pytest.mark.asyncio
async def test_a_tapped_offer_button_after_the_withdrawal_books_nothing(world):
    """PRO-104: a tap carries its lead; on a withdrawn one it acts on nothing."""
    await world.standard_cast()
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    lead = await _deal_to_offer(world)
    await world.send("בטל")
    await world.send("1")
    world.recorder.clear()

    await world.tap(
        f"{Messages.Keywords.APPROVE_COMMANDS[0]}#{lead['_id']}", chat_id=pro_chat
    )

    assert (await world.lead_by_id(lead["_id"]))["status"] == LeadStatus.CANCELLED
    world.recorder.assert_text_to(pro_chat, Messages.Pro.NO_PENDING_APPROVALS)
    world.recorder.assert_silent(world.customer, "a withdrawn request books nobody")


@pytest.mark.asyncio
async def test_cancel_then_two_keeps_the_offer_and_the_sla_still_nudges(world):
    from app.services.monitor_service import check_pro_approval_sla

    await world.standard_cast()
    world.set_business_hours(True)
    lead = await world.awaiting_approval_job(world.pros[R.PRO_PRIMARY])
    ttl_before = await world.state_ttl()

    await world.send("ביטול")
    await world.assert_state(UserStates.AWAITING_CANCEL_CONFIRMATION)
    world.recorder.clear()
    await world.send("2")

    kept = await world.lead_by_id(lead["_id"])
    assert kept["status"] == LeadStatus.NEW
    assert "cancel_reason" not in kept
    await world.assert_state(UserStates.AWAITING_PRO_APPROVAL)
    # What was left of the approval window, not a fresh one.
    assert 0 < await world.state_ttl() <= ttl_before
    world.recorder.assert_text_to(world.customer, Messages.Customer.CANCEL_REQUEST_KEPT)
    world.recorder.assert_silent(
        world.pro_chat(R.PRO_PRIMARY), "a kept offer is not news to the pro"
    )

    await world.age(
        lead["_id"], WorkerConstants.APPROVAL_NUDGE_MINUTES + 1, "pro_notified_at"
    )
    world.recorder.clear()
    await check_pro_approval_sla()

    world.recorder.assert_text_to(
        world.pro_chat(R.PRO_PRIMARY), static_prefix(Messages.Pro.APPROVAL_NUDGE)
    )


@pytest.mark.asyncio
async def test_an_unanswered_prompt_lapses_back_to_awaiting_approval(world):
    """Expiry is the other way of not confirming: the offer stays, and so does the
    wait the SLA reads — on its next tick and on the customer's next message."""
    from app.services.monitor_service import check_pro_approval_sla

    await world.standard_cast()
    world.set_business_hours(True)
    lead = await world.awaiting_approval_job(world.pros[R.PRO_PRIMARY])

    await world.send("בטל")
    await world.assert_state(UserStates.AWAITING_CANCEL_CONFIRMATION)
    await world.expire_state()  # CANCEL_CONFIRM_TTL_SECONDS ran out
    await world.age(
        lead["_id"], WorkerConstants.APPROVAL_NUDGE_MINUTES + 1, "pro_notified_at"
    )
    world.recorder.clear()

    await check_pro_approval_sla()

    world.recorder.assert_text_to(
        world.pro_chat(R.PRO_PRIMARY), static_prefix(Messages.Pro.APPROVAL_NUDGE)
    )
    await world.assert_state(UserStates.AWAITING_PRO_APPROVAL)
    assert (await world.lead_by_id(lead["_id"]))["status"] == LeadStatus.NEW

    world.recorder.clear()
    await world.send("מתי הוא מגיע?")
    world.recorder.assert_text_to(world.customer, Messages.Customer.STILL_WAITING)


@pytest.mark.asyncio
async def test_cancel_after_the_approval_state_expired(world):
    """AWAITING_PRO_APPROVAL lasts an hour and the offer outlives it."""
    await world.standard_cast()
    lead = await world.awaiting_approval_job(world.pros[R.PRO_PRIMARY])
    await world.expire_state()
    await world.assert_state(UserStates.IDLE)

    await world.send("בטל את הבקשה")

    world.recorder.assert_text_to(
        world.customer, Messages.Customer.CANCEL_REQUEST_CONFIRM_PROMPT
    )
    assert world.ai.calls == [], "a cancel keyword is no longer the AI's to answer"
    await world.assert_state(UserStates.AWAITING_CANCEL_CONFIRMATION)

    world.recorder.clear()
    await world.send("1")

    assert (await world.lead_by_id(lead["_id"]))["status"] == LeadStatus.CANCELLED
    await world.assert_state(UserStates.IDLE)
    world.recorder.assert_text_to(world.customer, Messages.Customer.REQUEST_WITHDRAWN)
    world.recorder.assert_text_to(
        world.pro_chat(R.PRO_PRIMARY),
        static_prefix(Messages.Pro.CUSTOMER_WITHDREW_OFFER),
    )


@pytest.mark.asyncio
async def test_pro_approves_between_the_prompt_and_the_confirmation(world):
    """The pro's אשר wins: the customer's '1' cancels nothing and is told the job
    is booked, with the BOOKED cancel path (which asks again) one word away."""
    await world.standard_cast()
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    lead = await world.awaiting_approval_job(world.pros[R.PRO_PRIMARY])

    await world.send("בטל")
    await world.send("אשר", chat_id=pro_chat)
    assert (await world.lead_by_id(lead["_id"]))["status"] == LeadStatus.BOOKED
    # The open prompt survives the approval, so the answer still reaches it.
    await world.assert_state(UserStates.AWAITING_CANCEL_CONFIRMATION)
    world.recorder.clear()

    await world.send("1")

    booked = await world.lead_by_id(lead["_id"])
    assert booked["status"] == LeadStatus.BOOKED
    assert "cancel_reason" not in booked
    await world.assert_state(UserStates.IDLE)
    world.recorder.assert_text_to(
        world.customer, Messages.Customer.CANCEL_REQUEST_ALREADY_BOOKED
    )
    world.recorder.assert_silent(pro_chat, "the pro keeps the job they approved")

    # The advertised keyword reaches the BOOKED path's own confirmation.
    world.recorder.clear()
    await world.send(Messages.Keywords.CANCEL_KEYWORDS[3])  # "ביטול"
    world.recorder.assert_text_to(
        world.customer, Messages.Customer.CANCEL_CONFIRM_PROMPT
    )
    assert (await world.lead_by_id(lead["_id"]))["status"] == LeadStatus.BOOKED


@pytest.mark.asyncio
async def test_reset_while_an_offer_is_open_says_how_to_cancel(world):
    await world.standard_cast()
    lead = await world.awaiting_approval_job(world.pros[R.PRO_PRIMARY])

    await world.send("התחלה")

    world.recorder.assert_text_to(
        world.customer, Messages.Customer.RESET_REQUEST_STILL_OPEN
    )
    # Still a reset: no trap, nothing held.
    await world.assert_state(UserStates.IDLE)
    assert (await world.lead_by_id(lead["_id"]))["status"] == LeadStatus.NEW
    world.recorder.assert_silent(world.pro_chat(R.PRO_PRIMARY), "a reset tells no pro")

    # And the keyword it advertises does what it says.
    world.recorder.clear()
    await world.send("ביטול")
    world.recorder.assert_text_to(
        world.customer, Messages.Customer.CANCEL_REQUEST_CONFIRM_PROMPT
    )


@pytest.mark.asyncio
async def test_reset_with_no_offer_stays_silent(world):
    await world.standard_cast()
    await world.booked_job(world.pros[R.PRO_PRIMARY], status=LeadStatus.CONTACTED)
    await world.set_state(UserStates.AWAITING_ADDRESS)

    await world.send("התחלה")

    world.recorder.assert_silent(world.customer, "the 2026-08-27 silent reset")
    await world.assert_state(UserStates.IDLE)
