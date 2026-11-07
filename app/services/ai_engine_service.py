from google import genai
from google.genai import types
from app.core.config import settings
from app.core.logger import logger
from app.core.messages import Messages
from pydantic import BaseModel, Field
from typing import Optional
import json
import os
import tempfile
import asyncio
from app.core.http_client import get_http_client
from app.core.background_tasks import spawn_background_task
from app.core.database import users_collection
from bson import ObjectId


class ExtractedData(BaseModel):
    city: Optional[str] = Field(
        description="The extracted city/location from the user's input."
    )
    issue: Optional[str] = Field(
        description="The extracted issue or service description."
    )
    customer_name: Optional[str] = Field(
        default=None,
        description="The customer's first name, as they introduced themselves.",
    )
    street: Optional[str] = Field(
        default=None, description="Street name only, e.g. 'הרצל'."
    )
    street_number: Optional[str] = Field(
        default=None, description="Building number, e.g. '15'."
    )
    floor: Optional[str] = Field(default=None, description="Floor number, e.g. '2'.")
    apartment: Optional[str] = Field(
        default=None, description="Apartment number, e.g. '4'."
    )
    appointment_time: Optional[str] = Field(
        description="Preferred time for the appointment, exactly as the customer phrased it (e.g. 'מחר ב-10', 'בהקדם')."
    )
    appointment_datetime: Optional[str] = Field(
        default=None,
        description="The appointment resolved to an absolute ISO 8601 datetime (e.g. '2026-07-18T10:00:00'). Compute it from the CURRENT DATE/TIME given in the system prompt and the customer's stated time, resolving relative expressions ('מחר', 'היום', 'יום ראשון', 'עוד שעה'). Leave null if the customer gave no specific time or only said something open-ended like 'בהקדם'.",
    )
    quoted_price: Optional[str] = Field(
        default=None,
        description="The price range you quoted the customer, as DIGITS ONLY, e.g. '400-600' for 'בסביבות 400-600₪'. No ₪ symbol, no words. Leave null if you have not given a price estimate.",
    )


class AIResponse(BaseModel):
    reply_to_user: str = Field(
        description="The reply sent to the user on WhatsApp, in Hebrew, written to the STYLE rules of the system prompt."
    )
    transcription: Optional[str] = Field(
        description="Full text transcription if the user sent an audio message."
    )
    # NOTE: this field must NOT carry a `Field(description=...)`. On pydantic
    # 2.6.x (the version requirements.txt pins), attaching any sibling metadata
    # to a nested-model field makes model_json_schema() emit
    # `{"allOf": [{"$ref": ...}], "description": ...}` instead of a bare `$ref`.
    # google-genai's process_schema() understands `$ref` and `anyOf` but not
    # `allOf`, so it reaches an inner sub-schema with no "type" key and blows up
    # with `'NoneType' object has no attribute 'upper'` — before any HTTP call,
    # which means *every* model in the fallback hierarchy fails identically.
    # The per-field descriptions inside ExtractedData carry the semantics.
    extracted_data: ExtractedData
    is_deal: bool = Field(
        description="Set to True ONLY if the user has provided specific Time AND Address and agreed to book. Otherwise False."
    )


MAX_CONVERSATION_TURNS = 5  # 5 turns = 10 messages (user + model)


def _no_text_notice(media_mime_type: str | None) -> str:
    """What to say when the file failed and there is no text to answer."""
    if media_mime_type and "audio" not in media_mime_type:
        return Messages.Errors.MEDIA_UPLOAD_FAILED
    return Messages.Errors.MEDIA_UPLOAD_FAILED_TEXT_ONLY


def file_notice_in(reply: str) -> str | None:
    """The failed-file notice a reply opens with, for an exit that won't send it."""
    for notice in (
        Messages.Errors.MEDIA_FAILED_TEXT_ANSWERED,
        Messages.Errors.MEDIA_UPLOAD_FAILED,
        Messages.Errors.MEDIA_UPLOAD_FAILED_TEXT_ONLY,
    ):
        if reply.startswith(notice):
            return notice
    return None


def _file_was_refused(error: Exception | None) -> bool:
    """Did the models turn this request down, as opposed to being unavailable?"""
    if error is None:  # every model answered, none of it parseable
        return True
    code = getattr(error, "code", None)
    return isinstance(code, int) and 400 <= code < 500 and code != 429


def _canned_reply(text: str, require_json: bool) -> AIResponse | str:
    """A reply the model did not write, in the shape the caller asked for."""
    if not require_json:
        return text
    return AIResponse(
        reply_to_user=text,
        transcription=None,
        extracted_data=ExtractedData(city=None, issue=None, appointment_time=None),
        is_deal=False,
    )


async def _track_token_usage(pro_id: str, token_count: int) -> None:
    """Fire-and-forget: increment total_tokens_used for a pro in MongoDB."""
    try:
        await users_collection.update_one(
            {"_id": ObjectId(pro_id)}, {"$inc": {"total_tokens_used": token_count}}
        )
    except Exception as e:
        logger.warning(f"Token tracking failed for pro {pro_id}: {e}")


class AIEngine:
    """
    Handles interactions with Google Gemini API.
    Designed to be run within a background worker (ARQ) due to potential latency
    in media processing and LLM generation.
    """

    def __init__(self):
        self.client = genai.Client(api_key=settings.GEMINI_API_KEY.get_secret_value())
        # Define the fallback hierarchy from settings
        self.model_hierarchy = settings.AI_MODELS

    async def analyze_conversation(
        self,
        history: list,
        user_text: str,
        custom_system_prompt: str,
        media_data: bytes = None,
        media_mime_type: str = None,
        require_json: bool = True,
        media_url: str = None,
        pro_id: str = None,
        media_lost: bool = False,
    ) -> AIResponse | str:
        contents = []
        for msg in history:
            parts = [types.Part(text=p) for p in msg.get("parts", [])]
            contents.append(types.Content(role=msg["role"], parts=parts))

        # Limit to last N turns to prevent token bloat / hallucination
        contents = contents[-(MAX_CONVERSATION_TURNS * 2) :]

        current_parts = []

        # Handle media: URL takes precedence for large files (audio/video), bytes for small (images)
        if (
            media_url
            and media_mime_type
            and ("audio" in media_mime_type or "video" in media_mime_type)
        ):
            # For Audio/Video, we must use the File API to avoid size limits and timeouts
            tmp_path = None
            try:
                http_client = await get_http_client()
                async with http_client.stream("GET", media_url) as resp:
                    if resp.status_code == 200:
                        with tempfile.NamedTemporaryFile(
                            delete=False, suffix=f".{media_mime_type.split('/')[-1]}"
                        ) as tmp:
                            tmp_path = tmp.name
                            async for chunk in resp.aiter_bytes():
                                tmp.write(chunk)

                        uploaded_file = await self.client.aio.files.upload(
                            file=tmp_path,
                            config=types.UploadFileConfig(mime_type=media_mime_type),
                        )

                        if "video" in media_mime_type:
                            max_wait_seconds = 120
                            waited = 0
                            while waited < max_wait_seconds:
                                file_status = await self.client.aio.files.get(
                                    name=uploaded_file.name
                                )
                                if file_status.state.name == "ACTIVE":
                                    break
                                elif file_status.state.name == "FAILED":
                                    raise Exception("Gemini File Processing Failed")
                                logger.info(
                                    f"Waiting for video processing: {file_status.state.name}"
                                )
                                await asyncio.sleep(2)
                                waited += 2
                            else:
                                raise Exception(
                                    f"Gemini video processing timed out after {max_wait_seconds}s"
                                )

                        current_parts.append(
                            types.Part.from_uri(
                                file_uri=uploaded_file.uri, mime_type=media_mime_type
                            )
                        )
                    else:
                        logger.error(
                            f"Failed to download media for Gemini from {media_url}"
                        )
            except Exception as e:
                logger.error(f"Error handling media URL for Gemini: {e}")
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    os.unlink(tmp_path)

        elif media_data and media_mime_type:
            # Fallback to bytes for images or if URL failed/not provided
            current_parts.append(
                types.Part.from_bytes(data=media_data, mime_type=media_mime_type)
            )
        elif media_url:
            # detect_and_fetch_media swallows its own failures and returns
            # (None, None), so a photo we could not download arrives here.
            logger.error("Media for this turn was not fetched; nothing to send Gemini")

        # A file we could not attach here: a failed download or upload, or a video
        # that never went ACTIVE. Answering as usual is a confident reply about
        # something nobody read. One lost before here arrives as media_lost, with
        # no media_url. One the models refuse is handled after the call below.
        media_attached = bool(current_parts)
        media_failed = (bool(media_url) or media_lost) and not media_attached
        typed_text = user_text
        has_text = bool((user_text or "").strip())
        if media_failed:
            # Nothing left to answer, so no model call. The caller still runs its
            # sticky gate on this reply, which keeps the file's link on the lead.
            if not has_text:
                return _canned_reply(_no_text_notice(media_mime_type), require_json)
            user_text += f"\n{Messages.AISystemPrompts.MEDIA_NOT_SEEN}"
        # One newline: _strip_deal_marker turns a blank line into a space.
        answered_notice = f"{Messages.Errors.MEDIA_FAILED_TEXT_ANSWERED}\n"

        if media_mime_type and not media_failed:
            if "image" in media_mime_type:
                user_text = (
                    user_text or ""
                ) + f"\n{Messages.AISystemPrompts.ANALYZE_IMAGE}"
            elif "audio" in media_mime_type:
                user_text = (
                    user_text or ""
                ) + f"\n{Messages.AISystemPrompts.TRANSCRIBE_AUDIO}"
            elif "video" in media_mime_type:
                user_text = (
                    user_text or ""
                ) + f"\n{Messages.AISystemPrompts.ANALYZE_VIDEO}"

        if user_text:
            current_parts.append(types.Part(text=user_text))

        if current_parts:
            contents.append(types.Content(role="user", parts=current_parts))

        if not custom_system_prompt:
            custom_system_prompt = Messages.AISystemPrompts.DEFAULT_SYSTEM

        config_args = {"system_instruction": custom_system_prompt, "temperature": 0.3}

        if require_json:
            config_args["response_mime_type"] = "application/json"
            config_args["response_schema"] = AIResponse

        reply, error = await self._generate_reply(
            contents,
            config_args,
            require_json,
            pro_id,
            answered_notice if media_failed else "",
        )
        if reply is not None:
            return reply
        overload = _canned_reply(Messages.Errors.AI_OVERLOAD, require_json)
        if media_failed:
            # The file failed and so did the models, so nothing was answered.
            notice = _no_text_notice(media_mime_type)
            return _canned_reply(
                f"{notice}\n{Messages.Errors.AI_OVERLOAD}", require_json
            )
        # An outage is not the file's fault, and a second round would only add
        # calls while it lasts.
        if not media_attached or not _file_was_refused(error):
            return overload

        # Every model refused a turn with a file attached, and "try again" would
        # send the same file back. It may be the file, so ask again without it. If
        # that fails too it wasn't the file, and the customer is not told it was.
        if not has_text:
            return _canned_reply(_no_text_notice(media_mime_type), require_json)
        contents[-1] = types.Content(
            role="user",
            parts=[
                types.Part(
                    text=f"{typed_text}\n{Messages.AISystemPrompts.MEDIA_NOT_SEEN}"
                )
            ],
        )
        reply, _ = await self._generate_reply(
            contents, config_args, require_json, pro_id, answered_notice
        )
        return overload if reply is None else reply

    async def _generate_reply(
        self,
        contents: list,
        config_args: dict,
        require_json: bool,
        pro_id: str | None,
        notice: str,
    ) -> tuple[AIResponse | str | None, Exception | None]:
        """Walk the model hierarchy. (reply, None), or (None, the last error) when
        every model failed; that error is None if they all returned bad JSON."""
        last_error = None

        # Iterate through models in hierarchy
        for model_name in self.model_hierarchy:
            try:
                # Use the Async IO (.aio) accessor
                response = await self.client.aio.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=types.GenerateContentConfig(**config_args),
                )

                # Non-blocking token accounting (pro-phase calls only)
                if (
                    pro_id
                    and hasattr(response, "usage_metadata")
                    and response.usage_metadata
                ):
                    token_count = getattr(
                        response.usage_metadata, "total_token_count", 0
                    )
                    if token_count:
                        spawn_background_task(
                            _track_token_usage(pro_id, token_count),
                            name=f"track_token_usage:{pro_id}",
                        )

                if require_json:
                    try:
                        if hasattr(response, "parsed") and response.parsed:
                            parsed = response.parsed
                        else:
                            parsed = AIResponse(**json.loads(response.text))
                        if notice:
                            parsed.reply_to_user = notice + parsed.reply_to_user
                        return parsed, None
                    except Exception as parse_error:
                        preview = response.text[:500] if response.text else "EMPTY"
                        logger.warning(
                            f"JSON Parse Error with {model_name}: {parse_error} - "
                            f"response text (first 500 chars): {preview}"
                        )
                        # Treat JSON parse error as a hard failure for this model → fall back.
                        continue

                return notice + response.text.strip(), None

            except Exception as e:
                logger.warning(
                    f"Model {model_name} failed: {type(e).__name__}: {e}. "
                    "Trying next fallback..."
                )
                last_error = e
                continue

        # If all models failed. `traceback.format_exc()` is useless here — we are
        # outside the `except` block, so it renders as "NoneType: None". Attach the
        # captured exception instead so the real stack reaches the logs/Sentry.
        if last_error is not None:
            logger.opt(exception=last_error).error(
                f"All AI models failed. Last error: {type(last_error).__name__}: {last_error}"
            )
        else:
            logger.error(
                "All AI models failed (every model returned unparseable JSON)."
            )
        return None, last_error

    async def detect_service_intent(self, text: str) -> bool:
        """Lightweight classifier: does this text describe a home-service need?

        Uses a single-shot low-token Gemini call at temperature=0.0.
        Returns False conservatively on any error so it never blocks a Pro from the help menu.
        """
        if not text or len(text.strip()) < 3:
            return False
        try:
            response = await self.client.aio.models.generate_content(
                model=self.model_hierarchy[0],
                contents=text.strip(),
                config=types.GenerateContentConfig(
                    system_instruction=(
                        "Analyze the following text. Does it describe a home service malfunction, "
                        "repair request, or a need for a professional (e.g., 'my AC is leaking', "
                        "'need a plumber')? Reply ONLY with 'True' or 'False'."
                    ),
                    temperature=0.0,
                ),
            )
            result = (response.text or "").strip().lower()
            return "true" in result
        except Exception as e:
            logger.warning(f"detect_service_intent failed: {e}")
            return False
