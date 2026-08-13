"""PRO-89 — Settings.require_cloud_provider_config: selecting
WHATSAPP_PROVIDER=cloud without the Meta credentials it needs must fail at
boot, not at the first send. Follows the direct-construction pattern in
tests/test_settings_secret_masking.py (Settings() with the minimal required
fields plus the case under test) rather than monkeypatching a live settings
object, since the validator only runs at construction time.
"""

import pytest
from pydantic import ValidationError

from app.core.config import Settings

_BASE_REQUIRED = dict(
    GEMINI_API_KEY="k",
    CLOUDINARY_CLOUD_NAME="c",
    CLOUDINARY_API_KEY="k2",
    CLOUDINARY_API_SECRET="s",
)


def test_cloud_provider_without_meta_creds_fails_validation():
    """WHATSAPP_DRY_RUN must be pinned False here — the project .env sets it
    true for local dev, which would otherwise exempt this construction from
    the transmit-tier requirement being tested."""
    with pytest.raises(ValidationError, match="META_ACCESS_TOKEN"):
        Settings(**_BASE_REQUIRED, WHATSAPP_PROVIDER="cloud", WHATSAPP_DRY_RUN=False)


def test_cloud_provider_with_dry_run_true_boots_without_meta_creds():
    """The emergency mute must keep working even on a half-configured box —
    the outage runbook has the operator flip WHATSAPP_DRY_RUN under
    pressure."""
    s = Settings(
        **_BASE_REQUIRED,
        WHATSAPP_PROVIDER="cloud",
        WHATSAPP_DRY_RUN=True,
    )
    assert s.WHATSAPP_PROVIDER == "cloud"
    assert s.WHATSAPP_DRY_RUN is True


def test_cloud_provider_in_staging_without_webhook_auth_creds_fails(monkeypatch):
    """META_ACCESS_TOKEN + META_PHONE_NUMBER_ID satisfy the transmit tier, but
    staging (prod-like) additionally requires META_APP_SECRET +
    META_VERIFY_TOKEN to authenticate inbound webhooks."""
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)

    with pytest.raises(ValidationError, match="META_APP_SECRET"):
        Settings(
            **_BASE_REQUIRED,
            WHATSAPP_PROVIDER="cloud",
            ENVIRONMENT="staging",
            WEBHOOK_TOKEN="wt",
            META_ACCESS_TOKEN="token",
            META_PHONE_NUMBER_ID="123",
        )


def test_cloud_provider_in_staging_with_all_four_meta_settings_boots(monkeypatch):
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)

    s = Settings(
        **_BASE_REQUIRED,
        WHATSAPP_PROVIDER="cloud",
        ENVIRONMENT="staging",
        WEBHOOK_TOKEN="wt",
        META_ACCESS_TOKEN="token",
        META_PHONE_NUMBER_ID="123",
        META_APP_SECRET="app-secret",
        META_VERIFY_TOKEN="verify-token",
    )
    assert s.META_ACCESS_TOKEN.get_secret_value() == "token"
    assert s.META_APP_SECRET.get_secret_value() == "app-secret"


def test_cloud_provider_in_development_does_not_require_webhook_auth_creds():
    """Development is not prod-like — only the transmit tier applies."""
    s = Settings(
        **_BASE_REQUIRED,
        WHATSAPP_PROVIDER="cloud",
        META_ACCESS_TOKEN="token",
        META_PHONE_NUMBER_ID="123",
    )
    assert s.META_APP_SECRET is None
    assert s.META_VERIFY_TOKEN is None


def test_dryrun_provider_never_requires_meta_creds():
    s = Settings(**_BASE_REQUIRED, WHATSAPP_PROVIDER="dryrun")
    assert s.META_ACCESS_TOKEN is None
