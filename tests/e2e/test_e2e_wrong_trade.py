"""PRO-215: a customer is never left talking to a pro from the wrong trade.

Seen on staging: "קצר במטבח" was assigned to a plumber, and the bot then answered
in the plumber's name that he is not an electrician. These run real routing over
the geo shim, because the bug was routing never reading the issue at all.
"""

from unittest.mock import AsyncMock

import pytest
from bson import ObjectId

from app.core.constants import LeadStatus, UserStates
from app.core.messages import Messages
from app.core.prompts import Prompts
from tests.e2e import reserved_numbers as R
from tests.e2e.ai_replay import deal, reply
from tests.e2e.world import media_url_for


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
    # It has a trade, so this is a coverage gap, not an unknown trade.
    assert "escalation_reason" not in lead
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
@pytest.mark.parametrize("trade", ["handyman", "general"])
async def test_a_held_electrical_lead_is_not_lost_to_the_electrical_rule(world, trade):
    """That rule is for new assignment. No flag, and an electrician is free to take
    the job, yet the pro already holding it keeps it."""
    holder = await world.add_pro(R.PRO_PRIMARY, name="בעל מקצוע", profession=trade)
    await _electrician(world)
    await world.grant_consent()
    await _contacted_lead(world, holder)
    world.ai.script(reply("שלום, כאן בעל המקצוע."))

    await world.send("מתי אתה מגיע?")

    lead = await world.lead()
    assert lead["pro_id"] == holder["_id"]
    assert lead["status"] == LeadStatus.CONTACTED
    world.recorder.assert_silent(world.pro_chat(R.PRO_PRIMARY), "nothing was dropped")


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
async def test_vague_issue_records_the_trade_and_never_the_customers_words(world):
    """The dispatcher is skipped once a pro is assigned, so this message is all that
    can reveal the trade. `issue_type` reaches unapproved pros, logs and pages (PRO-59),
    so it takes the trade's name and none of what the customer typed."""
    plumber = await _plumber(world)
    await _electrician(world)
    await world.grant_consent()
    await _contacted_lead(world, plumber, issue_type="תקלה במטבח")
    world.ai.script(reply("רגע."), reply("שלום, כאן דני החשמלאי."))

    await world.send("הפקקים קפצו אצל לוי, דיזנגוף 50, טלפון 0501234567")

    lead = await world.lead()
    assert lead["issue_type"] == "תקלה במטבח (חשמלאי)"
    for pro_phone in (R.PRO_PRIMARY, R.PRO_SECONDARY):
        for body in world.recorder.bodies_to(world.pro_chat(pro_phone)):
            for private in ("0501234567", "דיזנגוף", "לוי"):
                assert private not in body


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    ["משפחת לוי", "זמן קצר", "דוד המלך 10", "דלת ברזל", "הבטיח", "אצבע", "השקעה"],
)
async def test_ordinary_words_leave_the_pro_alone(world, text):
    """A cleaner fits none of the trades these contain by substring, so any
    misreading would drop them."""
    cleaner = await world.add_pro(
        R.PRO_PRIMARY, name="מיכל ניקיון", profession="cleaner"
    )
    await world.grant_consent()
    await _contacted_lead(world, cleaner, issue_type="תקלה במטבח")
    world.ai.script(reply("שלום, כאן מיכל."))

    await world.send(text)

    lead = await world.lead()
    assert lead["pro_id"] == cleaner["_id"]
    assert lead["issue_type"] == "תקלה במטבח"
    world.recorder.assert_text_to(world.customer, "כאן מיכל")


@pytest.mark.asyncio
async def test_passing_mention_of_another_trade_leaves_a_fitting_pro_alone(world):
    plumber = await _plumber(world)
    await _electrician(world)
    await world.grant_consent()
    await _contacted_lead(world, plumber, issue_type="נזילה במטבח")
    world.ai.script(reply("שלום, כאן אבי."))

    await world.send("השקע ליד הכיור לא עובד")

    lead = await world.lead()
    assert lead["pro_id"] == plumber["_id"]
    assert lead["issue_type"] == "נזילה במטבח"


@pytest.mark.asyncio
async def test_contacted_drop_tells_the_pro_and_the_customer(world):
    plumber = await _plumber(world)
    await _electrician(world)
    await world.grant_consent()
    await _contacted_lead(world, plumber, quoted_price="450")
    world.ai.script(
        reply("רגע.", city="תל אביב", issue="קצר במטבח"),
        reply("שלום, כאן דני החשמלאי."),
    )

    await world.send("מתי אתה מגיע?")

    assert "quoted_price" not in await world.lead()
    world.recorder.assert_text_to(
        world.pro_chat(R.PRO_PRIMARY),
        Messages.Pro.LEAD_OUTSIDE_TRADE.format(issue="קצר במטבח"),
    )
    world.recorder.assert_text_to(
        world.customer, Messages.Customer.MOVING_TO_FITTING_PRO
    )
    assert Messages.SOS.PRO_LOST_LEAD not in world.recorder.bodies_to(
        world.pro_chat(R.PRO_PRIMARY)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement_exists", [True, False])
async def test_offered_drop_tells_the_pro_on_every_outcome(world, replacement_exists):
    plumber = await _plumber(world)
    if replacement_exists:
        await _electrician(world)
    await world.grant_consent()
    await world.awaiting_approval_job(plumber, issue_type="קצר במטבח")
    await world.set_state(UserStates.IDLE)

    await world.send("מתי אתה מגיע?")

    world.recorder.assert_text_to(
        world.pro_chat(R.PRO_PRIMARY),
        Messages.Pro.LEAD_OUTSIDE_TRADE_OFFER.format(issue="קצר במטבח"),
    )
    expected = LeadStatus.NEW if replacement_exists else LeadStatus.PENDING_ADMIN_REVIEW
    assert (await world.lead())["status"] == expected
    # Its apology for the wait would be false here.
    assert Messages.SOS.CUSTOMER_REASSIGNING not in world.recorder.bodies_to(
        world.customer
    )


@pytest.mark.asyncio
async def test_offered_drop_records_the_turn_before_reassigning(world):
    plumber = await _plumber(world)
    electrician = await _electrician(world)
    await world.grant_consent()
    await world.awaiting_approval_job(plumber, issue_type="קצר במטבח")
    await world.set_state(UserStates.IDLE)

    await world.send("הנה תמונה", media="image")

    lead = await world.lead()
    assert lead["pro_id"] == electrician["_id"]
    assert media_url_for("image") in lead["media_urls"]
    assert media_url_for("image") in "".join(
        world.recorder.bodies_to(world.pro_chat(R.PRO_SECONDARY))
    )


@pytest.mark.asyncio
async def test_offered_drop_records_an_emergency_declared_this_turn(world):
    plumber = await _plumber(world)
    electrician = await _electrician(world)
    await world.grant_consent()
    await world.awaiting_approval_job(plumber, issue_type="קצר במטבח")
    await world.set_state(UserStates.IDLE)

    await world.send("יש שריפה בלוח")

    lead = await world.lead()
    assert lead["is_emergency"] is True
    assert lead["pro_id"] == electrician["_id"]


@pytest.mark.asyncio
async def test_a_blocked_notice_is_logged(world, monkeypatch):
    from app.core.logger import logger
    from app.services import workflow_service

    plumber = await _plumber(world)
    await world.grant_consent()
    lead = await _contacted_lead(world, plumber)
    world.ai.script(reply("רגע.", city="תל אביב", issue="קצר במטבח"))
    real_send = workflow_service.whatsapp.send_message
    pro_chat = world.pro_chat(R.PRO_PRIMARY)

    async def blocked_for_the_pro(chat_id, text, *args, **kwargs):
        if chat_id == pro_chat:
            return None
        return await real_send(chat_id, text, *args, **kwargs)

    monkeypatch.setattr(workflow_service.whatsapp, "send_message", blocked_for_the_pro)
    logged: list[str] = []
    sink_id = logger.add(lambda message: logged.append(str(message)), level="WARNING")
    try:
        await world.send("מתי אתה מגיע?")
    finally:
        logger.remove(sink_id)

    assert any(
        "did not" in line and str(lead["_id"]) in line for line in logged
    ), logged


@pytest.mark.asyncio
async def test_emergency_without_an_issue_pages_and_reaches_review(world):
    """The dispatcher may keep asking what happened. An emergency doesn't wait."""
    await _plumber(world)
    await world.grant_consent()
    world.ai.script(reply("מה בדיוק קרה?", city="תל אביב"))

    await world.send("יש שריפה בבית בתל אביב")

    lead = await world.lead()
    assert lead["status"] == LeadStatus.PENDING_ADMIN_REVIEW
    assert lead["escalation_reason"] == "unknown_trade"
    world.assert_paged("EMERGENCY", str(lead["_id"]), "names no known trade")
    world.recorder.assert_text_to(world.customer, Messages.Customer.PENDING_REVIEW)


def test_the_prompt_allows_the_city_that_test_scripts():
    """The test above scripts a city on the first turn; the prompt must allow it."""
    assert "EMERGENCY EXCEPTION" in Prompts.DISPATCHER_SYSTEM
    assert "including the first message" in Prompts.DISPATCHER_SYSTEM


@pytest.mark.asyncio
async def test_an_extracted_issue_carrying_a_phone_never_reaches_the_lead(world):
    await _plumber(world)
    await world.grant_consent()
    world.ai.script(reply("מה קרה?", city="תל אביב", issue="נזילה, 050-1234567"))

    await world.send("נזילה בתל אביב, הטלפון שלי 050-1234567")

    lead = await world.lead()
    assert "1234567" not in lead["issue_type"]
    world.recorder.assert_silent(world.pro_chat(R.PRO_PRIMARY), "no issue yet")


@pytest.mark.asyncio
async def test_a_trade_named_in_chat_is_kept_when_the_pro_fits(world):
    """Otherwise a later passing mention of another trade is read as the first."""
    plumber = await _plumber(world)
    await _electrician(world)
    await world.grant_consent()
    await _contacted_lead(world, plumber, issue_type="תקלה במטבח")
    world.ai.script(reply("הבנתי."), reply("בסדר."))

    await world.send("יש נזילה")

    lead = await world.lead()
    assert lead["issue_type"] == "תקלה במטבח (אינסטלטור)"
    assert lead["pro_id"] == plumber["_id"]

    await world.send("גם השקע לא עובד")

    assert (await world.lead())["pro_id"] == plumber["_id"]


@pytest.mark.asyncio
async def test_a_placeholder_issue_gets_a_hebrew_word_before_the_label(world):
    plumber = await _plumber(world)
    await world.grant_consent()
    await _contacted_lead(world, plumber, issue_type="Issue")
    world.ai.script(reply("רגע.", city="תל אביב"))

    await world.send("נפל הזרם בכל הבית")

    assert (await world.lead())["issue_type"] == "תקלה (חשמלאי)"
    world.recorder.assert_text_to(
        world.pro_chat(R.PRO_PRIMARY),
        Messages.Pro.LEAD_OUTSIDE_TRADE.format(issue="תקלה (חשמלאי)"),
    )


@pytest.mark.asyncio
async def test_emergency_with_no_replacement_pages_the_operator(world):
    plumber = await _plumber(world)
    await world.grant_consent()
    lead = await world.awaiting_approval_job(
        plumber, issue_type="קצר במטבח", is_emergency=True
    )
    await world.set_state(UserStates.IDLE)

    await world.send("מתי אתה מגיע?")

    world.assert_paged("EMERGENCY", str(lead["_id"]))


@pytest.mark.asyncio
async def test_a_raising_reassignment_does_not_strand_the_lead(world, monkeypatch):
    from app.services import monitor_service

    plumber = await _plumber(world)
    await world.grant_consent()
    await world.awaiting_approval_job(plumber, issue_type="קצר במטבח")
    await world.set_state(UserStates.IDLE)
    monkeypatch.setattr(
        monitor_service,
        "reassign_lead",
        AsyncMock(side_effect=RuntimeError("boom")),
    )

    await world.send("מתי אתה מגיע?")

    lead = await world.lead()
    assert lead["status"] == LeadStatus.PENDING_ADMIN_REVIEW
    assert lead["escalation_reason"] == "wrong_trade_rematch_failed"
    world.recorder.assert_text_to(world.customer, Messages.Customer.PENDING_REVIEW)


@pytest.mark.asyncio
async def test_a_lead_that_moved_first_is_left_alone(world, monkeypatch):
    """The pro approved between this turn's read and its write."""
    from app.services import workflow_service

    plumber = await _plumber(world)
    await world.grant_consent()
    await _contacted_lead(world, plumber)
    real = workflow_service.leads_collection

    class MovesFirst:
        def __getattr__(self, name):
            return getattr(real, name)

        async def update_one(self, query, update, **kwargs):
            if "status" in query:
                await real.update_one(
                    {"_id": query["_id"]}, {"$set": {"status": LeadStatus.BOOKED}}
                )
            return await real.update_one(query, update, **kwargs)

    monkeypatch.setattr(workflow_service, "leads_collection", MovesFirst())

    await world.send("מתי אתה מגיע?")

    lead = await world.lead()
    assert lead["status"] == LeadStatus.BOOKED
    assert lead["pro_id"] == plumber["_id"]
    world.recorder.assert_silent(world.pro_chat(R.PRO_PRIMARY), "nothing was dropped")


@pytest.mark.asyncio
async def test_a_vaguer_extraction_does_not_lift_the_trade(world):
    """Otherwise the dropped plumber is matched straight back."""
    plumber = await _plumber(world)
    await world.grant_consent()
    await _contacted_lead(world, plumber)
    world.ai.script(reply("רגע.", city="תל אביב", issue="תקלה במטבח"))

    await world.send("מתי אתה מגיע?")

    lead = await world.lead()
    assert lead["issue_type"] == "קצר במטבח"
    assert lead["pro_id"] is None
    assert lead["status"] == LeadStatus.PENDING_ADMIN_REVIEW


@pytest.mark.asyncio
async def test_closing_a_deal_keeps_the_classified_issue(world):
    """`_finalize_deal` writes the persona's extraction over the lead."""
    electrician = await _electrician(world)
    await world.grant_consent()
    await _contacted_lead(world, electrician)
    world.ai.script(
        deal(
            "קבענו!",
            city="תל אביב",
            issue="תקלה במטבח",
            street="דיזנגוף",
            street_number="50",
            floor="3",
            apartment="12",
            appointment_time="מחר ב-10:00",
        )
    )

    await world.send("דיזנגוף 50 קומה 3 דירה 12, מחר ב-10")

    lead = await world.lead()
    assert lead["status"] == LeadStatus.NEW
    assert lead["issue_type"] == "קצר במטבח"


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
async def test_unknown_trade_escalates_at_the_point_of_matching(world):
    plumber = await _plumber(world)
    await world.grant_consent()
    world.ai.script(reply("הבנתי.", city="תל אביב", issue="המזגן לא מקרר"))

    await world.send("המזגן לא מקרר, תל אביב")

    lead = await world.lead()
    assert lead["status"] == LeadStatus.PENDING_ADMIN_REVIEW
    assert lead["pro_id"] is None
    assert lead["escalation_reason"] == "unknown_trade"
    world.recorder.assert_text_to(world.customer, Messages.Customer.PENDING_REVIEW)
    world.recorder.assert_silent(
        world.pro_chat(R.PRO_PRIMARY), plumber["business_name"]
    )


@pytest.mark.asyncio
async def test_a_vague_first_message_does_not_escalate(world):
    """Nothing is extracted yet, so matching hasn't run: the dispatcher asks, and the
    next turn's answer routes normally."""
    plumber = await _plumber(world)
    await world.grant_consent()
    world.ai.script(
        reply("מה בדיוק קרה?", city="תל אביב"),
        reply("הבנתי.", city="תל אביב", issue="נזילה מתחת לכיור"),
        reply("שלום, כאן אבי."),
    )

    await world.send("יש לי בעיה בבית בתל אביב")

    lead = await world.lead()
    assert lead["status"] == LeadStatus.CONTACTED
    world.recorder.assert_never_contains(
        Messages.Customer.PENDING_REVIEW, "the dispatcher is still asking"
    )

    await world.send("נזילה מתחת לכיור")

    lead = await world.lead()
    assert lead["status"] == LeadStatus.CONTACTED
    assert lead["pro_id"] == plumber["_id"]


@pytest.mark.asyncio
async def test_unknown_trade_leaves_a_pro_already_holding_the_lead(world):
    """The rule is for a new assignment."""
    plumber = await _plumber(world)
    await world.grant_consent()
    await _contacted_lead(world, plumber, issue_type="תקלה במטבח")
    world.ai.script(reply("שלום, כאן אבי."))

    await world.send("מתי אתה מגיע?")

    lead = await world.lead()
    assert lead["pro_id"] == plumber["_id"]
    assert lead["status"] == LeadStatus.CONTACTED


@pytest.mark.asyncio
async def test_unknown_trade_makes_no_loyalty_offer(world):
    plumber = await _plumber(world)
    await world.grant_consent()
    await world.db.leads.insert_one(
        {
            "chat_id": world.customer,
            "status": LeadStatus.COMPLETED,
            "pro_id": plumber["_id"],
            "issue_type": "נזילה",
            "created_at": world.anchor,
        }
    )
    world.ai.script(reply("הבנתי.", city="תל אביב", issue="המזגן לא מקרר"))

    await world.send("המזגן לא מקרר, תל אביב")

    await world.assert_lead_status(LeadStatus.PENDING_ADMIN_REVIEW)
    assert plumber["business_name"] not in "".join(
        world.recorder.bodies_to(world.customer)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "trade,flag,gets_the_job",
    [
        ("electrician", False, True),
        ("handyman", True, True),
        ("general", True, True),
        ("handyman", False, False),
        ("general", False, False),
    ],
)
async def test_electrical_issue_routing(world, trade, flag, gets_the_job):
    pro = await world.add_pro(R.PRO_PRIMARY, name="בעל מקצוע", profession=trade)
    await world.db.users.update_one(
        {"_id": pro["_id"]}, {"$set": {"handles_electrical": flag}}
    )
    await world.grant_consent()
    world.ai.script(
        reply("הבנתי.", city="תל אביב", issue="קצר במטבח"),
        reply("שלום."),
    )

    await world.send("יש קצר במטבח בתל אביב")

    lead = await world.lead()
    if gets_the_job:
        assert lead["pro_id"] == pro["_id"]
    else:
        assert lead["pro_id"] is None
        assert lead["status"] == LeadStatus.PENDING_ADMIN_REVIEW


# --- PRO-225: the current message's trade, and never the dropped pro again ---


@pytest.mark.asyncio
@pytest.mark.parametrize("offered", [False, True])
async def test_a_message_naming_the_pros_trade_keeps_them(world, offered):
    """The staging sequence: a lead stored as "קצר במטבח", held by a plumber, and
    the customer now writes about a leaking sink. The plumber fits what they say
    now, so nothing moves and nobody is told anything moved."""
    plumber = await _plumber(world)
    await _electrician(world)
    await world.grant_consent()
    if offered:
        await world.awaiting_approval_job(plumber, issue_type="קצר במטבח")
        await world.set_state(UserStates.IDLE)
    else:
        await _contacted_lead(world, plumber)
    world.ai.script(reply("שלום, כאן אבי."))

    await world.send("יש לי נזילה בכיור בתל אביב")

    lead = await world.lead()
    assert lead["pro_id"] == plumber["_id"]
    assert lead["issue_type"] == "תקלה (אינסטלטור)"
    assert not lead.get("excluded_pro_ids")
    pro_bodies = world.recorder.bodies_to(world.pro_chat(R.PRO_PRIMARY))
    for notice in (
        Messages.Pro.LEAD_OUTSIDE_TRADE,
        Messages.Pro.LEAD_OUTSIDE_TRADE_OFFER,
    ):
        assert notice.format(issue="קצר במטבח") not in pro_bodies
    assert Messages.Customer.MOVING_TO_FITTING_PRO not in world.recorder.bodies_to(
        world.customer
    )


@pytest.mark.asyncio
async def test_an_electrical_message_still_moves_the_lead_and_never_back(world):
    plumber = await _plumber(world)
    electrician = await _electrician(world)
    await world.grant_consent()
    await _contacted_lead(world, plumber)
    world.ai.script(
        reply("רגע.", city="תל אביב", issue="קצר במטבח"),
        reply("שלום, כאן דני החשמלאי."),
    )

    await world.send("יש קצר חשמלי במטבח")

    lead = await world.lead()
    assert lead["pro_id"] == electrician["_id"]
    assert lead["issue_type"] == "קצר במטבח"
    assert lead["excluded_pro_ids"] == [plumber["_id"]]
    world.recorder.assert_text_to(
        world.customer, Messages.Customer.MOVING_TO_FITTING_PRO
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    ["אני לא צריך אינסטלטור, יש קצר", "שלחתם אינסטלטור אבל יש קצר", "יש קצר"],
)
async def test_a_job_title_never_overrides_the_stored_trade(world, text):
    """A title names who was sent, or who isn't wanted, as often as the job."""
    plumber = await _plumber(world)
    electrician = await _electrician(world)
    await world.grant_consent()
    await _contacted_lead(world, plumber)
    world.ai.script(
        reply("רגע.", city="תל אביב", issue="קצר במטבח"),
        reply("שלום, כאן דני החשמלאי."),
    )

    await world.send(text)

    lead = await world.lead()
    assert lead["pro_id"] == electrician["_id"]
    assert lead["issue_type"] == "קצר במטבח"
    assert lead["excluded_pro_ids"] == [plumber["_id"]]


@pytest.mark.asyncio
async def test_an_electricians_title_never_overrides_a_stored_leak(world):
    electrician = await _electrician(world)
    await world.grant_consent()
    await _contacted_lead(world, electrician, issue_type="נזילה במטבח")
    world.ai.script(reply("רגע.", city="תל אביב", issue="נזילה במטבח"))

    await world.send("שלחתם חשמלאי")

    lead = await world.lead()
    assert lead["pro_id"] is None
    assert lead["issue_type"] == "נזילה במטבח"
    assert lead["excluded_pro_ids"] == [electrician["_id"]]


@pytest.mark.parametrize(
    "text,titles,trade",
    [
        ("שלחתם חשמלאי ויש נזילה", True, "plumber"),
        ("צריך אינסטלטור", True, "plumber"),
        ("צריך אינסטלטור", False, None),
        ("יש נזילה וגם הפקקים קפצו", True, None),
    ],
)
def test_fault_words_outrank_job_titles(text, titles, trade):
    from app.services.matching_service import trade_named_in_chat

    assert trade_named_in_chat(text, titles=titles) == trade


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement_exists", [True, False])
async def test_an_offered_drop_says_it_moved_only_once_it_has(
    world, replacement_exists
):
    plumber = await _plumber(world)
    if replacement_exists:
        await _electrician(world)
    await world.grant_consent()
    await world.awaiting_approval_job(plumber, issue_type="קצר במטבח")
    await world.set_state(UserStates.IDLE)

    await world.send("מתי אתה מגיע?")

    bodies = world.recorder.bodies_to(world.customer)
    assert Messages.Customer.MOVING_TO_FITTING_PRO not in bodies
    if replacement_exists:
        world.recorder.assert_text_to(world.customer, "דני החשמלאי")
    else:
        world.recorder.assert_text_to(world.customer, Messages.Customer.PENDING_REVIEW)


@pytest.mark.asyncio
async def test_the_dropped_pro_is_not_matched_straight_back(world):
    """The dispatcher re-extracts a plumbing issue after the drop. Matching used to
    find the plumber it had just dropped, and the customer heard "אעביר…" and then
    the same plumber."""
    plumber = await _plumber(world)
    await world.grant_consent()
    await _contacted_lead(world, plumber)
    world.ai.script(
        reply("רגע.", city="תל אביב", issue="נזילה בכיור"),
        reply("שלום, כאן אבי."),
    )

    await world.send("מתי אתה מגיע?")

    lead = await world.lead()
    assert lead["pro_id"] is None
    assert lead["status"] == LeadStatus.PENDING_ADMIN_REVIEW
    assert Messages.Pro.EARLY_LEAD_HEADER not in "".join(
        world.recorder.bodies_to(world.pro_chat(R.PRO_PRIMARY))
    )
    bodies = world.recorder.bodies_to(world.customer)
    assert not any("כאן אבי" in body for body in bodies)
    # It went to review, which says so itself; "אעביר…" would promise a pro.
    assert Messages.Customer.MOVING_TO_FITTING_PRO not in bodies


@pytest.mark.asyncio
async def test_reassignment_never_picks_a_pro_dropped_for_trade(world):
    from app.services import monitor_service

    plumber = await _plumber(world)
    await world.grant_consent()
    lead = await _contacted_lead(
        world,
        plumber,
        pro_id=None,
        issue_type="נזילה בכיור",
        excluded_pro_ids=[plumber["_id"]],
    )

    assert not await monitor_service.reassign_lead(lead, announce=False)

    assert (await world.lead())["pro_id"] is None
    world.recorder.assert_silent(world.pro_chat(R.PRO_PRIMARY), "dropped for trade")


@pytest.mark.asyncio
async def test_a_pro_dropped_for_trade_cannot_claim_the_lead_back(world):
    """`מצא` reads this predicate."""
    from app.services.matching_service import is_pro_eligible_for_lead

    plumber = await _plumber(world)
    lead = {
        "chat_id": world.customer,
        "issue_type": "נזילה בכיור",
        "city": "תל אביב",
    }
    assert await is_pro_eligible_for_lead(plumber, lead)

    lead["excluded_pro_ids"] = [plumber["_id"]]
    assert not await is_pro_eligible_for_lead(plumber, lead)
