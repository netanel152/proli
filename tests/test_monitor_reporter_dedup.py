"""
PRO-162 — the SOS Reporter (`send_periodic_admin_report`) used to page the
operator (`page_operator` -> `page_critical` -> Sentry fatal -> email) for
EVERY lead in NEW/CONTACTED/PENDING_ADMIN_REVIEW older than
`WorkerConstants.SOS_TIMEOUT_MINUTES`, on EVERY 4-hourly tick. One
unresolvable lead therefore paged the operator forever.

The fix claims each candidate lead with an atomic `find_one_and_update`
stamping `admin_reported_at`, gated by `stuck_lead_report_due_filter` (mirrors
`customer_flow.completion_check_due_filter`). Only leads that win the claim go
into the digest, so a lead is paged once, then again only after
`WorkerConstants.SOS_REPORT_REPAGE_HOURS`.

These tests cover: the acceptance criterion (two ticks over the same lead ->
one page), fresh leads still paging promptly, the repage window, the DB
stamp itself, the "too young to page" guard, PENDING_ADMIN_REVIEW staying in
scope, a simulated concurrent-claim loss, the due-filter shape directly, and
the fail-open exception contract.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.constants import LeadStatus, WorkerConstants
from app.core.logger import logger as app_logger
from app.services import monitor_service
from app.services.monitor_service import (
    reassign_lead,
    send_periodic_admin_report,
    stuck_lead_report_due_filter,
)


async def _captured_logs_async(coro_fn, level="WARNING") -> str:
    """Attach a real loguru sink and run ``coro_fn`` — caplog does not see
    loguru output (it does not propagate to stdlib logging). Mirrors the
    helper in ``tests/test_cloud_api_provider.py``."""
    lines: list[str] = []
    sink_id = app_logger.add(lambda m: lines.append(str(m)), level=level)
    try:
        await coro_fn()
    finally:
        app_logger.remove(sink_id)
    return "".join(lines)


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _wire_leads_collection(mock_db, monkeypatch):
    monkeypatch.setattr(monitor_service, "leads_collection", mock_db.leads)
    return mock_db


@pytest.fixture
def mock_page_operator(monkeypatch):
    pages = []
    monkeypatch.setattr(monitor_service, "page_operator", pages.append)
    return pages


async def _insert_stuck_lead(mock_db, **overrides):
    now_utc = datetime.now(timezone.utc)
    doc = {
        "chat_id": "972500001234@c.us",
        "status": LeadStatus.NEW,
        "issue_type": "leak",
        "full_address": "הרצל 1, תל אביב",
        # well past SOS_TIMEOUT_MINUTES so it always qualifies as "stuck"
        # unless a test overrides it to check the young-lead guard.
        "created_at": now_utc
        - timedelta(minutes=WorkerConstants.SOS_TIMEOUT_MINUTES + 30),
    }
    doc.update(overrides)
    res = await mock_db.leads.insert_one(doc)
    return await mock_db.leads.find_one({"_id": res.inserted_id})


@pytest.fixture
def mock_whatsapp(monkeypatch):
    """``reassign_lead`` sends over the module-level ``whatsapp`` in
    monitor_service — mirrors the fixture of the same name in
    tests/test_reassign_escalation.py."""
    mock = MagicMock()
    mock.send_message = AsyncMock()
    mock.send_file_by_url = AsyncMock()
    mock.send_location_link = AsyncMock()
    monkeypatch.setattr(monitor_service, "whatsapp", mock)
    return mock


# ---------------------------------------------------------------------------
# 1. Acceptance criterion — one page per lead across ticks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_consecutive_ticks_over_same_stuck_lead_produce_exactly_one_page(
    mock_db, mock_page_operator
):
    await mock_db.leads.delete_many({})
    lead = await _insert_stuck_lead(mock_db)

    await send_periodic_admin_report()
    await send_periodic_admin_report()

    assert len(mock_page_operator) == 1
    local = lead["chat_id"].split("@")[0]
    assert f"***{local[-4:]}" in mock_page_operator[0]


# ---------------------------------------------------------------------------
# 2. A newly-stuck lead still pages promptly; a second new lead only pages on
#    the tick after it becomes stuck, and the digest for that tick names only
#    the new one.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_newly_stuck_lead_is_paged_on_the_very_next_tick(
    mock_db, mock_page_operator
):
    await mock_db.leads.delete_many({})
    await _insert_stuck_lead(mock_db)

    await send_periodic_admin_report()

    assert len(mock_page_operator) == 1


@pytest.mark.asyncio
async def test_second_new_lead_appearing_after_tick_one_pages_alone_on_tick_two(
    mock_db, mock_page_operator
):
    await mock_db.leads.delete_many({})
    first = await _insert_stuck_lead(mock_db, chat_id="972500001111@c.us")

    await send_periodic_admin_report()
    assert len(mock_page_operator) == 1

    second = await _insert_stuck_lead(mock_db, chat_id="972500002222@c.us")

    await send_periodic_admin_report()

    assert len(mock_page_operator) == 2
    second_digest = mock_page_operator[1]
    first_local = first["chat_id"].split("@")[0]
    second_local = second["chat_id"].split("@")[0]
    assert f"***{second_local[-4:]}" in second_digest
    assert f"***{first_local[-4:]}" not in second_digest


# ---------------------------------------------------------------------------
# 3. Re-page after the long interval; no re-page before it.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lead_stamped_past_repage_window_is_paged_again(
    mock_db, mock_page_operator
):
    await mock_db.leads.delete_many({})
    now_utc = datetime.now(timezone.utc)
    stale_stamp = now_utc - timedelta(hours=WorkerConstants.SOS_REPORT_REPAGE_HOURS + 1)
    await _insert_stuck_lead(mock_db, admin_reported_at=stale_stamp)

    await send_periodic_admin_report()

    assert len(mock_page_operator) == 1


@pytest.mark.asyncio
async def test_lead_stamped_recently_is_not_paged_again(mock_db, mock_page_operator):
    await mock_db.leads.delete_many({})
    now_utc = datetime.now(timezone.utc)
    recent_stamp = now_utc - timedelta(hours=1)
    await _insert_stuck_lead(mock_db, admin_reported_at=recent_stamp)

    await send_periodic_admin_report()

    assert mock_page_operator == []


# ---------------------------------------------------------------------------
# 4. The claimed lead is actually stamped in the DB.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_paged_lead_is_stamped_with_admin_reported_at(
    mock_db, mock_page_operator
):
    await mock_db.leads.delete_many({})
    lead = await _insert_stuck_lead(mock_db)
    assert "admin_reported_at" not in lead or lead.get("admin_reported_at") is None

    # mongomock (like real BSON) only preserves millisecond precision, so
    # the bounds are widened by one millisecond either side rather than
    # asserting exact microsecond containment.
    before = datetime.now(timezone.utc) - timedelta(milliseconds=1)
    await send_periodic_admin_report()
    after = datetime.now(timezone.utc) + timedelta(milliseconds=1)

    updated = await mock_db.leads.find_one({"_id": lead["_id"]})
    stamp = updated.get("admin_reported_at")
    assert stamp is not None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    assert before <= stamp <= after


# ---------------------------------------------------------------------------
# 5. A lead younger than SOS_TIMEOUT_MINUTES is never paged.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lead_younger_than_sos_timeout_is_never_paged(
    mock_db, mock_page_operator
):
    await mock_db.leads.delete_many({})
    now_utc = datetime.now(timezone.utc)
    await _insert_stuck_lead(
        mock_db,
        created_at=now_utc
        - timedelta(minutes=WorkerConstants.SOS_TIMEOUT_MINUTES - 10),
    )

    await send_periodic_admin_report()

    assert mock_page_operator == []


# ---------------------------------------------------------------------------
# 6. PENDING_ADMIN_REVIEW leads stay in scope (once).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pending_admin_review_lead_is_included_and_paged_once(
    mock_db, mock_page_operator
):
    await mock_db.leads.delete_many({})
    lead = await _insert_stuck_lead(mock_db, status=LeadStatus.PENDING_ADMIN_REVIEW)

    await send_periodic_admin_report()
    await send_periodic_admin_report()

    assert len(mock_page_operator) == 1
    local = lead["chat_id"].split("@")[0]
    assert f"***{local[-4:]}" in mock_page_operator[0]


# ---------------------------------------------------------------------------
# 7. Concurrent claim: another replica wins the atomic update first ->
#    find_one_and_update returns None -> the lead is excluded from the digest
#    and no page happens for it.
# ---------------------------------------------------------------------------


class _RaceLosingCollection:
    """Wraps a real mongomock collection; `find` still sees the candidate
    (mirrors the read a concurrent replica raced against), but the claim
    `find_one_and_update` for that one document returns None, exactly like a
    real Mongo replica that already won the atomic update."""

    def __init__(self, real, losing_id):
        self._real = real
        self._losing_id = losing_id
        self.find_one_and_update_calls = 0

    def find(self, *args, **kwargs):
        return self._real.find(*args, **kwargs)

    async def find_one_and_update(self, filt, update, *args, **kwargs):
        self.find_one_and_update_calls += 1
        if filt.get("_id") == self._losing_id:
            return None
        return await self._real.find_one_and_update(filt, update, *args, **kwargs)

    async def count_documents(self, *args, **kwargs):
        return await self._real.count_documents(*args, **kwargs)

    async def find_one(self, *args, **kwargs):
        return await self._real.find_one(*args, **kwargs)


@pytest.mark.asyncio
async def test_lead_lost_to_a_concurrent_claim_is_excluded_and_not_paged(
    mock_db, mock_page_operator, monkeypatch
):
    await mock_db.leads.delete_many({})
    winner = await _insert_stuck_lead(mock_db, chat_id="972500003333@c.us")
    loser = await _insert_stuck_lead(mock_db, chat_id="972500004444@c.us")

    race_collection = _RaceLosingCollection(mock_db.leads, loser["_id"])
    monkeypatch.setattr(monitor_service, "leads_collection", race_collection)

    await send_periodic_admin_report()

    assert len(mock_page_operator) == 1
    digest = mock_page_operator[0]
    winner_local = winner["chat_id"].split("@")[0]
    loser_local = loser["chat_id"].split("@")[0]
    assert f"***{winner_local[-4:]}" in digest
    assert f"***{loser_local[-4:]}" not in digest

    # the "loser" was never actually stamped by our tick — a real concurrent
    # replica owns that write, not us.
    still_unclaimed = await mock_db.leads.find_one({"_id": loser["_id"]})
    assert still_unclaimed.get("admin_reported_at") is None


# ---------------------------------------------------------------------------
# 8. stuck_lead_report_due_filter shape, tested directly against the DB.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_due_filter_matches_missing_null_and_stale_stamp(mock_db):
    await mock_db.leads.delete_many({})
    now_utc = datetime.now(timezone.utc)
    stale_cutoff = now_utc - timedelta(
        hours=WorkerConstants.SOS_REPORT_REPAGE_HOURS + 5
    )

    missing = await mock_db.leads.insert_one({"chat_id": "a"})
    null_stamp = await mock_db.leads.insert_one(
        {"chat_id": "b", "admin_reported_at": None}
    )
    stale_stamp = await mock_db.leads.insert_one(
        {"chat_id": "c", "admin_reported_at": stale_cutoff}
    )

    matched_ids = {
        doc["_id"]
        async for doc in mock_db.leads.find(stuck_lead_report_due_filter(now_utc))
    }

    assert missing.inserted_id in matched_ids
    assert null_stamp.inserted_id in matched_ids
    assert stale_stamp.inserted_id in matched_ids


@pytest.mark.asyncio
async def test_due_filter_excludes_fresh_stamp(mock_db):
    await mock_db.leads.delete_many({})
    now_utc = datetime.now(timezone.utc)
    fresh_stamp = now_utc - timedelta(hours=1)

    fresh = await mock_db.leads.insert_one(
        {"chat_id": "d", "admin_reported_at": fresh_stamp}
    )

    matched_ids = {
        doc["_id"]
        async for doc in mock_db.leads.find(stuck_lead_report_due_filter(now_utc))
    }

    assert fresh.inserted_id not in matched_ids


def test_due_filter_wraps_the_or_in_an_and():
    """The reviewer's WARNING/style fix — the filter now composes like
    ``customer_flow.completion_check_due_filter`` (``$and`` of one ``$or``)
    instead of returning a bare top-level ``$or``, so a caller can merge it
    into a larger query without clobbering another top-level ``$or`` key. The
    three missing/null/stale clauses are unchanged (covered above)."""
    filt = stuck_lead_report_due_filter(datetime.now(timezone.utc))

    assert set(filt.keys()) == {"$and"}
    assert isinstance(filt["$and"], list)
    assert len(filt["$and"]) == 1
    assert "$or" in filt["$and"][0]
    assert len(filt["$and"][0]["$or"]) == 3


# ---------------------------------------------------------------------------
# 10. BLOCKER fix — nothing fallible between the stamp and the page. A raise
#     from the standing-backlog `count_documents` (a second, independent Mongo
#     round trip *after* the page) must not un-send the page or un-stamp the
#     leads already claimed.
# ---------------------------------------------------------------------------


class _CountDocumentsExplodes:
    """Wraps a real mongomock collection; every read/write the Reporter needs
    before paging works normally, but the standing-backlog count blows up —
    simulating the documented transient-Mongo-failure condition (PRO-112)."""

    def __init__(self, real):
        self._real = real

    def find(self, *args, **kwargs):
        return self._real.find(*args, **kwargs)

    async def find_one_and_update(self, *args, **kwargs):
        return await self._real.find_one_and_update(*args, **kwargs)

    async def find_one(self, *args, **kwargs):
        return await self._real.find_one(*args, **kwargs)

    async def count_documents(self, *args, **kwargs):
        raise RuntimeError("simulated standing-backlog count failure")


@pytest.mark.asyncio
async def test_standing_backlog_count_failure_does_not_lose_the_page_or_the_stamp(
    mock_db, mock_page_operator, monkeypatch
):
    await mock_db.leads.delete_many({})
    lead = await _insert_stuck_lead(mock_db)
    monkeypatch.setattr(
        monitor_service, "leads_collection", _CountDocumentsExplodes(mock_db.leads)
    )

    # must not raise, and the earlier page/stamp must survive the later error
    await send_periodic_admin_report()

    assert len(mock_page_operator) == 1
    updated = await mock_db.leads.find_one({"_id": lead["_id"]})
    assert updated.get("admin_reported_at") is not None


# ---------------------------------------------------------------------------
# 11. BLOCKER fix — one failing per-lead claim must not discard leads already
#     stamped in earlier loop iterations.
# ---------------------------------------------------------------------------


class _OneClaimExplodes:
    """Wraps a real mongomock collection; the atomic claim raises for one
    targeted document (simulating that lead's write hitting a transient
    error) and delegates normally for every other document."""

    def __init__(self, real, exploding_id):
        self._real = real
        self._exploding_id = exploding_id

    def find(self, *args, **kwargs):
        return self._real.find(*args, **kwargs)

    async def find_one_and_update(self, filt, update, *args, **kwargs):
        if filt.get("_id") == self._exploding_id:
            raise RuntimeError("simulated claim failure")
        return await self._real.find_one_and_update(filt, update, *args, **kwargs)

    async def find_one(self, *args, **kwargs):
        return await self._real.find_one(*args, **kwargs)

    async def count_documents(self, *args, **kwargs):
        return await self._real.count_documents(*args, **kwargs)


@pytest.mark.asyncio
async def test_one_failing_claim_does_not_lose_the_other_claims(
    mock_db, mock_page_operator, monkeypatch
):
    await mock_db.leads.delete_many({})
    first = await _insert_stuck_lead(mock_db, chat_id="972500005555@c.us")
    failing = await _insert_stuck_lead(mock_db, chat_id="972500006666@c.us")
    third = await _insert_stuck_lead(mock_db, chat_id="972500007777@c.us")

    wrapper = _OneClaimExplodes(mock_db.leads, failing["_id"])
    monkeypatch.setattr(monitor_service, "leads_collection", wrapper)

    log_output = await _captured_logs_async(send_periodic_admin_report, level="ERROR")

    # one digest, with the two surviving leads and NOT the failing one
    assert len(mock_page_operator) == 1
    digest = mock_page_operator[0]
    first_local = first["chat_id"].split("@")[0]
    failing_local = failing["chat_id"].split("@")[0]
    third_local = third["chat_id"].split("@")[0]
    assert f"***{first_local[-4:]}" in digest
    assert f"***{third_local[-4:]}" in digest
    assert f"***{failing_local[-4:]}" not in digest

    # the two survivors were stamped; the failing lead was not
    first_doc = await mock_db.leads.find_one({"_id": first["_id"]})
    third_doc = await mock_db.leads.find_one({"_id": third["_id"]})
    failing_doc = await mock_db.leads.find_one({"_id": failing["_id"]})
    assert first_doc.get("admin_reported_at") is not None
    assert third_doc.get("admin_reported_at") is not None
    assert failing_doc.get("admin_reported_at") is None

    # the per-lead claim failure was logged
    assert "Claim failed" in log_output
    assert str(failing["_id"]) in log_output


# ---------------------------------------------------------------------------
# 12. WARNING fix / acceptance criterion 2 — a lead that was paged, resolved
#     (given a fresh owner via reassign_lead), and then goes stuck again must
#     be able to page again immediately, not stay muted for the remainder of
#     SOS_REPORT_REPAGE_HOURS from the *previous* incident's stamp.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repage_after_reassign_lead_clears_the_old_stamp(
    mock_db, mock_page_operator, mock_whatsapp, monkeypatch
):
    monkeypatch.setattr(monitor_service, "users_collection", mock_db.users)
    await mock_db.leads.delete_many({})
    lead = await _insert_stuck_lead(
        mock_db, status=LeadStatus.NEW, reassignment_count=0
    )

    # Tick 1 — paged and stamped, well within the 24h repage window.
    await send_periodic_admin_report()
    assert len(mock_page_operator) == 1
    stamped = await mock_db.leads.find_one({"_id": lead["_id"]})
    assert stamped.get("admin_reported_at") is not None

    # The lead gets a fresh owner.
    new_pro = {
        "_id": "new_pro_id",
        "business_name": "אבי אינסטלציה",
        "phone_number": "972500009999",
    }
    monkeypatch.setattr(
        "app.services.matching_service.determine_best_pro",
        AsyncMock(return_value=new_pro),
    )
    monkeypatch.setattr(
        monitor_service, "notify_pro_new_lead", AsyncMock(return_value=True)
    )
    result = await reassign_lead(stamped)
    assert result is True

    reassigned = await mock_db.leads.find_one({"_id": lead["_id"]})
    assert (
        "admin_reported_at" not in reassigned
        or reassigned.get("admin_reported_at") is None
    )

    # Simulate the lead going stuck again under the new pro — reassign_lead
    # resets created_at to "now", so age it back past SOS_TIMEOUT_MINUTES the
    # same way real time passing would.
    now_utc = datetime.now(timezone.utc)
    await mock_db.leads.update_one(
        {"_id": lead["_id"]},
        {
            "$set": {
                "created_at": now_utc
                - timedelta(minutes=WorkerConstants.SOS_TIMEOUT_MINUTES + 30)
            }
        },
    )

    # Tick 2 — well under SOS_REPORT_REPAGE_HOURS since the FIRST stamp, but
    # that stamp is gone: this must page again rather than staying muted.
    await send_periodic_admin_report()

    assert len(mock_page_operator) == 2


# ---------------------------------------------------------------------------
# 9. Fail-open: an exception inside the job never propagates.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_periodic_admin_report_swallows_exceptions(
    mock_db, mock_page_operator, monkeypatch
):
    await mock_db.leads.delete_many({})
    await _insert_stuck_lead(mock_db)

    class _ExplodingFind:
        def find(self, *args, **kwargs):
            raise RuntimeError("simulated DB outage")

    monkeypatch.setattr(monitor_service, "leads_collection", _ExplodingFind())

    # must not raise
    await send_periodic_admin_report()

    assert mock_page_operator == []
