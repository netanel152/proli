from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator, model_validator
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

    # Backup (optional - S3 upload). AWS credentials are read by boto3
    # directly from the environment; no need to re-declare them here.
    BACKUP_S3_BUCKET: str | None = None

    # SMS fallback (optional)
    SMS_API_KEY: str | None = None
    SMS_SENDER_ID: str = "Proli"
    SMS_API_URL: str = "https://api.inforu.co.il/SendSMS/SendSMS"

    # Sentry (optional — error reporting for worker)
    # When unset, Sentry is disabled (no-op). When set, only CRITICAL-level
    # log events are forwarded as issues; regular INFO/WARNING/ERROR stays in
    # stdout/loguru. See SENTRY_SETUP.md for alert rule recommendations.
    SENTRY_DSN: str | None = None
    SENTRY_TRACES_SAMPLE_RATE: float = 0.0  # no perf tracing by default

    # Geocoding (Google Maps) — resolves Israeli city/address names to
    # coordinates for the matching service's $geoNear pipeline. When unset,
    # geocoding falls back to the static ISRAEL_CITIES_COORDS dict only.
    # Enabling this is what closes the gap for cities not in the static
    # dict (e.g. ראש העין, תל-מונד, טמרה) without shipping a new release.
    GOOGLE_MAPS_API_KEY: str | None = None
    # Negative cache TTL (seconds). Positive lookups are cached forever
    # — city coordinates don't move. Failures expire so we re-try after
    # a quota reset or a corrected spelling.
    GEOCODING_NEGATIVE_TTL_SECONDS: int = 86400  # 24 hours

    @model_validator(mode="after")
    def require_webhook_token_in_production(self) -> "Settings":
        # Production must have an explicit WEBHOOK_TOKEN — the webhook endpoint
        # is the only untrusted-internet surface. Dev/test keep the None default
        # so local boots and pytest don't need to fabricate a value.
        if self.ENVIRONMENT == "production" and not self.WEBHOOK_TOKEN:
            raise ValueError(
                "WEBHOOK_TOKEN is required when ENVIRONMENT='production'. "
                "Set WEBHOOK_TOKEN to a random secret and configure the same "
                "value as the X-Webhook-Token header in Green API."
            )
        return self

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()