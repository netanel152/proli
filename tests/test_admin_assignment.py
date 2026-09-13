"""Tests for admin_panel/core/assignment.py (PRO-188).

`assign_pending_lead_sync` is the sync->async crossing the Streamlit panel
uses to reach `app.services.admin_flow.assign_lead_to_pro`. Every collaborator
is injectable (`assign`, `whatsapp_factory`, `run`), so these tests drive it
without a real event loop, Redis, or WhatsApp provider — mirroring the seam
already used for `lead_queries`/`schedule_queries`/`analytics_queries`.
"""

import asyncio
import concurrent.futures

import pytest
from unittest.mock import MagicMock

from admin_panel.core import assignment
from app.core.constants import LeadStatus


def _sync_run(coro, timeout):
    """Stand-in for `run_blocking`: just drive the coroutine to completion."""
    return asyncio.run(coro)


@pytest.mark.parametrize(
    "offer_sent, expected_outcome",
    [
        (True, assignment.ASSIGN_SENT),
        (False, assignment.ASSIGN_NOT_SENT),
        (None, assignment.ASSIGN_LOOKUP_MISSED),
    ],
)
def test_assign_pending_lead_sync_maps_tristate_to_outcome(
    offer_sent, expected_outcome
):
    # Deliberately distinct from the pro's own business_name: if the wrapper
    # ever fell back to the pro dict instead of returning the core's actual
    # value, this would be the assertion that catches it.
    pro = {"_id": "pro1", "business_name": "יוסי"}
    captured = {}

    async def fake_assign(lead_id, pro_arg, whatsapp, expected_status=None):
        captured["expected_status"] = expected_status
        return offer_sent, "אבי אינסטלציה"

    outcome, pro_name = assignment.assign_pending_lead_sync(
        "lead1",
        pro,
        assign=fake_assign,
        whatsapp_factory=MagicMock(return_value=MagicMock()),
        run=_sync_run,
    )

    assert outcome == expected_outcome
    assert pro_name == "אבי אינסטלציה"
    # The panel's write must be guarded on the lead still being under review —
    # a correctness property (stale-click safety), not an implementation detail.
    assert captured["expected_status"] == LeadStatus.PENDING_ADMIN_REVIEW


def test_assign_pending_lead_sync_raising_returns_failed_with_pro_name_from_pro_dict():
    """Never raises — a UI button handler that propagates leaves the operator
    not knowing what happened. The pro name still comes from the `pro` dict
    (not from `assign`'s return, which never arrived) so the operator's
    failure message still names somebody."""
    pro = {"_id": "pro1", "business_name": "אבי אינסטלציה"}

    async def boom(lead_id, pro_arg, whatsapp, expected_status=None):
        raise RuntimeError("boom")

    outcome, pro_name = assignment.assign_pending_lead_sync(
        "lead1",
        pro,
        assign=boom,
        whatsapp_factory=MagicMock(return_value=MagicMock()),
        run=_sync_run,
    )

    assert outcome == assignment.ASSIGN_FAILED
    assert pro_name == "אבי אינסטלציה"


def test_assign_pending_lead_sync_resolves_facade_via_factory_and_passes_result():
    """`whatsapp_factory` must be the thing that resolves the facade (PRO-86 —
    never a provider built ad hoc), and whatever it returns must be exactly
    what `assign` receives."""
    pro = {"_id": "pro1", "business_name": "יוסי"}
    sentinel_whatsapp = object()
    fake_factory = MagicMock(return_value=sentinel_whatsapp)
    captured = {}

    async def fake_assign(lead_id, pro_arg, whatsapp, expected_status=None):
        captured["whatsapp"] = whatsapp
        return True, "יוסי"

    assignment.assign_pending_lead_sync(
        "lead1",
        pro,
        assign=fake_assign,
        whatsapp_factory=fake_factory,
        run=_sync_run,
    )

    fake_factory.assert_called_once()
    assert captured["whatsapp"] is sentinel_whatsapp


def test_assign_pending_lead_sync_timeout_returns_timed_out_not_failed():
    """`concurrent.futures.TimeoutError` is caught before the generic
    `Exception` handler: `run_blocking` cancels on timeout and the status
    write is the coroutine's first statement, so a timeout almost certainly
    means assigned-with-the-offer-cancelled — ASSIGN_NOT_SENT's situation,
    not ASSIGN_FAILED's silence-about-the-write."""
    pro = {"_id": "pro1", "business_name": "יוסי"}

    def timing_out_run(coro, timeout):
        coro.close()  # avoid an "unawaited coroutine" warning
        raise concurrent.futures.TimeoutError()

    async def fake_assign(lead_id, pro_arg, whatsapp, expected_status=None):
        return True, "יוסי"

    outcome, pro_name = assignment.assign_pending_lead_sync(
        "lead1",
        pro,
        assign=fake_assign,
        whatsapp_factory=MagicMock(return_value=MagicMock()),
        run=timing_out_run,
    )

    assert outcome == assignment.ASSIGN_TIMED_OUT
    assert outcome != assignment.ASSIGN_FAILED
    assert pro_name == "יוסי"


def test_assign_pending_lead_sync_stale_lead_returns_assign_stale():
    """The one outcome where the lead is NOT assigned to this pro — somebody
    else already took it between page render and button click."""
    pro = {"_id": "pro1", "business_name": "יוסי"}

    async def fake_assign(lead_id, pro_arg, whatsapp, expected_status=None):
        return assignment.LEAD_ALREADY_TAKEN, "יוסי"

    outcome, pro_name = assignment.assign_pending_lead_sync(
        "lead1",
        pro,
        assign=fake_assign,
        whatsapp_factory=MagicMock(return_value=MagicMock()),
        run=_sync_run,
    )

    assert outcome == assignment.ASSIGN_STALE
    assert pro_name == "יוסי"


def test_assign_pending_lead_sync_pro_none_raising_assign_returns_failed_empty_name():
    """Pins the `(pro or {})` guard in the exception handler: a `None` pro
    plus a raising `assign` must still land on ASSIGN_FAILED with an empty
    name, never an AttributeError from `.get` on `None`."""

    async def boom(lead_id, pro_arg, whatsapp, expected_status=None):
        raise RuntimeError("boom")

    outcome, pro_name = assignment.assign_pending_lead_sync(
        "lead1",
        None,
        assign=boom,
        whatsapp_factory=MagicMock(return_value=MagicMock()),
        run=_sync_run,
    )

    assert outcome == assignment.ASSIGN_FAILED
    assert pro_name == ""
