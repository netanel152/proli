"""PRO-215: a customer is never left talking to a pro from the wrong trade.

Seen on staging: "קצר במטבח" was assigned to a plumber, and the bot then answered
in the plumber's name that he is not an electrician. These run real routing over
the geo shim, because the bug was routing never reading the issue at all.
"""

import pytest
from bson import ObjectId

from app.core.constants import LeadStatus, UserStates
from app.core.messages import Messages
from tests.e2e import reserved_numbers as R
from tests.e2e.ai_replay import reply


async def _plumber(world):
    return await world.add_pro(R.PRO_PRIMARY, name="אבי אינסטלציה", rating=5.0)


async def _electrician(world, city="תל אביב"):
    return await world.add_pro(
        R.PRO_SECONDARY,
        name="דני החשמלאי",
        city=city,
        rating=4.0,
        service_areas=[city],
        profession="electrician",
    )


async def _contacted_lead(world, pro, **overrides):
    """A lead sitting with `pro` mid-intake, as the staging one was."""
    lead = {
        "_id": ObjectId(),
        "chat_id": world.customer,
        "status": LeadStatus.CONTACTED,
        "pro_id": pro["_id"],
        "issue_type": "קצר במטבח",
        "full_address": "תל אביב",
        "city": "תל אביב",
        "appointment_time": "Pending",
        "created_at": world.anchor,
        "status_history": [],
    }
    lead.update(overrides)
    await world.db.leads.insert_one(lead)
    return lead


@pytest.mark.asyncio
async def test_electrical_issue_with_only_a_plumber_goes_to_admin_review(world):
    plumber = await _plumber(world)
    await world.grant_consent()
    world.ai.script(reply("הבנתי.", city="תל אביב", issue="קצר במטבח"))

    await world.send("יש קצר במטבח בתל אביב")

    lead = await world.lead()
    assert lead["status"] == LeadStatus.PENDING_ADMIN_REVIEW
    assert lead["pro_id"] is None
    world.recorder.assert_text_to(world.customer, Messages.Customer.PENDING_REVIEW)
    world.recorder.assert_silent(
        world.pro_chat(R.PRO_PRIMARY),
        f"{plumber['business_name']} is not an electrician",
    )


@pytest.mark.asyncio
async def test_matching_widens_the_radius_past_a_nearer_pro_of_the_wrong_trade(world):
    """Rehovot is past the 20 km step from Tel Aviv. A better-rated plumber in the
    first step must not stop the search reaching the electrician."""
    await _plumber(world)
    electrician = await _electrician(world, city="רחובות")
    await world.grant_consent()
    world.ai.script(
        reply("הבנתי.", city="תל אביב", issue="קצר במטבח"),
        reply("שלום, כאן דני החשמלאי."),
    )

    await world.send("יש קצר במטבח בתל אביב")

    assert (await world.lead())["pro_id"] == electrician["_id"]
    world.recorder.assert_silent(world.pro_chat(R.PRO_PRIMARY), "wrong trade")


@pytest.mark.asyncio
async def test_lead_already_with_the_wrong_trade_is_rematched(world):
    plumber = await _plumber(world)
    electrician = await _electrician(world)
    await world.grant_consent()
    await _contacted_lead(world, plumber)
    world.ai.script(
        reply("רגע.", city="תל אביב", issue="קצר במטבח"),
        reply("שלום, כאן דני החשמלאי."),
    )

    await world.send("מתי אתה מגיע?")

    assert (await world.lead())["pro_id"] == electrician["_id"]
    world.recorder.assert_text_to(world.customer, "כאן דני החשמלאי")
    world.recorder.assert_text_to(
        world.pro_chat(R.PRO_SECONDARY), Messages.Pro.EARLY_LEAD_HEADER
    )


@pytest.mark.asyncio
async def test_lead_with_the_wrong_trade_and_nobody_fitting_escalates(world):
    plumber = await _plumber(world)
    await world.grant_consent()
    await _contacted_lead(world, plumber)
    world.ai.script(reply("רגע.", city="תל אביב", issue="קצר במטבח"))

    await world.send("מתי אתה מגיע?")

    lead = await world.lead()
    assert lead["status"] == LeadStatus.PENDING_ADMIN_REVIEW
    assert lead["pro_id"] is None
    world.recorder.assert_text_to(world.customer, Messages.Customer.PENDING_REVIEW)


@pytest.mark.asyncio
async def test_offered_lead_with_the_wrong_trade_goes_through_reassignment(world):
    plumber = await _plumber(world)
    electrician = await _electrician(world)
    await world.grant_consent()
    await world.awaiting_approval_job(plumber, issue_type="קצר במטבח")
    await world.set_state(UserStates.IDLE)

    await world.send("מתי אתה מגיע?")

    lead = await world.lead()
    assert lead["status"] == LeadStatus.NEW
    assert lead["pro_id"] == electrician["_id"]
    assert lead["reassigned_from"] == plumber["_id"]
    world.recorder.assert_text_to(world.customer, "דני החשמלאי")


@pytest.mark.asyncio
async def test_vague_issue_is_read_with_what_the_customer_just_wrote(world):
    """The dispatcher is skipped once a pro is assigned, so a vague issue never gets
    refined. The customer's own words are all that can reveal the trade."""
    plumber = await _plumber(world)
    await world.grant_consent()
    await _contacted_lead(world, plumber, issue_type="תקלה במטבח")
    world.ai.script(reply("רגע.", city="תל אביב", issue="תקלה במטבח קצר בכל המטבח"))

    await world.send("קצר בכל המטבח")

    lead = await world.lead()
    assert lead["issue_type"] == "תקלה במטבח קצר בכל המטבח"
    assert lead["status"] == LeadStatus.PENDING_ADMIN_REVIEW


@pytest.mark.asyncio
async def test_a_humans_explicit_assignment_stands(world):
    """The panel and the wizard can assign any pro on purpose. The fast path must not
    undo that on the customer's next message."""
    plumber = await _plumber(world)
    await _electrician(world)
    await world.grant_consent()
    await _contacted_lead(world, plumber, assigned_by_admin_at=world.anchor)
    world.ai.script(reply("שלום, כאן אבי."))

    await world.send("מתי אתה מגיע?")

    assert (await world.lead())["pro_id"] == plumber["_id"]
    world.recorder.assert_text_to(world.customer, "כאן אבי")


@pytest.mark.asyncio
async def test_unknown_trade_imposes_no_constraint(world):
    """An issue naming no trade keeps today's behaviour: the best pro nearby."""
    plumber = await _plumber(world)
    await world.grant_consent()
    world.ai.script(
        reply("הבנתי.", city="תל אביב", issue="משהו לא עובד במטבח"),
        reply("שלום, כאן אבי."),
    )

    await world.send("משהו לא עובד במטבח, תל אביב")

    assert (await world.lead())["pro_id"] == plumber["_id"]
