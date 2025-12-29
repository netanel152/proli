import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import json
from app.services.ai_engine import AIEngine, AIResponse, ExtractedData

@pytest.fixture
def ai_engine():
    # Patch settings to avoid needing real API key
    with patch("app.services.ai_engine.settings") as mock_settings:
        mock_settings.GEMINI_API_KEY = "fake_key"
        # Patch genai.Client to prevent real network calls during init
        with patch("google.genai.Client"):
            engine = AIEngine()
            # Reset client to a MagicMock we can configure per test
            engine.client = MagicMock()
            engine.client.aio = MagicMock()
            engine.client.aio.models = MagicMock()
            yield engine

@pytest.mark.asyncio
async def test_malformed_json_raw_text(ai_engine):
    """
    Scenario: LLM returns raw text that isn't JSON.
    Expected: Catch JSONDecodeError and return fallback AIResponse.
    """
    # Mock Response Object
    mock_response = MagicMock()
    # Simulate SDK returning None for parsed (failed to parse natively)
    # Depending on SDK, 'parsed' attribute might not exist or be None
    mock_response.parsed = None 
    mock_response.text = "I cannot answer this request."
    
    # Setup async generation
    ai_engine.client.aio.models.generate_content = AsyncMock(return_value=mock_response)
    
    result = await ai_engine.analyze_conversation([], "Hi", custom_system_prompt="")
    
    assert isinstance(result, AIResponse)
    assert result.reply_to_user == "סליחה, לא הבנתי. תוכל לחזור על זה?"
    assert result.extracted_data.city is None

@pytest.mark.asyncio
async def test_valid_json_parsing(ai_engine):
    """
    Scenario: LLM returns valid JSON string.
    Expected: Correctly parsed into AIResponse model.
    """
    valid_json = {
        "reply_to_user": "Shalom",
        "transcription": None,
        "extracted_data": {
            "city": "Haifa",
            "issue": "Broken door",
            "full_address": None,
            "appointment_time": None
        },
        "is_deal": False
    }
    
    mock_response = MagicMock()
    mock_response.parsed = None
    mock_response.text = json.dumps(valid_json)
    
    ai_engine.client.aio.models.generate_content = AsyncMock(return_value=mock_response)
    
    result = await ai_engine.analyze_conversation([], "Hi", custom_system_prompt="")
    
    assert isinstance(result, AIResponse)
    assert result.reply_to_user == "Shalom"
    assert result.extracted_data.city == "Haifa"
    assert result.extracted_data.issue == "Broken door"

@pytest.mark.asyncio
async def test_sdk_native_parsing(ai_engine):
    """
    Scenario: SDK successfully parses JSON into Pydantic model (feature of v2 SDK).
    Expected: Return the parsed object directly.
    """
    expected_response = AIResponse(
        reply_to_user="Native Parse",
        extracted_data=ExtractedData(city="Eilat", issue=None, full_address=None, appointment_time=None),
        transcription=None
    )
    mock_response = MagicMock()
    mock_response.parsed = expected_response
    
    ai_engine.client.aio.models.generate_content = AsyncMock(return_value=mock_response)
    
    result = await ai_engine.analyze_conversation([], "Hi", custom_system_prompt="")
    
    assert result == expected_response
    assert result.reply_to_user == "Native Parse"

@pytest.mark.asyncio
async def test_sdk_failure_exception(ai_engine):
    """
    Scenario: SDK raises an exception (e.g. Timeout, API Error).
    Expected: Return "System Overload" fallback response.
    """
    ai_engine.client.aio.models.generate_content = AsyncMock(side_effect=Exception("API Timeout"))
    
    result = await ai_engine.analyze_conversation([], "Hi", custom_system_prompt="")
    
    assert isinstance(result, AIResponse)
    assert "עומס" in result.reply_to_user
    assert result.extracted_data.city is None
