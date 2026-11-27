"""The replay engine's own contract (PRO-83).

The harness's integrity rests on one property: a flow that asks the model something
nothing has an answer for must **fail loudly**, naming the key. A stand-in that
quietly returned a default would let a coverage cell pass while testing nothing —
the exact failure this ticket exists to prevent. These tests pin that, plus the
fixture round-trip the ``PROLI_E2E_RECORD=1`` switch writes and reads.
"""

import pytest
from pydantic import SecretStr

from app.core.config import settings
from app.core.messages import Messages
from tests.e2e import reserved_numbers as R
from tests.e2e.ai_replay import (
    FIXTURE_PATH,
    RECORD_ENV,
    MissingAIFixture,
    RecordingFailed,
    ReplayAIEngine,
    canned_reply_in,
    deal,
    dict_to_response,
    fixture_key,
    live_key_label,
    load_fixtures,
    recording_enabled,
    reply,
    response_to_dict,
)

# In record mode an input nothing has recorded is recorded, not refused, so the
# two tests of the refusal would turn their own text into a fixture (PRO-292).
_REFUSAL_NEEDS_REPLAY = pytest.mark.skipif(
    recording_enabled(),
    reason="PROLI_E2E_RECORD=1 would record this input instead of refusing it",
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


@_REFUSAL_NEEDS_REPLAY
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


@_REFUSAL_NEEDS_REPLAY
@pytest.mark.asyncio
async def test_a_missing_fixture_surfaces_in_flow_instead_of_faking_an_answer(world):
    """``workflow_service`` catches every AI exception and replies with the overload
    message, so a missing fixture degrades to a visible, obviously-wrong reply —
    never to a plausible-looking fake that could let a coverage cell pass."""
    await world.standard_cast()

    await world.send("משהו שאיש לא הקליט תשובה עבורו")

    world.recorder.assert_text_to(world.customer, Messages.Errors.AI_OVERLOAD)
    assert await world.lead() is None, "no lead may be built on a missing fixture"


@pytest.mark.asyncio
async def test_a_recorded_response_drives_a_real_flow(world):
    """End-to-end through the fixture file rather than scripted turns.

    Two recorded turns, because the dispatcher prompt forbids extracting the
    issue on the customer's first message (PRO-169 re-record: the model now
    obeys that rule, where the pre-PRO-169 recording did not). Turn one is the
    greeting question: the recording extracts the city but not the issue, so it
    opens an unassigned CONTACTED shell. Turn two carries the answer that
    completes city + issue and routes the lead to a pro."""
    from app.core.constants import LeadStatus

    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]

    await world.send("יש לי נזילה מתחת לכיור בתל אביב")

    world.recorder.assert_text_to(world.customer, "איך קוראים לך")
    lead = await world.lead()
    # The city-only turn opens a CONTACTED shell with no pro yet, whose
    # issue_type is the placeholder default — what must not be there is the
    # real issue.
    assert lead is not None and lead.get("pro_id") is None
    assert "נזילה" not in (
        lead.get("issue_type") or ""
    ), "the first message must not extract the issue — the prompt says so"

    await world.send("דנה. הנזילה מתחת לכיור במטבח, התחילה הבוקר")

    lead = await world.lead()
    assert lead is not None
    assert lead["status"] == LeadStatus.CONTACTED
    assert lead["city"] == "תל אביב"
    assert "נזילה" in lead["issue_type"]
    assert lead["pro_id"] == pro["_id"], "city + issue is enough to route"


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


# ---------------------------------------------------------------------------
# Record mode refuses to save junk (PRO-292)
# ---------------------------------------------------------------------------

# 39 characters, like a real Gemini key, and none of the placeholder markers.
REALISTIC_KEY = "AIzaSy" + "Q7x2Lm9RtV4bNc8Hd1Ks5Gw3Pf6JzYu0A"


@pytest.mark.parametrize(
    "key",
    [
        pytest.param("web-session-not-a-real-key", id="web-session-placeholder"),
        pytest.param("AIzaSy" + "short", id="too-short-to-be-a-key"),
        pytest.param("AIzaSy" + "fakefakefakefakefakefakefakefake", id="marker"),
    ],
)
def test_live_key_label_refuses_a_placeholder_key(monkeypatch, key):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", SecretStr(key))

    with pytest.raises(RecordingFailed) as exc:
        live_key_label()

    assert "Nothing was recorded" in str(exc.value)
    assert key not in str(exc.value), "the key itself is never printed"


def test_live_key_label_names_the_variable_and_only_the_last_four_characters(
    monkeypatch,
):
    assert len(REALISTIC_KEY) == 39, "the arrangement: a key shaped like a real one"
    monkeypatch.setattr(settings, "GEMINI_API_KEY", SecretStr(REALISTIC_KEY))

    label = live_key_label()

    assert label == f"GEMINI_API_KEY …{REALISTIC_KEY[-4:]}"
    assert REALISTIC_KEY not in label
    assert REALISTIC_KEY[:-4] not in label


@pytest.mark.parametrize(
    "text, expected",
    [
        (Messages.Errors.AI_OVERLOAD, "AI_OVERLOAD"),
        (
            f"{Messages.Errors.MEDIA_UPLOAD_FAILED}\n{Messages.Errors.AI_OVERLOAD}",
            "AI_OVERLOAD",
        ),
        (Messages.Errors.MEDIA_UPLOAD_FAILED, "MEDIA_UPLOAD_FAILED"),
        (
            Messages.Errors.MEDIA_UPLOAD_FAILED_TEXT_ONLY,
            "MEDIA_UPLOAD_FAILED_TEXT_ONLY",
        ),
        # A notice is only a fallback when it is the whole reply.
        (f"{Messages.Errors.MEDIA_UPLOAD_FAILED} and more", None),
        ("an ordinary model reply", None),
    ],
    ids=[
        "overload",
        "overload-after-a-notice",
        "media-notice",
        "media-notice-text-only",
        "notice-with-extra-text",
        "ordinary-reply",
    ],
)
def test_canned_reply_in_names_the_engines_own_fallback(text, expected):
    assert canned_reply_in(text) == expected


class _OverloadedEngine:
    """Stands in for the live `AIEngine`: every call fails the way an outage does,
    so the engine answers with its canned overload text."""

    built = 0

    def __init__(self):
        type(self).built += 1

    async def analyze_conversation(self, **_kwargs):
        return reply(Messages.Errors.AI_OVERLOAD)

    async def detect_service_intent(self, text, raise_errors=False):
        # Like the real one: an outage is False unless the caller asks to see it.
        if raise_errors:
            raise RuntimeError("network down")
        return False


@pytest.fixture
def recording(monkeypatch):
    """Record mode on, a live engine that cannot reach the model, no real I/O."""
    _OverloadedEngine.built = 0
    monkeypatch.setenv(RECORD_ENV, "1")
    monkeypatch.setattr("app.services.ai_engine_service.AIEngine", _OverloadedEngine)
    saved = []
    monkeypatch.setattr("tests.e2e.ai_replay.save_fixtures", saved.append)
    return saved


@pytest.mark.asyncio
async def test_record_mode_raises_instead_of_saving_the_overload_reply(
    monkeypatch, recording
):
    from app.core.prompts import Prompts

    monkeypatch.setattr(settings, "GEMINI_API_KEY", SecretStr(REALISTIC_KEY))
    before = FIXTURE_PATH.read_bytes()
    engine = ReplayAIEngine()

    with pytest.raises(RecordingFailed) as exc:
        await engine.analyze_conversation(
            history=[],
            user_text="a question nobody has recorded",
            custom_system_prompt=Prompts.DISPATCHER_SYSTEM,
        )
    engine.flush_recording()

    assert "AI_OVERLOAD" in str(exc.value)
    assert recording == [], "nothing may be written to the fixture store"
    assert FIXTURE_PATH.read_bytes() == before
    assert not any(
        "a question nobody has recorded" in key for key in load_fixtures()["responses"]
    )


@pytest.mark.asyncio
async def test_record_mode_raises_instead_of_saving_a_failed_intent_call_as_false(
    monkeypatch, recording
):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", SecretStr(REALISTIC_KEY))
    before = FIXTURE_PATH.read_bytes()
    text = "an intent nobody has recorded"
    engine = ReplayAIEngine()

    with pytest.raises(RecordingFailed) as exc:
        await engine.detect_service_intent(text)
    engine.flush_recording()

    assert isinstance(exc.value.__cause__, RuntimeError), "the outage is the cause"
    assert recording == [], "nothing may be written to the fixture store"
    assert FIXTURE_PATH.read_bytes() == before
    assert text not in load_fixtures()["intents"]


@pytest.mark.asyncio
async def test_record_mode_with_a_placeholder_key_stops_before_any_live_call(
    monkeypatch, recording
):
    from app.core.prompts import Prompts

    monkeypatch.setattr(
        settings, "GEMINI_API_KEY", SecretStr("web-session-not-a-real-key")
    )
    engine = ReplayAIEngine()

    with pytest.raises(RecordingFailed):
        await engine.analyze_conversation(
            history=[],
            user_text="a question nobody has recorded",
            custom_system_prompt=Prompts.DISPATCHER_SYSTEM,
        )

    assert _OverloadedEngine.built == 0, "no live engine was even constructed"
    assert recording == []
