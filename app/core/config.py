import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    GREEN_API_ID: str
    GREEN_API_TOKEN: str
    GEMINI_API_KEY: str
    MONGO_URI: str
    ADMIN_PASSWORD: str
    CLOUDINARY_CLOUD_NAME: str
    CLOUDINARY_API_KEY: str
    CLOUDINARY_API_SECRET: str
    
    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()