from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, SecretStr, field_validator, model_validator
import os

from app.core.constants import (
    DEVELOPMENT_ENV,
    DRYRUN_PROVIDER,
    PRODUCTION_ENV,
    PROD_LIKE_ENVIRONMENTS,
    VALID_ENVIRONMENTS,
    VALID_WHATSAPP_PROVIDERS,
    normalize_environment,
)

DEFAULT_MONGO_URI = "mongodb://localhost:27017/proli_db"


def _unwrap(v):
    """Accept either a raw value or an already-wrapped SecretStr in a
    ``mode="before"`` validator.

    Env vars and ``.env`` always arrive as ``str``, but a caller constructing
    ``Settings(MONGO_URI=SecretStr(...))`` directly — tests do — would otherwise
    have the validator compare a SecretStr against a plain string and silently
    take the wrong branch.
    """
    return v.get_secret_value() if isinstance(v, SecretStr) else v


class Settings(BaseSettings):
    """Application configuration.

    PRO-94: every credential-bearing field is typed ``SecretStr``. This is not
    cosmetic — pydantic's default ``__repr__`` prints *all* field values, so
    before this change any traceback that touched the Settings object (a
    pytest ``AttributeError``, a Sentry exception context, a Railway deploy
    log) dumped the entire secret set in plaintext. It happened for real on
    2026-08-09 and burned a token that had been rotated an hour earlier.

    ``SecretStr`` masks on ``repr()``, ``str()`` and f-string interpolation, so
    the leak is structurally impossible rather than merely unlikely. The cost
    is that every consumer must call ``.get_secret_value()`` at the point of
    use — deliberately at the point of use only, never into a local that then
    flows through a log line. ``app/core/logger.py``'s redaction filter is kept
    as a second layer for values that escape by some other route (PRO-80).

    Any new credential — ``*_TOKEN``, ``*_KEY``, ``*_SECRET``, a DSN or a URI
    with embedded auth — must be typed ``SecretStr`` here; the logger's
    redaction list picks it up automatically.
    """

    GEMINI_API_KEY: SecretStr
    MONGO_URI: SecretStr = Field(default=SecretStr(DEFAULT_MONGO_URI))

    @field_validator("MONGO_URI", mode="before")
    @classmethod
    def assemble_mongo_uri(cls, v) -> str:
        raw = _unwrap(v)
        if raw and raw != DEFAULT_MONGO_URI:
            return raw
        # Try common cloud provider env vars
        return os.getenv("MONGODB_URI") or os.getenv("MONGO_URL") or DEFAULT_MONGO_URI

    # Carries the same Atlas credentials as MONGO_URI — same leak class.
    MONGO_TEST_URI: SecretStr | None = None
    MONGO_MAX_POOL_SIZE: int = 100
    MONGO_MIN_POOL_SIZE: int = 10
    MONGO_MAX_IDLE_TIME_MS: int = 30000
    ADMIN_PASSWORD: SecretStr | None = None

    @field_validator("ADMIN_PASSWORD", mode="before")
    @classmethod
    def validate_admin_password(cls, v):
        raw = _unwrap(v)
        if raw is not None and len(raw) < 8:
            raise ValueError("ADMIN_PASSWORD must be at least 8 characters long")
        return raw

    # Redis
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    # A managed Redis DSN embeds its password (redis://:pw@host) — secret.
    REDIS_URL: SecretStr | None = Field(default=None)

    @field_validator("REDIS_URL", mode="before")
    @classmethod
    def assemble_redis_url(cls, v) -> str | None:
        raw = _unwrap(v)
        if raw:
            return raw
        return os.getenv("REDIS_URL") or os.getenv("REDIS_TLS_URL")

    # The cloud *name* is public (it appears in every delivery URL); the key
    # and secret are not.
    CLOUDINARY_CLOUD_NAME: str
    CLOUDINARY_API_KEY: SecretStr
    CLOUDINARY_API_SECRET: SecretStr
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
    WEBHOOK_TOKEN: SecretStr | None = None
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

    # PRO-86: which transport the outbound facade uses.
    #   dryrun — logs, never transmits (the offline test suite + E2E harness)
    #   cloud  — Meta Cloud API; stub until PRO-89 implements it
    # An unrecognised value falls back to dryrun rather than to anything that
    # transmits, and says so at ERROR.
    WHATSAPP_PROVIDER: str = DRYRUN_PROVIDER

    @field_validator("WHATSAPP_PROVIDER", mode="before")
    @classmethod
    def validate_whatsapp_provider(cls, v: str | None) -> str:
        """Reject an unknown transport at boot rather than at the first send.

        ``build_provider()`` also falls back to dry-run on an unknown name, but
        that fallback surfaces as one ERROR line inside a worker — i.e. a service
        that looks healthy and silently sends nothing. Failing here puts the typo
        in the deploy log instead.
        """
        if v is None or not str(v).strip():
            return DRYRUN_PROVIDER
        normalized = str(v).strip().lower()
        if normalized not in VALID_WHATSAPP_PROVIDERS:
            raise ValueError(
                f"WHATSAPP_PROVIDER must be one of "
                f"{', '.join(VALID_WHATSAPP_PROVIDERS)} (got {v!r})"
            )
        return normalized

    # PRO-79: when True, force the DryRunProvider regardless of
    # WHATSAPP_PROVIDER, so dev / simulation never cold-initiates a real message.
    # This is the operator's emergency mute — the WhatsApp outage runbook has it
    # switched on during an incident — so it deliberately overrides provider
    # selection rather than sitting alongside it. Default False so a
    # misconfigured env can never silently disable production sends (never
    # coupled to ENVIRONMENT for the same reason).
    #
    # PRO-86 moved the divergence up a layer: it used to be an httpx transport
    # swap underneath a real Green client, and is now provider *selection*. The
    # facade above it — circuit breaker, kill switch, payload construction — is
    # the same code either way, so the offline E2E harness (PRO-83) still
    # asserts on the exact bytes a recipient would have received.
    WHATSAPP_DRY_RUN: bool = False
    LOG_LEVEL: str = "INFO"

    # Backup (optional - S3 upload). AWS credentials are read by boto3
    # directly from the environment; no need to re-declare them here.
    BACKUP_S3_BUCKET: str | None = None

    # Sentry (optional — error reporting for worker)
    # When unset, Sentry is disabled (no-op). When set, only CRITICAL-level
    # log events are forwarded as issues; regular INFO/WARNING/ERROR stays in
    # stdout/loguru. See SENTRY_SETUP.md for alert rule recommendations.
    # A DSN embeds the project's ingest key — anyone holding it can forge
    # events into the project, so it is a credential, not a URL.
    SENTRY_DSN: SecretStr | None = None
    SENTRY_TRACES_SAMPLE_RATE: float = 0.0  # no perf tracing by default

    # Geocoding (Google Maps) — resolves Israeli city/address names to
    # coordinates for the matching service's $geoNear pipeline. When unset,
    # geocoding falls back to the static ISRAEL_CITIES_COORDS dict only.
    # Enabling this is what closes the gap for cities not in the static
    # dict (e.g. ראש העין, תל-מונד, טמרה) without shipping a new release.
    GOOGLE_MAPS_API_KEY: SecretStr | None = None
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

    @model_validator(mode="after")
    def require_webhook_auth_in_prod_like(self):
        """PRO-86: ``/webhook`` has exactly one authentication mechanism left.

        The route used to also reject payloads whose ``instanceData.idInstance``
        did not match ``GREEN_API_INSTANCE_ID``. That check died with the Green
        provider, so an unset ``WEBHOOK_TOKEN`` now means *no* authentication at
        all: any caller could POST a crafted payload and make the worker enqueue
        AI work and send WhatsApp messages to an arbitrary chat id.

        Refused at boot rather than left to discover in production. Development
        is exempt so a local checkout still runs without ceremony; PRO-89 should
        add the Meta ``X-Hub-Signature-256`` HMAC on top of this.
        """
        if self.is_prod_like and not self.WEBHOOK_TOKEN:
            raise ValueError(
                "WEBHOOK_TOKEN is required when ENVIRONMENT is staging or "
                "production: it is the only thing authenticating POST /webhook "
                "since PRO-86 removed the sender instance-id check."
            )
        return self

    def iter_secret_values(self, *, min_length: int = 8) -> list[str]:
        """Every configured secret, unwrapped — for the log redaction filter only.

        Enumerates the ``SecretStr`` fields rather than naming them, so a
        credential added in a future ticket (PRO-89's ``META_ACCESS_TOKEN``) is
        covered the moment it is typed correctly, with no second list to keep
        in sync. Unset and empty values are skipped so nothing over-redacts.

        ``min_length`` guards the pathological case: a short secret (an operator
        setting ``ADMIN_PASSWORD=password123`` — or shorter) would otherwise
        turn every incidental occurrence of that substring in an unrelated log
        line into ``***REDACTED***``, which corrupts logs without protecting
        anything a value that guessable was ever going to protect.

        This is the only sanctioned bulk unwrap. Everywhere else, call
        ``.get_secret_value()`` on the one field you need, at the point of use.
        """
        values = []
        for field_name in type(self).model_fields:
            value = getattr(self, field_name, None)
            if not isinstance(value, SecretStr):
                continue
            raw = value.get_secret_value()
            if raw and len(raw) >= min_length:
                values.append(raw)
        return values

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
