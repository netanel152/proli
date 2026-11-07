import asyncio

from app.core.logger import logger
from app.core.constants import Defaults
from app.core.http_client import get_http_client
from app.providers.whatsapp.cloud_api import META_MEDIA_SCHEME, fetch_meta_media
from app.services.cloudinary_client_service import upload_media_bytes


async def resolve_inbound_media(media_url: str | None) -> str | None:
    """PRO-89: turn a ``meta-media://<id>`` marker into a permanent URL.

    Meta webhooks carry a media *id*; the real CDN URL needs an authorized
    fetch and expires within minutes, so the route enqueues the marker and the
    worker re-hosts the bytes on Cloudinary here — downstream code (lead
    ``media_urls``, the pro's offer message, the AI engine) then sees an
    ordinary public URL, exactly as it always has. Failure degrades to
    text-only processing (returns ``None``), never to a crashed task.

    Called only past the consent gate: the upload is a durable copy of the
    customer's file on a third party, so it must not happen before they agree.
    """
    if not media_url or not media_url.startswith(META_MEDIA_SCHEME):
        return media_url
    media_id = media_url[len(META_MEDIA_SCHEME) :]
    data, _mime = await fetch_meta_media(media_id)
    if data is None:
        logger.error(f"Could not fetch Meta media {media_id} — processing text only.")
        return None
    hosted_url = await asyncio.to_thread(upload_media_bytes, data)
    if hosted_url is None:
        logger.error(f"Could not re-host Meta media {media_id} — processing text only.")
    return hosted_url


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
                media_mime = resp.headers.get(
                    "Content-Type", Defaults.DEFAULT_MIME_TYPE
                )
                logger.info(
                    f"Downloaded image media: {len(media_data)} bytes, type: {media_mime}"
                )
            else:
                logger.warning(
                    f"Failed to download media from {media_url}, status: {resp.status_code}"
                )

    except Exception as e:
        logger.error(f"Error handling media check: {e}")

    return media_data, media_mime
