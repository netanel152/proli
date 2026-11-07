"""PRO-223: a pro who writes in as a customer is never offered their own lead.

Seen on staging: the owner's phone is both the test customer and an electrician,
so an electrical lead in his own area could be matched to himself. These run
real routing over the geo shim.
"""

import pytest

from app.core.constants import LeadStatus, UserStates
from app.services import matching_service
from app.services.monitor_service import reassign_lead
from tests.e2e import reserved_numbers as R
from tests.e2e.ai_replay import reply


async def _cast(world):
    """The customer is the best-rated electrician; another one is close behind."""
    plumber = await world.add_pro(R.PRO_PRIMARY, name="אבי אינסטלציה", rating=3.0)
    self_pro = await world.add_pro(
        R.CUSTOMER, name="מוטי חשמל", rating=5.0, profession="electrician"
    )
    other = await world.add_pro(
        R.PRO_SECONDARY, name="דני החשמלאי", rating=4.0, profession="electrician"
    )
    return plumber, self_pro, other


async def _electrical_lead(world, pro, chat_id=None):
    return await world.awaiting_approval_job(
        pro, issue_type="קצר במטבח", **({"chat_id": chat_id} if chat_id else {})
    )


@pytest.mark.asyncio
async def test_first_match_skips_the_customers_own_pro_profile(world):
    """The staging case: the pro's open lead keeps them in customer mode, and the
    plumber holding it is rematched to an electrician — not to the customer."""
    plumber, self_pro, other = await _cast(world)
    await world.grant_consent()
    await world.db.leads.insert_one(
        {
            "chat_id": world.customer,
            "status": LeadStatus.CONTACTED,
            "pro_id": plumber["_id"],
            "issue_type": "קצר במטבח",
            "full_address": "תל אביב",
            "city": "תל אביב",
            "appointment_time": "Pending",
            "created_at": world.anchor,
            "status_history": [],
        }
    )
    world.ai.script(
        reply("רגע.", city="תל אביב", issue="קצר במטבח"),
        reply("שלום, כאן דני החשמלאי."),
    )

    await world.send("מתי אתה מגיע?")

    assert (await world.lead())["pro_id"] == other["_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stored", [R.CUSTOMER, R.chat(R.CUSTOMER), "0" + R.CUSTOMER[3:]]
)
async def test_matching_excludes_the_customer_in_any_stored_format(world, stored):
    _, self_pro, other = await _cast(world)
    await world.db.users.update_one(
        {"_id": self_pro["_id"]}, {"$set": {"phone_number": stored}}
    )

    pro = await matching_service.determine_best_pro(
        issue_type="קצר במטבח", location="תל אביב", customer_chat_id=world.customer
    )

    assert pro["_id"] == other["_id"]


@pytest.mark.asyncio
async def test_reassignment_skips_the_customers_own_pro_profile(world):
    plumber, _, other = await _cast(world)
    lead = await _electrical_lead(world, plumber)

    assert await reassign_lead(await world.lead_by_id(lead["_id"])) is True

    assert (await world.lead_by_id(lead["_id"]))["pro_id"] == other["_id"]


@pytest.mark.asyncio
async def test_the_same_pro_still_gets_another_customers_lead(world):
    plumber, self_pro, _ = await _cast(world)
    lead = await _electrical_lead(world, plumber, chat_id=R.chat(R.CUSTOMER_B))

    assert await reassign_lead(await world.lead_by_id(lead["_id"])) is True

    assert (await world.lead_by_id(lead["_id"]))["pro_id"] == self_pro["_id"]
    assert await matching_service.is_pro_eligible_for_lead(
        self_pro, {**lead, "issue_type": "קצר במטבח"}
    )


@pytest.mark.asyncio
async def test_pro_search_cannot_claim_their_own_lead(world):
    """`מצא` reads the same gates, so it is no back door either."""
    _, self_pro, _ = await _cast(world)
    lead = {
        "chat_id": world.customer,
        "issue_type": "קצר במטבח",
        "city": "תל אביב",
    }

    assert not await matching_service.is_pro_eligible_for_lead(self_pro, lead)


@pytest.mark.asyncio
async def test_no_loyalty_offer_names_the_customer_themselves(world):
    """A past job already matched to themselves must not come back as an offer."""
    _, self_pro, other = await _cast(world)
    await world.grant_consent()
    await world.db.leads.insert_one(
        {
            "chat_id": world.customer,
            "status": LeadStatus.COMPLETED,
            "pro_id": self_pro["_id"],
            "issue_type": "קצר בחשמל",
            "created_at": world.anchor,
        }
    )
    await world.set_state(UserStates.CUSTOMER_MODE)
    world.ai.script(
        reply("הבנתי.", city="תל אביב", issue="קצר במטבח"),
        reply("שלום, כאן דני החשמלאי."),
    )

    await world.send("יש קצר במטבח בתל אביב")

    assert self_pro["business_name"] not in "".join(
        world.recorder.bodies_to(world.customer)
    )
    lead = await world.lead(status={"$ne": LeadStatus.COMPLETED})
    assert lead["pro_id"] == other["_id"]
