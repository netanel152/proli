import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from bson import ObjectId
from datetime import datetime, timezone
from app.core.constants import ISRAEL_CITIES_COORDS
from app.services.matching_service import (
    determine_best_pro,
    get_coordinates,
    WorkerConstants,
    is_pro_eligible_for_lead,
    required_profession,
)


def _mock_leads_aggregate(load_map):
    """Returns a mock aggregate that yields load counts from a dict {pro_id: count}."""

    async def _aiter(*args, **kwargs):
        for pid, count in load_map.items():
            yield {"_id": pid, "count": count}

    mock = MagicMock(side_effect=_aiter)
    return mock


def _mock_users_aggregate(pros_list):
    """Returns a mock aggregate for $geoNear that yields pro documents."""

    async def _aiter(*args, **kwargs):
        for p in pros_list:
            yield p

    return MagicMock(side_effect=_aiter)


def _mock_empty_aggregate():
    """Returns a mock aggregate that yields nothing."""

    async def _aiter(*args, **kwargs):
        return
        yield  # noqa: make it an async generator

    return MagicMock(side_effect=_aiter)


@pytest.fixture
def mock_matching_dependencies(monkeypatch):
    with patch("app.services.matching_service.users_collection") as mock_users, patch(
        "app.services.matching_service.leads_collection"
    ) as mock_leads:

        # Default: aggregation returns no load counts (all pros have 0 active leads)
        mock_leads.aggregate = _mock_leads_aggregate({})

        # Default: users aggregate returns nothing
        mock_users.aggregate = _mock_empty_aggregate()

        # Default: users find returns empty (for text-based queries)
        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(return_value=[])
        mock_users.find.return_value = mock_cursor

        # Mock resolve_city_to_coords to avoid real API calls and control fallback logic.
        # This ensures 'Unknown City' returns None and triggers regex-based matching.
        async def mock_resolve(location):
            if not location:
                return None
            loc_lower = location.lower().strip()
            if loc_lower == "tel aviv" or loc_lower == "tlv":
                return [34.7818, 32.0853]
            return None

        monkeypatch.setattr(
            "app.services.matching_service.resolve_city_to_coords", mock_resolve
        )

        yield mock_users, mock_leads


def test_get_coordinates():
    # Test known city
    assert get_coordinates("Tel Aviv") == [34.7818, 32.0853]
    assert get_coordinates("tel aviv") == [34.7818, 32.0853]

    # Test known city (alias)
    assert get_coordinates("TLV") == [34.7818, 32.0853]

    # Test unknown city
    assert get_coordinates("Unknown City") is None

    # Test empty/None
    assert get_coordinates(None) is None
    assert get_coordinates("") is None


@pytest.mark.asyncio
async def test_determine_best_pro_geo_success(mock_matching_dependencies):
    """
    Scenario: User asks for 'Tel Aviv'. System should use $geoNear aggregation.
    Expected: Returns the highest-rated pro from the first radius step.
    """
    mock_users, mock_leads = mock_matching_dependencies

    pro1 = {
        "_id": ObjectId(),
        "business_name": "Pro 1",
        "role": "professional",
        "is_active": True,
        "social_proof": {"rating": 5.0},
    }
    pro2 = {
        "_id": ObjectId(),
        "business_name": "Pro 2",
        "role": "professional",
        "is_active": True,
        "social_proof": {"rating": 4.5},
    }

    # $geoNear aggregate returns pros on first radius step
    mock_users.aggregate = _mock_users_aggregate([pro1, pro2])
    mock_leads.aggregate = _mock_leads_aggregate({})

    result = await determine_best_pro(location="Tel Aviv", issue_type="Leak")

    # Should return highest-rated (pro1 with 5.0)
    assert result == pro1

    # Verify $geoNear pipeline was called
    mock_users.aggregate.assert_called()
    pipeline = mock_users.aggregate.call_args[0][0]
    assert "$geoNear" in pipeline[0]
    assert pipeline[0]["$geoNear"]["near"]["coordinates"] == [34.7818, 32.0853]


@pytest.mark.asyncio
async def test_progressive_radius_expansion(mock_matching_dependencies):
    """
    Scenario: No pro found at 10km, found at 20km.
    Expected: System expands radius and finds pro on second attempt.
    """
    mock_users, mock_leads = mock_matching_dependencies

    pro1 = {
        "_id": ObjectId(),
        "business_name": "Distant Pro",
        "social_proof": {"rating": 4.0},
    }

    call_count = 0

    async def _expanding_agg(pipeline):
        nonlocal call_count
        call_count += 1
        max_dist = pipeline[0]["$geoNear"]["maxDistance"]
        # Only return pro at 20km radius (second attempt)
        if max_dist >= 20000:
            yield pro1

    mock_users.aggregate = MagicMock(side_effect=_expanding_agg)
    mock_leads.aggregate = _mock_leads_aggregate({})

    result = await determine_best_pro(location="Tel Aviv")

    assert result == pro1
    assert call_count == 2  # First call (10km) empty, second call (20km) found


@pytest.mark.asyncio
async def test_no_pro_at_max_radius_returns_none(mock_matching_dependencies):
    """
    Scenario: No pro found at any radius (10km, 20km, 30km).
    Expected: Returns None (no global fallback). Lead should go to PENDING_ADMIN_REVIEW.
    """
    mock_users, mock_leads = mock_matching_dependencies

    # All aggregate calls return empty
    mock_users.aggregate = _mock_empty_aggregate()

    result = await determine_best_pro(location="Tel Aviv")

    assert result is None
    # Should have been called 3 times (one per radius step)
    assert mock_users.aggregate.call_count == 3


@pytest.mark.asyncio
async def test_determine_best_pro_text_fallback(mock_matching_dependencies):
    """
    Scenario: User asks for 'Unknown City' (no coordinates).
    Expected: System falls back to Regex text search and sorts by Rating.
    """
    mock_users, mock_leads = mock_matching_dependencies

    pro1 = {
        "_id": ObjectId(),
        "business_name": "Rating 3",
        "role": "professional",
        "social_proof": {"rating": 3.0},
    }
    pro2 = {
        "_id": ObjectId(),
        "business_name": "Rating 5",
        "role": "professional",
        "social_proof": {"rating": 5.0},
    }

    mock_cursor = MagicMock()
    mock_cursor.to_list = AsyncMock(return_value=[pro1, pro2])
    mock_users.find.return_value = mock_cursor

    result = await determine_best_pro(location="Unknown City", issue_type="Leak")

    # Should pick highest rated
    assert result == pro2

    # Verify text-based query (not $geoNear)
    args, _ = mock_users.find.call_args
    query = args[0]
    assert "location" not in query
    assert "service_areas" in query
    assert query["service_areas"]["$regex"] == "Unknown City"


@pytest.mark.asyncio
async def test_load_balancing_filtering(mock_matching_dependencies):
    """
    Scenario: Top rated pro is overloaded (>= MAX_PRO_LOAD active leads).
    Expected: System skips overloaded pro and picks the available one.
    """
    mock_users, mock_leads = mock_matching_dependencies

    pro_busy = {
        "_id": ObjectId(),
        "business_name": "Busy Pro",
        "role": "professional",
        "social_proof": {"rating": 5.0},
    }
    pro_avail = {
        "_id": ObjectId(),
        "business_name": "Available Pro",
        "role": "professional",
        "social_proof": {"rating": 4.5},
    }

    mock_cursor = MagicMock()
    mock_cursor.to_list = AsyncMock(return_value=[pro_busy, pro_avail])
    mock_users.find.return_value = mock_cursor

    # pro_busy has MAX_LOAD active leads
    mock_leads.aggregate = _mock_leads_aggregate(
        {pro_busy["_id"]: WorkerConstants.MAX_PRO_LOAD}
    )

    result = await determine_best_pro(location="Unknown City")

    assert result == pro_avail


@pytest.mark.asyncio
async def test_all_pros_overloaded_returns_none(mock_matching_dependencies):
    """
    Scenario: All matching pros are overloaded.
    Expected: Returns None (no emergency fallback to overloaded pro).
    """
    mock_users, mock_leads = mock_matching_dependencies

    pro1 = {
        "_id": ObjectId(),
        "business_name": "Pro 1",
        "role": "professional",
        "social_proof": {"rating": 5.0},
    }

    mock_cursor = MagicMock()
    mock_cursor.to_list = AsyncMock(return_value=[pro1])
    mock_users.find.return_value = mock_cursor

    mock_leads.aggregate = _mock_leads_aggregate(
        {pro1["_id"]: WorkerConstants.MAX_PRO_LOAD + 1}
    )

    result = await determine_best_pro(location="Unknown City")

    assert result is None


@pytest.mark.asyncio
async def test_exclude_pro_ids(mock_matching_dependencies):
    """
    Scenario: We explicitly exclude a pro ID (e.g. they rejected the lead).
    Expected: Query includes $nin for that ID.
    """
    mock_users, _ = mock_matching_dependencies

    mock_cursor = MagicMock()
    mock_cursor.to_list = AsyncMock(return_value=[])
    mock_users.find.return_value = mock_cursor

    excluded_id = str(ObjectId())
    await determine_best_pro(location="Unknown City", excluded_pro_ids=[excluded_id])

    # The first find() call is the regex query which includes the base_filter with $nin
    first_call = mock_users.find.call_args_list[0]
    query = first_call[0][0]

    assert "_id" in query
    assert "$nin" in query["_id"]
    assert str(query["_id"]["$nin"][0]) == excluded_id


@pytest.mark.asyncio
async def test_exclude_pro_ids_geo(mock_matching_dependencies):
    """
    Scenario: Geo search with excluded IDs.
    Expected: $geoNear pipeline query includes $nin for the excluded IDs.
    """
    mock_users, _ = mock_matching_dependencies

    # All geo calls return empty
    mock_users.aggregate = _mock_empty_aggregate()

    excluded_id = str(ObjectId())
    await determine_best_pro(location="Tel Aviv", excluded_pro_ids=[excluded_id])

    # Check the $geoNear pipeline query filter
    pipeline = mock_users.aggregate.call_args[0][0]
    geo_query = pipeline[0]["$geoNear"]["query"]
    assert "_id" in geo_query
    assert "$nin" in geo_query["_id"]
    assert str(geo_query["_id"]["$nin"][0]) == excluded_id


@pytest.mark.asyncio
async def test_geo_sorts_by_rating(mock_matching_dependencies):
    """
    Scenario: Multiple pros found at same radius.
    Expected: Sorted by rating (highest first).
    """
    mock_users, mock_leads = mock_matching_dependencies

    pro_low = {
        "_id": ObjectId(),
        "business_name": "Low Rating",
        "social_proof": {"rating": 2.0},
    }
    pro_high = {
        "_id": ObjectId(),
        "business_name": "High Rating",
        "social_proof": {"rating": 5.0},
    }

    # $geoNear returns both (pipeline already sorts by rating, but candidates also sorted in Python)
    mock_users.aggregate = _mock_users_aggregate([pro_low, pro_high])
    mock_leads.aggregate = _mock_leads_aggregate({})

    result = await determine_best_pro(location="Tel Aviv")

    assert result == pro_high


@pytest.mark.asyncio
async def test_geo_no_show_penalty_deprioritizes_higher_rated_pro(
    mock_matching_dependencies,
):
    """PRO-45: `no_show_count` is now a real signal on `candidate_score`
    (`rating - no_shows * 0.5`). A pro with enough recorded no-shows must lose
    to a lower-rated pro with a clean record, or the field the customer-flow
    handler now writes still can't move a routing decision."""
    mock_users, mock_leads = mock_matching_dependencies

    pro_high_but_flaky = {
        "_id": ObjectId(),
        "business_name": "High Rating, Flaky",
        "social_proof": {"rating": 5.0},
        "no_show_count": 6,  # penalty 3.0 -> effective 2.0
    }
    pro_low_but_reliable = {
        "_id": ObjectId(),
        "business_name": "Lower Rating, Reliable",
        "social_proof": {"rating": 3.0},  # no no-shows -> effective 3.0
    }

    mock_users.aggregate = _mock_users_aggregate(
        [pro_high_but_flaky, pro_low_but_reliable]
    )
    mock_leads.aggregate = _mock_leads_aggregate({})

    result = await determine_best_pro(location="Tel Aviv")

    assert result == pro_low_but_reliable


# ---------------------------------------------------------------------------
# is_pro_eligible_for_lead / required_profession (PRO-123)
#
# The `מצא` proactive search claims a lead directly, bypassing
# determine_best_pro entirely -- this predicate is what stops it handing out
# work the routing engine would never have offered: an inactive/unapproved
# pro, a pro this exact lead already rejected, a pro outside the max geo
# radius (or with no way to place them near the job at all), a profession
# mismatch, or a pro already at MAX_PRO_LOAD.
# ---------------------------------------------------------------------------


def _pro(**overrides):
    base = {"_id": ObjectId(), "is_active": True, "pending_approval": False}
    base.update(overrides)
    return base


def _lead(**overrides):
    base = {"_id": ObjectId(), "issue_type": "נזילה", "city": "תל אביב"}
    base.update(overrides)
    return base


@pytest.fixture
def eligible_env(monkeypatch):
    """count_documents defaults to 0 active leads; resolve_city_to_coords only
    resolves names present in the static dict -- anything else is treated as
    a city geocoding genuinely cannot place, forcing the service_areas
    fallback branch."""
    mock_leads = MagicMock()
    mock_leads.count_documents = AsyncMock(return_value=0)
    monkeypatch.setattr("app.services.matching_service.leads_collection", mock_leads)

    async def mock_resolve(name):
        coords = ISRAEL_CITIES_COORDS.get(name)
        return list(coords) if coords else None

    monkeypatch.setattr(
        "app.services.matching_service.resolve_city_to_coords", mock_resolve
    )
    return mock_leads


@pytest.mark.asyncio
async def test_is_pro_eligible_for_lead_pro_state_and_load_gates(eligible_env):
    """Any one of these disqualifies a pro from claiming a lead via search:
    inactive, still pending admin approval, already rejected this exact
    lead, or already at MAX_PRO_LOAD active jobs. Established against a
    baseline pro who passes everything else, so each case below is shown to
    be the thing that flips the result -- not some other missing field."""
    mock_leads = eligible_env
    lead = _lead()
    baseline = _pro(location={"type": "Point", "coordinates": [34.7818, 32.0853]})
    assert await is_pro_eligible_for_lead(baseline, lead) is True

    inactive = {**baseline, "is_active": False}
    assert await is_pro_eligible_for_lead(inactive, lead) is False

    pending = {**baseline, "pending_approval": True}
    assert await is_pro_eligible_for_lead(pending, lead) is False

    rejected_lead = {**lead, "rejected_by": [baseline["_id"]]}
    assert await is_pro_eligible_for_lead(baseline, rejected_lead) is False

    mock_leads.count_documents = AsyncMock(return_value=WorkerConstants.MAX_PRO_LOAD)
    assert await is_pro_eligible_for_lead(baseline, lead) is False


@pytest.mark.asyncio
async def test_is_pro_eligible_for_lead_no_show_gate_is_scoped_to_reported_pro(
    eligible_env,
):
    """PRO-45: the pro named on the lead when `no_show_reported_at` is set must
    not reclaim their own lead through the מצא search -- but the guard is
    scoped to that pro specifically, not the lead as a whole: a different
    (replacement) pro is still eligible for the same lead."""
    baseline = _pro(location={"type": "Point", "coordinates": [34.7818, 32.0853]})
    other_pro = _pro(location={"type": "Point", "coordinates": [34.7818, 32.0853]})
    reported_lead = _lead(
        no_show_reported_at=datetime.now(timezone.utc), pro_id=baseline["_id"]
    )

    assert await is_pro_eligible_for_lead(baseline, reported_lead) is False
    assert await is_pro_eligible_for_lead(other_pro, reported_lead) is True


@pytest.mark.asyncio
async def test_is_pro_eligible_for_lead_location_gate(eligible_env):
    """Geo path (both sides resolve to coordinates) when in range or not;
    falls back to the same service_areas text match determine_best_pro's
    regex branch uses when either side has no coordinates at all."""
    lead_tlv = _lead(city="תל אביב")

    within_radius = _pro(location={"type": "Point", "coordinates": [34.7818, 32.0853]})
    assert await is_pro_eligible_for_lead(within_radius, lead_tlv) is True

    outside_radius = _pro(
        location={"type": "Point", "coordinates": list(ISRAEL_CITIES_COORDS["באר שבע"])}
    )
    assert await is_pro_eligible_for_lead(outside_radius, lead_tlv) is False

    lead_unresolvable = _lead(city="עיר בדיונית שלא קיימת")

    area_match = _pro(service_areas=["עיר בדיונית שלא קיימת"])
    assert await is_pro_eligible_for_lead(area_match, lead_unresolvable) is True

    area_mismatch = _pro(service_areas=["חיפה"])
    assert await is_pro_eligible_for_lead(area_mismatch, lead_unresolvable) is False


@pytest.mark.asyncio
async def test_is_pro_eligible_for_lead_profession_gate(eligible_env):
    """A lead that names a specific profession blocks a mismatched pro, but
    'general'/'handyman' pros and a pro with no declared profession at all
    are never constrained by it -- and a lead that names no profession
    imposes no constraint on anyone."""
    pro_location = {"type": "Point", "coordinates": [34.7818, 32.0853]}
    lead = _lead(issue_type="צריך חשמלאי דחוף")  # names "electrician"

    mismatched = _pro(location=pro_location, profession_type="plumber")
    assert await is_pro_eligible_for_lead(mismatched, lead) is False

    general = _pro(location=pro_location, profession_type="general")
    assert await is_pro_eligible_for_lead(general, lead) is True

    handyman = _pro(location=pro_location, profession_type="handyman")
    assert await is_pro_eligible_for_lead(handyman, lead) is True

    unspecified = _pro(location=pro_location)  # no profession_type at all
    assert await is_pro_eligible_for_lead(unspecified, lead) is True

    # Careful: not "בעיה כללית" -- "כללית" contains "כללי" (TYPE_MAP's
    # "general" key) as a substring, which would make this its own mismatch
    # case rather than the "no profession named" one it's meant to be.
    generic_lead = _lead(issue_type="יש לי תקלה בבית")  # no profession named
    plumber = _pro(location=pro_location, profession_type="plumber")
    assert await is_pro_eligible_for_lead(plumber, generic_lead) is True


def test_required_profession_ignores_type_map_digit_keys():
    """TYPE_MAP's digit keys ("1".."7") are onboarding menu shortcuts, not
    profession names that can appear in free text -- issue_type containing a
    literal '1' must not be read as naming 'plumber'."""
    assert required_profession({"issue_type": "יש לי בעיה, אני בדירה 1"}) is None
    assert required_profession({"issue_type": "צריך אינסטלטור דחוף"}) == "plumber"
    # Not "בעיה כללית" -- "כללית" contains "כללי" (TYPE_MAP's "general" key)
    # as a substring, which would defeat the point of this "no match" case.
    assert required_profession({"issue_type": "יש לי תקלה בבית"}) is None
