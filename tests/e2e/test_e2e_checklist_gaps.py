"""Pilot-checklist behaviours that only mocked unit tests covered until now.

Each test maps to a scenario in ``docs/PILOT_E2E_CHECKLIST.md`` and drives the
production orchestrator through ``world``: the real guard chain, the real
facade over the recording provider, mongomock and fakeredis. What each one
adds over its unit counterpart is that the stores are read back for real — a
lead, a ``messages`` row, a consent row and a Redis key are either there or
not, rather than a mock having been called or not.

* scenario 13 — a life-threatening report (PRO-222)
* scenario 2  — the emergency lane, as the pro sees it (PRO-121)
* scenario 6  — the status pull at three lifecycle stages
* scenario 9  — a voice note as the opening message
* scenario 14 — three messages sent without waiting (PRO-214)
* scenario 15b — the pro's approval racing the customer's "find someone else"
"""

import asyncio
from datetime import datetime, timezone

import pytest
from arq import Retry

from app.core import chat_order
from app.core.arq_worker import process_message_task
from app.core.constants import LeadStatus, UserStates, WorkerConstants
from app.core.messages import Messages
from app.core.phone import to_local_phone
from app.services import workflow_service
from tests.copy_util import static_prefix
from tests.e2e import reserved_numbers as R
from tests.e2e.ai_replay import reply
from tests.e2e.world import media_url_for

APPROVE_WORD = Messages.Keywords.APPROVE_COMMANDS[0]
FINISH_WORD = Messages.Keywords.FINISH_COMMANDS[0]

# The checklist's own wording for scenario 13, all of it matched by
# `is_life_threatening_text` (see tests/test_life_threatening.py).
FIRE = "יש שריפה בדירה"
FIRE_AGAIN = "יש שריפה"
GAS = "יש ריח גז"
# Urgent, not life-threatening: the PRO-121 fast path.
FLOOD = "יש לי הצפה בתל אביב!!"


def _hold_key(chat_id: str) -> str:
    return f"consent_hold:{chat_id}"


def _life_threat_pages(world) -> list[str]:
    return [page for page in world.pages if "LIFE-THREATENING" in page]


async def _stored_rows(world, chat_id: str) -> dict:
    """How many documents each customer-keyed collection holds for a chat."""
    return {
        name: await world.db[name].count_documents({"chat_id": chat_id})
        for name in ("leads", "messages", "consent")
    }


# ===========================================================================
# Scenario 13 — a life-threatening report
# ===========================================================================


@pytest.mark.asyncio
async def test_life_threatening_before_consent_stores_nothing_and_pages_once(world):
    """13(a): no consent on record. The notice goes out, the operator is paged,
    and the chat leaves no trace in Mongo or in the pre-consent hold."""
    await world.add_pro(R.PRO_PRIMARY, name="נתנאל אינסטלציה")
    assert await world.db.consent.count_documents({}) == 0  # the arrangement

    await world.send(FIRE)

    assert world.recorder.bodies_to(world.customer) == [
        Messages.Customer.LIFE_THREATENING
    ]
    assert world.recorder.recipients() == [world.customer], "no pro may be told"
    assert await _stored_rows(world, world.customer) == {
        "leads": 0,
        "messages": 0,
        "consent": 0,
    }
    assert await world.redis.exists(_hold_key(world.customer)) == 0
    assert await world.state() != UserStates.AWAITING_CONSENT
    assert world.ai.calls == []

    assert len(_life_threat_pages(world)) == 1, world.pages
    page = world.pages[0]
    assert FIRE not in page, "the page carries none of the customer's words"
    assert R.CUSTOMER not in page, "the number is masked in the page"


@pytest.mark.asyncio
async def test_life_threatening_drops_what_was_held_before_consent(world):
    """13(a), the chat that had already been asked for consent: an ordinary
    opening is held in Redis, and the report that follows discards it."""
    opening = "יש לי נזילה במטבח"
    await world.send(opening)
    # The arrangement: the consent gate asked, and is holding the opening.
    await world.assert_state(UserStates.AWAITING_CONSENT)
    assert await world.redis.exists(_hold_key(world.customer)) == 1
    world.recorder.clear()

    await world.send(GAS)

    assert world.recorder.bodies_to(world.customer) == [
        Messages.Customer.LIFE_THREATENING
    ]
    assert await world.redis.exists(_hold_key(world.customer)) == 0
    assert await world.state() != UserStates.AWAITING_CONSENT
    assert await _stored_rows(world, world.customer) == {
        "leads": 0,
        "messages": 0,
        "consent": 0,
    }
    assert len(_life_threat_pages(world)) == 1, world.pages


@pytest.mark.asyncio
async def test_life_threatening_again_inside_the_window_does_not_page_twice(world):
    """13(c): the customer is answered every time, the operator is paged once."""
    await world.send(FIRE)
    assert len(_life_threat_pages(world)) == 1  # the first report did page

    await world.send(FIRE_AGAIN)

    assert (
        world.recorder.bodies_to(world.customer)
        == [Messages.Customer.LIFE_THREATENING] * 2
    )
    assert len(_life_threat_pages(world)) == 1, world.pages
    ttl = await world.redis.ttl(f"life_threat_paged:{world.customer}")
    assert 0 < ttl <= 600, "the page is held back for ten minutes, not for good"


@pytest.mark.asyncio
async def test_life_threatening_withdraws_an_offer_awaiting_the_pros_approval(world):
    """13(b): the open lead goes to a human, and the pro who still holds the
    offer cannot book it or learn who the customer is."""
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    lead = await world.awaiting_approval_job(pro)
    customer_phone = to_local_phone(world.customer)

    await world.send(GAS)

    assert world.recorder.bodies_to(world.customer) == [
        Messages.Customer.LIFE_THREATENING
    ]
    assert world.recorder.recipients() == [world.customer], "no pro may be told"
    moved = await world.lead_by_id(lead["_id"])
    assert moved["status"] == LeadStatus.PENDING_ADMIN_REVIEW
    assert moved["escalation_reason"] == "life_threatening"
    assert (await world.status_history(lead["_id"]))[-1] == (
        LeadStatus.PENDING_ADMIN_REVIEW
    )
    assert len(await world.leads(chat_id=world.customer)) == 1, "no new lead"
    assert await world.state() != UserStates.AWAITING_PRO_APPROVAL
    assert world.ai.calls == []
    page = world.assert_paged("LIFE-THREATENING", str(lead["_id"]))
    assert GAS not in page
    assert len(_life_threat_pages(world)) == 1

    # The pro answers the offer they were sent before the report.
    world.recorder.clear()
    await world.send(APPROVE_WORD, chat_id=pro_chat)

    after = await world.lead_by_id(lead["_id"])
    assert after["status"] == LeadStatus.PENDING_ADMIN_REVIEW
    assert not after.get("booked_slot_id")
    assert await world.db.slots.count_documents({"is_taken": True}) == 0
    to_pro = world.recorder.bodies_to(pro_chat)
    assert to_pro == [Messages.Pro.NO_PENDING_APPROVALS]
    world.recorder.assert_never_contains(
        customer_phone, "a withdrawn offer must not release the customer's number"
    )
    world.recorder.assert_silent(world.customer, "nothing was booked")


@pytest.mark.asyncio
async def test_a_pro_at_work_writing_the_same_words_gets_no_notice(world):
    """13(d): from a pro in pro mode the words are a pro's message. (A pro
    whose pro mode has lapsed is read as a customer, and gets the notice.)"""
    await world.standard_cast()
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    # A real pro command is what puts a pro in pro mode.
    await world.send(Messages.Keywords.ACTIVE_JOBS_COMMANDS[0], chat_id=pro_chat)
    await world.assert_state(UserStates.PRO_MODE, chat_id=pro_chat)  # arrangement
    world.recorder.clear()
    # Not a request for service: the pro's own command handling answers it.
    world.ai.script_intent(False)

    await world.send(FIRE, chat_id=pro_chat)

    to_pro = world.recorder.bodies_to(pro_chat)
    assert to_pro, "the pro is answered, as a pro"
    assert Messages.Customer.LIFE_THREATENING not in to_pro
    assert world.pages == []
    assert await world.redis.exists(f"life_threat_paged:{pro_chat}") == 0
    await world.assert_state(UserStates.PRO_MODE, chat_id=pro_chat)
    assert await world.db.leads.count_documents({}) == 0


@pytest.mark.asyncio
async def test_an_urgent_flood_before_consent_is_held_not_sent_to_emergency_services(
    world,
):
    """13, the contrast: a flood is urgent, so it takes the ordinary road —
    the consent question, and the message held for the answer."""
    await world.send(FLOOD)

    bodies = world.recorder.bodies_to(world.customer)
    assert bodies, "the customer is asked for consent"
    assert Messages.Customer.LIFE_THREATENING not in bodies
    await world.assert_state(UserStates.AWAITING_CONSENT)
    assert await world.redis.exists(_hold_key(world.customer)) == 1
    assert world.pages == []


# ===========================================================================
# Scenario 2 — the emergency lane, pro side
# ===========================================================================


@pytest.mark.asyncio
async def test_emergency_with_only_a_city_is_offered_to_a_pro_at_once(world):
    """A flood with a city and no street, floor or apartment: the address gate
    gives way, and the pro's offer opens with the emergency header."""
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    mid_intake = "באיזה רחוב ובאיזו קומה?"
    world.ai.script(
        reply("מה הכתובת המדויקת?", city="תל אביב", issue="הצפה"),
        reply(mid_intake),
    )

    await world.send(FLOOD)

    lead = await world.lead()
    assert lead is not None
    assert lead["is_emergency"] is True
    assert lead["status"] == LeadStatus.NEW
    assert lead["pro_id"] == pro["_id"]
    assert lead["city"] == "תל אביב"
    for part in ("street", "street_number", "floor", "apartment"):
        assert not lead.get(part), f"{part} was never given"
    await world.assert_state(UserStates.AWAITING_PRO_APPROVAL)
    assert await world.status_history(lead["_id"]) == [
        LeadStatus.CONTACTED,
        LeadStatus.NEW,
    ]

    offer = world.recorder.assert_text_to(
        pro_chat,
        Messages.Pro.EMERGENCY_LEAD_HEADER,
        static_prefix(Messages.Pro.APPROVAL_REQUEST),
        "תל אביב",
    )
    assert to_local_phone(world.customer) not in offer.body
    # The "no need to act yet" notice would contradict the offer beside it.
    early = [
        body
        for body in world.recorder.bodies_to(pro_chat)
        if Messages.Pro.EARLY_LEAD_HEADER in body
    ]
    assert not early, early
    assert world.recorder.recipients() == [world.customer, pro_chat]

    to_customer = world.recorder.bodies_to(world.customer)
    assert Messages.Customer.EMERGENCY_ACK in to_customer
    world.recorder.assert_text_to(
        world.customer,
        static_prefix(Messages.Customer.AWAITING_APPROVAL_TRANSPARENT),
        pro["business_name"],
    )
    assert mid_intake not in to_customer, "an emergency is not asked for a floor"
    assert _life_threat_pages(world) == []


# ===========================================================================
# Scenario 6 — the status pull at three lifecycle stages
# ===========================================================================


async def _status_pull(world, word: str, lead_id) -> str:
    """Send one status word and return the reply, having checked that the pull
    read the lead and left everything else as it was."""
    before_lead = await world.lead_by_id(lead_id)
    before_state = await world.state()
    before_calls = len(world.ai.calls)
    world.recorder.clear()

    await world.send(word)

    bodies = world.recorder.bodies_to(world.customer)
    assert len(bodies) == 1, world.recorder.transcript()
    assert world.recorder.recipients() == [world.customer]
    assert len(world.ai.calls) == before_calls, "a status pull never reaches the AI"
    assert await world.lead_by_id(lead_id) == before_lead
    assert await world.state() == before_state
    return bodies[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "word",
    [
        Messages.Keywords.STATUS_COMMANDS_WORDS[0],
        Messages.Keywords.STATUS_COMMANDS_EXACT[0],
    ],
)
async def test_status_pull_reports_each_stage_without_the_ai(world, word):
    """Offered, booked, completed: the same lead, moved by the pro's own
    commands, asked about at each stop."""
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    lead = await world.awaiting_approval_job(pro)
    issue = lead["issue_type"]

    # (a) offered to a pro, not yet approved
    offered = await _status_pull(world, word, lead["_id"])
    assert static_prefix(Messages.Customer.STATUS_NEW) in offered
    assert issue in offered
    assert pro["business_name"] not in offered, "nobody is confirmed yet"
    await world.assert_lead_status(LeadStatus.NEW)
    await world.assert_state(UserStates.AWAITING_PRO_APPROVAL)

    # (b) booked
    await world.send(APPROVE_WORD, chat_id=pro_chat)
    await world.assert_lead_status(LeadStatus.BOOKED)  # the arrangement
    booked = await _status_pull(world, word, lead["_id"])
    assert booked == Messages.Customer.STATUS_BOOKED.format(
        pro_name=pro["business_name"],
        issue=issue,
        appointment_time=lead["appointment_time"],
    )
    assert booked != offered

    # (c) completed, with the rating question still open
    await world.send(FINISH_WORD, chat_id=pro_chat)
    done = await world.assert_lead_status(LeadStatus.COMPLETED)
    assert done["waiting_for_rating"] is True  # the arrangement
    completed = await _status_pull(world, word, lead["_id"])
    assert completed == Messages.Customer.STATUS_COMPLETED
    assert await world.reviews() == [], "the status word is not read as a rating"


# ===========================================================================
# Scenario 9 — a voice note
# ===========================================================================


@pytest.mark.asyncio
async def test_voice_note_opening_reaches_the_ai_as_audio_and_finds_a_pro(world):
    """The audio reaches the model as audio, and what the model heard — city
    and issue — builds the lead and picks the pro, as typed text would."""
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    heard = "יש לי נזילה מתחת לכיור בתל אביב"
    answer = "הבנתי, נזילה מתחת לכיור. מה הכתובת המדויקת?"
    world.ai.script(
        reply(
            "שמעתי. מה הכתובת?",
            transcription=heard,
            city="תל אביב",
            issue="נזילה מתחת לכיור",
        ),
        reply(answer),
    )

    await world.send(None, media="audio")

    first = world.ai.calls[0]
    assert first["media_mime"] == "audio/ogg"
    assert first["key"].startswith("dispatcher|audio|")
    assert first["user_text"] == "", "nothing was typed"
    assert first["media_lost"] is False

    lead = await world.lead()
    assert lead is not None
    assert lead["status"] == LeadStatus.CONTACTED
    assert lead["city"] == "תל אביב"
    assert lead["issue_type"] == "נזילה מתחת לכיור"
    assert lead["pro_id"] == pro["_id"]
    assert media_url_for("audio") in lead["media_urls"]

    world.recorder.assert_text_to(
        pro_chat, Messages.Pro.EARLY_LEAD_HEADER, "נזילה מתחת לכיור", "תל אביב"
    )
    assert world.recorder.bodies_to(world.customer) == [answer]


# ===========================================================================
# Scenario 14 — three messages sent without waiting
# ===========================================================================


def _job_ctx(kwargs: dict) -> dict:
    """What arq hands a task: the id the webhook queued it under, and when."""
    return {
        "job_id": kwargs["_job_id"],
        "job_try": 1,
        "enqueue_time": datetime.now(timezone.utc),
    }


async def _run_job(job) -> bool:
    """Run one captured job as a worker slot would. False when it stood aside
    for an earlier message of the same chat."""
    _name, args, kwargs = job
    task_kwargs = {k: v for k, v in kwargs.items() if not k.startswith("_")}
    try:
        await process_message_task(_job_ctx(kwargs), *args, **task_kwargs)
    except Retry:
        return False
    return True


@pytest.mark.asyncio
async def test_three_quick_messages_are_answered_in_the_order_they_were_sent(world):
    """Three deliveries are queued before any runs, and the worker reaches for
    them newest first. Each later one stands aside until the earlier has run,
    so the replies, the stored rows and the lead all follow the send order."""
    await world.standard_cast()
    texts = ["אני דנה מתל אביב", "רחוב דיזנגוף 50", "יש לי נזילה מתחת לכיור"]
    replies = ["נעים מאוד דנה.", "רשמתי את הכתובת.", "מיד בודק מי פנוי."]
    world.ai.script(
        reply(replies[0], customer_name="דנה", city="תל אביב"),
        reply(replies[1], city="תל אביב", street="דיזנגוף", street_number="50"),
        reply("מה הבעיה?", city="תל אביב", issue="נזילה מתחת לכיור"),
        reply(replies[2]),  # the matched pro's persona answers the third
    )

    for index, text in enumerate(texts, start=1):
        result = await world.post_webhook(
            world.webhook_payload(text, id_message=f"wamid.burst-{index}")
        )
        assert result["status"] == "processing_message"
    jobs = list(world._enqueued)
    world._enqueued.clear()
    assert [job[1][1] for job in jobs] == texts  # the arrangement: all queued
    queued = await world.redis.zrange(chat_order.order_key(world.customer), 0, -1)
    assert queued == [job[2]["_job_id"] for job in jobs]
    world.recorder.assert_nothing_sent("nothing has run yet")

    # Out of turn: the newest, then the middle one. Neither may run.
    assert await _run_job(jobs[2]) is False
    assert await _run_job(jobs[1]) is False
    world.recorder.assert_nothing_sent("a later message waits for the earlier one")
    assert world.ai.calls == []
    assert await world.db.messages.count_documents({}) == 0

    # The worker keeps reaching for the newest first until none is left.
    waiting = list(jobs)
    for _ in range(len(jobs)):
        for job in reversed(list(waiting)):
            if await _run_job(job):
                waiting.remove(job)
                break
    assert waiting == []
    await world.settle()

    assert world.recorder.bodies_to(world.customer) == replies
    stored = await world.db.messages.find(
        {"chat_id": world.customer, "role": "user"}
    ).to_list(length=10)
    assert sorted(row["text"] for row in stored) == sorted(texts)
    dispatcher_turns = [
        call["user_text"]
        for call in world.ai.calls
        if call["key"].startswith("dispatcher|")
    ]
    assert dispatcher_turns == texts
    assert world.ai.pending_script == 0

    leads = await world.leads(chat_id=world.customer)
    assert len(leads) == 1
    lead = leads[0]
    assert lead["customer_name"] == "דנה"
    assert lead["city"] == "תל אביב"
    assert lead["street"] == "דיזנגוף"
    assert lead["street_number"] == "50"
    assert lead["issue_type"] == "נזילה מתחת לכיור"
    assert lead["pro_id"] == world.pros[R.PRO_PRIMARY]["_id"]
    assert await world.redis.zcard(chat_order.order_key(world.customer)) == 0


# ===========================================================================
# Scenario 15(b) — the pro approves while the customer asks for someone else
# ===========================================================================


async def _offered_lead(world):
    """A NEW lead whose pro stayed silent until the customer was offered a
    reassignment (the SLA job has run and sent REASSIGN_OFFER)."""
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
    assert offered["reassign_offered"] is True  # the arrangement
    world.recorder.assert_text_to(world.customer, Messages.Customer.REASSIGN_OFFER)
    return offered


@pytest.mark.asyncio
@pytest.mark.parametrize("pro_first", [True, False])
async def test_approval_racing_the_reassign_reply_leaves_one_owner(world, pro_first):
    """Either the lead is booked with the pro who approved, or it is offered
    to the next pro. Never both, and the side that lost learns nothing about
    the customer and holds no slot."""
    offered = await _offered_lead(world)
    primary = world.pros[R.PRO_PRIMARY]
    secondary = world.pros[R.PRO_SECONDARY]
    primary_chat = world.pro_chat(R.PRO_PRIMARY)
    secondary_chat = world.pro_chat(R.PRO_SECONDARY)
    customer_phone = to_local_phone(world.customer)
    world.recorder.clear()

    # The inner function, as the consent-hold race does: the two turns belong
    # to different chats, so no chat lock stands between them either way.
    approve = workflow_service._process_incoming_message_inner(
        primary_chat, APPROVE_WORD
    )
    reassign = workflow_service._process_incoming_message_inner(world.customer, "1")
    await asyncio.gather(*((approve, reassign) if pro_first else (reassign, approve)))
    await world.settle()

    lead = await world.lead_by_id(offered["_id"])
    assert world.recorder.sends, "both sides were answered"
    taken = await world.db.slots.find({"is_taken": True}).to_list(length=10)
    told_the_number = {
        send.chat_id for send in world.recorder.sends if customer_phone in send.body
    }
    offered_to_secondary = [
        body
        for body in world.recorder.bodies_to(secondary_chat)
        if Messages.Pro.NEW_LEAD_HEADER in body
    ]

    assert (lead["status"], lead["pro_id"]) in {
        (LeadStatus.BOOKED, primary["_id"]),
        (LeadStatus.NEW, secondary["_id"]),
    }, f"neither outcome holds: {lead['status']} with {lead['pro_id']}"

    if lead["status"] == LeadStatus.BOOKED:
        # The approval won: one slot, the approving pro's, and nobody else heard.
        assert [slot["pro_id"] for slot in taken] == [primary["_id"]]
        assert lead["booked_slot_id"] == taken[0]["_id"]
        assert told_the_number == {primary_chat}
        assert not offered_to_secondary, offered_to_secondary
        world.recorder.assert_silent(secondary_chat, "the lead was never moved")
        assert not lead.get("reassignment_count")
        world.recorder.assert_text_to(
            world.customer,
            static_prefix(Messages.Customer.PRO_FOUND),
            primary["business_name"],
        )
    else:
        # The reassignment won: nothing is booked, and the pro who approved a
        # moment too late gets no contact details.
        assert taken == []
        assert not lead.get("booked_slot_id")
        assert told_the_number == set()
        assert len(offered_to_secondary) == 1
        assert lead["reassignment_count"] == 1
        assert Messages.Pro.APPROVE_SUCCESS not in "\n".join(
            world.recorder.bodies_to(primary_chat)
        )
        found = [
            body
            for body in world.recorder.bodies_to(world.customer)
            if static_prefix(Messages.Customer.PRO_FOUND) in body
        ]
        assert not found, found
