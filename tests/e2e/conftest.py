"""Wiring for the offline E2E harness (PRO-83).

The parent ``tests/conftest.py`` replaces ``whatsapp`` with an ``AsyncMock`` and
``ai`` with a canned ``MagicMock``. That is right for unit tests and wrong here:
this harness exists to prove the *real* send path builds the *real* payload. So
this conftest undoes those two substitutions and replaces them with the recording
transport and the replay engine, then restores several other production objects the
unit conftest stubs out (``ContextManager``, ``has_consent``) so their behaviour is
genuinely exercised rather than assumed.

The zero-real-sends guarantee is enforced in three independent layers, so no single
mistake can produce a live message:

1. ``settings.WHATSAPP_DRY_RUN`` is forced on, so even a freshly constructed
   ``WhatsAppClient`` gets the production dry-run transport.
2. Every ``WhatsAppClient`` in ``sys.modules`` is pointed at the
   ``OutboundRecorder`` transport.
3. ``httpx``'s real network transports are patched to **raise** for the whole
   package — the hard assert the acceptance criteria require.

Layers 2 and 3 together mean a send is either recorded or fails the run. Layer 1
sits *under* that: a client the harness somehow failed to redirect would instead be
absorbed by the production dry-run transport — no real message, but no record
either, so an ``assert_silent`` could pass vacuously. That trade is deliberate
(never send, at the cost of a possibly-missed assertion), and layer 2's discovery
sweep is what keeps it from mattering in practice.
"""

from __future__ import annotations

import sys

import httpx
import pytest
import pytest_asyncio
from mongomock_motor import AsyncMongoMockClient

from app.core.config import settings

from tests.e2e import reserved_numbers as R
from tests.e2e.ai_replay import ReplayAIEngine
from tests.e2e.geo_shim import GeoAwareCollection
from tests.e2e.recorder import OutboundRecorder
from tests.e2e.world import (
    World,
    fake_detect_and_fetch_media,
    fake_resolve_city_to_coords,
)


@pytest.fixture(scope="package", autouse=True)
def forbid_real_network():
    """Hard assert: no HTTP call may reach a WhatsApp provider (or anywhere else) for real.

    Patched at the ``httpx`` transport classes rather than at the client, so it
    covers every client in the process — including one built by code the harness
    forgot to redirect. ``ASGITransport`` (used to drive the in-process FastAPI
    app) is a different class and is deliberately untouched.

    Package-scoped, not session-scoped: the patch is a global mutation of ``httpx``,
    and at session scope it stayed active while the *rest* of the suite ran, where
    several tests legitimately drive transports that this guard would then count as
    escapes. It must bracket ``tests/e2e`` and nothing else.
    """
    escapes: list[str] = []
    mp = pytest.MonkeyPatch()

    async def _async_boom(self, request, *args, **kwargs):
        escapes.append(str(request.url))
        raise AssertionError(
            f"E2E harness attempted a REAL outbound HTTP request to {request.url.host} "
            f"— zero real sends is non-negotiable (PRO-83)."
        )

    def _sync_boom(self, request, *args, **kwargs):
        escapes.append(str(request.url))
        raise AssertionError(
            f"E2E harness attempted a REAL outbound HTTP request to {request.url.host} "
            f"— zero real sends is non-negotiable (PRO-83)."
        )

    mp.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _async_boom)
    mp.setattr(httpx.HTTPTransport, "handle_request", _sync_boom)
    yield escapes
    mp.undo()
    assert not escapes, f"real HTTP requests escaped the harness: {escapes}"


@pytest.fixture
def mock_db():
    """Function-scoped override of the module-scoped ``mock_db`` in tests/conftest.py.

    Two reasons, both specific to a suite this size:

    *Isolation* — the state × input matrix alone is 200+ parametrized cases in one
    module, and a module-scoped database would let one case's lead be visible to
    the next.

    *Correctness* — ``mongomock_motor`` re-wraps ``collection._insert`` on every
    collection access (``patches.py:_patch_insert_and_ensure_uniques``), so the
    wrappers stack on the shared underlying collection object. Across a hundred
    tests that reaches ``RecursionError`` on the next insert. A fresh client per
    test bounds the stack to a handful of frames.
    """
    return AsyncMongoMockClient().proli_db


@pytest.fixture
def recorder() -> OutboundRecorder:
    return OutboundRecorder()


@pytest_asyncio.fixture
async def world(
    request, monkeypatch, mock_db, patch_dependencies, fake_redis, recorder
):
    """A wiped, fully-wired universe for one test.

    Depends on ``patch_dependencies`` explicitly so the unit-test mocks are applied
    *first* and everything below deliberately overrides them.
    """
    # Imported eagerly (not only for the names used below) so every module holding
    # a collection reference is in sys.modules before the rebinding sweep in step 5.
    import app.scheduler as scheduler_module
    import app.services.admin_flow  # noqa: F401
    import app.services.analytics_service  # noqa: F401
    import app.services.customer_flow as customer_flow
    import app.services.data_management_service  # noqa: F401
    import app.services.lead_manager_service  # noqa: F401
    import app.services.matching_service as matching_service
    import app.services.monitor_service as monitor_service
    import app.services.notification_service as notification_service
    import app.services.pro_flow as pro_flow
    import app.services.pro_onboarding_service  # noqa: F401
    import app.services.scheduling_service  # noqa: F401
    import app.services.workflow_service as workflow_service
    import app.core.arq_worker  # noqa: F401
    from app.api.routes import webhook as webhook_route
    from app.services.context_manager_service import ContextManager
    from app.services.data_management_service import has_consent
    from app.providers.whatsapp.facade import WhatsAppFacade

    # --- 1. Dry-run on, and no real number can become a recipient -------------
    monkeypatch.setattr(settings, "WHATSAPP_DRY_RUN", True)
    monkeypatch.setattr(settings, "ADMIN_PHONE", R.ADMIN)
    monkeypatch.setattr(settings, "ONCALL_PHONE", R.ONCALL)
    monkeypatch.setattr(settings, "WEBHOOK_TOKEN", None)

    # --- 2. One real facade, recording at the provider ------------------------
    # PRO-86: the swap moved up a layer. It used to be an httpx MockTransport under
    # a real Green client; it is now a recording *provider* under the real facade,
    # so the breaker and kill-switch code above it is still the production path.
    client = WhatsAppFacade(recorder.provider())
    # The recording provider is marked `transmits` so the breaker genuinely
    # applies to it — otherwise the harness's breaker assertions would pass
    # vacuously. That makes PRO-82's fail-closed default bite here too: seed the
    # positive state confirmation the deauth monitor would have written, so a
    # world starts out representing a healthy account. Tests that want the
    # breaker engaged set the pause keys (or delete this one) themselves.
    await fake_redis.set("wa:instance:state", "authorized")
    # Rebind by discovery rather than a fixed list: app.core.arq_worker does
    # `from app.services.workflow_service import ... whatsapp`, taking its own
    # copy, and its error path would otherwise send through the *production*
    # dry-run transport — absorbed silently, never recorded, never asserted on.
    # Matched by attribute name, not by isinstance: the parent conftest has already
    # replaced these with MagicMocks, so an isinstance check would skip exactly the
    # modules that most need redirecting.
    for module in [m for n, m in list(sys.modules.items()) if n.startswith("app.")]:
        if hasattr(module, "whatsapp"):
            monkeypatch.setattr(module, "whatsapp", client)

    # --- 3. Gemini replayed, media fixtured -----------------------------------
    ai = ReplayAIEngine()
    monkeypatch.setattr(workflow_service, "ai", ai)
    monkeypatch.setattr(
        workflow_service, "detect_and_fetch_media", fake_detect_and_fetch_media
    )

    # --- 4. Restore production objects the unit conftest stubs out ------------
    # ContextManager is Redis-backed and Redis is fakeredis here, so the real one
    # works — which makes "context is cleared on flow exit" a genuine assertion
    # instead of a call-count on a mock.
    monkeypatch.setattr(pro_flow, "ContextManager", ContextManager)
    monkeypatch.setattr(customer_flow, "ContextManager", ContextManager)
    # Same for the consent gate: it reads mongomock, so let it run for real.
    monkeypatch.setattr(workflow_service, "has_consent", has_consent)

    # --- 5. Every collection binding in the app, not a hand-maintained list ---
    # Modules do `from app.core.database import X_collection`, which copies the
    # reference at import time, so patching `app.core.database` alone leaves each
    # importer pointed at the real Mongo. tests/conftest.py enumerates the pairs by
    # hand and the list has gaps (customer_flow.slots_collection among them, which
    # silently sent the reschedule slot claim to the real database). An E2E harness
    # touches far more of the app than any unit test, so rebind by discovery.
    collections = {
        "users_collection": mock_db.users,
        "leads_collection": mock_db.leads,
        "slots_collection": mock_db.slots,
        "messages_collection": mock_db.messages,
        "settings_collection": mock_db.settings,
        "reviews_collection": mock_db.reviews,
        "consent_collection": mock_db.consent,
        "audit_log_collection": mock_db.audit_log,
        "admins_collection": mock_db.admins,
        "wa_delivery_collection": mock_db.wa_delivery,
    }
    for module in [
        m for name, m in list(sys.modules.items()) if name.startswith("app.")
    ]:
        for attr, target in collections.items():
            if hasattr(module, attr):
                monkeypatch.setattr(module, attr, target, raising=False)

    # --- 6. Real $geoNear routing over mongomock -----------------------------
    monkeypatch.setattr(
        matching_service, "users_collection", GeoAwareCollection(mock_db.users)
    )
    monkeypatch.setattr(
        matching_service, "resolve_city_to_coords", fake_resolve_city_to_coords
    )

    # --- 7. Webhook → in-memory queue instead of Redis/ARQ -------------------
    enqueued: list[tuple] = []

    class _CapturingArqPool:
        async def enqueue_job(self, name, *args, **kwargs):
            enqueued.append((name, args))

    async def _fake_get_arq_pool():
        return _CapturingArqPool()

    monkeypatch.setattr(webhook_route, "get_arq_pool", _fake_get_arq_pool)

    w = World(mock_db, recorder, ai, monkeypatch, fake_redis)
    w.client = client
    w._enqueued = enqueued

    # PRO-88: operator alerts no longer travel over WhatsApp, so they are
    # invisible to the outbound recorder. Capture them separately — a harness
    # that can only see WhatsApp would report "the admin was never told"
    # when in fact they were paged.
    monkeypatch.setattr(notification_service, "page_operator", w.pages.append)
    monkeypatch.setattr(monitor_service, "page_operator", w.pages.append)

    await w.reset()

    yield w

    ai.flush_recording()
    await client.close()
