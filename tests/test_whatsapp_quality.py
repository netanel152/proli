"""PRO-90 — the WhatsApp quality watchdog, its gate, its counter and /health.

Runs on the suite's fakeredis, so the dedupe and staleness behaviour is the real
Redis logic rather than a stub. Only the provider's reading and page_critical are
faked: the reading is what Meta would say, the page is what the operator would get.
"""

import json
import time
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytz
from fastapi.testclient import TestClient

import app.api.routes.health as health_route
import app.scheduler as sched
import app.services.monitor_service as monitor
from app.core.config import settings
from app.core.constants import WorkerConstants
from app.core.startup_checks import warn_if_waba_id_missing
from app.main import app
from app.providers.whatsapp import delivery, quality
from app.providers.whatsapp.cloud_api import CloudAPIProvider
from app.providers.whatsapp.facade import WhatsAppFacade
from app.providers.whatsapp.window import open_service_window
from app.services.pro_flow import _execute_finish

GREEN = {
    "quality_rating": "GREEN",
    "messaging_tier": "TIER_250",
    "phone_status": "CONNECTED",
    "can_send_message": "AVAILABLE",
    "health_errors": [],
}
HEALTH_HEADERS = {"X-Health-Token": "health-secret"}


def reading(**overrides):
    return {**GREEN, **overrides}


@pytest.fixture
def production(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")


@pytest.fixture
def staging(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "staging")


@pytest.fixture(autouse=True)
def waba_configured(monkeypatch):
    monkeypatch.setattr(settings, "META_WABA_ID", "waba-1")


@pytest.fixture
def pages(monkeypatch):
    page = MagicMock()
    monkeypatch.setattr(monitor, "page_critical", page)
    return page


@pytest.fixture
def poll(monkeypatch):
    """Install what Meta answers. Call it with a reading, or None for a failure."""
    provider = MagicMock()
    provider.transmits = True
    provider.get_quality = AsyncMock(return_value=GREEN)
    whatsapp = WhatsAppFacade(provider)
    monkeypatch.setattr(monitor, "whatsapp", whatsapp)
    monkeypatch.setattr("app.providers.whatsapp.get_whatsapp", lambda: whatsapp)

    async def _tick(answer=GREEN):
        provider.get_quality.return_value = answer
        await monitor.check_whatsapp_quality()

    return _tick


# ---------------------------------------------------------------------------
# Provider: what is read from Meta
# ---------------------------------------------------------------------------


def _install_graph(monkeypatch, handler):
    seen = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(recording))
    monkeypatch.setattr(
        "app.providers.whatsapp.cloud_api.get_http_client",
        AsyncMock(return_value=client),
    )
    monkeypatch.setattr(settings, "META_PHONE_NUMBER_ID", "pn-1")
    token = MagicMock()
    token.get_secret_value.return_value = "secret-token"
    monkeypatch.setattr(settings, "META_ACCESS_TOKEN", token)
    return seen


def _graph_answer(request):
    if "waba-1" in str(request.url):
        return httpx.Response(
            200,
            json={
                "health_status": {
                    "can_send_message": "BLOCKED",
                    "errors": [{"error_code": 141006, "error_description": "pay"}],
                    "entities": [{"errors": [{"error_code": 141010}]}],
                }
            },
        )
    return httpx.Response(
        200,
        json={
            "status": "CONNECTED",
            "quality_rating": "GREEN",
            "whatsapp_business_manager_messaging_limit": "TIER_250",
        },
    )


@pytest.mark.asyncio
async def test_get_quality_reads_phone_and_waba_nodes(monkeypatch):
    seen = _install_graph(monkeypatch, _graph_answer)

    result = await CloudAPIProvider().get_quality()

    assert result == {
        "quality_rating": "GREEN",
        "messaging_tier": "TIER_250",
        "phone_status": "CONNECTED",
        "can_send_message": "BLOCKED",
        "health_errors": ["141006", "141010"],
    }
    assert "messaging_limit_tier" not in str(seen[0].url), "deprecated by Meta"
    assert all(r.method == "GET" for r in seen), "the watchdog must never write"
    assert all("secret-token" not in str(r.url) for r in seen)


@pytest.mark.asyncio
async def test_get_quality_phone_failure_is_no_reading(monkeypatch):
    _install_graph(monkeypatch, lambda r: httpx.Response(500, json={}))

    assert await CloudAPIProvider().get_quality() is None


@pytest.mark.asyncio
async def test_get_quality_waba_failure_keeps_the_rating(monkeypatch):
    def handler(request):
        if "waba-1" in str(request.url):
            return httpx.Response(403, json={})
        return _graph_answer(request)

    _install_graph(monkeypatch, handler)

    result = await CloudAPIProvider().get_quality()

    assert result["quality_rating"] == "GREEN"
    assert result["can_send_message"] is None


@pytest.mark.asyncio
async def test_get_quality_without_waba_id_reads_only_the_phone(monkeypatch):
    monkeypatch.setattr(settings, "META_WABA_ID", None)
    seen = _install_graph(monkeypatch, _graph_answer)

    result = await CloudAPIProvider().get_quality()

    assert len(seen) == 1
    assert result["can_send_message"] is None


@pytest.mark.parametrize(
    "status,expected",
    [("CONNECTED", "authorized"), ("FLAGGED", "authorized"), ("BANNED", "banned")],
)
@pytest.mark.asyncio
async def test_flagged_keeps_the_breaker_open_so_dispatch_survives(
    monkeypatch, status, expected
):
    _install_graph(monkeypatch, lambda r: httpx.Response(200, json={"status": status}))

    assert await CloudAPIProvider().get_state() == expected


@pytest.mark.asyncio
async def test_facade_turns_a_raising_provider_into_no_reading():
    provider = MagicMock()
    provider.get_quality = AsyncMock(side_effect=RuntimeError("boom"))

    assert await WhatsAppFacade(provider).get_quality() is None


# ---------------------------------------------------------------------------
# Paging: leaving GREEN, deduped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_green_pages_nobody(production, pages, poll):
    await poll()

    pages.assert_not_called()
    assert (await quality.read_snapshot())["tightened"] is False


@pytest.mark.parametrize("rating", ["YELLOW", "RED", "SOMETHING_NEW"])
@pytest.mark.asyncio
async def test_leaving_green_pages_and_tightens(production, pages, poll, rating):
    await poll()
    await poll(reading(quality_rating=rating))

    pages.assert_called_once()
    assert rating in pages.call_args.args[0]
    assert (await quality.read_snapshot())["tightened"] is True
    assert await quality.non_essential_sends_paused() is True


@pytest.mark.parametrize("rating", ["NA", "UNKNOWN"])
@pytest.mark.asyncio
async def test_a_fresh_unrated_number_does_not_page(production, pages, poll, rating):
    await poll(reading(quality_rating=rating))

    pages.assert_not_called()


@pytest.mark.asyncio
async def test_a_standing_condition_pages_exactly_once(production, pages, poll):
    for _ in range(50):
        await poll(reading(quality_rating="RED"))

    pages.assert_called_once()


@pytest.mark.asyncio
async def test_a_standing_condition_outlives_the_cooldown_without_repaging(
    production, pages, poll, fake_redis
):
    """The tick refreshes the claim, so the cooldown never lapses mid-episode."""
    await poll(reading(quality_rating="RED"))
    ttl = await fake_redis.ttl("wa:quality:paged:rating:RED")
    await fake_redis.expire("wa:quality:paged:rating:RED", 5)

    await poll(reading(quality_rating="RED"))

    pages.assert_called_once()
    assert await fake_redis.ttl("wa:quality:paged:rating:RED") >= ttl - 5


@pytest.mark.asyncio
async def test_a_flapping_rating_pages_once(production, pages, poll):
    for _ in range(10):
        await poll(reading(quality_rating="YELLOW"))
        await poll()

    pages.assert_called_once()


@pytest.mark.asyncio
async def test_a_worse_rating_pages_again(production, pages, poll):
    await poll(reading(quality_rating="YELLOW"))
    await poll(reading(quality_rating="RED"))

    assert pages.call_count == 2


@pytest.mark.asyncio
async def test_recovery_releases_the_tightening_and_can_page_after_cooldown(
    production, pages, poll, fake_redis
):
    await poll(reading(quality_rating="RED"))
    await poll()
    assert await quality.non_essential_sends_paused() is False

    await fake_redis.delete("wa:quality:paged:rating:RED")  # the cooldown lapsing
    await poll(reading(quality_rating="RED"))

    assert pages.call_count == 2


@pytest.mark.asyncio
async def test_redis_outage_still_pages_but_only_once(
    production, pages, poll, monkeypatch
):
    monkeypatch.setattr(quality, "_LOCAL_PAGE_CLAIMS", set())
    broken = AsyncMock(side_effect=ConnectionError("redis down"))
    monkeypatch.setattr(quality, "get_redis_client", broken)

    for _ in range(5):
        await poll(reading(quality_rating="RED"))

    pages.assert_called_once()


@pytest.mark.parametrize("can_send", ["LIMITED", "BLOCKED"])
@pytest.mark.asyncio
async def test_waba_health_is_an_early_warning_in_production(
    production, pages, poll, can_send
):
    await poll(reading(can_send_message=can_send, health_errors=["141006"]))

    pages.assert_called_once()
    assert f"waba:{can_send}" in pages.call_args.args[0]
    assert "141006" in pages.call_args.args[0]
    assert await quality.non_essential_sends_paused() is True


@pytest.mark.asyncio
async def test_meta_flagging_the_number_pages_and_tightens(production, pages, poll):
    await poll(reading(phone_status="FLAGGED"))

    assert "phone:FLAGGED" in pages.call_args.args[0]
    assert await quality.non_essential_sends_paused() is True


# ---------------------------------------------------------------------------
# Staging: the Test Number's BLOCKED WABA is expected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_staging_observes_but_never_pages_or_tightens(staging, pages, poll):
    await poll(
        reading(quality_rating="RED", can_send_message="BLOCKED", health_errors=["1"])
    )
    for _ in range(WorkerConstants.WA_QUALITY_POLL_FAILURE_THRESHOLD + 1):
        await poll(None)

    pages.assert_not_called()
    snapshot = await quality.read_snapshot()
    assert snapshot["tightened"] is False
    assert "waba:BLOCKED" in snapshot["conditions"]
    assert await quality.non_essential_sends_paused() is False


# ---------------------------------------------------------------------------
# A poller that can't reach Meta must not look healthy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_failed_poll_is_quiet_but_no_longer_reports_green(
    production, pages, poll
):
    await poll()
    await poll(None)

    pages.assert_not_called()
    fields = await quality.health_fields(transmits=True)
    assert fields["quality_rating"] == "UNKNOWN"
    assert fields["window_state"] == "unknown"
    assert fields["quality_poll"]["status"] == "failed"
    assert fields["last_known"]["quality_rating"] == "GREEN"


@pytest.mark.asyncio
async def test_sustained_failure_pages_once_and_tightens(production, pages, poll):
    await poll()
    for _ in range(WorkerConstants.WA_QUALITY_POLL_FAILURE_THRESHOLD - 1):
        await poll(None)
    pages.assert_not_called()

    for _ in range(10):
        await poll(None)

    pages.assert_called_once()
    assert "poll:failed" in pages.call_args.args[0]
    assert await quality.non_essential_sends_paused() is True


@pytest.mark.asyncio
async def test_a_failed_poll_does_not_clear_a_standing_condition(
    production, pages, poll
):
    await poll(reading(quality_rating="RED"))
    await poll(None)

    assert await quality.non_essential_sends_paused() is True


@pytest.mark.parametrize(
    "answer",
    [
        reading(quality_rating=None),
        reading(can_send_message=None),
    ],
)
@pytest.mark.asyncio
async def test_a_partial_answer_counts_as_a_failed_poll(production, poll, answer):
    await poll(answer)

    assert (await quality.read_snapshot())["consecutive_failures"] == 1


@pytest.mark.asyncio
async def test_no_waba_id_does_not_count_as_a_failed_poll(
    production, poll, monkeypatch
):
    monkeypatch.setattr(settings, "META_WABA_ID", None)

    await poll(reading(can_send_message=None, health_errors=None))

    fields = await quality.health_fields(transmits=True)
    assert fields["quality_poll"]["status"] == "ok"
    assert fields["can_send_message"] == "UNCONFIGURED"
    assert fields["window_state"] == "unknown"


@pytest.mark.asyncio
async def test_a_dead_watchdog_reads_stale_and_tightens(production, poll, fake_redis):
    await poll()
    snapshot = await quality.read_snapshot()
    snapshot["last_attempt_at"] = (
        time.time() - WorkerConstants.WA_QUALITY_STALE_MINUTES * 60 - 1
    )
    await fake_redis.set("wa:quality:snapshot", json.dumps(snapshot))

    fields = await quality.health_fields(transmits=True)

    assert fields["quality_rating"] == "UNKNOWN"
    assert fields["quality_poll"]["status"] == "stale"
    assert fields["tightened"] is True
    assert await quality.non_essential_sends_paused() is True


@pytest.mark.asyncio
async def test_unsetting_the_waba_id_clears_a_retained_blocked(
    production, poll, monkeypatch
):
    await poll(reading(can_send_message="BLOCKED"))
    assert await quality.non_essential_sends_paused() is True

    monkeypatch.setattr(settings, "META_WABA_ID", None)
    await poll(reading(can_send_message=None, health_errors=None))

    assert await quality.non_essential_sends_paused() is False


@pytest.mark.asyncio
async def test_no_reading_yet_in_production_pauses_non_essential(production, poll):
    assert await quality.non_essential_sends_paused() is True


@pytest.mark.asyncio
async def test_unreadable_redis_pauses_non_essential(production, poll, monkeypatch):
    broken = AsyncMock(side_effect=ConnectionError("redis down"))
    monkeypatch.setattr(quality, "get_redis_client", broken)

    assert await quality.non_essential_sends_paused() is True


@pytest.mark.asyncio
async def test_muted_production_has_no_number_to_protect(
    production, pages, monkeypatch
):
    provider = MagicMock()
    provider.transmits = False
    provider.get_quality = AsyncMock()
    whatsapp = WhatsAppFacade(provider)
    monkeypatch.setattr(monitor, "whatsapp", whatsapp)
    monkeypatch.setattr("app.providers.whatsapp.get_whatsapp", lambda: whatsapp)

    await monitor.check_whatsapp_quality()

    provider.get_quality.assert_not_called()
    pages.assert_not_called()
    assert await quality.non_essential_sends_paused() is False
    fields = await quality.health_fields(transmits=False)
    assert fields["quality_rating"] == "UNKNOWN"
    assert fields["quality_poll"]["status"] == "not_applicable"
    assert fields["tightened"] is False


# ---------------------------------------------------------------------------
# window_state
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({}, "open"),
        ({"quality_rating": "RED"}, "open"),
        ({"can_send_message": "LIMITED"}, "limited"),
        ({"phone_status": "FLAGGED"}, "limited"),
        ({"can_send_message": "BLOCKED"}, "blocked"),
        ({"phone_status": "RESTRICTED"}, "blocked"),
        ({"phone_status": "BANNED"}, "blocked"),
        ({"can_send_message": None}, "unknown"),
        ({"phone_status": None}, "unknown"),
    ],
)
def test_window_state_table(overrides, expected):
    assert quality.window_state(reading(**overrides), "ok") == expected


@pytest.mark.parametrize("status", ["failed", "stale", "never", "not_applicable"])
def test_window_state_is_unknown_without_a_fresh_reading(status):
    assert quality.window_state(GREEN, status) == "unknown"


# ---------------------------------------------------------------------------
# Business-initiated counter vs the cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_only_a_template_outside_the_window_counts(monkeypatch):
    monkeypatch.setattr(delivery, "wa_delivery_collection", AsyncMock())
    await open_service_window("in-window@c.us")

    await delivery.record_outbound("w1", "cold@c.us", "template")
    await delivery.record_outbound("w2", "cold@c.us", "template")
    await delivery.record_outbound("w3", "in-window@c.us", "template")
    await delivery.record_outbound("w4", "cold2@c.us", "text")

    assert await quality.business_initiated_count() == 1


@pytest.mark.asyncio
async def test_a_counter_failure_never_blocks_the_send(monkeypatch):
    collection = AsyncMock()
    monkeypatch.setattr(delivery, "wa_delivery_collection", collection)
    broken = AsyncMock(side_effect=ConnectionError("redis down"))
    monkeypatch.setattr(quality, "get_redis_client", broken)
    monkeypatch.setattr(
        delivery, "is_service_window_open", AsyncMock(return_value=False)
    )

    await delivery.record_outbound("w1", "cold@c.us", "template")

    collection.update_one.assert_awaited_once()
    assert await quality.business_initiated_count() is None


@pytest.mark.asyncio
async def test_the_counter_is_a_rolling_24_hours(fake_redis):
    now = time.time()
    await fake_redis.zadd(
        "wa:quality:business_initiated",
        {"old@c.us": now - 86401, "recent@c.us": now - 86000},
    )

    assert await quality.business_initiated_count() == 1

    await quality.record_business_initiated("new@c.us")
    assert await fake_redis.zcard("wa:quality:business_initiated") == 2


async def _seed_recipients(fake_redis, count):
    now = time.time()
    await fake_redis.zadd(
        "wa:quality:business_initiated",
        {f"{i}@c.us": now for i in range(count)},
    )


@pytest.mark.asyncio
async def test_alerts_at_80_percent_of_the_250_cap_once(
    production, pages, poll, fake_redis
):
    await _seed_recipients(fake_redis, 199)
    await poll()
    pages.assert_not_called()

    await _seed_recipients(fake_redis, 200)
    for _ in range(5):
        await poll()

    pages.assert_called_once()
    assert "200/250" in pages.call_args.args[0]
    # An alert, not a tightening: the AC asks only for the warning.
    assert await quality.non_essential_sends_paused() is False


@pytest.mark.asyncio
async def test_the_cap_follows_the_live_tier(production, pages, poll, fake_redis):
    await _seed_recipients(fake_redis, 200)

    await poll(reading(messaging_tier="TIER_2K"))

    pages.assert_not_called()


@pytest.mark.parametrize(
    "tier,cap",
    [("TIER_250", 250), ("TIER_2K", 2000), ("TIER_NEW", 250), (None, 250)],
)
def test_cap_for_tier(tier, cap):
    assert quality.business_initiated_cap(tier) == cap


def test_the_fallback_cap_is_configurable_and_the_live_limit_wins(monkeypatch):
    monkeypatch.setattr(settings, "WA_BUSINESS_INITIATED_CAP", 2000)

    assert quality.business_initiated_cap(None) == 2000
    assert quality.business_initiated_cap("TIER_NEW") == 2000
    assert quality.business_initiated_cap("TIER_250") == 250


# ---------------------------------------------------------------------------
# The gate: what pauses, and that dispatch does not
# ---------------------------------------------------------------------------


def _booked_lead_in_tier_2():
    now = datetime.now(pytz.utc)
    return {
        "chat_id": "972501111111@c.us",
        "status": "booked",
        "created_at": now - timedelta(hours=10),
    }


@pytest.fixture
def business_hours(monkeypatch):
    fake = MagicMock(wraps=datetime)
    fake.now.side_effect = lambda tz=None: datetime(
        2026, 11, 8, 12, 0, tzinfo=pytz.utc
    ).astimezone(tz or pytz.utc)
    monkeypatch.setattr(sched, "datetime", fake)
    return fake.now(pytz.utc)


async def _run_tier_2(monkeypatch, lead_time):
    lead = {**_booked_lead_in_tier_2(), "created_at": lead_time - timedelta(hours=10)}
    await sched.leads_collection.insert_one(lead)
    send = AsyncMock()
    monkeypatch.setattr(sched, "send_customer_completion_check", send)
    monkeypatch.setattr(sched, "send_pro_reminder", AsyncMock())
    await sched.monitor_unfinished_jobs()
    return send


@pytest.mark.asyncio
async def test_completion_check_runs_when_green(
    production, poll, monkeypatch, business_hours
):
    await poll()

    send = await _run_tier_2(monkeypatch, business_hours)

    send.assert_awaited_once()


@pytest.mark.asyncio
async def test_completion_check_holds_while_tightened(
    production, poll, monkeypatch, business_hours
):
    await poll(reading(quality_rating="RED"))

    send = await _run_tier_2(monkeypatch, business_hours)

    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_tightened_still_reminds_the_pro_on_the_same_tick(
    production, poll, monkeypatch, business_hours
):
    """Tier 1 is a live job, not a review request, so it must keep going."""
    await poll(reading(quality_rating="RED"))
    await sched.leads_collection.insert_one(
        {
            **_booked_lead_in_tier_2(),
            "created_at": business_hours - timedelta(hours=5),
        }
    )
    reminder = AsyncMock()
    monkeypatch.setattr(sched, "send_pro_reminder", reminder)
    monkeypatch.setattr(sched, "send_customer_completion_check", AsyncMock())

    await sched.monitor_unfinished_jobs()

    reminder.assert_awaited_once()


async def _finish_job(monkeypatch):
    monkeypatch.setattr(
        "app.services.pro_flow.ContextManager.clear_context", AsyncMock()
    )
    monkeypatch.setattr("app.services.pro_flow.StateManager.set_state", AsyncMock())
    monkeypatch.setattr("app.services.pro_flow.StateManager.set_metadata", AsyncMock())
    status = AsyncMock()
    monkeypatch.setattr("app.services.pro_flow.set_lead_status", status)
    whatsapp = AsyncMock()
    await _execute_finish(
        {"_id": "lead-1", "chat_id": "972501111111@c.us"},
        {"_id": "pro-1", "business_name": "Pro"},
        whatsapp,
        "972502222222@c.us",
    )
    return whatsapp, status.call_args.kwargs["extra_set"]


@pytest.mark.asyncio
async def test_finish_asks_for_a_rating_when_green(production, poll, monkeypatch):
    await poll()

    whatsapp, extra_set = await _finish_job(monkeypatch)

    whatsapp.send_message.assert_awaited_once()
    assert extra_set["waiting_for_rating"] is True


@pytest.mark.asyncio
async def test_finish_completes_without_a_review_request_while_tightened(
    production, poll, monkeypatch
):
    await poll(reading(quality_rating="RED"))

    whatsapp, extra_set = await _finish_job(monkeypatch)

    whatsapp.send_message.assert_not_awaited()
    # The rating window must stay shut, or a later stray digit reads as a rating.
    assert extra_set["waiting_for_rating"] is False
    assert extra_set["completed_at"] is not None


def test_the_dispatch_path_never_consults_the_gate():
    """The AC's other half, pinned at the source: only these callers may pause."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "app"
    callers = {
        str(path.relative_to(root)).replace("\\", "/")
        for path in root.rglob("*.py")
        if "non_essential_sends_paused" in path.read_text(encoding="utf-8")
    }
    assert callers == {
        "providers/whatsapp/quality.py",
        "scheduler.py",
        "services/pro_flow.py",
        "services/conversation_pipeline.py",
    }


# ---------------------------------------------------------------------------
# /health: behind the token only
# ---------------------------------------------------------------------------


@pytest.fixture
def health_client(monkeypatch):
    monkeypatch.setattr(health_route, "check_db_connection", lambda: True)
    monkeypatch.setattr(settings, "HEALTH_TOKEN", MagicMock())
    settings.HEALTH_TOKEN.get_secret_value.return_value = "health-secret"
    provider = MagicMock()
    provider.name = "cloud"
    provider.transmits = True
    provider.get_state = AsyncMock(return_value="authorized")
    monkeypatch.setattr(health_route, "get_whatsapp", lambda: WhatsAppFacade(provider))
    return TestClient(app)


def test_public_health_exposes_none_of_it(health_client):
    response = health_client.get("/health")

    assert set(response.json()) == {"status", "uptime_seconds"}
    assert "quality_rating" not in response.text


def test_wrong_token_exposes_none_of_it(health_client):
    response = health_client.get("/health", headers={"X-Health-Token": "nope"})

    assert "quality_rating" not in response.text


def test_token_with_no_reading_yet_does_not_look_healthy(health_client):
    response = health_client.get("/health", headers=HEALTH_HEADERS)

    whatsapp = response.json()["checks"]["whatsapp"]
    assert whatsapp["quality_rating"] == "UNKNOWN"
    assert whatsapp["messaging_tier"] == "UNKNOWN"
    assert whatsapp["window_state"] == "unknown"
    assert whatsapp["quality_poll"]["status"] == "never"
    assert response.json()["status"] == "healthy", "a page, not a restart"


def test_token_shows_the_fresh_reading(health_client, monkeypatch):
    # Stubbed, not seeded: TestClient runs on its own loop, and fakeredis binds
    # to the loop that first used it.
    snapshot = {**GREEN, "poll_ok": True, "last_attempt_at": time.time()}
    monkeypatch.setattr(quality, "read_snapshot", AsyncMock(return_value=snapshot))
    monkeypatch.setattr(quality, "business_initiated_count", AsyncMock(return_value=0))

    response = health_client.get("/health", headers=HEALTH_HEADERS)

    whatsapp = response.json()["checks"]["whatsapp"]
    assert whatsapp["quality_rating"] == "GREEN"
    assert whatsapp["messaging_tier"] == "TIER_250"
    assert whatsapp["window_state"] == "open"
    assert whatsapp["business_initiated_24h"] == {"count": 0, "cap": 250}


# ---------------------------------------------------------------------------
# Boot check and registration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "environment,dry_run,provider,waba,reports",
    [
        ("production", False, "cloud", None, True),
        ("production", False, "cloud", "waba-1", False),
        ("production", True, "cloud", None, False),
        ("production", False, "dryrun", None, False),
        ("staging", False, "cloud", None, False),
    ],
)
def test_boot_check_reports_only_production_transmitting_blind(
    environment, dry_run, provider, waba, reports
):
    fake = MagicMock(
        is_production=environment == "production",
        WHATSAPP_DRY_RUN=dry_run,
        WHATSAPP_PROVIDER=provider,
        META_WABA_ID=waba,
    )

    assert warn_if_waba_id_missing(fake) is reports


@pytest.mark.asyncio
async def test_job_respects_the_shared_toggle(monkeypatch):
    check = AsyncMock()
    monkeypatch.setattr(sched, "check_whatsapp_quality", check)
    monkeypatch.setattr(
        sched,
        "settings_collection",
        MagicMock(find_one=AsyncMock(return_value={"whatsapp_monitor_active": False})),
    )

    await sched.run_whatsapp_quality_monitor()

    check.assert_not_awaited()


@pytest.mark.asyncio
async def test_job_registers_at_the_configured_interval():
    scheduler = sched.start_scheduler()
    try:
        job = scheduler.get_job("whatsapp_quality_monitor")
        assert job.trigger.interval == timedelta(
            minutes=WorkerConstants.WA_QUALITY_CHECK_INTERVAL_MINUTES
        )
    finally:
        scheduler.shutdown(wait=False)
