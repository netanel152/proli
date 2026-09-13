"""Tests for admin_panel/core/assignment.py (PRO-188).

`assign_pending_lead_sync` is the sync->async crossing the Streamlit panel
uses to reach `app.services.admin_flow.assign_lead_to_pro`. Every collaborator
is injectable (`assign`, `whatsapp_factory`, `run`), so these tests drive it
without a real event loop, Redis, or WhatsApp provider — mirroring the seam
already used for `lead_queries`/`schedule_queries`/`analytics_queries`.
"""

import asyncio

import pytest
from unittest.mock import MagicMock

from admin_panel.core import assignment


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
    pro = {"_id": "pro1", "business_name": "יוסי"}

    async def fake_assign(lead_id, pro_arg, whatsapp):
        return offer_sent, "יוסי"

    outcome, pro_name = assignment.assign_pending_lead_sync(
        "lead1",
        pro,
        assign=fake_assign,
        whatsapp_factory=MagicMock(return_value=MagicMock()),
        run=_sync_run,
    )

    assert outcome == expected_outcome
    assert pro_name == "יוסי"


def test_assign_pending_lead_sync_raising_returns_failed_with_pro_name_from_pro_dict():
    """Never raises — a UI button handler that propagates leaves the operator
    not knowing what happened. The pro name still comes from the `pro` dict
    (not from `assign`'s return, which never arrived) so the operator's
    failure message still names somebody."""
    pro = {"_id": "pro1", "business_name": "אבי אינסטלציה"}

    async def boom(lead_id, pro_arg, whatsapp):
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

    async def fake_assign(lead_id, pro_arg, whatsapp):
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
