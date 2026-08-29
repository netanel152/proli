"""
PRO-173 — an operator page masked the customer's phone to its last 4 digits
and then included the customer's full street address (``full_address``)
verbatim in the same Sentry event / operator email, undoing the masking one
field later.

Fixed by ``monitor_service.page_safe_city(lead)``, which never returns
free-form text copied out of the lead: **neither** ``city`` nor
``full_address`` is trusted as written — both are the same AI parse of the
same customer message (``ai_engine_service.ExtractedData.city`` is a plain
``Optional[str]`` with no vocabulary check, and ``workflow_service``'s
sticky-facts fallback can copy a composed street address straight into
``city``). Both fields are run through the same ``ISRAEL_CITIES_COORDS``
allowlist — ``city`` first, then ``full_address`` — preferring the
**rightmost** match (a Hebrew address puts the city last) with the longest
name as the tiebreak, else the literal ``"unknown city"``. Non-string values
degrade rather than raise. ``_alert_admin_lead_escalated`` and
``send_periodic_admin_report`` both now page ``city=page_safe_city(lead)``
instead of ``address={full_address}``.

These tests pin: the helper's closed output (an allowlist key or the literal
fallback, never a value copied out of the lead — including the regression
case where a composed street address lives in the ``city`` field itself),
and that the two page sites that changed do not leak ``full_address`` while
still carrying enough to find the lead (id, masked phone, issue, city).
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.core.constants import LeadStatus, WorkerConstants
from app.services import monitor_service
from app.services.monitor_service import (
    _alert_admin_lead_escalated,
    page_safe_city,
    send_periodic_admin_report,
)


# ---------------------------------------------------------------------------
# page_safe_city — table-driven over the fallback chain
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "lead, expected",
    [
        pytest.param(
            {"city": "  חולון  "},
            "חולון",
            id="city_field_holding_a_plain_city_name",
        ),
        pytest.param(
            {"city": "רחוב הרצל 5, תל אביב"},
            "תל אביב",
            id="city_field_holding_a_street_address_is_narrowed",
        ),
        pytest.param(
            {"full_address": "רחוב הרצל 5, תל אביב"},
            "תל אביב",
            id="city_absent_extracted_from_full_address",
        ),
        pytest.param(
            {"full_address": "רחוב הרצל 5, תל אביב יפו"},
            "תל אביב יפו",
            id="longest_match_preferred_over_shorter_substring",
        ),
        pytest.param(
            {"full_address": "רחוב באר שבע 3, חולון"},
            "חולון",
            id="rightmost_match_preferred_over_a_street_named_after_a_city",
        ),
        pytest.param(
            {"city": "", "full_address": "somewhere unknown"},
            "unknown city",
            id="unrecognized_text_in_both_fields_falls_back_to_unknown_city",
        ),
        pytest.param(
            {},
            "unknown city",
            id="missing_city_and_full_address_keys",
        ),
        pytest.param(
            {"city": 123, "full_address": "נתניה"},
            "נתניה",
            id="non_string_city_degrades_instead_of_raising",
        ),
    ],
)
def test_page_safe_city_fallback_chain(lead, expected):
    assert page_safe_city(lead) == expected


# ---------------------------------------------------------------------------
# _alert_admin_lead_escalated — no street address, but still triage-able
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_page_operator(monkeypatch):
    pages = []
    monkeypatch.setattr(monitor_service, "page_operator", pages.append)
    return pages


@pytest.mark.asyncio
async def test_escalation_alert_omits_street_but_keeps_lookup_fields(
    mock_page_operator,
):
    lead = {
        "_id": "lead123",
        "chat_id": "972501234567@c.us",
        "issue_type": "leak",
        "full_address": "רחוב הרצל 5, תל אביב",
        "city": None,
    }

    await _alert_admin_lead_escalated(lead, attempts=3)

    assert len(mock_page_operator) == 1
    page = mock_page_operator[0]

    # Street part of the address must never reach the pager.
    assert "הרצל" not in page

    # Still enough to find and triage the lead.
    assert "***4567" in page
    assert "lead=lead123" in page
    assert "city=תל אביב" in page
    assert "issue=leak" in page


# ---------------------------------------------------------------------------
# send_periodic_admin_report — digest omits full_address, keeps lead= and mask
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _wire_leads_collection(mock_db, monkeypatch):
    monkeypatch.setattr(monitor_service, "leads_collection", mock_db.leads)
    return mock_db


async def _insert_stuck_lead(mock_db, **overrides):
    now_utc = datetime.now(timezone.utc)
    doc = {
        "chat_id": "972501234567@c.us",
        "status": LeadStatus.NEW,
        "issue_type": "leak",
        "full_address": "רחוב הרצל 5, תל אביב",
        "created_at": now_utc
        - timedelta(minutes=WorkerConstants.SOS_TIMEOUT_MINUTES + 30),
    }
    doc.update(overrides)
    res = await mock_db.leads.insert_one(doc)
    return await mock_db.leads.find_one({"_id": res.inserted_id})


@pytest.mark.asyncio
async def test_reporter_digest_omits_street_and_keeps_lead_id_and_mask(
    mock_db, mock_page_operator
):
    await mock_db.leads.delete_many({})
    lead = await _insert_stuck_lead(mock_db)

    await send_periodic_admin_report()

    assert len(mock_page_operator) == 1
    digest = mock_page_operator[0]

    assert "הרצל" not in digest
    assert f"lead={lead['_id']}" in digest
    assert "***4567" in digest
    assert "תל אביב" in digest


@pytest.mark.asyncio
async def test_reporter_digest_degrades_to_unknown_city_without_crashing(
    mock_db, mock_page_operator
):
    await mock_db.leads.delete_many({})
    await _insert_stuck_lead(
        mock_db,
        city=None,
        full_address="כתובת שלא ניתן לזהות בה עיר",
    )

    # must not raise
    await send_periodic_admin_report()

    assert len(mock_page_operator) == 1
    assert "unknown city" in mock_page_operator[0]
