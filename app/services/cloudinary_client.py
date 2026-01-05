import cloudinary
import cloudinary.uploader
import asyncio
from concurrent.futures import ThreadPoolExecutor
from app.core.config import settings

# Configure Cloudinary
cloudinary.config( 
  cloud_name = settings.CLOUDINARY_CLOUD_NAME, 
  api_key = settings.CLOUDINARY_API_KEY, 
  api_secret = settings.CLOUDINARY_API_SECRET,
  secure = True
)

# Create a thread pool for blocking I/O
executor = ThreadPoolExecutor(max_workers=5)

def upload_image(file_object) -> str | None:
    """
    Synchronous upload (Blocking) - used by Admin Panel (Streamlit).
    """
    try:
        response = cloudinary.uploader.upload(file_object)
        return response.get("secure_url")
    except Exception as e:
        print(f"Error uploading to Cloudinary: {e}")
        return None

async def upload_image_async(file_object) -> str | None:
    """
    Asynchronous upload (Non-blocking) - used by FastAPI Backend.
    Wraps the synchronous upload in a thread pool.
    """
    try:
        # Run synchronous upload in a separate thread
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(executor, cloudinary.uploader.upload, file_object)
        return response.get("secure_url")
    except Exception as e:
        print(f"Error uploading to Cloudinary: {e}")
        return None
