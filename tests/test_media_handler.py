"""
Tests for media_handler.py: media type detection and fetching.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.media_handler import detect_and_fetch_media


@pytest.fixture
def mock_http_client():
    client = MagicMock()
    client.head = AsyncMock()
    client.get = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_detect_image_downloads_content(mock_http_client):
    head_resp = MagicMock()
    head_resp.headers = {"Content-Type": "image/jpeg"}
    mock_http_client.head.return_value = head_resp

    get_resp = MagicMock()
    get_resp.status_code = 200
    get_resp.content = b"\xff\xd8\xff\xe0"  # JPEG bytes
    get_resp.headers = {"Content-Type": "image/jpeg"}
    mock_http_client.get.return_value = get_resp

    with patch("app.services.media_handler.get_http_client", new_callable=AsyncMock, return_value=mock_http_client):
        data, mime = await detect_and_fetch_media("http://example.com/photo.jpg")

    assert data == b"\xff\xd8\xff\xe0"
    assert mime == "image/jpeg"


@pytest.mark.asyncio
async def test_detect_audio_returns_url_only(mock_http_client):
    head_resp = MagicMock()
    head_resp.headers = {"Content-Type": "audio/ogg"}
    mock_http_client.head.return_value = head_resp

    with patch("app.services.media_handler.get_http_client", new_callable=AsyncMock, return_value=mock_http_client):
        data, mime = await detect_and_fetch_media("http://example.com/voice.ogg")

    assert data is None  # Not downloaded
    assert mime == "audio/ogg"
    mock_http_client.get.assert_not_called()


@pytest.mark.asyncio
async def test_detect_video_returns_url_only(mock_http_client):
    head_resp = MagicMock()
    head_resp.headers = {"Content-Type": "video/mp4"}
    mock_http_client.head.return_value = head_resp

    with patch("app.services.media_handler.get_http_client", new_callable=AsyncMock, return_value=mock_http_client):
        data, mime = await detect_and_fetch_media("http://example.com/clip.mp4")

    assert data is None
    assert mime == "video/mp4"


@pytest.mark.asyncio
async def test_detect_failed_download(mock_http_client):
    head_resp = MagicMock()
    head_resp.headers = {"Content-Type": "image/png"}
    mock_http_client.head.return_value = head_resp

    get_resp = MagicMock()
    get_resp.status_code = 404
    mock_http_client.get.return_value = get_resp

    with patch("app.services.media_handler.get_http_client", new_callable=AsyncMock, return_value=mock_http_client):
        data, mime = await detect_and_fetch_media("http://example.com/gone.png")

    assert data is None
    assert mime is None


@pytest.mark.asyncio
async def test_detect_network_error():
    with patch("app.services.media_handler.get_http_client", new_callable=AsyncMock, side_effect=Exception("Network error")):
        data, mime = await detect_and_fetch_media("http://bad-url.com/file")

    assert data is None
    assert mime is None
