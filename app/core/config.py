from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator, model_validator
import os

from app.core.constants import (
    DEVELOPMENT_ENV,
    PRODUCTION_ENV,
    PROD_LIKE_ENVIRONMENTS,
    VALID_ENVIRONMENTS,
    normalize_environment,
)


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
        return (
            os.getenv("MONGODB_URI")
            or os.getenv("MONGO_URL")
            or "mongodb://localhost:27017/proli_db"
        )

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
    AI_MODELS: list[str] = [
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash",
        "gemini-2.5-flash",
        "gemini-1.5-flash",
    ]
    TIMEZONE: str = "Asia/Jerusalem"

    # New Configs
    PROJECT_NAME: str = "Proli Bot Server"
    # In production, set to your actual domain(s). Defaults to localhost only.
    BACKEND_CORS_ORIGINS: list[str] = ["http://localhost:8501", "http://localhost:3000"]
    MAX_CHAT_HISTORY: int = 20
    ADMIN_PHONE: str = "972524828796"
    # On-call number for high-urgency infra alerts (e.g. Green API deauth).
    # When unset, falls back to ADMIN_PHONE. Set to a separate operator's
    # number to route paging away from the day-to-day admin channel.
    ONCALL_PHONE: str | None = None
    WEBHOOK_TOKEN: str | None = None
    # PRO-34: exactly one of "development" | "staging" | "production".
    # Anything else fails fast at startup (see validate_environment) rather than
    # silently falling through to the non-prod branch everywhere.
    ENVIRONMENT: str = DEVELOPMENT_ENV

    @field_validator("ENVIRONMENT", mode="before")
    @classmethod
    def validate_environment(cls, v: str | None) -> str:
        """Normalize and reject unknown or empty environments.

        A typo like ``ENVIRONMENT=prod`` must never quietly degrade a deploy to
        dev behaviour — unstructured logs, no PII filter, no MONGO_URI guard,
        and a destructive seed that is no longer refused.

        An *unset* ENVIRONMENT never reaches this validator: pydantic defaults
        to ``validate_default=False``, so the field default is used as-is. An
        empty value here therefore means the operator explicitly set it to ""
        — a misconfiguration, not an omission — so it is rejected too. The
        blank-value idiom is real in this repo (``docker-compose.prod.yml``
        uses ``- MONGO_URI=`` to mean "inherit"), and Railway allows empty
        variables.
        """
        if v is None or not str(v).strip():
            raise ValueError(
                "ENVIRONMENT is set but empty. Either unset it (defaults to "
                f"{DEVELOPMENT_ENV}) or set one of: "
                f"{', '.join(VALID_ENVIRONMENTS)}"
            )
        normalized = normalize_environment(v)
        if normalized not in VALID_ENVIRONMENTS:
            raise ValueError(
                f"ENVIRONMENT must be one of {', '.join(VALID_ENVIRONMENTS)} "
                f"(got {v!r})"
            )
        return normalized

    @property
    def is_prod_like(self) -> bool:
        """True for ``staging`` and ``production``.

        The single check every call-site should use for "behave like a real
        deployment": structured JSON logs, PII filter, no localhost DB
        fallback. Use ``is_production`` only where staging and production must
        genuinely differ (e.g. the destructive-seed guard).
        """
        return self.ENVIRONMENT in PROD_LIKE_ENVIRONMENTS

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == PRODUCTION_ENV

    # PRO-79: when True, WhatsAppClient absorbs outbound sends at the transport
    # layer instead of calling Green API. Set WHATSAPP_DRY_RUN=true in local .env
    # so dev / simulation never cold-initiates a real message from the pilot
    # number. Default False keeps staging & production sending for real (never
    # coupled to ENVIRONMENT — a misconfigured env silently disabling prod sends
    # would be worse than this).
    #
    # PRO-83: the divergence is exactly one point — the httpx transport. Payload
    # construction, the PRO-71 circuit breaker and the retry policy all still run,
    # so a dry run exercises the real send path and the offline E2E harness can
    # assert on the exact bytes a recipient would have received.
    WHATSAPP_DRY_RUN: bool = False
    LOG_LEVEL: str = "INFO"

    # Backup (optional - S3 upload). AWS credentials are read by boto3
    # directly from the environment; no need to re-declare them here.
    BACKUP_S3_BUCKET: str | None = None

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
    # TTL for negative geocoding results (failures). Cached for 24h to avoid
    # immediate retries of unresolvable names, while still allowing for
    # a quota reset or a corrected spelling.
    # Applies only to *definitive* misses — Google answered and the name is
    # unresolvable (ZERO_RESULTS / outside Israel).
    # Lower-bounded: ttl=0 would take the `ex=0` branch in _cache_set, which
    # redis rejects and the helper swallows at debug — caching silently off,
    # every lookup re-paying the 5s call, with nothing in the logs.
    GEOCODING_NEGATIVE_TTL_SECONDS: int = Field(default=86400, ge=1)  # 24 hours
    # PRO-19: TTL for *transient* geocoding failures — missing API key,
    # REQUEST_DENIED, OVER_QUERY_LIMIT, network error. These say nothing
    # about the city, so they must not inherit the 24h TTL: a lapsed billing
    # account would otherwise keep every name attempted during the outage
    # unresolvable for a full day after the fix. Short, but non-zero: it also
    # sets how long the geocoding circuit breaker stays open, which is what
    # actually keeps a sustained Google outage off the dispatcher's hot path
    # (one 5s probe per window process-wide, not one per distinct name).
    # Upper-bounded because this value does double duty: raising it to "reduce
    # retry churn" also extends how long geocoding stays globally disabled
    # after a single blip.
    GEOCODING_TRANSIENT_TTL_SECONDS: int = Field(default=60, ge=1, le=600)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
