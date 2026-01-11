from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator
import os

class Settings(BaseSettings):
    GREEN_API_ID: str
    GREEN_API_TOKEN: str
    GEMINI_API_KEY: str
    MONGO_URI: str = Field(default="mongodb://localhost:27017/proli_db")
    
    @field_validator("MONGO_URI", mode="before")
    @classmethod
    def assemble_mongo_uri(cls, v: str | None) -> str:
        if v and v != "mongodb://localhost:27017/proli_db":
            return v
        # Try common cloud provider env vars
        return os.getenv("MONGODB_URI") or os.getenv("MONGO_URL") or "mongodb://localhost:27017/proli_db"

    MONGO_TEST_URI: str | None = None
    MONGO_MAX_POOL_SIZE: int = 100
    MONGO_MIN_POOL_SIZE: int = 10
    MONGO_MAX_IDLE_TIME_MS: int = 30000
    ADMIN_PASSWORD: str | None = None
    
    # Redis
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_URL: str | None = Field(default=None)

    @field_validator("REDIS_URL", mode="before")
    @classmethod
    def assemble_redis_url(cls, v: str | None) -> str | None:
        if v:
            return v
        return os.getenv("REDIS_URL") or os.getenv("REDIS_TLS_URL")

    CLOUDINARY_CLOUD_NAME: str
    CLOUDINARY_API_KEY: str
    CLOUDINARY_API_SECRET: str
    AI_MODELS: list[str] = ["gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-1.5-flash"]
    TIMEZONE: str = "Asia/Jerusalem"
    
    # New Configs
    PROJECT_NAME: str = "Proli Bot Server"
    BACKEND_CORS_ORIGINS: list[str] = ["*"]
    MAX_CHAT_HISTORY: int = 20
    ENVIRONMENT: str = "development"  # "production" or "development"
    LOG_LEVEL: str = "INFO"
    
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()