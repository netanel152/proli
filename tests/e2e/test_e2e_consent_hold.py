"""What a customer writes before consent: held in Redis, replayed once on accept.

Driven through the real orchestrator, StateManager, Redis fake and Mongo fake. The
privacy half inspects the stores directly, not the code: it searches every Mongo
collection and every Redis key for the customer's words.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import fakeredis.aioredis
import pytest
from arq.connections import ArqRedis
from arq.worker import Worker, func
from fakeredis import FakeServer
from loguru import logger

from app.core.arq_worker import WorkerSettings
from app.core.config import settings
from app.core.constants import UserStates, WorkerConstants
from app.core.messages import Messages
from app.services import media_handler, workflow_service
from tests.e2e import reserved_numbers as R
from tests.e2e.ai_replay import reply

OPENING = "יש לי נזילה במטבח"
ALL_COLLECTIONS = (
    "users",
    "leads",
    "slots",
    "messages",
    "settings",
    "reviews",
    "consent",
    "audit_log",
    "admins",
    "wa_delivery",
)


def _key(world):
    return f"consent_hold:{world.customer}"


async def _mongo_mentions(world, text):
    found = []
    for name in ALL_COLLECTIONS:
        for doc in await world.db[name].find({}).to_list(length=1000):
            if text in str(doc):
                found.append(name)
    return found


async def _redis_keys_mentioning(world, text):
    found = []
    for key in await world.redis.keys("*"):
        kind = await world.redis.type(key)
        if kind == "string":
            value = await world.redis.get(key)
        elif kind == "list":
            value = " ".join(await world.redis.lrange(key, 0, -1))
        else:
            continue
        # json.dumps escapes Hebrew, so search for the escaped form as well.
        if text in value or json.dumps(text).strip('"') in value:
            found.append(key)
    return found


async def _user_rows(world, text):
    return await world.db.messages.count_documents(
        {"chat_id": world.customer, "role": "user", "text": text}
    )


# ---------------------------------------------------------------------------
# Before an answer, and on decline or expiry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_before_any_answer_the_text_is_only_in_the_ttld_redis_key(world):
    await world.send(OPENING)
    await world.send("מה זה?")

    assert await world.db.messages.count_documents({}) == 0
    assert await _mongo_mentions(world, OPENING) == []
    assert await _redis_keys_mentioning(world, OPENING) == [_key(world)]
    assert world.ai.calls == []
    assert await world.lead() is None
    assert 0 < await world.redis.ttl(_key(world))
    assert await world.redis.ttl(_key(world)) <= (
        WorkerConstants.CONSENT_HOLD_TTL_SECONDS
    )


@pytest.mark.asyncio
async def test_before_any_answer_the_text_reaches_no_log_line(world):
    lines = []
    sink = logger.add(lambda message: lines.append(str(message)), level="DEBUG")
    try:
        await world.send(OPENING)
        await world.send("לא")
    finally:
        logger.remove(sink)

    assert lines, "the capture saw nothing, so this would pass vacuously"
    assert not [line for line in lines if OPENING in line]


@pytest.mark.asyncio
async def test_decline_leaves_no_copy_anywhere(world):
    await world.send(OPENING)
    await world.send("לא")

    world.recorder.assert_text_to(world.customer, Messages.Consent.DECLINED)
    assert await world.db.messages.count_documents({}) == 0
    assert await _mongo_mentions(world, OPENING) == []
    assert await _redis_keys_mentioning(world, OPENING) == []
    assert world.ai.calls == []


@pytest.mark.asyncio
async def test_a_declined_message_does_not_come_back_on_a_later_accept(world):
    await world.send(OPENING)
    await world.send("לא")
    await world.send("היי שוב")
    world.ai.script(reply("במה אפשר לעזור?"))
    await world.send("כן")

    assert [call["user_text"] for call in world.ai.calls] == ["היי שוב"]
    assert await _mongo_mentions(world, OPENING) == []


@pytest.mark.asyncio
async def test_expiry_drops_it_and_an_accept_gets_the_bare_ack(world):
    await world.send(OPENING)
    await world.redis.pexpire(_key(world), 1)
    await asyncio.sleep(0.01)
    await world.send("כן")

    world.recorder.assert_text_to(world.customer, Messages.Consent.ACCEPTED)
    assert world.ai.calls == []
    assert await world.db.messages.count_documents({}) == 0
    assert await _redis_keys_mentioning(world, OPENING) == []


@pytest.mark.asyncio
async def test_a_reset_drops_it(world):
    await world.send(OPENING)
    await world.send(Messages.Keywords.RESET_COMMANDS[0])

    assert await _redis_keys_mentioning(world, OPENING) == []


# ---------------------------------------------------------------------------
# Accept: through the normal pipeline, exactly once
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accept_runs_the_opening_through_the_dispatcher(world):
    await world.send(OPENING)
    world.ai.script(reply("באיזו עיר?", issue="נזילה"))
    await world.send("כן")

    world.recorder.assert_text_to(world.customer, Messages.Consent.ACCEPTED)
    world.recorder.assert_text_to(world.customer, "באיזו עיר?")
    assert [call["user_text"] for call in world.ai.calls] == [OPENING]
    assert await _user_rows(world, OPENING) == 1
    assert await _user_rows(world, "כן") == 0, "the answer is not part of the problem"
    assert await world.redis.exists(_key(world)) == 0
    await world.assert_state(UserStates.IDLE)
    assert (await world.db.consent.find_one({}))["accepted"] is True


@pytest.mark.asyncio
async def test_a_hello_then_the_problem_both_reach_the_dispatcher(world):
    """The ordinary shape: the first message is rarely the one that matters."""
    await world.send("שלום")
    await world.send(OPENING)
    world.ai.script(reply("באיזו עיר?"))
    await world.send("כן")

    assert [call["user_text"] for call in world.ai.calls] == [f"שלום\n{OPENING}"]


@pytest.mark.asyncio
async def test_a_bare_yes_as_the_first_message_is_not_replayed(world):
    await world.send("כן")
    await world.send("כן")

    world.recorder.assert_text_to(world.customer, Messages.Consent.ACCEPTED)
    assert world.ai.calls == []


@pytest.mark.asyncio
async def test_the_hold_is_capped(world):
    for n in range(WorkerConstants.CONSENT_HOLD_MAX_MESSAGES + 3):
        await world.send(f"הודעה {n}")

    assert (
        await world.redis.llen(_key(world)) == WorkerConstants.CONSENT_HOLD_MAX_MESSAGES
    )


@pytest.mark.asyncio
async def test_a_double_accept_replays_once(world):
    await world.send(OPENING)
    world.ai.script(reply("באיזו עיר?"), reply("באיזו עיר בדיוק?"))
    await world.send("כן")
    await world.send("כן")

    assert await _user_rows(world, OPENING) == 1
    assert [call["user_text"] for call in world.ai.calls] == [OPENING, "כן"]


@pytest.mark.asyncio
async def test_concurrent_accepts_have_one_taker(world):
    await world.send(OPENING)

    world.ai.script(reply("באיזו עיר?"))

    # The inner function, because the chat lock is what would otherwise hide this:
    # the lock can lapse under a slow turn, and the take is what must still hold.
    await asyncio.gather(
        workflow_service._process_incoming_message_inner(world.customer, "כן"),
        workflow_service._process_incoming_message_inner(world.customer, "כן"),
    )

    assert await _user_rows(world, OPENING) == 1
    assert [call["user_text"] for call in world.ai.calls] == [OPENING]


@pytest.mark.asyncio
async def test_a_redelivered_opening_is_not_doubled(world):
    await world.send(OPENING)
    await world.send(OPENING)
    world.ai.script(reply("באיזו עיר?"))
    await world.send("כן")

    assert [call["user_text"] for call in world.ai.calls] == [OPENING]


@pytest.mark.asyncio
async def test_a_job_that_dies_before_the_log_replays_on_retry(world, monkeypatch):
    """CancelledError is what a worker shutdown raises, and arq re-runs that job."""
    failure = asyncio.CancelledError
    await world.send(OPENING)
    real_log = workflow_service.lead_manager.log_message
    monkeypatch.setattr(
        workflow_service.lead_manager, "log_message", AsyncMock(side_effect=failure)
    )

    with pytest.raises(failure):
        await world.send("כן")

    assert await world.db.messages.count_documents({}) == 0
    assert world.ai.calls == []
    await world.assert_state(UserStates.AWAITING_CONSENT)
    assert 0 < await world.redis.ttl(_key(world))

    monkeypatch.setattr(workflow_service.lead_manager, "log_message", real_log)
    world.ai.script(reply("באיזו עיר?"))
    await world.send("כן")

    assert await _user_rows(world, OPENING) == 1
    assert [call["user_text"] for call in world.ai.calls] == [OPENING]
    assert await world.redis.exists(_key(world)) == 0


@pytest.mark.asyncio
async def test_after_a_failed_accept_the_next_message_is_not_asked_for_consent(
    world, monkeypatch
):
    """arq does not re-run a job that raised anything else, so what comes next is
    the customer writing again."""
    await world.send(OPENING)
    real_log = workflow_service.lead_manager.log_message
    monkeypatch.setattr(
        workflow_service.lead_manager,
        "log_message",
        AsyncMock(side_effect=RuntimeError),
    )
    with pytest.raises(RuntimeError):
        await world.send("כן")
    monkeypatch.setattr(workflow_service.lead_manager, "log_message", real_log)
    world.recorder.clear()

    world.ai.script(reply("תודה."))
    await world.send("בתל אביב")

    assert not [
        send
        for send in world.recorder.to(world.customer)
        if Messages.Consent.REQUEST in str(send)
    ]
    assert [call["user_text"] for call in world.ai.calls] == [f"{OPENING}\nבתל אביב"]
    assert await _user_rows(world, f"{OPENING}\nבתל אביב") == 1
    await world.assert_state(UserStates.IDLE)


@pytest.mark.asyncio
async def test_after_a_failed_accept_they_can_still_decline(world, monkeypatch):
    await world.send(OPENING)
    monkeypatch.setattr(
        workflow_service.lead_manager,
        "log_message",
        AsyncMock(side_effect=RuntimeError),
    )
    with pytest.raises(RuntimeError):
        await world.send("כן")

    await world.send("לא")

    assert (await world.db.consent.find_one({}))["accepted"] is False
    assert await _redis_keys_mentioning(world, OPENING) == []


@pytest.mark.asyncio
async def test_a_guard_below_the_gate_answering_does_not_lose_the_opening(world):
    thanks = Messages.Keywords.THANKS_KEYWORDS[0]
    await world.send(thanks)
    await world.send("כן")

    world.recorder.assert_text_to(world.customer, Messages.Customer.YOU_ARE_WELCOME)
    assert await _user_rows(world, thanks) == 1


@pytest.mark.asyncio
async def test_a_guard_below_the_gate_cannot_fire_twice_on_a_rerun(world, monkeypatch):
    """Its side effect happened, then the job died: the re-run must not repeat it."""
    thanks = Messages.Keywords.THANKS_KEYWORDS[0]
    await world.send(thanks)
    real_send = workflow_service.whatsapp.send_message

    async def _die_on_the_answer(chat_id, text, *args, **kwargs):
        await real_send(chat_id, text, *args, **kwargs)
        if text == Messages.Customer.YOU_ARE_WELCOME:
            raise asyncio.CancelledError

    monkeypatch.setattr(workflow_service.whatsapp, "send_message", _die_on_the_answer)
    with pytest.raises(asyncio.CancelledError):
        await world.send("כן")
    monkeypatch.setattr(workflow_service.whatsapp, "send_message", real_send)
    world.recorder.clear()

    world.ai.script(reply("במה אפשר לעזור?"))
    await world.send("כן")

    assert not [
        send
        for send in world.recorder.to(world.customer)
        if Messages.Customer.YOU_ARE_WELCOME in str(send)
    ]
    assert await _user_rows(world, thanks) == 1


@pytest.mark.asyncio
async def test_an_emergency_that_dies_past_the_log_pages_the_operator(
    world, monkeypatch
):
    await world.send("יש שריפה")
    monkeypatch.setattr(
        workflow_service, "_handle_completion", AsyncMock(side_effect=RuntimeError)
    )
    page = MagicMock()
    monkeypatch.setattr(workflow_service.notification_service, "page_operator", page)

    with pytest.raises(RuntimeError):
        await world.send("כן")

    page.assert_called_once()
    assert "שריפה" not in page.call_args.args[0]
    assert world.customer not in page.call_args.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure, pages", [(RuntimeError, 1), (asyncio.CancelledError, 0)]
)
async def test_an_emergency_that_fails_before_the_log_pages_unless_it_will_rerun(
    world, monkeypatch, failure, pages
):
    await world.send("יש שריפה")
    monkeypatch.setattr(
        workflow_service.lead_manager, "log_message", AsyncMock(side_effect=failure)
    )
    page = MagicMock()
    monkeypatch.setattr(workflow_service.notification_service, "page_operator", page)

    with pytest.raises(failure):
        await world.send("כן")

    assert page.call_count == pages
    for call in page.call_args_list:
        assert "שריפה" not in call.args[0]
        assert world.customer not in call.args[0]
        assert "consent" in call.args[0], "says where to find the number"


@pytest.mark.asyncio
async def test_the_page_names_the_lead_when_there_is_one(world, monkeypatch):
    page = MagicMock()
    monkeypatch.setattr(workflow_service.notification_service, "page_operator", page)
    lead_id = (
        await world.db.leads.insert_one(
            {"chat_id": world.customer, "created_at": world.anchor}
        )
    ).inserted_id

    await workflow_service.page_emergency_turn(world.customer, "failed")

    assert str(lead_id) in page.call_args.args[0]


@pytest.mark.asyncio
async def test_a_redelivered_message_after_a_failed_accept_is_not_doubled(
    world, monkeypatch
):
    await world.send(OPENING)
    real_log = workflow_service.lead_manager.log_message
    monkeypatch.setattr(
        workflow_service.lead_manager,
        "log_message",
        AsyncMock(side_effect=RuntimeError),
    )
    with pytest.raises(RuntimeError):
        await world.send("כן")
    with pytest.raises(RuntimeError):
        await world.send("בתל אביב")
    monkeypatch.setattr(workflow_service.lead_manager, "log_message", real_log)

    world.ai.script(reply("תודה."))
    await world.send("בתל אביב")

    assert [call["user_text"] for call in world.ai.calls] == [f"{OPENING}\nבתל אביב"]


@pytest.mark.asyncio
async def test_an_ordinary_opening_that_dies_past_the_log_does_not_page(
    world, monkeypatch
):
    await world.send(OPENING)
    monkeypatch.setattr(
        workflow_service, "_handle_completion", AsyncMock(side_effect=RuntimeError)
    )
    page = MagicMock()
    monkeypatch.setattr(workflow_service.notification_service, "page_operator", page)

    with pytest.raises(RuntimeError):
        await world.send("כן")

    page.assert_not_called()


@pytest.mark.asyncio
async def test_a_job_that_dies_after_the_log_does_not_replay(world, monkeypatch):
    """The opening is in the history by then, so the re-run must not repeat it, and
    the dispatcher still reads it there."""
    await world.send(OPENING)
    real_completion = workflow_service._handle_completion
    monkeypatch.setattr(
        workflow_service, "_handle_completion", AsyncMock(side_effect=RuntimeError)
    )

    with pytest.raises(RuntimeError):
        await world.send("כן")

    assert await _user_rows(world, OPENING) == 1
    await world.assert_state(UserStates.IDLE)
    assert await world.redis.exists(_key(world)) == 0

    monkeypatch.setattr(workflow_service, "_handle_completion", real_completion)
    seen = {}
    real_analyze = world.ai.analyze_conversation

    async def _spy(history, *args, **kwargs):
        seen["history"] = str(history)
        return await real_analyze(history, *args, **kwargs)

    monkeypatch.setattr(world.ai, "analyze_conversation", _spy)
    world.ai.script(reply("באיזו עיר?"))
    await world.send("כן")

    assert await _user_rows(world, OPENING) == 1
    assert OPENING in seen["history"]


@pytest.mark.asyncio
async def test_an_emergency_opening_is_still_an_emergency_after_the_replay(world):
    """The emergency flag is computed from the turn's text, and the turn's text
    was "כן" when the orchestrator computed it."""
    await world.standard_cast()
    await world.db.consent.delete_many({})
    await world.send("יש שריפה בתל אביב")
    world.ai.script(reply("מאתר איש מקצוע.", city="תל אביב", issue="שריפה"))
    await world.send("כן")

    lead = await world.lead()
    assert lead is not None
    assert lead["is_emergency"] is True


@pytest.mark.asyncio
async def test_a_thank_you_opening_runs_the_guards_below_the_gate(world):
    await world.send(Messages.Keywords.THANKS_KEYWORDS[0])
    await world.send("כן")

    world.recorder.assert_text_to(world.customer, Messages.Customer.YOU_ARE_WELCOME)
    assert world.ai.calls == []


@pytest.mark.asyncio
async def test_a_pro_is_not_held(world):
    await world.add_pro(R.PRO_PRIMARY, name="נתנאל אינסטלציה")
    chat_id = world.pro_chat(R.PRO_PRIMARY)

    await world.send("שלום", chat_id=chat_id)

    assert await world.redis.exists(f"consent_hold:{chat_id}") == 0


@pytest.mark.asyncio
async def test_the_re_ask_keeps_the_policy_links(world, monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_BASE_ORIGIN", "https://example.test")
    await world.send(OPENING)
    world.recorder.clear()
    await world.send("מה זה?")

    world.recorder.assert_text_to(world.customer, "https://example.test/privacy")


# ---------------------------------------------------------------------------
# Media: nothing leaves Meta before consent
# ---------------------------------------------------------------------------


@pytest.fixture
def cloudinary(monkeypatch):
    fetch = AsyncMock(return_value=(b"bytes", "image/jpeg"))
    uploads = []

    def _upload(data):
        uploads.append(data)
        return "https://res.cloudinary.com/proli/hosted/image"

    monkeypatch.setattr(media_handler, "fetch_meta_media", fetch)
    monkeypatch.setattr(media_handler, "upload_media_bytes", _upload)
    return fetch, uploads


@pytest.mark.asyncio
async def test_opening_media_is_not_fetched_or_uploaded_until_accept(world, cloudinary):
    fetch, uploads = cloudinary
    await workflow_service.process_incoming_message(
        world.customer, "תראו", "meta-media://M1"
    )

    fetch.assert_not_awaited()
    assert uploads == []
    assert await _mongo_mentions(world, "M1") == []

    world.ai.script(reply("באיזו עיר?"))
    await world.send("כן")

    fetch.assert_awaited_once_with("M1")
    assert len(uploads) == 1
    logged = await world.db.messages.find_one({"role": "user"})
    assert (
        logged["text"] == "תראו [MEDIA: https://res.cloudinary.com/proli/hosted/image]"
    )
    assert world.ai.calls[0]["media_mime"] == "image/jpeg"


@pytest.mark.asyncio
async def test_declined_media_is_never_fetched_or_uploaded(world, cloudinary):
    fetch, uploads = cloudinary
    await workflow_service.process_incoming_message(
        world.customer, "", "meta-media://M1"
    )
    await world.send("לא")

    fetch.assert_not_awaited()
    assert uploads == []
    assert await _redis_keys_mentioning(world, "M1") == []


@pytest.mark.asyncio
async def test_only_the_first_held_media_survives(world, cloudinary):
    fetch, _uploads = cloudinary
    for media_id in ("M1", "M2"):
        await workflow_service.process_incoming_message(
            world.customer, "", f"meta-media://{media_id}"
        )
    world.ai.script(reply("באיזו עיר?"))
    await world.send("כן")

    fetch.assert_awaited_once_with("M1")


@pytest.mark.asyncio
async def test_a_consented_customers_media_is_still_rehosted(world, cloudinary):
    await world.grant_consent()
    fetch, uploads = cloudinary
    world.ai.script(reply("באיזו עיר?"))
    await workflow_service.process_incoming_message(
        world.customer, "תראו", "meta-media://M1"
    )

    fetch.assert_awaited_once_with("M1")
    assert len(uploads) == 1


# ---------------------------------------------------------------------------
# The ARQ job itself: its args are the customer's text
# ---------------------------------------------------------------------------


async def _keys_holding(redis, words):
    found = []
    for key in await redis.keys("*"):
        kind = await redis.type(key)
        if kind == b"string" and words in await redis.get(key):
            found.append(key)
    return found


@pytest.mark.asyncio
@pytest.mark.parametrize("over_max_tries", [False, True])
async def test_a_finished_job_leaves_its_args_in_neither_redis_nor_the_logs(
    monkeypatch, over_max_tries
):
    """A real arq worker, with this project's settings, on both ways a job ends. It
    used to do both: an INFO line with the args, and a seven-day `arq:result` copy."""

    async def _no_server_info(*_args, **_kwargs):
        return None  # fakeredis has no INFO command

    monkeypatch.setattr("arq.worker.log_redis_info", _no_server_info)

    async def _job(ctx, chat_id, user_text, media_url=None):
        return None

    lines = []
    sink = logger.add(lambda message: lines.append(str(message)), level="DEBUG")
    redis = ArqRedis(
        connection_pool=fakeredis.aioredis.FakeRedis(
            server=FakeServer()
        ).connection_pool
    )
    try:
        job = await redis.enqueue_job("_job", "972500000001@c.us", "secret words")
        assert await _keys_holding(redis, b"secret words"), "nothing to search for"
        if over_max_tries:
            await redis.set(f"arq:retry:{job.job_id}", WorkerSettings.max_tries)
        worker = Worker(
            functions=[func(_job, name="_job")],
            redis_pool=redis,
            burst=True,
            handle_signals=False,
            poll_delay=0.01,
            max_tries=WorkerSettings.max_tries,
            keep_result=WorkerSettings.keep_result,
        )
        await worker.main()
    finally:
        logger.remove(sink)

    assert await _keys_holding(redis, b"secret words") == []
    assert not [line for line in lines if "secret words" in line]
    if over_max_tries:
        assert worker.jobs_failed == 1
        # Also proves the capture sees arq's records, or the line above is vacuous.
        assert [line for line in lines if "max retries" in line]
    else:
        assert worker.jobs_complete == 1
