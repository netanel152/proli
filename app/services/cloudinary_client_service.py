import io

import cloudinary
import cloudinary.uploader
from app.core.config import settings
from app.core.logger import logger

cloudinary.config(
    cloud_name=settings.CLOUDINARY_CLOUD_NAME,
    api_key=settings.CLOUDINARY_API_KEY.get_secret_value(),
    api_secret=settings.CLOUDINARY_API_SECRET.get_secret_value(),
    secure=True,
)


def upload_image(file_object) -> str | None:
    """Synchronous upload used by Admin Panel (Streamlit)."""
    try:
        response = cloudinary.uploader.upload(file_object)
        return response.get("secure_url")
    except Exception as e:
        logger.error(f"Error uploading to Cloudinary: {e}")
        return None


def upload_media_bytes(data: bytes) -> str | None:
    """Synchronous upload of raw media bytes; ``resource_type="auto"`` so
    audio/video land as playable assets, not broken images.

    PRO-89: inbound Meta media arrives as a short-lived, auth-gated CDN URL —
    useless to store on a lead or forward to a pro. The worker re-hosts the
    bytes here to get a permanent public URL. Blocking cloudinary SDK call —
    async callers must run it via ``asyncio.to_thread``.
    """
    try:
        response = cloudinary.uploader.upload(io.BytesIO(data), resource_type="auto")
        return response.get("secure_url")
    except Exception as e:
        logger.error(f"Error uploading media bytes to Cloudinary: {e}")
        return None
