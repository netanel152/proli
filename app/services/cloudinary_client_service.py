import cloudinary
import cloudinary.uploader
from app.core.config import settings
from app.core.logger import logger

cloudinary.config(
  cloud_name = settings.CLOUDINARY_CLOUD_NAME,
  api_key = settings.CLOUDINARY_API_KEY,
  api_secret = settings.CLOUDINARY_API_SECRET,
  secure = True
)

def upload_image(file_object) -> str | None:
    """Synchronous upload used by Admin Panel (Streamlit)."""
    try:
        response = cloudinary.uploader.upload(file_object)
        return response.get("secure_url")
    except Exception as e:
        logger.error(f"Error uploading to Cloudinary: {e}")
        return None
