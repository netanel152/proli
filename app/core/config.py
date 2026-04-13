from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator
import os

class Settings(BaseSettings):
    GREEN_API_INSTANCE_ID: str
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

    @field_validator("ADMIN_PASSWORD", mode="before")
    @classmethod
    def validate_admin_password(cls, v: str | None) -> str | None:
        if v is not None and len(v) < 8:
            raise ValueError("ADMIN_PASSWORD must be at least 8 characters long")
        return v
    
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
    # In production, set to your actual domain(s). Defaults to localhost only.
    BACKEND_CORS_ORIGINS: list[str] = ["http://localhost:8501", "http://localhost:3000"]
    MAX_CHAT_HISTORY: int = 20
    ADMIN_PHONE: str = "972524828796"
    WEBHOOK_TOKEN: str | None = None
    ENVIRONMENT: str = "development"  # "production" or "development"
    LOG_LEVEL: str = "INFO"

    # Backup (optional - S3 upload)
    BACKUP_S3_BUCKET: str | None = None
    AWS_ACCESS_KEY_ID: str | None = None
    AWS_SECRET_ACCESS_KEY: str | None = None
    AWS_REGION: str = "eu-west-1"

    # SMS fallback (optional)
    SMS_API_KEY: str | None = None
    SMS_SENDER_ID: str = "Proli"
    SMS_API_URL: str = "https://api.inforu.co.il/SendSMS/SendSMS"
    
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()