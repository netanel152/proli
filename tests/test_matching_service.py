import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from bson import ObjectId
from app.services.matching_service import determine_best_pro, get_coordinates, WorkerConstants


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
    with patch("app.services.matching_service.users_collection") as mock_users, \
         patch("app.services.matching_service.leads_collection") as mock_leads:

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

        monkeypatch.setattr("app.services.matching_service.resolve_city_to_coords", mock_resolve)

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

    pro1 = {"_id": ObjectId(), "business_name": "Pro 1", "role": "professional", "is_active": True, "social_proof": {"rating": 5.0}}
    pro2 = {"_id": ObjectId(), "business_name": "Pro 2", "role": "professional", "is_active": True, "social_proof": {"rating": 4.5}}

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

    pro1 = {"_id": ObjectId(), "business_name": "Distant Pro", "social_proof": {"rating": 4.0}}

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

    pro1 = {"_id": ObjectId(), "business_name": "Rating 3", "role": "professional", "social_proof": {"rating": 3.0}}
    pro2 = {"_id": ObjectId(), "business_name": "Rating 5", "role": "professional", "social_proof": {"rating": 5.0}}

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

    pro_busy = {"_id": ObjectId(), "business_name": "Busy Pro", "role": "professional", "social_proof": {"rating": 5.0}}
    pro_avail = {"_id": ObjectId(), "business_name": "Available Pro", "role": "professional", "social_proof": {"rating": 4.5}}

    mock_cursor = MagicMock()
    mock_cursor.to_list = AsyncMock(return_value=[pro_busy, pro_avail])
    mock_users.find.return_value = mock_cursor

    # pro_busy has MAX_LOAD active leads
    mock_leads.aggregate = _mock_leads_aggregate({
        pro_busy["_id"]: WorkerConstants.MAX_PRO_LOAD
    })

    result = await determine_best_pro(location="Unknown City")

    assert result == pro_avail


@pytest.mark.asyncio
async def test_all_pros_overloaded_returns_none(mock_matching_dependencies):
    """
    Scenario: All matching pros are overloaded.
    Expected: Returns None (no emergency fallback to overloaded pro).
    """
    mock_users, mock_leads = mock_matching_dependencies

    pro1 = {"_id": ObjectId(), "business_name": "Pro 1", "role": "professional", "social_proof": {"rating": 5.0}}

    mock_cursor = MagicMock()
    mock_cursor.to_list = AsyncMock(return_value=[pro1])
    mock_users.find.return_value = mock_cursor

    mock_leads.aggregate = _mock_leads_aggregate({
        pro1["_id"]: WorkerConstants.MAX_PRO_LOAD + 1
    })

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

    pro_low = {"_id": ObjectId(), "business_name": "Low Rating", "social_proof": {"rating": 2.0}}
    pro_high = {"_id": ObjectId(), "business_name": "High Rating", "social_proof": {"rating": 5.0}}

    # $geoNear returns both (pipeline already sorts by rating, but candidates also sorted in Python)
    mock_users.aggregate = _mock_users_aggregate([pro_low, pro_high])
    mock_leads.aggregate = _mock_leads_aggregate({})

    result = await determine_best_pro(location="Tel Aviv")

    assert result == pro_high
