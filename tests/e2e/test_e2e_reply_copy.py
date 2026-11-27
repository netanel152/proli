"""PRO-292: the copy rules reach the customer, and the greeting is not said twice.

Two halves. The recorded model replies are scanned against `copy_violations`
once cleaned (and, as a strict xfail, raw, so a re-record flips it). Then the
real orchestrator is driven with scripted replies that break each rule, and
the text the customer *receives* is read off the outbound recorder.

The state x input matrix and the happy paths already run the pipeline end to
end (`test_e2e_flows.py`, `test_e2e_state_matrix.py`); what is added here is
only what they do not look at: the cleaned text on both send sites and the
"already welcomed" note on the dispatcher prompt.
"""

import re
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from app.core.constants import WorkerConstants
from app.core.prompts import Prompts
from app.services import workflow_service
from app.services.reply_copy import clean_customer_reply, copy_violations
from tests.e2e.ai_replay import load_fixtures, reply

OPENING = "יש לי נזילה במטבח"
NAME = "נתקס"


# ---------------------------------------------------------------------------
# The recorded replies
# ---------------------------------------------------------------------------


def _recorded_replies():
    for key, entry in load_fixtures()["responses"].items():
        name = (entry.get("extracted_data") or {}).get("customer_name")
        yield key, entry.get("reply_to_user", ""), name


def _repeats_the_name(text: str, name: str | None) -> bool:
    return bool(name) and bool(
        re.search(rf"{re.escape(name)}[\s,]*{re.escape(name)}", text)
    )


def test_a_recorded_reply_that_breaks_no_rule_is_sent_unchanged():
    """The cleaner shares its regexes with `copy_violations`, so "clean after
    cleaning" proves nothing. What can fail is the other direction: a reply that
    breaks no rule must come back byte for byte, or the cleaner is rewriting
    good copy."""
    already_clean = [
        (key, text, name)
        for key, text, name in _recorded_replies()
        if not copy_violations(text) and not _repeats_the_name(text, name)
    ]
    assert already_clean, "nothing recorded is clean, so this would pass vacuously"

    changed = {
        key: clean_customer_reply(text, name)
        for key, text, name in already_clean
        if clean_customer_reply(text, name) != text
    }

    assert changed == {}


@pytest.mark.xfail(
    strict=True,
    reason="fixtures predate the PRO-245 prompts; re-record with PROLI_E2E_RECORD=1 "
    "(PRO-292), then delete this marker",
)
def test_every_raw_recorded_reply_already_follows_the_copy_rules():
    offenders = {
        key: found
        for key, text, _name in _recorded_replies()
        if (found := copy_violations(text))
    }

    assert offenders == {}


# ---------------------------------------------------------------------------
# What the customer receives
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_doubled_name_and_an_emoji_do_not_reach_the_customer(world):
    """The first staging chat: "נעים להכיר, נתקסנתקס 👋"."""
    await world.grant_consent()
    world.ai.script(reply(f"נעים להכיר, {NAME}{NAME} 👋 מה קרה?", customer_name=NAME))

    await world.send(NAME)

    sent = world.recorder.bodies_to(world.customer)
    assert len(sent) == 1, world.recorder.transcript()
    assert sent[0].count(NAME) == 1
    assert copy_violations(sent[0]) == []
    # What the model reads on the next turn is the cleaned text too.
    logged = await world.db.messages.find_one(
        {"chat_id": world.customer, "role": "model"}
    )
    assert logged["text"] == sent[0]


@pytest.mark.asyncio
async def test_a_pro_phase_reply_never_has_the_customer_hear_proli_arrive(world):
    """Both customer send sites: the turn that matches a pro, and the next turn on
    the assigned-pro fast path."""
    await world.standard_cast()
    world.ai.script(
        reply("איך קוראים לך ומה קרה?"),
        reply("מעולה.", customer_name="דנה", city="תל אביב", issue="נזילה מתחת לכיור"),
        reply("ומתי נוח שאגיע? היום ב-18:00 — תיקון נזילה 👍"),
        reply("ומתי נוח שאגיע? 🙏"),
    )

    await world.send("היי")
    await world.send("יש לי נזילה מתחת לכיור בתל אביב")
    matched_turn = world.recorder.last_to(world.customer).body
    lead = await world.lead()
    assert lead is not None and lead["pro_id"], "the pro is matched on this turn"

    await world.send("מחר בבוקר")
    fast_path_turn = world.recorder.last_to(world.customer).body

    for body in (matched_turn, fast_path_turn):
        assert "איש המקצוע יגיע" in body
        assert copy_violations(body) == []
    assert world.ai.pending_script == 0


# ---------------------------------------------------------------------------
# The "already welcomed" note on the dispatcher prompt
# ---------------------------------------------------------------------------


def _capture_prompts(world) -> list[str]:
    """Every system prompt the model is called with (the replay engine keeps only
    the key, which is the persona and the text)."""
    prompts: list[str] = []
    real = world.ai.analyze_conversation

    async def _spy(*args, **kwargs):
        prompts.append(kwargs["custom_system_prompt"])
        return await real(*args, **kwargs)

    world.monkeypatch.setattr(world.ai, "analyze_conversation", _spy)
    return prompts


@pytest.mark.asyncio
async def test_only_the_first_dispatcher_turn_after_consent_is_told_it_was_welcomed(
    world,
):
    await world.grant_consent()
    prompts = _capture_prompts(world)
    world.ai.script(reply("איך קוראים לך?"), reply("ומה קרה?"))

    await world.send("היי")
    await world.send(NAME)

    assert len(prompts) == 2
    assert Prompts.DISPATCHER_ALREADY_WELCOMED in prompts[0]
    assert (
        Prompts.DISPATCHER_ALREADY_WELCOMED not in prompts[1]
    ), "the first model turn is in the history by now, so it cannot greet twice"


@pytest.mark.asyncio
async def test_a_customer_who_consented_long_ago_is_not_told_they_were_just_welcomed(
    world,
):
    stale = timedelta(seconds=WorkerConstants.CONSENT_HOLD_TTL_SECONDS + 60)
    await world.db.consent.insert_one(
        {"chat_id": world.customer, "accepted": True, "timestamp": world.anchor - stale}
    )
    prompts = _capture_prompts(world)
    world.ai.script(reply("איך קוראים לך?"))

    await world.send("היי")

    assert len(prompts) == 1
    assert Prompts.DISPATCHER_ALREADY_WELCOMED not in prompts[0]


@pytest.mark.asyncio
async def test_replaying_held_messages_after_accept_also_marks_the_customer_welcomed(
    world, monkeypatch
):
    """The replay is the other way into the first dispatcher turn: the consent
    lookup alone saying "no" must not hide the note."""
    monkeypatch.setattr(
        workflow_service, "consent_accepted_within", AsyncMock(return_value=False)
    )
    prompts = _capture_prompts(world)
    await world.send(OPENING)
    world.ai.script(reply("באיזו עיר?"))

    await world.send("כן")

    assert len(prompts) == 1
    assert Prompts.DISPATCHER_ALREADY_WELCOMED in prompts[0]


@pytest.mark.asyncio
async def test_a_failing_consent_lookup_still_lets_the_dispatcher_answer(
    world, monkeypatch
):
    """The note is a nicety: a Mongo error reading the consent record must not
    cost the customer their answer."""
    await world.grant_consent()
    monkeypatch.setattr(
        workflow_service,
        "consent_accepted_within",
        AsyncMock(side_effect=RuntimeError("mongo down")),
    )
    prompts = _capture_prompts(world)
    world.ai.script(reply("איך קוראים לך?"))

    await world.send("היי")

    assert len(prompts) == 1, "the model was still asked"
    assert Prompts.DISPATCHER_ALREADY_WELCOMED not in prompts[0]
    world.recorder.assert_text_to(world.customer, "איך קוראים לך?")
