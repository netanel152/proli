"""A file the model will not see gets a straight answer, not a guess."""

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


def _model_reply(text="AI reply"):
    response = MagicMock()
    response.parsed = AIResponse(
        reply_to_user=text,
        transcription=None,
        extracted_data=ExtractedData(city=None, issue=None, appointment_time=None),
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

    assert result.reply_to_user == f"{ANSWERED}\n\nAI reply"
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

    assert result.reply_to_user == f"{TEXT_ONLY}\n\n{Messages.Errors.AI_OVERLOAD}"


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


def test_the_notices_read_as_the_owner_decided():
    assert Messages.Errors.MEDIA_UPLOAD_FAILED.count("\n") == 1
    assert "קולית" in Messages.Errors.MEDIA_UPLOAD_FAILED
    assert TEXT_ONLY.count("\n") == 1
    assert "קולית" not in TEXT_ONLY
    # With text they already described the problem, so nothing is asked of them.
    assert "\n" not in ANSWERED
    assert "?" not in ANSWERED


# --- Every model refuses a turn with the file attached ---


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
    engine.client.aio.models.generate_content = AsyncMock(
        side_effect=[RuntimeError("bad file"), _model_reply()]
    )

    result = await _ask_with_image(engine, "נזילה בתל אביב")

    assert result.reply_to_user == f"{ANSWERED}\n\nAI reply"
    assert engine.client.aio.models.generate_content.await_count == 2
    retry_text = _sent_text(engine)[-1]
    assert retry_text.startswith("נזילה בתל אביב")
    assert Messages.AISystemPrompts.MEDIA_NOT_SEEN in retry_text
    assert Messages.AISystemPrompts.ANALYZE_IMAGE not in retry_text


@pytest.mark.asyncio
async def test_an_outage_is_not_reported_as_a_failed_file(engine):
    engine.client.aio.models.generate_content = AsyncMock(
        side_effect=RuntimeError("overloaded")
    )

    result = await _ask_with_image(engine, "נזילה בתל אביב")

    assert result.reply_to_user == Messages.Errors.AI_OVERLOAD
    assert engine.client.aio.models.generate_content.await_count == 2


@pytest.mark.asyncio
async def test_refused_file_with_no_text_gets_the_notice_and_no_retry(engine):
    engine.client.aio.models.generate_content = AsyncMock(
        side_effect=RuntimeError("bad file")
    )

    result = await _ask_with_image(engine, "")

    assert result.reply_to_user == Messages.Errors.MEDIA_UPLOAD_FAILED
    engine.client.aio.models.generate_content.assert_awaited_once()


@pytest.mark.asyncio
async def test_refused_voice_note_is_offered_text_only(engine):
    engine.client.aio.models.generate_content = AsyncMock(
        side_effect=RuntimeError("bad file")
    )

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


def _reply_with_facts(reply):
    return AIResponse(
        reply_to_user=reply,
        transcription=None,
        extracted_data=ExtractedData(
            city="תל אביב", issue="נזילה", appointment_time=None
        ),
        is_deal=False,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "notice, chat",
    [(ANSWERED, "972500217001@c.us"), (TEXT_ONLY, "972500217007@c.us")],
)
async def test_notice_survives_the_no_pro_exit(
    wf_mocks, mock_db, monkeypatch, notice, chat  # noqa: F811
):
    mock_wa, _, _, mock_ai, _ = wf_mocks
    mock_ai.analyze_conversation.return_value = _reply_with_facts(
        f"{notice}\n\nAI reply"
    )
    monkeypatch.setattr(
        app.services.workflow_service,
        "detect_and_fetch_media",
        AsyncMock(return_value=(None, "audio/ogg")),
    )
    monkeypatch.setattr(
        app.services.workflow_service,
        "determine_best_pro",
        AsyncMock(return_value=None),
    )
    await mock_db.leads.insert_one(
        {"chat_id": chat, "status": LeadStatus.CONTACTED, "city": "תל אביב"}
    )

    await process_incoming_message(chat, "נזילה", media_url=MEDIA_URL)

    sent = [c.args[1] for c in mock_wa.send_message.call_args_list]
    assert sent == [notice, Messages.Customer.PENDING_REVIEW]


@pytest.mark.asyncio
async def test_notice_survives_the_loyalty_exit(
    wf_mocks, mock_db, monkeypatch  # noqa: F811
):
    mock_wa, _, _, mock_ai, _ = wf_mocks
    mock_ai.analyze_conversation.return_value = _reply_with_facts(
        f"{ANSWERED}\n\nAI reply"
    )
    monkeypatch.setattr(
        app.services.workflow_service,
        "detect_and_fetch_media",
        AsyncMock(return_value=(None, "audio/ogg")),
    )
    monkeypatch.setattr(
        conversation_pipeline,
        "non_essential_sends_paused",
        AsyncMock(return_value=False),
    )
    chat = "972500217002@c.us"
    pro_id = ObjectId()
    await mock_db.users.insert_one(
        {"_id": pro_id, "is_active": True, "business_name": "יוסי"}
    )
    await mock_db.leads.insert_one(
        {"chat_id": chat, "status": LeadStatus.CONTACTED, "city": "תל אביב"}
    )
    await mock_db.leads.insert_one(
        {"chat_id": chat, "status": LeadStatus.COMPLETED, "pro_id": pro_id}
    )

    await process_incoming_message(chat, "נזילה", media_url=MEDIA_URL)

    sent = [c.args[1] for c in mock_wa.send_message.call_args_list]
    assert sent == [
        ANSWERED,
        Messages.Customer.LOYALTY_OFFER.format(pro_name="יוסי"),
    ]


def test_an_ordinary_reply_carries_no_notice():
    assert ai_engine_module.file_notice_in("AI reply") is None


# --- A file lost before the AI ---


@pytest.mark.asyncio
async def test_file_lost_rehosting_with_no_text_skips_the_ai(
    wf_mocks, monkeypatch  # noqa: F811
):
    mock_wa, _, _, mock_ai, mock_lm = wf_mocks
    monkeypatch.setattr(
        app.services.workflow_service,
        "resolve_inbound_media",
        AsyncMock(return_value=None),
    )
    chat = "972500217003@c.us"

    await process_incoming_message(chat, "", media_url="meta-media://M1")

    sent = [c.args[1] for c in mock_wa.send_message.call_args_list]
    assert sent == [TEXT_ONLY]
    mock_lm.log_message.assert_any_call(chat, "model", TEXT_ONLY)
    mock_ai.analyze_conversation.assert_not_awaited()


@pytest.mark.asyncio
async def test_file_lost_rehosting_with_text_answers_the_text(
    wf_mocks, monkeypatch  # noqa: F811
):
    mock_wa, _, _, mock_ai, mock_lm = wf_mocks
    monkeypatch.setattr(
        app.services.workflow_service,
        "resolve_inbound_media",
        AsyncMock(return_value=None),
    )
    chat = "972500217004@c.us"

    await process_incoming_message(chat, "נזילה", media_url="meta-media://M1")

    sent = [c.args[1] for c in mock_wa.send_message.call_args_list]
    assert sent == [ANSWERED, "AI response"]
    mock_lm.log_message.assert_any_call(chat, "model", ANSWERED)
    kwargs = mock_ai.analyze_conversation.call_args.kwargs
    assert kwargs["user_text"].startswith("נזילה")
    assert Messages.AISystemPrompts.MEDIA_NOT_SEEN in kwargs["user_text"]


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


@pytest.mark.asyncio
async def test_empty_response_keeps_the_address_parts_on_an_emergency_lead(
    wf_mocks, mock_db  # noqa: F811
):
    chat = "972500217005@c.us"
    pro = {"_id": ObjectId(), "business_name": "יוסי", "phone_number": "972500217099"}
    lead_id = (
        await mock_db.leads.insert_one(
            {
                "chat_id": chat,
                "status": LeadStatus.CONTACTED,
                "is_emergency": True,
                "street": "הרצל",
                "street_number": "10",
                "city": "תל אביב",
                "floor": "2",
                "apartment": "5",
            }
        )
    ).inserted_id
    empty = ai_engine_module._canned_reply(TEXT_ONLY, True)

    await app.services.workflow_service._finalize_deal(
        chat, pro, empty, "תל אביב", "נזילה", None, lead_id
    )

    lead = await mock_db.leads.find_one({"_id": lead_id})
    assert lead["status"] == LeadStatus.NEW
    assert lead["street"] == "הרצל"
    assert lead["street_number"] == "10"
    assert lead["city"] == "תל אביב"
    assert lead["floor"] == "2"
    assert lead["apartment"] == "5"


@pytest.mark.asyncio
async def test_a_newly_extracted_address_part_still_overwrites(
    wf_mocks, mock_db  # noqa: F811
):
    chat = "972500217006@c.us"
    pro = {"_id": ObjectId(), "business_name": "יוסי", "phone_number": "972500217099"}
    lead_id = (
        await mock_db.leads.insert_one(
            {
                "chat_id": chat,
                "status": LeadStatus.CONTACTED,
                "is_emergency": True,
                "street": "הרצל",
                "city": "תל אביב",
            }
        )
    ).inserted_id
    response = ai_engine_module._canned_reply("x", True)
    response.extracted_data.street = "ביאליק"

    await app.services.workflow_service._finalize_deal(
        chat, pro, response, "תל אביב", "נזילה", None, lead_id
    )

    lead = await mock_db.leads.find_one({"_id": lead_id})
    assert lead["street"] == "ביאליק"
    assert lead["city"] == "תל אביב"
