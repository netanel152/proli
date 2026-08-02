"""Safety guards for the offline E2E harness (PRO-83).

These are the tests that make the rest of the harness safe to run on every PR. They
are deliberately blunt: the failure mode being defended against is not a subtle
logic bug, it is a QA script quietly sending real WhatsApp messages to a real
handset (PRO-72), which cost the project a yellowCard.
"""

from pathlib import Path

import httpx
import pytest

from app.core.config import settings
from tests.e2e import reserved_numbers as R
from tests.e2e.ai_replay import deal, reply

HARNESS_ROOT = Path(__file__).parent


def _harness_sources() -> list[Path]:
    return sorted(p for p in HARNESS_ROOT.rglob("*") if p.is_file())


# ===========================================================================
# The numbers
# ===========================================================================


def test_no_real_subscriber_number_appears_anywhere_in_the_harness():
    """The two numbers that must never be in this tree:

    ``…3651414`` is the real handset PRO-72 was spamming; ``…4828796`` is the
    hardcoded ``ADMIN_PHONE`` default, which silently becomes the recipient of
    every SOS and escalation alert unless it is overridden.

    Every file under ``tests/e2e/`` is scanned with no exclusions — including the
    denylist module itself, which is why it stores its entries split.
    """
    offenders = []
    for path in _harness_sources():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for forbidden in R.FORBIDDEN_NUMBERS:
            if forbidden in text:
                offenders.append(
                    f"{path.relative_to(HARNESS_ROOT)} contains {forbidden}"
                )
    assert not offenders, "forbidden real numbers found in the harness:\n" + "\n".join(
        offenders
    )


def test_every_harness_number_is_structurally_unreachable():
    """An Israeli MSISDN is ``972`` + the national number with the trunk ``0``
    dropped, so the digit after ``972`` is never ``0``. Every harness number is
    ``9720…`` and therefore cannot route to a subscriber."""
    for number in R.ALL_RESERVED:
        assert number.startswith("9720"), f"{number} is outside the reserved range"
        assert len(number) == 12, f"{number} is not a well-formed intl number"
        assert R.is_reserved(number)
        assert R.is_reserved(R.chat(number))


def test_a_real_number_is_not_mistaken_for_a_reserved_one():
    for forbidden in R.FORBIDDEN_NUMBERS:
        assert not R.is_reserved(forbidden)


# ===========================================================================
# The network
# ===========================================================================


@pytest.mark.asyncio
async def test_a_real_http_attempt_fails_the_run(forbid_real_network):
    """The zero-real-sends guarantee is an assert, not a log line.

    Proven by driving the guard directly, then clearing the escape it recorded so
    the deliberate probe does not fail the session teardown.
    """
    async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport()) as client:
        with pytest.raises(AssertionError, match="REAL outbound HTTP request"):
            await client.get("https://api.green-api.com/health")

    assert forbid_real_network, "the guard should have recorded the escape attempt"
    forbid_real_network.clear()


@pytest.mark.asyncio
async def test_dry_run_is_forced_on_inside_the_harness(world):
    assert settings.WHATSAPP_DRY_RUN is True


@pytest.mark.asyncio
async def test_a_full_flow_reaches_green_api_zero_times(world, forbid_real_network):
    """The headline acceptance criterion: drive a complete booking and assert that
    every byte destined for Green API was intercepted, and none escaped."""
    await world.standard_cast()
    world.ai.script(
        reply("שלום! מה קרה?"),
        reply("הבנתי, נזילה. אני מאתר איש מקצוע.", city="תל אביב", issue="נזילה"),
        reply("מה הכתובת המדויקת?"),
        deal(
            "מעולה, סגרנו!",
            city="תל אביב",
            issue="נזילה",
            street="דיזנגוף",
            street_number="50",
            floor="3",
            apartment="12",
            appointment_time="מחר ב-10:00",
        ),
    )

    await world.send("היי")
    await world.send("יש לי נזילה בתל אביב")
    await world.send("דיזנגוף 50, קומה 3 דירה 12, מחר ב-10")

    assert world.recorder.sends, "the flow should have produced outbound traffic"
    assert not forbid_real_network, "no request may reach a real transport"


@pytest.mark.asyncio
async def test_every_outbound_recipient_is_a_reserved_number(world):
    """Nothing — not an SOS alert, not an admin escalation, not a pro
    notification — may be addressed to anything outside the reserved range."""
    await world.standard_cast()
    await world.add_admin_pro()
    world.ai.script(
        reply("שלום! מה קרה?"),
        reply("מאתר איש מקצוע.", city="תל אביב", issue="נזילה"),
        reply("מה הכתובת?"),
    )

    await world.send("היי")
    await world.send("יש לי נזילה בתל אביב")
    await world.send("נציג")  # SOS → alerts the admin and the assigned pro

    recipients = world.recorder.recipients()
    assert recipients, "expected outbound traffic"
    for chat_id in recipients:
        assert R.is_reserved(chat_id), (
            f"outbound message addressed to non-reserved {chat_id}\n"
            f"{world.recorder.transcript()}"
        )


@pytest.mark.asyncio
async def test_forbidden_numbers_never_appear_in_outbound_content(world):
    """Guards the other direction: a real number leaking into a message *body*
    (an SOS alert renders the customer's phone number, for instance)."""
    await world.standard_cast()
    await world.add_admin_pro()
    world.ai.script(reply("שלום! מה קרה?"))

    await world.send("היי")
    await world.send("נציג")

    for forbidden in R.FORBIDDEN_NUMBERS:
        world.recorder.assert_never_contains(
            forbidden, "a real subscriber number must never be rendered outbound"
        )
