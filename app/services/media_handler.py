from app.core.logger import logger
from app.core.constants import Defaults
from app.core.http_client import get_http_client


async def detect_and_fetch_media(media_url: str) -> tuple[bytes | None, str | None]:
    """
    Detects media type via HEAD request and fetches content if image.
    For audio/video, returns (None, mime_type) so URL can be passed to AI Engine.
    For images, returns (bytes, mime_type).

    Returns:
        (media_data, media_mime) tuple
    """
    media_data = None
    media_mime = None

    try:
        client = await get_http_client()
        head_resp = await client.head(media_url)
        content_type = head_resp.headers.get("Content-Type", "")

        if "audio" in content_type or "video" in content_type:
            media_mime = content_type
            logger.info(f"Detected A/V media ({media_mime}). Passing URL to AI Engine.")
        else:
            resp = await client.get(media_url)
            if resp.status_code == 200:
                media_data = resp.content
                media_mime = resp.headers.get("Content-Type", Defaults.DEFAULT_MIME_TYPE)
                logger.info(f"Downloaded image media: {len(media_data)} bytes, type: {media_mime}")
            else:
                logger.warning(f"Failed to download media from {media_url}, status: {resp.status_code}")

    except Exception as e:
        logger.error(f"Error handling media check: {e}")

    return media_data, media_mime
