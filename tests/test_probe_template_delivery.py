"""scripts/probe_template_delivery.py (PRO-232) against a fake facade. No real send.

The script's whole contract is its exit code: 0 only when the closed-window
template path was proven, so a pass is never reported for a send that did not
exercise it.
"""

import importlib.util
import pathlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import settings
from app.core.messages import Messages
from app.core.phone import to_chat_id
from app.providers.whatsapp.template_registry import DELIVERY_PROBE_KEY

_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "scripts"
    / "probe_template_delivery.py"
)
_spec = importlib.util.spec_from_file_location("probe_template_delivery", _PATH)
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)

NUMBER = "972500000042"
AS_TEMPLATE = {"idMessage": "wamid.1", "sent_as_template": DELIVERY_PROBE_KEY}
AS_TEXT = {"idMessage": "wamid.2"}


@pytest.fixture
def facade(monkeypatch):
    fake = MagicMock()
    fake.provider.transmits = True
    fake.send_message = AsyncMock(return_value=AS_TEMPLATE)
    fake.close = AsyncMock()
    monkeypatch.setattr(probe, "get_whatsapp", lambda: fake)
    return fake


@pytest.fixture
def armed(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_ARMED_TEMPLATES", DELIVERY_PROBE_KEY)


@pytest.mark.parametrize(
    "result, exit_code",
    # Literal on purpose: the numbers are the contract. 1 and 2 are left to an
    # uncaught exception and to argparse, so a verdict must never use them.
    [(AS_TEMPLATE, 0), (AS_TEXT, 3), (None, 4)],
    ids=["template-sent", "text-sent-window-open", "blocked"],
)
def test_the_exit_code_says_which_path_the_probe_took(facade, armed, result, exit_code):
    facade.send_message.return_value = result

    assert probe.main(["--to", NUMBER]) == exit_code

    facade.send_message.assert_awaited_once_with(
        to_chat_id(NUMBER),
        Messages.System.DELIVERY_PROBE,
        template_key=DELIVERY_PROBE_KEY,
        params={},
    )
    facade.close.assert_awaited_once()


def test_an_unarmed_probe_sends_nothing_and_exits_not_sent(facade, monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_ARMED_TEMPLATES", "")

    assert probe.main(["--to", NUMBER]) == 4

    facade.send_message.assert_not_awaited()


def test_a_provider_that_cannot_transmit_sends_nothing_and_exits_not_sent(
    facade, armed
):
    """A dry-run "send" would succeed on the text path and prove nothing."""
    facade.provider.transmits = False
    facade.provider.name = "dryrun"

    assert probe.main(["--to", NUMBER]) == 4

    facade.send_message.assert_not_awaited()


def test_a_raising_send_exits_not_sent_and_the_facade_is_still_closed(facade, armed):
    facade.send_message.side_effect = RuntimeError("Meta is down")

    assert probe.main(["--to", NUMBER]) == 4

    facade.close.assert_awaited_once()
