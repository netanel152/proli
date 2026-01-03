import cloudinary
import cloudinary.uploader
from app.core.config import settings

# Configure Cloudinary
cloudinary.config( 
  cloud_name = settings.CLOUDINARY_CLOUD_NAME, 
  api_key = settings.CLOUDINARY_API_KEY, 
  api_secret = settings.CLOUDINARY_API_SECRET,
  secure = True
)

def upload_image(file_object) -> str | None:
    """
    Uploads an image file object to Cloudinary and returns the secure URL.
    Returns None if upload fails.
    """
    try:
        # Cloudinary can handle file-like objects directly
        response = cloudinary.uploader.upload(file_object)
        return response.get("secure_url")
    except Exception as e:
        print(f"Error uploading to Cloudinary: {e}")
        return None
