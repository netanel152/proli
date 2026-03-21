import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from bson import ObjectId
from app.services.matching_service import determine_best_pro, get_coordinates, WorkerConstants


def _mock_aggregate(load_map):
    """Returns a mock aggregate that yields load counts from a dict {pro_id: count}."""
    async def _aiter(*args, **kwargs):
        for pid, count in load_map.items():
            yield {"_id": pid, "count": count}
    mock = MagicMock()
    mock.__aiter__ = _aiter
    return mock


@pytest.fixture
def mock_matching_dependencies():
    with patch("app.services.matching_service.users_collection") as mock_users, \
         patch("app.services.matching_service.leads_collection") as mock_leads:

        # Default: aggregation returns no load counts (all pros have 0 active leads)
        mock_leads.aggregate = MagicMock(return_value=_mock_aggregate({}))
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
    Scenario: User asks for 'Tel Aviv'. System should use Geo-Spatial query.
    Expected: Calls DB with $near and returns the closest pro (first result).
    """
    mock_users, mock_leads = mock_matching_dependencies
    
    # Mock Pros
    pro1 = {"_id": ObjectId(), "business_name": "Pro 1", "role": "professional", "is_active": True, "social_proof": {"rating": 5.0}}
    pro2 = {"_id": ObjectId(), "business_name": "Pro 2", "role": "professional", "is_active": True, "social_proof": {"rating": 4.5}}
    
    # Mock Find (Geo uses find() then to_list())
    mock_cursor = MagicMock()
    mock_cursor.to_list = AsyncMock(return_value=[pro1, pro2])
    mock_users.find.return_value = mock_cursor
    
    result = await determine_best_pro(location="Tel Aviv", issue_type="Leak")
    
    # Assertions
    assert result == pro1
    
    # Verify Query Structure
    args, _ = mock_users.find.call_args
    query = args[0]
    
    assert "location" in query
    assert "$near" in query["location"]
    assert query["location"]["$near"]["$geometry"]["coordinates"] == [34.7818, 32.0853] # Tel Aviv Coords

@pytest.mark.asyncio
async def test_determine_best_pro_text_fallback(mock_matching_dependencies):
    """
    Scenario: User asks for 'Unknown City'.
    Expected: System falls back to Regex text search and sorts by Rating.
    """
    mock_users, mock_leads = mock_matching_dependencies
    
    pro1 = {"_id": ObjectId(), "business_name": "Rating 3", "role": "professional", "social_proof": {"rating": 3.0}}
    pro2 = {"_id": ObjectId(), "business_name": "Rating 5", "role": "professional", "social_proof": {"rating": 5.0}}
    
    mock_cursor = MagicMock()
    # Unordered list from DB
    mock_cursor.to_list = AsyncMock(return_value=[pro1, pro2])
    mock_users.find.return_value = mock_cursor
    
    result = await determine_best_pro(location="Unknown City", issue_type="Leak")
    
    # Assertions
    assert result == pro2 # Should pick highest rated
    
    # Verify Query (Text based)
    args, _ = mock_users.find.call_args
    query = args[0]
    
    assert "location" not in query
    assert "service_areas" in query
    assert query["service_areas"]["$regex"] == "Unknown City"

@pytest.mark.asyncio
async def test_load_balancing_filtering(mock_matching_dependencies):
    """
    Scenario: Top rated/closest pro is overloaded.
    Expected: System skips overloaded pro and picks the next best one.
    """
    mock_users, mock_leads = mock_matching_dependencies
    
    pro_busy = {"_id": ObjectId(), "business_name": "Busy Pro", "role": "professional", "social_proof": {"rating": 5.0}}
    pro_avail = {"_id": ObjectId(), "business_name": "Available Pro", "role": "professional", "social_proof": {"rating": 4.5}}
    
    mock_cursor = MagicMock()
    mock_cursor.to_list = AsyncMock(return_value=[pro_busy, pro_avail])
    mock_users.find.return_value = mock_cursor
    
    # Mock Load Counts via aggregation
    # pro_busy has MAX_LOAD active leads, pro_avail has 0 (not in results)
    mock_leads.aggregate = MagicMock(return_value=_mock_aggregate({
        pro_busy["_id"]: WorkerConstants.MAX_PRO_LOAD
    }))

    result = await determine_best_pro(location="Unknown City")

    assert result == pro_avail

@pytest.mark.asyncio
async def test_all_pros_overloaded_fallback(mock_matching_dependencies):
    """
    Scenario: All matching pros are overloaded.
    Expected: System falls back to the highest rated one anyway (Emergency handling).
    """
    mock_users, mock_leads = mock_matching_dependencies
    
    pro1 = {"_id": ObjectId(), "business_name": "Pro 1", "role": "professional", "social_proof": {"rating": 5.0}}
    
    mock_cursor = MagicMock()
    mock_cursor.to_list = AsyncMock(return_value=[pro1])
    mock_users.find.return_value = mock_cursor
    
    # Mock Load -> Overloaded via aggregation
    mock_leads.aggregate = MagicMock(return_value=_mock_aggregate({
        pro1["_id"]: WorkerConstants.MAX_PRO_LOAD + 1
    }))

    result = await determine_best_pro(location="Unknown City")

    assert result == pro1  # Should still return pro1 rather than None

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
    await determine_best_pro(location="Tel Aviv", excluded_pro_ids=[excluded_id])
    
    args, _ = mock_users.find.call_args
    query = args[0]
    
    assert "_id" in query
    assert "$nin" in query["_id"]
    # Check if the ID in $nin matches our string
    assert str(query["_id"]["$nin"][0]) == excluded_id
