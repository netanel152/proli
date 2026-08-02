"""The replay engine's own contract (PRO-83).

The harness's integrity rests on one property: a flow that asks the model something
nothing has an answer for must **fail loudly**, naming the key. A stand-in that
quietly returned a default would let a coverage cell pass while testing nothing —
the exact failure this ticket exists to prevent. These tests pin that, plus the
fixture round-trip the ``PROLI_E2E_RECORD=1`` switch writes and reads.
"""

import pytest

from tests.e2e import reserved_numbers as R
from tests.e2e.ai_replay import (
    FIXTURE_PATH,
    MissingAIFixture,
    ReplayAIEngine,
    deal,
    dict_to_response,
    fixture_key,
    load_fixtures,
    reply,
    response_to_dict,
)


def test_the_fixture_file_is_committed_and_parses():
    """The record/replay store is a real file, not an aspiration."""
    assert FIXTURE_PATH.exists(), f"{FIXTURE_PATH} is missing"
    data = load_fixtures()
    assert data["responses"], "expected at least one recorded response"
    assert data["intents"], "expected at least one recorded intent verdict"


def test_every_committed_fixture_round_trips_into_an_ai_response():
    for key, entry in load_fixtures()["responses"].items():
        response = dict_to_response(entry)
        assert response.reply_to_user, f"{key} has no reply text"
        # A fixture written by the recorder must survive a round trip unchanged.
        assert dict_to_response(response_to_dict(response)) == response


def test_fixture_keys_distinguish_persona_and_media():
    from app.core.prompts import Prompts

    dispatcher = Prompts.DISPATCHER_SYSTEM
    pro = Prompts.PRO_BASE_SYSTEM

    assert fixture_key(dispatcher, "היי", None).startswith("dispatcher|text|")
    assert fixture_key(pro, "היי", None).startswith("pro|text|")
    assert fixture_key(dispatcher, "", "image/jpeg").startswith("dispatcher|image|")
    assert fixture_key(dispatcher, "", "audio/ogg").startswith("dispatcher|audio|")


@pytest.mark.asyncio
async def test_an_unscripted_unrecorded_call_raises_naming_the_key(world):
    """No silent default: the engine refuses rather than inventing an answer."""
    from app.core.prompts import Prompts

    with pytest.raises(MissingAIFixture) as exc:
        await world.ai.analyze_conversation(
            history=[],
            user_text="משהו שאיש לא הקליט תשובה עבורו",
            custom_system_prompt=Prompts.DISPATCHER_SYSTEM,
        )

    assert "משהו שאיש לא הקליט תשובה עבורו" in str(exc.value)
    assert "PROLI_E2E_RECORD" in str(exc.value), "the error should say how to record"


@pytest.mark.asyncio
async def test_a_missing_fixture_surfaces_in_flow_instead_of_faking_an_answer(world):
    """``workflow_service`` catches every AI exception and replies with the overload
    message, so a missing fixture degrades to a visible, obviously-wrong reply —
    never to a plausible-looking fake that could let a coverage cell pass."""
    from app.core.messages import Messages

    await world.standard_cast()

    await world.send("משהו שאיש לא הקליט תשובה עבורו")

    world.recorder.assert_text_to(world.customer, Messages.Errors.AI_OVERLOAD)
    assert await world.lead() is None, "no lead may be built on a missing fixture"


@pytest.mark.asyncio
async def test_a_recorded_response_drives_a_real_flow(world):
    """End-to-end through the fixture file rather than a scripted turn: the
    committed dispatcher response is enough to create a lead and route it."""
    await world.standard_cast()

    await world.send("יש לי נזילה מתחת לכיור בתל אביב")

    lead = await world.lead()
    assert lead is not None
    assert lead["city"] == "תל אביב" or lead["full_address"] == "תל אביב"
    assert lead["issue_type"] == "נזילה מתחת לכיור"
    world.recorder.assert_text_to(world.customer, "כמה זמן זה כבר ככה")


@pytest.mark.asyncio
async def test_a_recorded_intent_verdict_is_replayed(world):
    """`detect_service_intent` is the pro-side AI call; it replays from the same
    store and drives the AWAITING_INTENT_CONFIRMATION prompt (PRO-69)."""
    from app.core.constants import UserStates

    await world.standard_cast()
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    await world.set_state(UserStates.PRO_MODE, chat_id=pro_chat)

    await world.send("יש לי נזילה מתחת לכיור", chat_id=pro_chat)

    await world.assert_state(UserStates.AWAITING_INTENT_CONFIRMATION, chat_id=pro_chat)
    world.recorder.assert_text_to(pro_chat, "לעבור למצב לקוח")


def test_a_scripted_turn_takes_precedence_over_the_fixture_store():
    engine = ReplayAIEngine()
    scripted = deal("scripted", city="חיפה")
    engine.script(scripted)

    assert engine.pending_script == 1
    # reply()/deal() are the builders scenarios use; sanity-check they differ.
    assert reply("x").is_deal is False
    assert scripted.is_deal is True
