import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    GREEN_API_ID: str
    GREEN_API_TOKEN: str
    GEMINI_API_KEY: str
    MONGO_URI: str
    ADMIN_PASSWORD: str
    
    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
