"""Record/replay Gemini for the offline E2E harness (PRO-83).

CI must be deterministic and free, so the harness never calls Gemini. It replays
recorded ``AIResponse`` payloads through an object that satisfies the same
interface ``workflow_service`` and ``pro_flow`` call — ``analyze_conversation`` and
``detect_service_intent``. Everything downstream of the AI (the deal marker, the
address gate, the sticky-facts merge, the price validator) runs for real against
the replayed payload.

Two sources of a response, checked in order:

1. **A scripted turn.** ``world.ai.script(...)`` queues an exact sequence for a
   multi-turn scenario, where the same user text legitimately means different
   things at different points in the conversation.
2. **The fixture file** (``fixtures/gemini_responses.json``), keyed by persona +
   media kind + user text. This is what the state × input matrix uses: "what does
   the model do with 'סבבה מתאים לי' in this state" is a stable, recordable fact.

If neither produces a response the call **fails loudly with the exact key**. There
is deliberately no fall-through default: a silent stand-in would let a matrix cell
pass while testing nothing, which is the failure mode this ticket exists to
prevent.

Re-recording against live Gemini::

    PROLI_E2E_RECORD=1 GEMINI_API_KEY=... pytest tests/e2e -k <scenario>

That switch also stops ``tests/conftest.py`` stubbing out ``google.genai``, so the
real SDK loads. New responses are merged into the fixture file on the way out.

Record mode refuses to write junk (PRO-292). It prints which key it uses
(``GEMINI_API_KEY …last4``) and stops at once on a placeholder key. It raises
``RecordingFailed`` rather than saving the engine's own fallback (the overload
message, a media notice) as a model reply. The tests of the missing-fixture
path are skipped, since recording would turn their input into a fixture.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from app.core.prompts import Prompts
from app.services.ai_engine_service import AIResponse, ExtractedData

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "gemini_responses.json"

RECORD_ENV = "PROLI_E2E_RECORD"


def recording_enabled() -> bool:
    return bool(os.getenv(RECORD_ENV))


class RecordingFailed(RuntimeError):
    """Raised in record mode when what came back is not the model's own answer."""


# PRO-292: a key that cannot reach Gemini makes every live call fail, and the
# engine then returns its canned overload reply, which a recorder that does not
# check saves as if the model had said it. The web-session setup writes
# "web-session-not-a-real-key"; the others are the usual stand-ins.
_PLACEHOLDER_KEY_MARKERS = ("not-a-real", "placeholder", "dummy", "fake", "changeme")
_MIN_KEY_LENGTH = 30  # a Gemini API key is 39 characters


def live_key_label() -> str:
    """`GEMINI_API_KEY …abcd`, or raise if the key is a placeholder.

    Names the variable and only its last four characters, so a re-record log
    says which key it used without printing it.
    """
    from app.core.config import settings

    secret = settings.GEMINI_API_KEY.get_secret_value().strip()
    lowered = secret.lower()
    if len(secret) < _MIN_KEY_LENGTH or any(
        marker in lowered for marker in _PLACEHOLDER_KEY_MARKERS
    ):
        raise RecordingFailed(
            f"{RECORD_ENV}=1 needs a real GEMINI_API_KEY; the one loaded "
            f"(…{secret[-4:]}, {len(secret)} characters) is a placeholder. "
            "Nothing was recorded."
        )
    return f"GEMINI_API_KEY …{secret[-4:]}"


def canned_reply_in(reply_text: str) -> str | None:
    """The engine's own fallback text in a reply, if that is what it is.

    `AIEngine.analyze_conversation` answers with these when the models failed
    or never saw the file, so none of them is a recording of the model.
    """
    from app.core.messages import Messages

    if Messages.Errors.AI_OVERLOAD in (reply_text or ""):
        return "AI_OVERLOAD"
    for name in ("MEDIA_UPLOAD_FAILED", "MEDIA_UPLOAD_FAILED_TEXT_ONLY"):
        if (reply_text or "").strip() == getattr(Messages.Errors, name):
            return name
    return None


# Persona is derived from the source prompts rather than hardcoded, so a prompt
# rewrite can never silently misfile every recorded response under one persona.
_DISPATCHER_MARKER = Prompts.DISPATCHER_SYSTEM.strip().split("\n", 1)[0]
assert _DISPATCHER_MARKER not in Prompts.PRO_BASE_SYSTEM, (
    "the dispatcher prompt's first line is no longer distinctive — ai_replay can "
    "no longer tell the dispatcher persona from the pro persona"
)


def _persona(system_prompt: str) -> str:
    return "dispatcher" if _DISPATCHER_MARKER in (system_prompt or "") else "pro"


def _media_kind(mime: str | None) -> str:
    if not mime:
        return "text"
    for kind in ("image", "audio", "video"):
        if kind in mime:
            return kind
    return "other"


def fixture_key(system_prompt: str, user_text: str, media_mime: str | None) -> str:
    return f"{_persona(system_prompt)}|{_media_kind(media_mime)}|{(user_text or '').strip()}"


def response_to_dict(response: AIResponse) -> dict:
    return {
        "reply_to_user": response.reply_to_user,
        "transcription": response.transcription,
        "is_deal": response.is_deal,
        "extracted_data": response.extracted_data.model_dump(),
    }


def dict_to_response(data: dict) -> AIResponse:
    """Build an ``AIResponse`` from a fixture entry.

    ``city``, ``issue``, ``appointment_time`` and ``transcription`` have no
    pydantic defaults, so they are filled with ``None`` when a fixture omits them —
    that keeps fixtures readable (only the fields that matter are written down)
    without loosening the production model.
    """
    extracted = dict(data.get("extracted_data") or {})
    for required in ("city", "issue", "appointment_time"):
        extracted.setdefault(required, None)
    return AIResponse(
        reply_to_user=data.get("reply_to_user", ""),
        transcription=data.get("transcription"),
        is_deal=bool(data.get("is_deal", False)),
        extracted_data=ExtractedData(**extracted),
    )


def load_fixtures() -> dict:
    if not FIXTURE_PATH.exists():
        return {"responses": {}, "intents": {}}
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    data.setdefault("responses", {})
    data.setdefault("intents", {})
    return data


def save_fixtures(data: dict) -> None:
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


class MissingAIFixture(AssertionError):
    """Raised when a flow asks the model something nothing has recorded an answer for."""


class ReplayAIEngine:
    """Drop-in stand-in for ``AIEngine`` backed by scripted turns + recorded fixtures."""

    def __init__(self) -> None:
        self._fixtures = load_fixtures()
        self._scripted: list[AIResponse] = []
        self._scripted_intents: list[bool] = []
        self.calls: list[dict] = []
        self._recorded_this_run: dict = {"responses": {}, "intents": {}}
        self._key_checked = False

    # -- scripting ----------------------------------------------------------
    def script(self, *responses: AIResponse) -> None:
        """Queue an exact sequence of responses for the next ``analyze_conversation``
        calls, consumed in order. Used where conversation position — not the text —
        determines what the model would say."""
        self._scripted.extend(responses)

    def script_intent(self, *values: bool) -> None:
        self._scripted_intents.extend(values)

    @property
    def pending_script(self) -> int:
        return len(self._scripted)

    # -- the AIEngine interface --------------------------------------------
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
    ):
        key = fixture_key(custom_system_prompt, user_text, media_mime_type)
        self.calls.append(
            {
                "key": key,
                "user_text": user_text,
                "media_mime": media_mime_type,
                "media_lost": media_lost,
            }
        )

        if self._scripted:
            return self._scripted.pop(0)

        if recording_enabled():
            return await self._record(
                key,
                history,
                user_text,
                custom_system_prompt,
                media_data,
                media_mime_type,
                media_url,
                pro_id,
            )

        entry = self._fixtures["responses"].get(key)
        if entry is None:
            raise MissingAIFixture(
                f"no recorded Gemini response for key {key!r}.\n"
                f"Either queue one with world.ai.script(...) for this scenario, or "
                f"re-record with {RECORD_ENV}=1.\n"
                f"Known keys for this persona: "
                f"{sorted(k for k in self._fixtures['responses'] if k.startswith(key.split('|')[0]))}"
            )
        return dict_to_response(entry)

    async def detect_service_intent(self, text: str) -> bool:
        key = (text or "").strip()
        self.calls.append({"key": f"intent|{key}", "user_text": text})

        if self._scripted_intents:
            return self._scripted_intents.pop(0)

        if recording_enabled():
            from app.services.ai_engine_service import AIEngine

            self._check_live_key()
            try:
                value = await AIEngine().detect_service_intent(text, raise_errors=True)
            except Exception as e:
                raise RecordingFailed(
                    f"live detect_service_intent call for {key!r} failed "
                    f"({type(e).__name__}); nothing was saved for this key."
                ) from e
            self._recorded_this_run["intents"][key] = value
            self._fixtures["intents"][key] = value
            return value

        if key not in self._fixtures["intents"]:
            raise MissingAIFixture(
                f"no recorded detect_service_intent verdict for {key!r}. "
                f"Queue one with world.ai.script_intent(...) or re-record with "
                f"{RECORD_ENV}=1."
            )
        return bool(self._fixtures["intents"][key])

    # -- recording ----------------------------------------------------------
    def _check_live_key(self) -> None:
        """Once per engine: refuse a placeholder key, and say which key is used."""
        if self._key_checked:
            return
        print(f"[ai_replay] recording with {live_key_label()}")
        self._key_checked = True

    async def _record(
        self,
        key,
        history,
        user_text,
        custom_system_prompt,
        media_data,
        media_mime_type,
        media_url,
        pro_id,
    ):
        from app.services.ai_engine_service import AIEngine

        self._check_live_key()
        live = await AIEngine().analyze_conversation(
            history=history,
            user_text=user_text,
            custom_system_prompt=custom_system_prompt,
            media_data=media_data,
            media_mime_type=media_mime_type,
            require_json=True,
            media_url=media_url,
            pro_id=pro_id,
        )
        canned = canned_reply_in(live.reply_to_user)
        if canned:
            # The live call failed and the engine answered for the model. Saving
            # that would overwrite a real recording with the outage message.
            raise RecordingFailed(
                f"live Gemini call for {key!r} returned the engine's {canned} "
                "fallback, not a model reply; nothing was saved for this key."
            )
        payload = response_to_dict(live)
        self._recorded_this_run["responses"][key] = payload
        self._fixtures["responses"][key] = payload
        return live

    def flush_recording(self) -> None:
        """Merge anything captured this run back into the fixture file."""
        if not recording_enabled() or not (
            self._recorded_this_run["responses"] or self._recorded_this_run["intents"]
        ):
            return
        on_disk = load_fixtures()
        on_disk["responses"].update(self._recorded_this_run["responses"])
        on_disk["intents"].update(self._recorded_this_run["intents"])
        save_fixtures(on_disk)


# --- Convenience builders for scripted turns --------------------------------


def reply(text: str, **extracted) -> AIResponse:
    """A clarifying/conversational turn: the model talks, extracts nothing final."""
    return AIResponse(
        reply_to_user=text,
        transcription=extracted.pop("transcription", None),
        is_deal=False,
        extracted_data=ExtractedData(
            city=extracted.pop("city", None),
            issue=extracted.pop("issue", None),
            appointment_time=extracted.pop("appointment_time", None),
            **extracted,
        ),
    )


def deal(text: str, **extracted) -> AIResponse:
    """A closing turn: the customer agreed, address + time are on the table."""
    response = reply(text, **extracted)
    return AIResponse(
        reply_to_user=response.reply_to_user,
        transcription=response.transcription,
        is_deal=True,
        extracted_data=response.extracted_data,
    )
