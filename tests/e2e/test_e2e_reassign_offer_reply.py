"""PRO-238: a "1"/"2" answering the PRO-56 reassign offer stays tied to that offer.

The offer's reply used to be read only while the customer sat in
AWAITING_PRO_APPROVAL. Once the pro approved, that state was cleared and a late
"1" reached the completion check, which closed the job the customer had just
been given. The in-time reply (state still set) is covered by
``test_approval_sla_*`` in ``test_e2e_flows.py``; these cover the replies that
outlive the state or the situation the offer asked about.
"""

import pytest

from app.core.constants import LeadStatus, UserStates, WorkerConstants
from app.core.messages import Messages
from app.services.state_manager_service import StateManager
from tests.e2e import reserved_numbers as R

APPROVE_WORD = Messages.Keywords.APPROVE_COMMANDS[0]
REJECT_WORD = Messages.Keywords.REJECT_COMMANDS[0]


async def _offered_lead(world):
    """A NEW lead whose pro stayed silent long enough for the customer to be offered
    a reassignment (the SLA job has run and sent REASSIGN_OFFER)."""
    from app.services.monitor_service import check_pro_approval_sla

    await world.standard_cast()
    world.set_business_hours(True)
    lead = await world.awaiting_approval_job(world.pros[R.PRO_PRIMARY])
    await world.age(
        lead["_id"],
        WorkerConstants.APPROVAL_REASSIGN_OFFER_MINUTES + 1,
        "pro_notified_at",
    )
    await check_pro_approval_sla()
    offered = await world.lead_by_id(lead["_id"])
    assert offered["reassign_offered"] is True  # the arrangement, not the behaviour
    world.recorder.assert_text_to(world.customer, Messages.Customer.REASSIGN_OFFER)
    return offered


@pytest.mark.asyncio
async def test_reply_one_still_reassigns_after_the_awaiting_state_expired(world):
    offered = await _offered_lead(world)
    await StateManager.clear_state(world.customer)
    world.recorder.clear()

    await world.send("1")

    updated = await world.lead_by_id(offered["_id"])
    assert updated["pro_id"] == world.pros[R.PRO_SECONDARY]["_id"]
    assert updated["status"] == LeadStatus.NEW
    assert updated.get("waiting_for_rating") is not True
    world.recorder.assert_text_to(world.customer, Messages.SOS.CUSTOMER_REASSIGNING)


@pytest.mark.asyncio
@pytest.mark.parametrize("digit", ["1", "2"])
async def test_late_reply_after_the_pro_approved_leaves_the_job_booked(world, digit):
    offered = await _offered_lead(world)
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    await world.send(APPROVE_WORD, chat_id=pro_chat)
    assert (await world.lead_by_id(offered["_id"]))["status"] == LeadStatus.BOOKED
    assert await world.state() != UserStates.AWAITING_PRO_APPROVAL
    world.recorder.clear()

    await world.send(digit)

    after = await world.lead_by_id(offered["_id"])
    assert after["status"] == LeadStatus.BOOKED
    assert after["pro_id"] == world.pros[R.PRO_PRIMARY]["_id"]
    assert not after.get("waiting_for_rating")
    assert not after.get("completed_at")
    world.recorder.assert_text_to(
        world.customer, Messages.Customer.REASSIGN_REPLY_PRO_APPROVED
    )
    world.recorder.assert_silent(
        pro_chat, "the customer did not report the job finished"
    )


@pytest.mark.asyncio
async def test_late_reply_after_the_lead_moved_to_another_pro_changes_nothing(world):
    offered = await _offered_lead(world)
    await world.send(REJECT_WORD, chat_id=world.pro_chat(R.PRO_PRIMARY))
    moved = await world.lead_by_id(offered["_id"])
    assert moved["pro_id"] == world.pros[R.PRO_SECONDARY]["_id"]  # the arrangement
    assert moved["status"] == LeadStatus.NEW
    world.recorder.clear()

    await world.send("1")

    after = await world.lead_by_id(offered["_id"])
    assert after["pro_id"] == moved["pro_id"]
    assert after["status"] == LeadStatus.NEW
    assert after["reassignment_count"] == moved["reassignment_count"]
    world.recorder.assert_text_to(
        world.customer, Messages.Customer.REASSIGN_REPLY_ALREADY_MOVED
    )


@pytest.mark.asyncio
async def test_late_reply_after_the_request_closed_says_so(world):
    offered = await _offered_lead(world)
    await world.db.leads.update_one(
        {"_id": offered["_id"]}, {"$set": {"status": LeadStatus.CANCELLED}}
    )
    await StateManager.clear_state(world.customer)
    world.recorder.clear()

    await world.send("1")

    after = await world.lead_by_id(offered["_id"])
    assert after["status"] == LeadStatus.CANCELLED
    assert after["pro_id"] == offered["pro_id"]
    world.recorder.assert_text_to(
        world.customer, Messages.Customer.REASSIGN_REPLY_REQUEST_CLOSED
    )
