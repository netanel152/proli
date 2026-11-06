"""PRO-204: a file the model will not see gets a straight answer, not a guess."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

import app.services.ai_engine_service as ai_engine_module
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


FAILURES = [
    pytest.param(_break_upload, "audio/ogg", id="upload-raises"),
    pytest.param(_break_processing, "video/mp4", id="video-processing-failed"),
    pytest.param(_never_active, "video/mp4", id="video-never-active"),
    pytest.param(_break_download, "video/mp4", id="download-not-200"),
    pytest.param(_nothing_fetched, None, id="image-not-fetched"),
]


def _sent_text(ai):
    return [
        call.kwargs["text"]
        for call in ai_engine_module.types.Part.call_args_list
        if "text" in call.kwargs
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("arrange, mime", FAILURES)
async def test_media_only_failure_answers_without_calling_the_model(
    engine, arrange, mime
):
    arrange(engine)

    with patch.object(ai_engine_module, "logger") as mock_logger:
        result = await engine.analyze_conversation(
            [], "", "prompt", media_mime_type=mime, media_url=MEDIA_URL
        )

    assert result.reply_to_user == Messages.Errors.MEDIA_UPLOAD_FAILED
    assert result.is_deal is False
    engine.client.aio.models.generate_content.assert_not_awaited()
    # logger.error is what the Sentry bridge picks up.
    mock_logger.error.assert_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("arrange, mime", FAILURES)
async def test_failure_with_text_answers_the_text_and_says_so(engine, arrange, mime):
    arrange(engine)

    result = await engine.analyze_conversation(
        [], "נזילה בתל אביב", "prompt", media_mime_type=mime, media_url=MEDIA_URL
    )

    assert result.reply_to_user == (
        f"{Messages.Errors.MEDIA_UPLOAD_FAILED}\n\nAI reply"
    )
    engine.client.aio.models.generate_content.assert_awaited_once()
    ai_engine_module.types.Part.from_uri.assert_not_called()
    text = _sent_text(engine)[-1]
    assert text.startswith("נזילה בתל אביב")
    assert Messages.AISystemPrompts.MEDIA_NOT_SEEN in text
    # Never ask the model to transcribe a recording it was not given.
    assert Messages.AISystemPrompts.TRANSCRIBE_AUDIO not in text
    assert Messages.AISystemPrompts.ANALYZE_VIDEO not in text


@pytest.mark.asyncio
async def test_all_models_failing_still_mentions_the_file(engine):
    _break_upload(engine)
    engine.client.aio.models.generate_content = AsyncMock(
        side_effect=RuntimeError("overloaded")
    )

    result = await engine.analyze_conversation(
        [], "נזילה", "prompt", media_mime_type="audio/ogg", media_url=MEDIA_URL
    )

    assert result.reply_to_user == (
        f"{Messages.Errors.MEDIA_UPLOAD_FAILED}\n\n{Messages.Errors.AI_OVERLOAD}"
    )


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
    assert sent == [Messages.Errors.MEDIA_UPLOAD_FAILED]
    mock_lm.log_message.assert_any_call(
        chat, "model", Messages.Errors.MEDIA_UPLOAD_FAILED
    )
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
    assert not any(Messages.Errors.MEDIA_UPLOAD_FAILED in s for s in sent)
