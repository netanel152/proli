"""PRO-86 — the single outbound egress, and PRO-82's fail-closed breaker.

Replaces tests/test_whatsapp_client.py and
tests/test_whatsapp_client_circuit_breaker.py, both of which tested the deleted
Green API client. What is asserted here is the same guarantee one layer up and
transport-independent: nothing leaves the process unless the facade says so.

The headline test is
``test_send_is_blocked_when_no_state_probe_has_ever_succeeded`` — the PRO-82
boot-window regression. Before PRO-86 an absent Redis key was indistinguishable
from a healthy account, so every worker start opened a window in which a flagged
number could be sent from.
"""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock

import app.providers.whatsapp as provider_pkg
import app.providers.whatsapp.facade as facade_module
from app.core.config import settings
from app.core.constants import WorkerConstants
from app.providers.whatsapp.base import NormalizedMessage, WhatsAppProvider
from app.providers.whatsapp.cloud_api import CloudAPIProvider
from app.providers.whatsapp.dry_run import DryRunProvider
from app.providers.whatsapp.facade import WhatsAppFacade, record_account_state

_STATE_KEY = "wa:instance:state"
_PAUSE_KEY = "wa:instance:paused"
_PAUSE_MANUAL_KEY = "wa:instance:paused:manual"


class _TransmittingProvider(WhatsAppProvider):
    """A provider that claims it can reach real handsets.

    The breaker only applies to transports that can actually hurt a phone
    number, so every breaker assertion needs one of these — asserting against
    DryRunProvider would pass vacuously.
    """

    name = "fake-transmitting"
    transmits = True

    def __init__(self):
        self.sent: list[tuple] = []

    async def send_text(self, chat_id, text):
        self.sent.append(("text", chat_id, text))
        return {"id": "1"}

    async def send_file(self, chat_id, url, caption="", file_name="media.jpg"):
        self.sent.append(("file", chat_id, url))
        return {"id": "2"}

    async def send_template(self, chat_id, template_name, params=None):
        self.sent.append(("template", chat_id, template_name))
        return {"id": "3"}

    async def send_interactive(self, chat_id, body, options):
        self.sent.append(("interactive", chat_id, body))
        return {"id": "4"}

    async def send_typing(self, chat_id):
        self.sent.append(("typing", chat_id))

    async def get_state(self):
        return "authorized"

    def parse_webhook(self, payload):
        return None


@pytest.fixture
def transmitting():
    return _TransmittingProvider()


# ===========================================================================
# Circuit breaker — fail-closed on an absent state key (PRO-82)
# ===========================================================================


@pytest.mark.asyncio
async def test_send_is_blocked_when_no_state_probe_has_ever_succeeded(
    transmitting, fake_redis
):
    """PRO-82 regression, the whole point of this ticket's breaker change.

    Fresh Redis, no key: the old client read that as healthy and sent. It must
    now suppress — a worker that has just booted has no evidence the account is
    safe to send from.
    """
    facade = WhatsAppFacade(transmitting)

    result = await facade.send_message("972500000001@c.us", "hello")

    assert result is None
    assert transmitting.sent == [], "a send escaped during the boot window"


@pytest.mark.asyncio
async def test_send_proceeds_once_a_probe_has_confirmed_authorized(
    transmitting, fake_redis
):
    await fake_redis.set(_STATE_KEY, "authorized")
    facade = WhatsAppFacade(transmitting)

    await facade.send_message("972500000001@c.us", "hello")

    assert transmitting.sent == [("text", "972500000001@c.us", "hello")]


@pytest.mark.asyncio
async def test_send_is_blocked_when_the_recorded_state_is_not_authorized(
    transmitting, fake_redis
):
    """'yellowCard' and 'blocked' are truthy — the check must be equality against
    'authorized', not truthiness."""
    await fake_redis.set(_STATE_KEY, "yellowCard")
    facade = WhatsAppFacade(transmitting)

    assert await facade.send_message("972500000001@c.us", "hi") is None
    assert transmitting.sent == []


@pytest.mark.asyncio
async def test_the_auto_pause_key_blocks_even_with_a_good_state(
    transmitting, fake_redis
):
    await fake_redis.set(_STATE_KEY, "authorized")
    await fake_redis.set(_PAUSE_KEY, "yellowCard")
    facade = WhatsAppFacade(transmitting)

    assert await facade.send_message("972500000001@c.us", "hi") is None
    assert transmitting.sent == []


@pytest.mark.asyncio
async def test_the_manual_kill_switch_blocks_even_with_a_good_state(
    transmitting, fake_redis
):
    """The operator switch lives in its own key so instance recovery — which
    clears the auto key — can never silently resume a deliberate halt."""
    await fake_redis.set(_STATE_KEY, "authorized")
    await fake_redis.set(_PAUSE_MANUAL_KEY, "1")
    facade = WhatsAppFacade(transmitting)

    assert await facade.send_message("972500000001@c.us", "hi") is None
    assert transmitting.sent == []


@pytest.mark.asyncio
async def test_a_redis_error_still_fails_open(transmitting, monkeypatch):
    """Deliberate, and explicitly preserved by PRO-82: the bug was the *absent
    key*, not an unreachable Redis. A monitoring dependency going down must not
    take the send path with it."""
    monkeypatch.setattr(
        facade_module,
        "get_redis_client",
        AsyncMock(side_effect=Exception("redis down")),
    )
    facade = WhatsAppFacade(transmitting)

    await facade.send_message("972500000001@c.us", "hi")

    assert transmitting.sent == [("text", "972500000001@c.us", "hi")]


@pytest.mark.asyncio
async def test_a_non_transmitting_provider_is_never_gated(fake_redis):
    """No state key, no pause key — a DryRunProvider still 'sends'. Gating a
    transport that cannot reach a handset protects nothing and would make the
    whole offline suite assert against a permanently-open breaker."""
    provider = DryRunProvider()
    facade = WhatsAppFacade(provider)

    await facade.send_message("972500000001@c.us", "hello")

    assert len(provider.sent) == 1
    assert provider.sent[0]["text"] == "hello"


@pytest.mark.asyncio
async def test_every_outbound_method_is_gated(transmitting, fake_redis):
    """A breaker that only covers send_message is the bypass class this ticket
    exists to remove."""
    facade = WhatsAppFacade(transmitting)

    await facade.send_message("972500000001@c.us", "t")
    await facade.send_file_by_url("972500000001@c.us", "http://x/y.jpg")
    await facade.send_location_link("972500000001@c.us", "Tel Aviv")
    await facade.send_chat_state_typing("972500000001@c.us")
    await facade.send_template("972500000001@c.us", "tpl")
    await facade.send_interactive("972500000001@c.us", "body", ["a", "b"])

    assert transmitting.sent == []


# ===========================================================================
# State probe — deliberately outside the breaker
# ===========================================================================


@pytest.mark.asyncio
async def test_state_probe_is_not_gated_by_the_breaker(transmitting, fake_redis):
    """Dry-run/halt means "send nothing to anybody", not "stop looking at our own
    account". The watchdog has to keep seeing true state through an incident —
    it is what detects recovery."""
    await fake_redis.set(_PAUSE_MANUAL_KEY, "1")
    facade = WhatsAppFacade(transmitting)

    assert await facade.get_state_instance() == "authorized"


@pytest.mark.asyncio
async def test_record_account_state_only_writes_on_authorized(fake_redis):
    await record_account_state("yellowCard")
    assert await fake_redis.get(_STATE_KEY) is None

    await record_account_state("authorized")
    assert await fake_redis.get(_STATE_KEY) == "authorized"


@pytest.mark.asyncio
async def test_record_account_state_sets_a_ttl(fake_redis):
    """The TTL is what makes the breaker fail closed when the monitor dies: the
    confirmation simply stops being refreshed."""
    await record_account_state("authorized")

    ttl = await fake_redis.ttl(_STATE_KEY)
    assert 0 < ttl <= WorkerConstants.WA_STATE_CONFIRM_TTL_SECONDS


# ===========================================================================
# Provider selection
# ===========================================================================


def test_dry_run_setting_forces_the_dry_run_provider(monkeypatch):
    """The operator's emergency mute must win over provider selection — the
    outage runbook has them set it during an incident."""
    monkeypatch.setattr(settings, "WHATSAPP_DRY_RUN", True)
    monkeypatch.setattr(settings, "WHATSAPP_PROVIDER", "cloud")

    assert isinstance(provider_pkg.build_provider(), DryRunProvider)


def test_cloud_is_selectable_when_dry_run_is_off(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_DRY_RUN", False)
    monkeypatch.setattr(settings, "WHATSAPP_PROVIDER", "cloud")

    assert isinstance(provider_pkg.build_provider(), CloudAPIProvider)


def test_an_unknown_provider_name_falls_back_to_dry_run(monkeypatch):
    """Fail safe, not fail loud-and-transmitting: a typo must never resolve to
    something that can reach a handset."""
    monkeypatch.setattr(settings, "WHATSAPP_DRY_RUN", False)
    monkeypatch.setattr(settings, "WHATSAPP_PROVIDER", "gren")

    assert isinstance(provider_pkg.build_provider(), DryRunProvider)


def test_provider_selection_is_case_and_whitespace_insensitive(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_DRY_RUN", False)
    monkeypatch.setattr(settings, "WHATSAPP_PROVIDER", "  CLOUD  ")

    assert isinstance(provider_pkg.build_provider(), CloudAPIProvider)


# ===========================================================================
# The providers themselves
# ===========================================================================


@pytest.mark.asyncio
async def test_dry_run_provider_records_but_never_transmits():
    provider = DryRunProvider()

    await provider.send_text("972500000001@c.us", "hello")
    await provider.send_file("972500000001@c.us", "http://x/y.jpg", caption="c")

    assert provider.transmits is False
    assert [e["kind"] for e in provider.sent] == ["text", "file"]


@pytest.mark.asyncio
async def test_dry_run_provider_reports_authorized():
    """A transport that cannot send also cannot be deauthorized. Reporting
    healthy keeps /health and the watchdog meaningful offline."""
    assert await DryRunProvider().get_state() == "authorized"


def test_dry_run_provider_parses_a_normalized_payload():
    provider = DryRunProvider()

    msg = provider.parse_webhook({"chat_id": "972500000001@c.us", "text": "hi"})

    assert msg == NormalizedMessage(chat_id="972500000001@c.us", text="hi")
    assert provider.parse_webhook({"text": "no chat id"}) is None
    assert provider.parse_webhook(None) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "call",
    [
        lambda p: p.send_text("c", "t"),
        lambda p: p.send_file("c", "u"),
        lambda p: p.send_template("c", "tpl"),
        lambda p: p.send_interactive("c", "b", []),
    ],
)
async def test_cloud_provider_raises_until_pro_89(call):
    """Selecting cloud before PRO-89 lands is a misconfiguration and must fail
    loudly at the first send rather than degrade to silence."""
    with pytest.raises(NotImplementedError, match="PRO-89"):
        await call(CloudAPIProvider())


@pytest.mark.asyncio
async def test_cloud_provider_state_is_none_not_a_raise():
    """The watchdog polls this on a timer — a raise would crash the scheduler
    job. None reads as 'not known-good', which fails the breaker closed."""
    assert await CloudAPIProvider().get_state() is None


@pytest.mark.asyncio
async def test_cloud_provider_is_marked_as_transmitting():
    """It is the reason the fail-closed path is real rather than theoretical."""
    assert CloudAPIProvider().transmits is True


# ===========================================================================
# The sync bridge used by the admin panel
# ===========================================================================


def test_sync_bridge_routes_through_the_facade(monkeypatch):
    """The admin panel's three raw httpx.post calls are gone; this is the only
    sanctioned way across the sync boundary, and it must still hit the facade."""
    from app.providers.whatsapp import sync as sync_module

    facade = MagicMock()
    facade.send_message = AsyncMock(return_value={"id": "1"})
    monkeypatch.setattr(sync_module, "get_whatsapp", lambda: facade)

    assert sync_module.send_text_sync("972500000001@c.us", "hi") is True
    facade.send_message.assert_awaited_once_with("972500000001@c.us", "hi")


def test_sync_bridge_swallows_failures(monkeypatch):
    """Every caller is a best-effort notification hanging off a UI action — a
    failed courtesy message must not abort the DB mutation that preceded it."""
    from app.providers.whatsapp import sync as sync_module

    facade = MagicMock()
    facade.send_message = AsyncMock(side_effect=Exception("boom"))
    monkeypatch.setattr(sync_module, "get_whatsapp", lambda: facade)

    assert sync_module.send_text_sync("972500000001@c.us", "hi") is False


# ===========================================================================
# A non-transmitting provider must not vouch for the account
#
# DryRunProvider.get_state() returns "authorized" because a transport that never
# sends can never be deauthorized. If that answer reaches shared Redis it forges
# the very confirmation the breaker fails closed without — and the outage runbook
# has the operator set WHATSAPP_DRY_RUN=true *during* an incident, so the forged
# key would be waiting for whichever process next comes up transmitting.
# ===========================================================================


@pytest.mark.asyncio
async def test_a_non_transmitting_provider_cannot_write_the_confirmation(fake_redis):
    await record_account_state("authorized", transmits=False)

    assert await fake_redis.get(_STATE_KEY) is None


@pytest.mark.asyncio
async def test_a_non_authorized_probe_clears_a_stale_confirmation(fake_redis):
    """Ageing the key out is not enough: for up to the TTL the confirmation and
    the pause key could disagree if one of the two writes failed."""
    await fake_redis.set(_STATE_KEY, "authorized")

    await record_account_state("yellowCard")

    assert await fake_redis.get(_STATE_KEY) is None


@pytest.mark.asyncio
async def test_the_deauth_monitor_skips_a_non_transmitting_provider(
    monkeypatch, fake_redis
):
    """Otherwise the monitor resets the deauth clock, deletes the auto pause key
    and can fire a false recovery page, all while the real account is unwatched."""
    import app.services.monitor_service as monitor_module

    provider = MagicMock()
    provider.name = "dryrun"
    provider.transmits = False
    facade = WhatsAppFacade(provider)
    facade.get_state_instance = AsyncMock(return_value="authorized")
    monkeypatch.setattr(monitor_module, "whatsapp", facade)
    await fake_redis.set("wa:instance:paused", "notAuthorized")

    await monitor_module.check_whatsapp_instance_state()

    facade.get_state_instance.assert_not_awaited()
    assert await fake_redis.get(_STATE_KEY) is None
    assert (
        await fake_redis.get("wa:instance:paused") == "notAuthorized"
    ), "a real incident's breaker key was cleared by a provider that cannot send"


@pytest.mark.asyncio
async def test_oncall_alert_pages_via_sentry_when_the_provider_cannot_transmit(
    monkeypatch,
):
    """A dry-run provider reports 'authorized', so without this the page would be
    handed to a sink that drops it — and send_oncall_alert would return True."""
    import app.services.notification_service as notif_module

    provider = MagicMock()
    provider.name = "dryrun"
    provider.transmits = False
    facade = WhatsAppFacade(provider)
    facade.send_message = AsyncMock()
    monkeypatch.setattr(notif_module, "whatsapp", facade)

    assert await notif_module.send_oncall_alert("instance down") is False
    facade.send_message.assert_not_awaited()


# ===========================================================================
# The sync bridge must survive more than one call
# ===========================================================================


def test_the_sync_bridge_reuses_one_event_loop(monkeypatch):
    """asyncio.run() closes its loop, but app.core.redis_client caches its client
    in a process global — so a per-call loop makes the *second* admin-panel send
    hit a pool bound to a dead loop, raise, and get waved through by the breaker's
    fail-open handler. Unguarded sends from call two onward: the same bypass this
    ticket closes, relocated."""
    from app.providers.whatsapp import sync as sync_module

    seen = []

    async def _capture(chat_id, text):
        seen.append(asyncio.get_running_loop())
        return {"id": "1"}

    facade = MagicMock()
    facade.send_message = _capture
    monkeypatch.setattr(sync_module, "get_whatsapp", lambda: facade)

    assert sync_module.send_text_sync("972500000001@c.us", "one") is True
    assert sync_module.send_text_sync("972500000001@c.us", "two") is True

    assert len(seen) == 2
    assert seen[0] is seen[1], "a fresh loop per call resurrects the closed-loop bug"
    assert not seen[0].is_closed()


def test_the_sync_bridge_reports_a_suppressed_send_as_failure(monkeypatch):
    """The facade returns None when the breaker suppresses a send. Reporting that
    as success gives the operator a 'Check sent!' toast for a message nobody got."""
    from app.providers.whatsapp import sync as sync_module

    facade = MagicMock()
    facade.send_message = AsyncMock(return_value=None)
    monkeypatch.setattr(sync_module, "get_whatsapp", lambda: facade)

    assert sync_module.send_text_sync("972500000001@c.us", "hi") is False


# ===========================================================================
# The interactive-message ban (CLAUDE.md)
# ===========================================================================


def test_no_flow_calls_send_interactive():
    """send_interactive exists on the ABC because Cloud API supports it, and
    DryRunProvider records it happily — so a button-style call site would work
    end-to-end in tests and trip no guard. Nothing may use it until PRO-88
    (template catalog) and PRO-89 land."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    offenders = []
    for folder in ("app/services", "app/api", "admin_panel"):
        for path in (root / folder).rglob("*.py"):
            if "send_interactive(" in path.read_text(encoding="utf-8"):
                offenders.append(str(path.relative_to(root)))

    assert not offenders, (
        f"send_interactive called outside the provider package: {offenders}. "
        "WhatsApp menus stay text-based until PRO-88/PRO-89 — see CLAUDE.md."
    )
