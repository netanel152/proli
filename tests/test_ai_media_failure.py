"""A file the model will not see gets a straight answer, not a guess."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bson import ObjectId
from pydantic import SecretStr

import app.services.ai_engine_service as ai_engine_module
import app.services.conversation_pipeline as conversation_pipeline
import app.services.media_handler as media_handler
import app.services.workflow_service
from app.core.constants import LeadStatus
from app.core.messages import Messages
from app.services.ai_engine_service import AIEngine, AIResponse, ExtractedData
from app.services.workflow_service import process_incoming_message
from tests.test_workflow_orchestrator import wf_mocks  # noqa: F401

MEDIA_URL = "http://example.com/file"


class _Stream:
    def __init__(self, status_code):
        self.status_code = status_code

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aiter_bytes(self):
        yield b"bytes"


def _file_in_state(state):
    file_status = MagicMock()
    file_status.state.name = state
    return AsyncMock(return_value=file_status)


def _model_reply(text="AI reply", city=None, issue=None):
    response = MagicMock()
    response.parsed = AIResponse(
        reply_to_user=text,
        transcription=None,
        extracted_data=ExtractedData(city=city, issue=issue, appointment_time=None),
        is_deal=False,
    )
    response.usage_metadata = None
    return response


@pytest.fixture
def engine(monkeypatch):
    with patch.object(ai_engine_module, "settings") as mock_settings:
        mock_settings.GEMINI_API_KEY = SecretStr("fake_key")
        mock_settings.AI_MODELS = ["model"]
        with patch.object(ai_engine_module.genai, "Client"):
            ai = AIEngine()
    ai.client = MagicMock()
    ai.client.aio.files.upload = AsyncMock(return_value=MagicMock())
    ai.client.aio.files.get = _file_in_state("ACTIVE")
    ai.client.aio.models.generate_content = AsyncMock(return_value=_model_reply())

    http_client = MagicMock()
    http_client.stream = MagicMock(return_value=_Stream(200))
    monkeypatch.setattr(
        ai_engine_module, "get_http_client", AsyncMock(return_value=http_client)
    )
    monkeypatch.setattr(ai_engine_module, "types", MagicMock())
    monkeypatch.setattr(ai_engine_module.asyncio, "sleep", AsyncMock())
    ai.http_client = http_client
    return ai


def _break_upload(ai):
    ai.client.aio.files.upload = AsyncMock(side_effect=RuntimeError("upload refused"))


def _break_processing(ai):
    ai.client.aio.files.get = _file_in_state("FAILED")


def _never_active(ai):
    ai.client.aio.files.get = _file_in_state("PROCESSING")


def _break_download(ai):
    ai.http_client.stream = MagicMock(return_value=_Stream(500))


def _nothing_fetched(ai):
    pass  # detect_and_fetch_media's (None, None), which is what a failed photo gets


TEXT_ONLY = Messages.Errors.MEDIA_UPLOAD_FAILED_TEXT_ONLY
ANSWERED = Messages.Errors.MEDIA_FAILED_TEXT_ANSWERED

# A voice note after a failed voice note can loop, and an unknown file may have
# been one, so only a file known not to be audio is offered it.
FAILURES = [
    pytest.param(_break_upload, "audio/ogg", TEXT_ONLY, id="upload-raises"),
    pytest.param(
        _break_processing,
        "video/mp4",
        Messages.Errors.MEDIA_UPLOAD_FAILED,
        id="video-processing-failed",
    ),
    pytest.param(
        _never_active,
        "video/mp4",
        Messages.Errors.MEDIA_UPLOAD_FAILED,
        id="video-never-active",
    ),
    pytest.param(
        _break_download,
        "video/mp4",
        Messages.Errors.MEDIA_UPLOAD_FAILED,
        id="download-not-200",
    ),
    pytest.param(_nothing_fetched, None, TEXT_ONLY, id="image-not-fetched"),
]


def _sent_text(ai):
    return [
        call.kwargs["text"]
        for call in ai_engine_module.types.Part.call_args_list
        if "text" in call.kwargs
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("arrange, mime, notice", FAILURES)
async def test_media_only_failure_answers_without_calling_the_model(
    engine, arrange, mime, notice
):
    arrange(engine)

    with patch.object(ai_engine_module, "logger") as mock_logger:
        result = await engine.analyze_conversation(
            [], "", "prompt", media_mime_type=mime, media_url=MEDIA_URL
        )

    assert result.reply_to_user == notice
    assert result.is_deal is False
    engine.client.aio.models.generate_content.assert_not_awaited()
    # logger.error is what the Sentry bridge picks up.
    mock_logger.error.assert_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("arrange, mime, _notice", FAILURES)
async def test_failure_with_text_answers_the_text_and_says_so(
    engine, arrange, mime, _notice
):
    arrange(engine)

    result = await engine.analyze_conversation(
        [], "נזילה בתל אביב", "prompt", media_mime_type=mime, media_url=MEDIA_URL
    )

    assert result.reply_to_user == f"{ANSWERED}\nAI reply"
    engine.client.aio.models.generate_content.assert_awaited_once()
    ai_engine_module.types.Part.from_uri.assert_not_called()
    text = _sent_text(engine)[-1]
    assert text.startswith("נזילה בתל אביב")
    assert Messages.AISystemPrompts.MEDIA_NOT_SEEN in text
    # Never ask the model to transcribe a recording it was not given.
    assert Messages.AISystemPrompts.TRANSCRIBE_AUDIO not in text
    assert Messages.AISystemPrompts.ANALYZE_VIDEO not in text


@pytest.mark.asyncio
async def test_failed_upload_and_failed_models_does_not_claim_an_answer(engine):
    _break_upload(engine)
    engine.client.aio.models.generate_content = AsyncMock(
        side_effect=RuntimeError("overloaded")
    )

    result = await engine.analyze_conversation(
        [], "נזילה", "prompt", media_mime_type="audio/ogg", media_url=MEDIA_URL
    )

    assert result.reply_to_user == f"{TEXT_ONLY}\n{Messages.Errors.AI_OVERLOAD}"


@pytest.mark.asyncio
async def test_successful_upload_changes_nothing(engine):
    result = await engine.analyze_conversation(
        [], "", "prompt", media_mime_type="audio/ogg", media_url=MEDIA_URL
    )

    assert result.reply_to_user == "AI reply"
    ai_engine_module.types.Part.from_uri.assert_called_once()
    text = _sent_text(engine)[-1]
    assert Messages.AISystemPrompts.TRANSCRIBE_AUDIO in text
    assert Messages.AISystemPrompts.MEDIA_NOT_SEEN not in text


@pytest.mark.asyncio
async def test_failed_upload_reaches_the_customer_not_the_model(
    engine, wf_mocks, monkeypatch  # noqa: F811
):
    """Through the real pipeline: told once, no model call, and the file's link
    still lands on the lead, so the pro can open it."""
    mock_wa, _, _, _, mock_lm = wf_mocks
    _break_upload(engine)
    monkeypatch.setattr(app.services.workflow_service, "ai", engine)
    monkeypatch.setattr(
        app.services.workflow_service,
        "detect_and_fetch_media",
        AsyncMock(return_value=(None, "audio/ogg")),
    )
    chat = "972500204001@c.us"

    await process_incoming_message(chat, "", media_url=MEDIA_URL)

    sent = [c.args[1] for c in mock_wa.send_message.call_args_list]
    assert sent == [TEXT_ONLY]
    mock_lm.log_message.assert_any_call(chat, "model", TEXT_ONLY)
    engine.client.aio.models.generate_content.assert_not_awaited()
    create_kwargs = mock_lm.create_lead_from_dict.call_args.kwargs
    assert create_kwargs["status"] == LeadStatus.CONTACTED
    assert create_kwargs["media_url"] == MEDIA_URL


@pytest.mark.asyncio
async def test_fetch_failure_already_apologised_for_is_not_repeated(
    engine, wf_mocks, monkeypatch  # noqa: F811
):
    mock_wa, _, _, _, _ = wf_mocks
    monkeypatch.setattr(app.services.workflow_service, "ai", engine)
    monkeypatch.setattr(
        app.services.workflow_service,
        "detect_and_fetch_media",
        AsyncMock(side_effect=RuntimeError("download failed")),
    )

    await process_incoming_message(
        "972500204002@c.us", "תראה מה קרה", media_url=MEDIA_URL
    )

    sent = [c.args[1] for c in mock_wa.send_message.call_args_list]
    assert sent.count(Messages.Errors.MEDIA_FETCH_FAILED) == 1
    assert not any(ANSWERED in s or TEXT_ONLY in s for s in sent)


# --- Copy ---


def test_the_notices_have_the_agreed_shape():
    assert Messages.Errors.MEDIA_UPLOAD_FAILED.count("\n") == 1
    assert TEXT_ONLY.count("\n") == 1
    assert TEXT_ONLY != Messages.Errors.MEDIA_UPLOAD_FAILED
    assert "\n" not in ANSWERED


# --- Every model fails on a turn with the file attached ---


class _ApiError(Exception):
    def __init__(self, code):
        super().__init__(f"{code} error")
        self.code = code


def _unparseable():
    response = MagicMock()
    response.parsed = None
    response.text = "not json"
    response.usage_metadata = None
    return response


async def _ask_with_image(engine, text):
    return await engine.analyze_conversation(
        [],
        text,
        "prompt",
        media_data=b"bytes",
        media_mime_type="image/jpeg",
        media_url=MEDIA_URL,
    )


@pytest.mark.asyncio
async def test_refused_file_with_text_retries_once_without_it(engine):
    types = ai_engine_module.types
    types.Content.side_effect = lambda role, parts: SimpleNamespace(
        role=role, parts=parts
    )
    file_part = types.Part.from_bytes.return_value
    parts_sent = []
    outcomes = [_ApiError(400), _model_reply()]

    async def generate(*, model, contents, config):
        # Copied: the retry rewrites this same list in place.
        parts_sent.append(list(contents[-1].parts))
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    engine.client.aio.models.generate_content = generate

    result = await _ask_with_image(engine, "נזילה בתל אביב")

    assert result.reply_to_user == f"{ANSWERED}\nAI reply"
    assert len(parts_sent) == 2
    assert file_part in parts_sent[0]
    assert file_part not in parts_sent[1]
    retry_text = _sent_text(engine)[-1]
    assert retry_text.startswith("נזילה בתל אביב")
    assert Messages.AISystemPrompts.MEDIA_NOT_SEEN in retry_text
    assert Messages.AISystemPrompts.ANALYZE_IMAGE not in retry_text


@pytest.mark.asyncio
async def test_all_unparseable_json_counts_as_refused(engine):
    engine.client.aio.models.generate_content = AsyncMock(
        side_effect=[_unparseable(), _model_reply()]
    )

    result = await _ask_with_image(engine, "נזילה בתל אביב")

    assert result.reply_to_user == f"{ANSWERED}\nAI reply"


@pytest.mark.asyncio
async def test_retry_that_also_fails_stays_overload(engine):
    engine.client.aio.models.generate_content = AsyncMock(side_effect=_ApiError(400))

    result = await _ask_with_image(engine, "נזילה בתל אביב")

    assert result.reply_to_user == Messages.Errors.AI_OVERLOAD
    assert engine.client.aio.models.generate_content.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        pytest.param(_ApiError(429), id="429"),
        pytest.param(_ApiError(503), id="503"),
        pytest.param(RuntimeError("connection reset"), id="no-code"),
    ],
)
@pytest.mark.parametrize("text", ["נזילה בתל אביב", ""], ids=["with-text", "no-text"])
async def test_an_outage_is_not_reported_as_a_failed_file(engine, error, text):
    engine.client.aio.models.generate_content = AsyncMock(side_effect=error)

    result = await _ask_with_image(engine, text)

    assert result.reply_to_user == Messages.Errors.AI_OVERLOAD
    # No second round: this runs once per model per turn while the outage lasts.
    engine.client.aio.models.generate_content.assert_awaited_once()


@pytest.mark.asyncio
async def test_refused_file_with_no_text_gets_the_notice_and_no_retry(engine):
    engine.client.aio.models.generate_content = AsyncMock(side_effect=_ApiError(400))

    result = await _ask_with_image(engine, "")

    assert result.reply_to_user == Messages.Errors.MEDIA_UPLOAD_FAILED
    engine.client.aio.models.generate_content.assert_awaited_once()


@pytest.mark.asyncio
async def test_refused_voice_note_is_offered_text_only(engine):
    engine.client.aio.models.generate_content = AsyncMock(side_effect=_ApiError(400))

    result = await engine.analyze_conversation(
        [], "", "prompt", media_mime_type="audio/ogg", media_url=MEDIA_URL
    )

    assert result.reply_to_user == TEXT_ONLY


@pytest.mark.asyncio
async def test_a_text_turn_in_an_outage_is_unchanged(engine):
    engine.client.aio.models.generate_content = AsyncMock(
        side_effect=RuntimeError("overloaded")
    )

    result = await engine.analyze_conversation([], "נזילה", "prompt")

    assert result.reply_to_user == Messages.Errors.AI_OVERLOAD
    engine.client.aio.models.generate_content.assert_awaited_once()


# --- Exits that send a fixed message instead of the reply ---


def _exit_cases():
    # Real shapes, from the real engine: with text the model answers and the notice
    # leads the reply; with none the engine returns the notice and calls nothing.
    return [
        pytest.param("נזילה", ANSWERED, "972500217001@c.us", id="with-text"),
        pytest.param(
            "", TEXT_ONLY.split("\n")[0], "972500217007@c.us", id="media-only"
        ),
    ]


def _wire_failed_upload(engine, monkeypatch):
    _break_upload(engine)
    engine.client.aio.models.generate_content = AsyncMock(
        return_value=_model_reply(city="תל אביב", issue="נזילה")
    )
    monkeypatch.setattr(app.services.workflow_service, "ai", engine)
    monkeypatch.setattr(
        app.services.workflow_service,
        "detect_and_fetch_media",
        AsyncMock(return_value=(None, "audio/ogg")),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("text, told, chat", _exit_cases())
async def test_notice_survives_the_no_pro_exit(
    engine, wf_mocks, mock_db, monkeypatch, text, told, chat  # noqa: F811
):
    mock_wa, _, _, _, mock_lm = wf_mocks
    _wire_failed_upload(engine, monkeypatch)
    monkeypatch.setattr(
        app.services.workflow_service,
        "determine_best_pro",
        AsyncMock(return_value=None),
    )
    await mock_db.leads.insert_one(
        {
            "chat_id": chat,
            "status": LeadStatus.CONTACTED,
            "city": "תל אביב",
            "issue_type": "נזילה",
        }
    )

    await process_incoming_message(chat, text, media_url=MEDIA_URL)

    sent = [c.args[1] for c in mock_wa.send_message.call_args_list]
    assert sent == [told, Messages.Customer.PENDING_REVIEW]
    mock_lm.log_message.assert_any_call(chat, "model", told)


@pytest.mark.asyncio
@pytest.mark.parametrize("text, told, chat", _exit_cases())
async def test_notice_survives_the_loyalty_exit(
    engine, wf_mocks, mock_db, monkeypatch, text, told, chat  # noqa: F811
):
    mock_wa, _, _, _, mock_lm = wf_mocks
    chat = chat.replace("97250021", "97250031")
    _wire_failed_upload(engine, monkeypatch)
    monkeypatch.setattr(
        conversation_pipeline,
        "non_essential_sends_paused",
        AsyncMock(return_value=False),
    )
    pro_id = ObjectId()
    await mock_db.users.insert_one(
        {"_id": pro_id, "is_active": True, "business_name": "יוסי"}
    )
    await mock_db.leads.insert_one(
        {
            "chat_id": chat,
            "status": LeadStatus.CONTACTED,
            "city": "תל אביב",
            "issue_type": "נזילה",
        }
    )
    await mock_db.leads.insert_one(
        {"chat_id": chat, "status": LeadStatus.COMPLETED, "pro_id": pro_id}
    )

    await process_incoming_message(chat, text, media_url=MEDIA_URL)

    sent = [c.args[1] for c in mock_wa.send_message.call_args_list]
    assert sent == [told, Messages.Customer.LOYALTY_OFFER.format(pro_name="יוסי")]
    mock_lm.log_message.assert_any_call(chat, "model", told)


def test_an_ordinary_reply_carries_no_notice():
    assert ai_engine_module.file_notice_in("AI reply") is None


# --- A file lost before the AI ---


def _lose_the_file(monkeypatch):
    monkeypatch.setattr(
        app.services.workflow_service,
        "resolve_inbound_media",
        AsyncMock(return_value=None),
    )


@pytest.mark.asyncio
async def test_file_lost_with_no_text_skips_the_ai_and_logs_a_placeholder(
    wf_mocks, monkeypatch  # noqa: F811
):
    mock_wa, _, _, mock_ai, mock_lm = wf_mocks
    _lose_the_file(monkeypatch)
    chat = "972500217003@c.us"

    await process_incoming_message(chat, "", media_url="meta-media://M1")

    sent = [c.args[1] for c in mock_wa.send_message.call_args_list]
    assert sent == [TEXT_ONLY]
    mock_lm.log_message.assert_any_call(chat, "user", "[MEDIA: lost]")
    mock_lm.log_message.assert_any_call(chat, "model", TEXT_ONLY)
    mock_ai.analyze_conversation.assert_not_awaited()


@pytest.mark.asyncio
async def test_file_lost_with_text_tells_the_customer_on_the_reply(
    engine, wf_mocks, monkeypatch  # noqa: F811
):
    mock_wa, _, _, _, mock_lm = wf_mocks
    _lose_the_file(monkeypatch)
    monkeypatch.setattr(app.services.workflow_service, "ai", engine)
    chat = "972500217004@c.us"

    await process_incoming_message(chat, "נזילה", media_url="meta-media://M1")

    expected = f"{ANSWERED}\nAI reply"
    sent = [c.args[1] for c in mock_wa.send_message.call_args_list]
    assert sent == [expected]
    mock_lm.log_message.assert_any_call(chat, "model", expected)
    # The instruction goes to the model, and only there.
    assert Messages.AISystemPrompts.MEDIA_NOT_SEEN in _sent_text(engine)[-1]
    mock_lm.log_message.assert_any_call(chat, "user", "נזילה")


@pytest.mark.asyncio
async def test_file_lost_says_nothing_when_no_reply_goes_out(
    engine, wf_mocks, monkeypatch  # noqa: F811
):
    """ "Only what you wrote was addressed" would be false ahead of an overload."""
    mock_wa, _, _, _, _ = wf_mocks
    _lose_the_file(monkeypatch)
    monkeypatch.setattr(app.services.workflow_service, "ai", engine)
    engine.client.aio.models.generate_content = AsyncMock(
        side_effect=RuntimeError("overloaded")
    )

    await process_incoming_message(
        "972500217008@c.us", "נזילה", media_url="meta-media://M1"
    )

    sent = [c.args[1] for c in mock_wa.send_message.call_args_list]
    assert not any(ANSWERED in message for message in sent)


@pytest.mark.asyncio
async def test_the_instruction_never_reaches_user_text(
    wf_mocks, mock_db, monkeypatch  # noqa: F811
):
    """user_text is the customer's words, and later code acts on it."""
    _, _, _, mock_ai, _ = wf_mocks
    _lose_the_file(monkeypatch)
    mock_ai.analyze_conversation.return_value = _model_reply(
        city="תל אביב", issue="נזילה"
    ).parsed
    pro = {"_id": ObjectId(), "business_name": "יוסי", "phone_number": "972500217098"}
    monkeypatch.setattr(
        app.services.workflow_service,
        "determine_best_pro",
        AsyncMock(return_value=pro),
    )
    build_pro = AsyncMock(return_value=_model_reply().parsed)
    monkeypatch.setattr(app.services.workflow_service, "_build_pro_response", build_pro)

    await process_incoming_message(
        "972500217009@c.us", "נזילה", media_url="meta-media://M1"
    )

    dispatcher = mock_ai.analyze_conversation.call_args.kwargs
    assert dispatcher["user_text"] == "נזילה"
    assert dispatcher["media_lost"] is True
    build_pro.assert_awaited_once()
    assert build_pro.call_args.args[2] == "נזילה"
    assert build_pro.call_args.kwargs["media_lost"] is True


@pytest.mark.asyncio
async def test_the_instruction_never_reaches_issue_type(
    wf_mocks, mock_db, monkeypatch  # noqa: F811
):
    """The wrong-trade check writes the customer's message into a vague issue_type,
    which goes on to the lead and to the next pro."""
    _lose_the_file(monkeypatch)
    chat = "972500217012@c.us"
    pro_id = ObjectId()
    await mock_db.users.insert_one(
        {
            "_id": pro_id,
            "is_active": True,
            "business_name": "יוסי",
            "type": "plumber",
        }
    )
    lead_id = (
        await mock_db.leads.insert_one(
            {
                "chat_id": chat,
                "status": LeadStatus.CONTACTED,
                "pro_id": pro_id,
                "issue_type": "בעיה",
            }
        )
    ).inserted_id

    await process_incoming_message(chat, "קצר בחשמל", media_url="meta-media://M1")

    lead = await mock_db.leads.find_one({"_id": lead_id})
    # The check did fire, or this would pass without testing anything.
    assert lead["pro_id"] is None
    assert lead["issue_type"] == "בעיה קצר בחשמל"


@pytest.mark.asyncio
async def test_media_lost_with_every_model_down_does_not_claim_an_answer(engine):
    engine.client.aio.models.generate_content = AsyncMock(
        side_effect=RuntimeError("overloaded")
    )

    result = await engine.analyze_conversation([], "נזילה", "prompt", media_lost=True)

    assert result.reply_to_user == f"{TEXT_ONLY}\n{Messages.Errors.AI_OVERLOAD}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fetched, hosted",
    [
        pytest.param((None, None), None, id="meta-fetch-failed"),
        pytest.param((b"bytes", "image/jpeg"), None, id="rehost-failed"),
    ],
)
async def test_a_lost_file_reaches_sentry(monkeypatch, fetched, hosted):
    monkeypatch.setattr(
        media_handler, "fetch_meta_media", AsyncMock(return_value=fetched)
    )
    monkeypatch.setattr(media_handler, "upload_media_bytes", lambda data: hosted)

    with patch.object(media_handler, "logger") as mock_logger:
        assert await media_handler.resolve_inbound_media("meta-media://M1") is None

    mock_logger.error.assert_called_once()


# --- A failed file must not erase what the lead already has ---


async def _finalize_on_emergency_lead(mock_db, chat, response, **lead):
    pro = {"_id": ObjectId(), "business_name": "יוסי", "phone_number": "972500217099"}
    lead_id = (
        await mock_db.leads.insert_one(
            {
                "chat_id": chat,
                "status": LeadStatus.CONTACTED,
                "is_emergency": True,
                **lead,
            }
        )
    ).inserted_id
    await app.services.workflow_service._finalize_deal(
        chat, pro, response, "תל אביב", "נזילה", None, lead_id
    )
    return await mock_db.leads.find_one({"_id": lead_id})


FULL_ADDRESS = {
    "street": "הרצל",
    "street_number": "10",
    "city": "תל אביב",
    "floor": "2",
    "apartment": "5",
}


@pytest.mark.asyncio
async def test_empty_response_keeps_the_address_on_an_emergency_lead(
    wf_mocks, mock_db  # noqa: F811
):
    empty = ai_engine_module._canned_reply(TEXT_ONLY, True)

    lead = await _finalize_on_emergency_lead(
        mock_db, "972500217005@c.us", empty, **FULL_ADDRESS
    )

    assert lead["status"] == LeadStatus.NEW
    for field, value in FULL_ADDRESS.items():
        assert lead[field] == value
    assert lead["full_address"] == "הרצל 10, תל אביב"


@pytest.mark.asyncio
async def test_empty_strings_do_not_wipe_address_parts(wf_mocks, mock_db):  # noqa: F811
    response = ai_engine_module._canned_reply("x", True)
    response.extracted_data.street = ""
    response.extracted_data.floor = ""

    lead = await _finalize_on_emergency_lead(
        mock_db, "972500217010@c.us", response, **FULL_ADDRESS
    )

    assert lead["street"] == "הרצל"
    assert lead["floor"] == "2"
    assert lead["full_address"] == "הרצל 10, תל אביב"


@pytest.mark.asyncio
async def test_a_newly_extracted_address_part_still_overwrites(
    wf_mocks, mock_db  # noqa: F811
):
    response = ai_engine_module._canned_reply("x", True)
    response.extracted_data.street = "ביאליק"

    lead = await _finalize_on_emergency_lead(
        mock_db, "972500217006@c.us", response, **FULL_ADDRESS
    )

    assert lead["street"] == "ביאליק"
    assert lead["city"] == "תל אביב"
    assert lead["full_address"] == "ביאליק 10, תל אביב"


@pytest.mark.asyncio
async def test_a_lead_with_no_street_still_gets_the_bare_city(
    wf_mocks, mock_db  # noqa: F811
):
    empty = ai_engine_module._canned_reply(TEXT_ONLY, True)

    lead = await _finalize_on_emergency_lead(
        mock_db, "972500217011@c.us", empty, city="תל אביב"
    )

    assert lead["full_address"] == "תל אביב"
